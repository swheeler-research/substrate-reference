"""
JPMorgan 'London Whale' modelled against the substrate.

Two operators:
  - JPMorgan (with desk_trader, risk_officer, and senior_risk_officer
    credentials in its delegation chain)
  - OCC (regulator, cross-operator audit)

Architectural property the substrate carries: confidence as a declared
spec section on each VaR model, propagated through composition into
authorise_position, gated by trade_clearance's confidence_gate. The
gate threshold is part of trade_clearance's content_id; substituting
a more lenient gate produces a new unit with a new content_id, visible
in audit.

Two VaR models:
  - var_model_v1 (calibrated; risk_factor 0.04)
  - var_model_v2 (recalibrated; risk_factor 0.015 — the 'new VaR model')

Each VaR model produces a `confidence` value reflecting on-invocation
calibration (how close the realised P&L volatility was to the
predicted figure). Drift criterion on the realised/predicted ratio
backstops systematic miscalibration over a window.

Run with: python -m examples.london_whale.run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.federation import CooperativeSubstrate, LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.operator import Operator, Substrate
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime

from examples.london_whale import _implementations as impls


CLEARANCE_MINIMUM_CONFIDENCE = 0.6


def _cred(name, parent_cids=(), authorities=("invoke:any",), policy_refs=()):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=authorities, credential_refs=tuple(parent_cids),
        policy_refs=tuple(policy_refs),
    )


def _make_substrate(custodian_name, code, creds):
    ledger = FederatedLedger()
    return Substrate(
        code=code, credentials=creds, ledger=ledger,
        custodian=LocalCustodian(name=custodian_name),
        runtime=Runtime(code, creds, ledger),
    )


def build_scene():
    code = CodeArchive()
    creds = CredentialsArchive()

    root = _cred("constitutional", authorities=("delegate:any",))
    root_cid = creds.put(root)

    jpm_root = _cred("jpmorgan_root", parent_cids=(root_cid,),
                     authorities=("delegate:any",))
    occ_root = _cred("occ_root", parent_cids=(root_cid,),
                     authorities=("delegate:any", "audit:any"))
    jpm_root_cid = creds.put(jpm_root)
    occ_root_cid = creds.put(occ_root)

    audit_coop = CredentialUnit(
        name="cooperative_substrate_jpmorgan_occ_audit",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:jpmorgan_plus_occ",
        authorities=("cross_operator:audit",),
        credential_refs=(jpm_root_cid, occ_root_cid),
    )
    audit_coop_cid = creds.put(audit_coop)

    # Implementations
    var_impl = python_implementation(impls.VAR_MODEL, name="var_model_impl")
    clearance_impl = python_implementation(impls.TRADE_CLEARANCE, name="trade_clearance_impl")
    limit_impl = python_implementation(impls.POSITION_LIMIT_POLICY, name="position_limit_policy_impl")
    authorise_impl = python_implementation(impls.AUTHORISE_POSITION, name="authorise_position_impl")
    outcome_impl = python_implementation(impls.REPORT_REALISED_PNL, name="report_realised_pnl_impl")
    backtest_impl = python_implementation(impls.BACKTEST_VAR_CALIBRATION, name="backtest_var_calibration_impl")
    audit_impl = python_implementation(impls.AUDIT_POSITIONS, name="audit_positions_impl")
    investigate_impl = python_implementation(impls.INVESTIGATE_BANK, name="investigate_bank_impl")
    var_impl_cid = code.put(var_impl)
    clearance_impl_cid = code.put(clearance_impl)
    limit_impl_cid = code.put(limit_impl)
    authorise_impl_cid = code.put(authorise_impl)
    outcome_impl_cid = code.put(outcome_impl)
    backtest_impl_cid = code.put(backtest_impl)
    audit_impl_cid = code.put(audit_impl)
    investigate_impl_cid = code.put(investigate_impl)

    # Position limit policy (binary; orthogonal to confidence)
    position_limit_policy = FunctionalUnit(
        name="position_limit_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "name": "position_limit_policy",
            "refuses_when": "notional > desk_limit AND no senior-risk escalation declared",
        },
        implementation_ref=limit_impl_cid,
        credential_refs=(root_cid, jpm_root_cid),
    )
    code.put(position_limit_policy)
    position_limit_policy_cid = position_limit_policy.content_id()

    position_limit_binding = CredentialUnit(
        name="position_limit_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance:position_limit",
        authorities=(),
        policy_refs=(position_limit_policy_cid,),
        credential_refs=(jpm_root_cid,),
    )
    position_limit_binding_cid = creds.put(position_limit_binding)

    # Trade clearance: confidence_gate on incoming model confidence. The
    # threshold is part of the unit's content_id; substituting a more
    # lenient threshold produces a new unit content_id (visible in audit).
    trade_clearance = FunctionalUnit(
        name="trade_clearance",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "confidence": "float",
                "predicted_var_million_usd": "float",
            },
            "outputs": {"cleared": "bool", "confidence": "float"},
            "confidence_gate": {
                "minimum_confidence": CLEARANCE_MINIMUM_CONFIDENCE,
                "applies_to_field": "confidence",
            },
            # Pass through the incoming confidence so propagation downstream works.
            "confidence": {
                "produces": True,
                "calibration": "passes through the incoming confidence after gate",
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=clearance_impl_cid,
        credential_refs=(root_cid, jpm_root_cid),
    )
    code.put(trade_clearance)
    trade_clearance_cid = trade_clearance.content_id()

    # Two VaR models. v1 calibrated; v2 recalibrated.
    def make_var_model(name, calibration_note):
        return FunctionalUnit(
            name=name,
            contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
            spec={
                "inputs": {
                    "position_notional_million_usd": "float",
                    "risk_factor": "float",
                    "actual_realised_volatility_million_usd": "float",
                },
                "outputs": {
                    "predicted_var_million_usd": "float",
                    "realised_over_predicted_ratio": "float",
                    "confidence": "float",
                },
                "drift_criteria": [
                    {"type": "mean_in", "field": "realised_over_predicted_ratio",
                     "bound": [0.8, 1.5], "window": 4},
                ],
                # Confidence-as-architectural-property declaration.
                "confidence": {
                    "produces": True,
                    "output_field": "confidence",
                    "calibration": (
                        "per-invocation: 1.0 at realised/predicted ratio of 1.0; "
                        "drops linearly to 0 as the ratio diverges by 2x"
                    ),
                    "acceptance_band": [0.0, 1.0],
                    # No propagation: var_model is a leaf (no sub-units invoked).
                },
                "calibration_note": calibration_note,
            },
            implementation_ref=var_impl_cid,
            credential_refs=(root_cid, jpm_root_cid),
        )

    var_model_v1 = make_var_model("var_model_v1", "calibrated risk_factor 0.04")
    var_model_v2 = make_var_model("var_model_v2", "recalibrated risk_factor 0.015 (new VaR model)")
    code.put(var_model_v1)
    code.put(var_model_v2)
    var_v1_cid = var_model_v1.content_id()
    var_v2_cid = var_model_v2.content_id()

    # Authorise position: composing unit that invokes var_model and
    # trade_clearance. Declares minimum propagation; the runtime injects
    # the propagated confidence into the unit's output automatically.
    authorise_position = FunctionalUnit(
        name="authorise_position",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "var_unit_id": "str",
                "clearance_unit_id": "str",
                "position_notional_million_usd": "float",
                "declared_desk_limit_million_usd": "float",
                "risk_factor": "float",
                "actual_realised_volatility_million_usd": "float",
                "escalation_credential_name": "str",
            },
            "outputs": {
                "position_authorised": "bool",
                "predicted_var_million_usd": "float",
                "confidence": "float",
            },
            # The substrate-native propagation: minimum across VaR and
            # clearance confidences. AND-composition: the authorisation
            # is no more confident than the least confident input.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": "minimum across composed VaR model and trade clearance confidences",
                "acceptance_band": [0.0, 1.0],
                "propagation": "minimum",
            },
        },
        implementation_ref=authorise_impl_cid,
        credential_refs=(root_cid, jpm_root_cid, position_limit_binding_cid),
        functional_refs=(
            position_limit_policy_cid, var_v1_cid, var_v2_cid, trade_clearance_cid,
        ),
        state_refs=(limit_impl_cid, var_impl_cid, clearance_impl_cid),
    )
    code.put(authorise_position)
    authorise_position_cid = authorise_position.content_id()

    # Outcome report unit (records realised P&L by position_id; used by backtest).
    report_realised_pnl = FunctionalUnit(
        name="report_realised_pnl",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"position_id": "str", "realised_loss_million_usd": "float"},
            "outputs": {"position_id": "str", "realised_loss_million_usd": "float"},
        },
        implementation_ref=outcome_impl_cid,
        credential_refs=(root_cid, jpm_root_cid),
    )
    code.put(report_realised_pnl)

    # Backtest: walks the ledger, pairs predictions with outcomes by
    # position_id, computes VaR exceedance rate, refuses if calibration
    # claim violated. The backtest impl uses substrate.backtest's
    # canonical metric functions.
    backtest_var_calibration = FunctionalUnit(
        name="backtest_var_calibration",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "authorise_unit_id": "str",
                "outcome_unit_id": "str",
                "max_exceedance_rate": "float",
            },
            "outputs": {"verdict": "str"},
        },
        implementation_ref=backtest_impl_cid,
        credential_refs=(root_cid, jpm_root_cid),
        functional_refs=(report_realised_pnl.content_id(),),
        state_refs=(outcome_impl_cid,),
    )
    code.put(backtest_var_calibration)

    # Audit unit on JPM side, exposed via audit coop
    audit_positions = FunctionalUnit(
        name="audit_positions",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"authorise_unit_id": "str"}},
        implementation_ref=audit_impl_cid,
        credential_refs=(root_cid, jpm_root_cid, occ_root_cid, audit_coop_cid),
    )
    code.put(audit_positions)

    # Investigate unit on OCC side
    investigate_bank = FunctionalUnit(
        name="investigate_bank",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"bank_operator_id": "str", "audit_unit_id": "str",
                         "audit_inputs": "dict"}},
        implementation_ref=investigate_impl_cid,
        credential_refs=(root_cid, occ_root_cid, jpm_root_cid, audit_coop_cid),
        functional_refs=(audit_positions.content_id(),),
        state_refs=(audit_impl_cid,),
    )
    code.put(investigate_bank)

    # Operating credentials
    desk_trader = _cred("desk_trader", parent_cids=(jpm_root_cid,))
    risk_officer = _cred("risk_officer", parent_cids=(jpm_root_cid,))
    senior_risk_officer = _cred("senior_risk_officer", parent_cids=(jpm_root_cid,))
    occ_inspector = _cred(
        "occ_inspector", parent_cids=(occ_root_cid,),
        authorities=("invoke:any", "cross_operator:audit"),
    )
    desk_trader_cid = creds.put(desk_trader)
    risk_officer_cid = creds.put(risk_officer)
    senior_risk_officer_cid = creds.put(senior_risk_officer)
    occ_inspector_cid = creds.put(occ_inspector)

    # Operators
    jpm = Operator(name="JPMorgan", root_credential=jpm_root,
                   substrate=_make_substrate("jpmorgan_custodian", code, creds))
    occ = Operator(name="OCC", root_credential=occ_root,
                   substrate=_make_substrate("occ_custodian", code, creds))

    audit_arrangement = CooperativeSubstrate(cooperative_credential=audit_coop)
    audit_arrangement.add(jpm)
    audit_arrangement.add(occ)

    # Compile and register
    jpm.runtime.register_compiled(compile_unit(var_model_v1, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(var_model_v2, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(trade_clearance, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(position_limit_policy, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(authorise_position, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(report_realised_pnl, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(backtest_var_calibration, code, creds, custodian=jpm.custodian))
    jpm.runtime.register_compiled(compile_unit(audit_positions, code, creds, custodian=audit_arrangement.custodian))
    occ.runtime.register_compiled(compile_unit(investigate_bank, code, creds, custodian=audit_arrangement.custodian))

    return {
        "jpm": jpm, "occ": occ,
        "audit_arrangement": audit_arrangement,
        "var_model_v1": var_model_v1, "var_v1_cid": var_v1_cid,
        "var_model_v2": var_model_v2, "var_v2_cid": var_v2_cid,
        "trade_clearance": trade_clearance, "trade_clearance_cid": trade_clearance_cid,
        "authorise_position": authorise_position,
        "report_realised_pnl": report_realised_pnl,
        "backtest_var_calibration": backtest_var_calibration,
        "audit_positions": audit_positions,
        "investigate_bank": investigate_bank,
        "desk_trader_cid": desk_trader_cid,
        "risk_officer_cid": risk_officer_cid,
        "senior_risk_officer_cid": senior_risk_officer_cid,
        "occ_inspector_cid": occ_inspector_cid,
    }


def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _authorise(scene, var_unit_id, risk_factor, notional, desk_limit,
               actual_vol, escalation_credential_name, invoking_cid, label,
               position_id=""):
    jpm = scene["jpm"]
    r = jpm.runtime.invoke(
        scene["authorise_position"].content_id(),
        {
            "var_unit_id": var_unit_id,
            "clearance_unit_id": scene["trade_clearance"].content_id(),
            "position_notional_million_usd": notional,
            "declared_desk_limit_million_usd": desk_limit,
            "risk_factor": risk_factor,
            "actual_realised_volatility_million_usd": actual_vol,
            "escalation_credential_name": escalation_credential_name,
            "position_id": position_id,
        },
        invoking_cid,
    )
    if isinstance(r, Permit):
        out = r.output
        conf = out.get("confidence")
        conf_str = f"propagated_confidence={conf:.3f}" if isinstance(conf, (int, float)) else "confidence=n/a"
        if out.get("position_authorised"):
            print(f"  {label}: PERMIT  notional={notional}m  "
                  f"predicted_VaR={out.get('predicted_var_million_usd'):.2f}m  {conf_str}")
            if out.get("escalation_credential_name"):
                print(f"      escalation_recorded: {out['escalation_credential_name']}")
        else:
            print(f"  {label}: not_authorised  rationale={out.get('rationale','')[:160]}  {conf_str}")
        return out
    else:
        print(f"  {label}: REFUSE  {r.rationale[:240]}")
        return None


def main() -> int:
    scene = build_scene()
    jpm = scene["jpm"]; occ = scene["occ"]

    _header("Setup")
    print(f"  Operators: JPMorgan, OCC (regulator)")
    print(f"  Cooperative substrate: JPMorgan + OCC (audit)")
    print()
    print(f"  Confidence-as-architectural-property is exercised end-to-end:")
    print(f"  - var_model (v1, v2): each declares a confidence section (produces=True,")
    print(f"    calibration claim, acceptance_band [0,1]). Per-invocation confidence")
    print(f"    is 1.0 at realised/predicted ratio 1.0; drops linearly as the ratio")
    print(f"    diverges.")
    print(f"  - trade_clearance: declares a confidence_gate (minimum {CLEARANCE_MINIMUM_CONFIDENCE}).")
    print(f"    The gate threshold is part of the unit's content_id; substituting a")
    print(f"    more lenient gate produces a new unit content_id, visible in audit.")
    print(f"  - authorise_position: declares propagation='minimum'. The runtime")
    print(f"    captures sub-unit confidences (VaR + clearance) and injects the")
    print(f"    minimum into the output. Impl override would let an author set a")
    print(f"    more sophisticated value; this demo uses the runtime-injected value.")
    print()
    print(f"  Position limit policy: refuses positions > desk_limit without a")
    print(f"  senior-risk escalation credential. Orthogonal to confidence.")
    print()
    print(f"  Drift criterion on each VaR model: mean realised/predicted ratio over")
    print(f"  last 4 observations must stay in [0.8, 1.5]. Backstop to confidence.")
    print()
    print(f"  Two VaR models with distinct content_ids:")
    print(f"    var_model_v1 (calibrated)        content_id={scene['var_v1_cid'][:16]}...")
    print(f"    var_model_v2 ('new VaR model')   content_id={scene['var_v2_cid'][:16]}...")
    print(f"  trade_clearance content_id={scene['trade_clearance_cid'][:16]}...")

    _header("Round 1: routine position under v1, realised vol matches predicted")
    print(f"  predicted_var = 500 * 0.04 = 20m; realised = 20m; ratio = 1.0; high confidence.")
    _authorise(
        scene,
        var_unit_id=scene["var_v1_cid"], risk_factor=0.04,
        notional=500.0, desk_limit=1000.0,
        actual_vol=20.0,
        escalation_credential_name="",
        invoking_cid=scene["desk_trader_cid"],
        label="desk_trader: position 500m under v1",
    )

    _header("Round 2: same notional under recalibrated v2, same actual volatility")
    print(f"  predicted_var = 500 * 0.015 = 7.5m; realised = 20m; ratio = 2.67;")
    print(f"  confidence ~ 0; the gate refuses with confidence below {CLEARANCE_MINIMUM_CONFIDENCE}.")
    print()
    _authorise(
        scene,
        var_unit_id=scene["var_v2_cid"], risk_factor=0.015,
        notional=500.0, desk_limit=1000.0,
        actual_vol=20.0,
        escalation_credential_name="",
        invoking_cid=scene["desk_trader_cid"],
        label="desk_trader: position 500m under v2",
    )
    print(f"  The substrate cannot mistake v2 for v1: their content_ids differ.")
    print(f"  Every act on the ledger records which model produced it.")

    _header("Round 3: v2 with realised volatilities feeding back; drift backstops")
    print(f"  Each trade reports its realised volatility back to the VaR model. v2")
    print(f"  systematically underestimates; ratios stay above 1.5 throughout. The")
    print(f"  drift criterion catches the systematic miscalibration over a window.")
    print()
    actuals = [16.0, 14.0, 15.0, 17.0]
    for i, av in enumerate(actuals):
        _authorise(
            scene,
            var_unit_id=scene["var_v2_cid"], risk_factor=0.015,
            notional=500.0, desk_limit=1000.0,
            actual_vol=av,
            escalation_credential_name="",
            invoking_cid=scene["desk_trader_cid"],
            label=f"desk_trader: trade {i + 1} (actual_vol={av}m)",
        )
    drifted = jpm.runtime.drift_monitor.is_drifted(scene["var_v2_cid"])
    print()
    print(f"  drift_monitor.is_drifted(var_model_v2) = {drifted}")

    _header("Round 4: position above desk limit (no escalation)")
    _authorise(
        scene,
        var_unit_id=scene["var_v1_cid"], risk_factor=0.04,
        notional=1500.0, desk_limit=1000.0,
        actual_vol=60.0,
        escalation_credential_name="",
        invoking_cid=scene["desk_trader_cid"],
        label="desk_trader: position 1500m (over limit, no escalation)",
    )
    print(f"  position_limit_policy refuses; trader cannot self-issue the escalation.")

    _header("Round 5: senior_risk_officer authorises the escalation")
    _authorise(
        scene,
        var_unit_id=scene["var_v1_cid"], risk_factor=0.04,
        notional=1500.0, desk_limit=1000.0,
        actual_vol=60.0,
        escalation_credential_name="senior_risk_officer",
        invoking_cid=scene["senior_risk_officer_cid"],
        label="senior_risk_officer: escalated position 1500m",
    )
    print(f"  The escalation credential is recorded on the ledger entry.")

    _header("Round 6: backtest the VaR calibration claim against realised P&L")
    print(f"  The calibration claim on var_model_v2 is a content-addressed string in")
    print(f"  the unit's spec. Drift (Round 3) is the runtime-window mechanism; the")
    print(f"  backtest is the complementary historical-ledger mechanism. The risk")
    print(f"  officer resets v2's drift state (judging the Round 3 drift transient)")
    print(f"  and resumes trading under v2; the backtest then checks v2's calibration")
    print(f"  claim against realised P&L drawn from the ledger.")
    print()
    jpm.reset_drift(scene["var_v2_cid"], scene["risk_officer_cid"])
    print(f"  reset_drift(var_model_v2) recorded as an administrative act.")
    print()
    print(f"  Setup: 10 positions authorised under v2 (predicted VaR = 500m * 0.015 =")
    print(f"  7.5m). Day-to-day volatility stays inside v2's drift band, so drift does")
    print(f"  not re-fire; but the realised P&L outcomes carry tail losses that v2's")
    print(f"  halved risk factor never priced. The backtest pairs authorise_position")
    print(f"  acts with report_realised_pnl outcomes by position_id and computes the")
    print(f"  exceedance rate (realised_loss > predicted_var).")
    print()
    realised_losses = [4.0, 5.0, 6.0, 18.0, 5.0, 4.0, 21.0, 6.0, 5.0, 17.0]
    # predicted_var for notional=500, risk_factor=0.015 is 7.5m. Losses > 7.5 are
    # exceedances. Indices 3 (18), 6 (21), 9 (17) exceed: 3/10 = 30%.
    for i, loss in enumerate(realised_losses):
        position_id = f"pos_{i:03d}"
        _authorise(
            scene,
            var_unit_id=scene["var_v2_cid"], risk_factor=0.015,
            notional=500.0, desk_limit=1000.0,
            actual_vol=7.5,
            escalation_credential_name="",
            invoking_cid=scene["desk_trader_cid"],
            label=f"position {position_id}",
            position_id=position_id,
        )
        # Outcome: report realised P&L for this position.
        jpm.runtime.invoke(
            scene["report_realised_pnl"].content_id(),
            {"position_id": position_id, "realised_loss_million_usd": loss},
            scene["risk_officer_cid"],
        )
    print()
    print(f"  Reported realised losses: {realised_losses}")
    print(f"  predicted_var for each = 7.5m. Losses > 7.5m are exceedances: 3 of 10.")
    print()
    print(f"  Backtest invocation (declared max_exceedance_rate = 0.05 for a 99% VaR):")
    r = jpm.runtime.invoke(
        scene["backtest_var_calibration"].content_id(),
        {
            "authorise_unit_id": scene["authorise_position"].content_id(),
            "outcome_unit_id": scene["report_realised_pnl"].content_id(),
            "max_exceedance_rate": 0.05,
        },
        scene["risk_officer_cid"],
    )
    if isinstance(r, Permit):
        print(f"    PERMIT  output: {r.output}")
    else:
        print(f"    REFUSE  rationale: {r.rationale[:240]}")
    print()
    print(f"  The backtest refusal is on the ledger. An authorised operator reads it")
    print(f"  and deprecates var_model_v2 via an administrative act; the deprecation")
    print(f"  propagates through the substrate's uniform invalidation surface. The")
    print(f"  substrate does not automate the cascade — every invalidation is an")
    print(f"  explicit ledger act.")
    print()
    jpm.deprecate_unit(scene["var_v2_cid"], scene["risk_officer_cid"])
    print(f"  deprecate_unit(var_model_v2) recorded. Attempting another position under v2:")
    _authorise(
        scene,
        var_unit_id=scene["var_v2_cid"], risk_factor=0.015,
        notional=500.0, desk_limit=1000.0,
        actual_vol=7.5,
        escalation_credential_name="",
        invoking_cid=scene["desk_trader_cid"],
        label="post-deprecation attempt",
        position_id="pos_post",
    )

    _header("Round 7: OCC cross-operator audit")
    print(f"  The OCC audits after the backtest and deprecation, so its forensic")
    print(f"  report reconstructs the full sequence: both VaR models, the drift")
    print(f"  event, the drift reset, the over-limit refusal, the escalation, the")
    print(f"  backtest refusal, and the deprecation.")
    print()
    r = occ.runtime.invoke(
        scene["investigate_bank"].content_id(),
        {
            "bank_operator_id": jpm.content_id,
            "audit_unit_id": scene["audit_positions"].content_id(),
            "audit_inputs": {
                "authorise_unit_id": scene["authorise_position"].content_id(),
            },
        },
        scene["occ_inspector_cid"],
    )
    if isinstance(r, Permit):
        out = r.output
        print(f"  Audit completed.  JPM ledger length: {out['ledger_length']}")
        print(f"  Permitted positions: {len(out['permitted_positions'])}")
        for p in out["permitted_positions"]:
            authorised = p.get("position_authorised")
            esc = p.get('escalation')
            conf = p.get("propagated_confidence")
            conf_str = f"conf={conf:.3f}" if isinstance(conf, (int, float)) else "conf=n/a"
            var_str = f"VaR={p['predicted_var_million_usd']:.2f}m" if p.get('predicted_var_million_usd') is not None else "VaR=?"
            esc_str = f" escalation={esc}" if esc else ""
            authd = "AUTHORISED" if authorised else "not_authorised"
            print(f"    permit  {authd}  notional={p['position_notional_million_usd']}m  "
                  f"{var_str}  {conf_str}{esc_str}")
        print(f"  Refused positions: {len(out['refused_positions'])}")
        for p in out["refused_positions"]:
            print(f"    refuse  notional={p['position_notional_million_usd']}m  by={p['by'][:16]}...")
            print(f"      rationale: {p.get('rationale','')[:160]}")
    else:
        print(f"  Audit refused: {r.rationale[:200]}")

    _header("Ledger integrity")
    for op in (jpm, occ):
        print(f"  {op.name}: {len(op.substrate.ledger)} acts, verify={op.substrate.ledger.verify()}")

    _header("What the substrate would have prevented in the London Whale — summary")
    print()
    print(f"  CONFIDENCE-AS-ARCHITECTURAL-PROPERTY. The VaR model declares a")
    print(f"  confidence section in its spec (produces=True, calibration claim,")
    print(f"  acceptance_band). The confidence value is produced at runtime by the")
    print(f"  impl based on calibration metrics. trade_clearance declares a")
    print(f"  confidence_gate with a compiled threshold; the threshold is in the")
    print(f"  unit's content_id, so substituting a more lenient gate produces a")
    print(f"  new content_id and is structurally visible in audit.")
    print()
    print(f"  SCALAR CONFIDENCE AGGREGATION. authorise_position declares")
    print(f"  propagation=minimum. The runtime collects sub-unit confidences (VaR +")
    print(f"  clearance) and injects the aggregated value into authorise_position's")
    print(f"  output. (NB: this is scalar aggregation of reliability metadata, NOT")
    print(f"  distributional uncertainty propagation; the latter is domain library")
    print(f"  territory — see docs/architectural_boundary.md.)")
    print()
    print(f"  BACKTEST CLOSES THE CALIBRATION LOOP. The VaR model's calibration")
    print(f"  claim is in spec. The backtest unit walks the ledger, pairs")
    print(f"  predictions with realised P&L outcomes, computes exceedance rate,")
    print(f"  refuses if the claim is violated. The refusal is on the ledger; the")
    print(f"  operator deprecates the unit via an administrative act; the")
    print(f"  deprecation propagates through the substrate's uniform invalidation")
    print(f"  surface. No automatic cascade — every invalidation is an explicit")
    print(f"  ledger event.")
    print()
    print(f"  CONTENT-ADDRESSED MODEL RECALIBRATION. var_model_v1 and var_model_v2")
    print(f"  have different content_ids. Every risk figure on the ledger records")
    print(f"  which model produced it. Quiet recalibration is impossible.")
    print()
    print(f"  DRIFT DETECTION as a backstop. Even if per-invocation confidences")
    print(f"  pass the gate threshold, systematic miscalibration trips drift over")
    print(f"  a window. The model refuses until reset by an authorised operator.")
    print()
    print(f"  ESCALATION AS A STRUCTURAL CREDENTIAL. position_limit_policy refuses")
    print(f"  positions beyond the desk limit without a senior-risk credential.")
    print(f"  Orthogonal to confidence: position-size policy and model-quality")
    print(f"  policy are separately enforced.")
    print()
    print(f"  CROSS-OPERATOR REGULATOR AUDIT. The OCC's audit produces a forensic")
    print(f"  report on its own ledger showing every position's propagated")
    print(f"  confidence, predicted VaR, escalation credential, and refusal rationale.")
    print()
    print(f"  WHAT THE SUBSTRATE DOES NOT PREVENT: an institution choosing to set")
    print(f"  weak calibration bounds, weak gate thresholds, weak position limits,")
    print(f"  or to authorise escalations as a matter of routine. The substrate")
    print(f"  makes the choices visible — bounds, thresholds, and escalations are")
    print(f"  content-addressed and audit-traceable — but the choices are the")
    print(f"  institution's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
