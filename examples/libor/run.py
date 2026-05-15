"""
LIBOR manipulation modelled against the substrate.

Five operators:
  - BankA, BankB, BankC (panel banks)
  - LIBORAdmin (administrator)
  - FCA (regulator)

Two cooperative substrates:
  - panel + admin (aggregation)
  - banks + admin + FCA (regulator audit)

Scenarios:
  1. Honest round: all 3 banks submit rates close to declared activity.
     Admin aggregates under the panel cooperative substrate's quorum.
     Benchmark produced.
  2. Manipulation attempt: BankA's trader submits a rate 10bps off the
     declared activity midpoint. divergence_policy refuses at submission
     time; the refusal is on the bank's ledger.
  3. Systematic-bias pattern: BankA submits 5 rates each 4bps off
     midpoint (passing the per-submission policy each time). After the
     fifth observation, drift detection on submit_rate fires; the next
     submission refuses with a drift rationale.
  4. Regulator audit: FCA cross-operator-invokes audit_submissions on
     BankA. The forensic report lands on the FCA ledger with every
     permitted and refused submission attributed.

Run with: python -m examples.libor.run
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

from examples.libor import _implementations as impls


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

    bank_a_root = _cred("bank_a_root", parent_cids=(root_cid,))
    bank_b_root = _cred("bank_b_root", parent_cids=(root_cid,))
    bank_c_root = _cred("bank_c_root", parent_cids=(root_cid,))
    admin_root = _cred("libor_admin_root", parent_cids=(root_cid,))
    fca_root = _cred("fca_root", parent_cids=(root_cid,),
                     authorities=("delegate:any", "audit:any"))
    bank_a_root_cid = creds.put(bank_a_root)
    bank_b_root_cid = creds.put(bank_b_root)
    bank_c_root_cid = creds.put(bank_c_root)
    admin_root_cid = creds.put(admin_root)
    fca_root_cid = creds.put(fca_root)

    # Panel aggregation cooperative substrate.
    panel_coop = CredentialUnit(
        name="cooperative_substrate_panel_aggregation",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:panel_plus_admin",
        authorities=("cross_operator:aggregate_libor",),
        credential_refs=(bank_a_root_cid, bank_b_root_cid, bank_c_root_cid, admin_root_cid),
    )
    panel_coop_cid = creds.put(panel_coop)

    # Regulator audit cooperative substrate.
    audit_coop = CredentialUnit(
        name="cooperative_substrate_regulator_audit",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:panel_plus_admin_plus_fca",
        authorities=("cross_operator:audit",),
        credential_refs=(bank_a_root_cid, bank_b_root_cid, bank_c_root_cid,
                         admin_root_cid, fca_root_cid),
    )
    audit_coop_cid = creds.put(audit_coop)

    # Implementations.
    submit_impl = python_implementation(impls.SUBMIT_RATE, name="submit_rate_impl")
    divergence_impl = python_implementation(impls.DIVERGENCE_POLICY, name="divergence_policy_impl")
    aggregate_impl = python_implementation(impls.AGGREGATE_LIBOR, name="aggregate_libor_impl")
    audit_impl = python_implementation(impls.AUDIT_BANK_SUBMISSIONS, name="audit_bank_submissions_impl")
    investigate_impl = python_implementation(impls.INVESTIGATE_PANEL, name="investigate_panel_impl")
    submit_impl_cid = code.put(submit_impl)
    divergence_impl_cid = code.put(divergence_impl)
    aggregate_impl_cid = code.put(aggregate_impl)
    audit_impl_cid = code.put(audit_impl)
    investigate_impl_cid = code.put(investigate_impl)

    # Divergence policy (functional unit).
    divergence_policy = FunctionalUnit(
        name="divergence_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "name": "divergence_policy",
            "evaluates": "submitted_rate vs declared activity midpoint",
            "refuses_when": "|submitted - midpoint| > 5 bps OR observed_volume == 0",
        },
        implementation_ref=divergence_impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(divergence_policy)
    divergence_policy_cid = divergence_policy.content_id()

    # Binding credential: brings the divergence policy into binding.
    divergence_binding = CredentialUnit(
        name="divergence_policy_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance:libor_panel",
        authorities=(),
        policy_refs=(divergence_policy_cid,),
        credential_refs=(root_cid,),
    )
    divergence_binding_cid = creds.put(divergence_binding)

    # Per-bank trader and admin credentials.
    trader_a = _cred("trader_a", parent_cids=(bank_a_root_cid,))
    trader_b = _cred("trader_b", parent_cids=(bank_b_root_cid,))
    trader_c = _cred("trader_c", parent_cids=(bank_c_root_cid,))
    admin_submitter = _cred("admin_submitter", parent_cids=(admin_root_cid,))
    fca_inspector = _cred(
        "fca_inspector", parent_cids=(fca_root_cid,),
        authorities=("invoke:any", "cross_operator:audit"),
    )
    trader_a_cid = creds.put(trader_a)
    trader_b_cid = creds.put(trader_b)
    trader_c_cid = creds.put(trader_c)
    admin_submitter_cid = creds.put(admin_submitter)
    fca_inspector_cid = creds.put(fca_inspector)

    # Operators.
    bank_a = Operator(name="BankA", root_credential=bank_a_root,
                      substrate=_make_substrate("bank_a_custodian", code, creds))
    bank_b = Operator(name="BankB", root_credential=bank_b_root,
                      substrate=_make_substrate("bank_b_custodian", code, creds))
    bank_c = Operator(name="BankC", root_credential=bank_c_root,
                      substrate=_make_substrate("bank_c_custodian", code, creds))
    admin = Operator(name="LIBORAdmin", root_credential=admin_root,
                     substrate=_make_substrate("libor_admin_custodian", code, creds))
    fca = Operator(name="FCA", root_credential=fca_root,
                   substrate=_make_substrate("fca_custodian", code, creds))

    # Cooperative substrate registries (per the pattern in robodebt: an
    # operator can be a member of multiple cooperative substrates; the
    # in-process back-ref is set per cooperative-substrate object).
    panel_arrangement = CooperativeSubstrate(cooperative_credential=panel_coop)
    panel_arrangement.add(bank_a)
    panel_arrangement.add(bank_b)
    panel_arrangement.add(bank_c)
    panel_arrangement.add(admin)

    audit_arrangement = CooperativeSubstrate(cooperative_credential=audit_coop)
    audit_arrangement.add(bank_a)
    audit_arrangement.add(bank_b)
    audit_arrangement.add(bank_c)
    audit_arrangement.add(admin)
    audit_arrangement.add(fca)

    # Per-bank submit_rate units. Behaviour-characterised with drift
    # criteria on the divergence-from-activity metric. Each unit declares
    # a confidence section: the substrate-native scalar reliability value
    # produced at runtime is computed from divergence and aggregated
    # through the aggregator.
    def make_submit_unit(bank_label, bank_root_cid):
        return FunctionalUnit(
            name=f"submit_rate_{bank_label.lower()}",
            contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
            spec={
                "inputs": {
                    "submitted_rate_bps": "float",
                    "observed_volume_million_usd": "float",
                    "observed_rate_range_low_bps": "float",
                    "observed_rate_range_high_bps": "float",
                    "submission_window": "str",
                    "bank_label": "str",
                },
                "outputs": {
                    "submitted_rate_bps": "float",
                    "divergence_from_activity_bps": "float",
                    "confidence": "float",
                },
                "drift_criteria": [
                    {"type": "mean_in", "field": "divergence_from_activity_bps",
                     "bound": [0.0, 3.0], "window": 5},
                ],
                "confidence": {
                    "produces": True,
                    "output_field": "confidence",
                    "calibration": (
                        "1.0 at zero divergence from declared activity midpoint; "
                        "drops linearly to 0 at the 5 bps policy threshold"
                    ),
                    "acceptance_band": [0.0, 1.0],
                },
            },
            implementation_ref=submit_impl_cid,
            credential_refs=(root_cid, bank_root_cid, divergence_binding_cid),
            functional_refs=(divergence_policy_cid,),
            state_refs=(divergence_impl_cid,),
        )

    submit_a = make_submit_unit("BankA", bank_a_root_cid)
    submit_b = make_submit_unit("BankB", bank_b_root_cid)
    submit_c = make_submit_unit("BankC", bank_c_root_cid)
    code.put(submit_a)
    code.put(submit_b)
    code.put(submit_c)

    # Aggregate unit (admin side, witnessed by the panel cooperative substrate).
    aggregate_unit = FunctionalUnit(
        name="aggregate_libor",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"submissions": "list", "submission_window": "str"},
            "outputs": {"benchmark_rate_bps": "float", "confidence": "float"},
            # Aggregate confidence is the mean of per-submission
            # confidences. The impl computes this explicitly because the
            # submissions arrive as data inputs (not as sub-invocations
            # of this call). Propagation declared here so the substrate
            # validates the section structure at compile-at-commit and
            # so the contract is structurally legible.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": (
                    "mean of per-submission confidences from the panel banks"
                ),
                "acceptance_band": [0.0, 1.0],
                "propagation": "mean",
            },
        },
        implementation_ref=aggregate_impl_cid,
        credential_refs=(root_cid, bank_a_root_cid, bank_b_root_cid,
                         bank_c_root_cid, admin_root_cid, panel_coop_cid),
    )
    code.put(aggregate_unit)

    # Audit units on each bank, exposed under the regulator audit coop.
    def make_audit_unit(bank_label, bank_root_cid):
        return FunctionalUnit(
            name=f"audit_submissions_{bank_label.lower()}",
            contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
            spec={"inputs": {"bank_label": "str"}},
            implementation_ref=audit_impl_cid,
            # The audit cooperative substrate's parent refs span every member
            # operator's root, so the wilful-inclusion requirement is that
            # each of those roots be top-level listed here.
            credential_refs=(root_cid, bank_a_root_cid, bank_b_root_cid,
                             bank_c_root_cid, admin_root_cid, fca_root_cid,
                             audit_coop_cid),
        )
    audit_a = make_audit_unit("BankA", bank_a_root_cid)
    audit_b = make_audit_unit("BankB", bank_b_root_cid)
    audit_c = make_audit_unit("BankC", bank_c_root_cid)
    code.put(audit_a)
    code.put(audit_b)
    code.put(audit_c)

    # Investigate unit (regulator side).
    investigate_unit = FunctionalUnit(
        name="investigate_panel",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"bank_operator_id": "str", "audit_unit_id": "str",
                         "audit_inputs": "dict"}},
        implementation_ref=investigate_impl_cid,
        credential_refs=(root_cid, fca_root_cid, bank_a_root_cid, bank_b_root_cid,
                         bank_c_root_cid, admin_root_cid, audit_coop_cid),
        functional_refs=(audit_a.content_id(), audit_b.content_id(), audit_c.content_id()),
        state_refs=(audit_impl_cid,),
    )
    code.put(investigate_unit)

    # Compile and register. Submit units witnessed by each bank's own
    # custodian (single-operator submissions). Divergence policy
    # registered on each bank's runtime (it is invoked there during
    # policy evaluation).
    for op, submit_unit in (
        (bank_a, submit_a), (bank_b, submit_b), (bank_c, submit_c),
    ):
        op.runtime.register_compiled(
            compile_unit(submit_unit, code, creds, custodian=op.custodian)
        )
        op.runtime.register_compiled(
            compile_unit(divergence_policy, code, creds, custodian=op.custodian)
        )

    # Aggregate unit on admin, witnessed by panel cooperative substrate.
    admin.runtime.register_compiled(
        compile_unit(aggregate_unit, code, creds, custodian=panel_arrangement.custodian)
    )

    # Audit units on each bank, witnessed by audit cooperative substrate.
    bank_a.runtime.register_compiled(
        compile_unit(audit_a, code, creds, custodian=audit_arrangement.custodian)
    )
    bank_b.runtime.register_compiled(
        compile_unit(audit_b, code, creds, custodian=audit_arrangement.custodian)
    )
    bank_c.runtime.register_compiled(
        compile_unit(audit_c, code, creds, custodian=audit_arrangement.custodian)
    )

    # Investigate unit on FCA, witnessed by audit cooperative substrate.
    fca.runtime.register_compiled(
        compile_unit(investigate_unit, code, creds, custodian=audit_arrangement.custodian)
    )

    return {
        "bank_a": bank_a, "bank_b": bank_b, "bank_c": bank_c,
        "admin": admin, "fca": fca,
        "panel_arrangement": panel_arrangement,
        "audit_arrangement": audit_arrangement,
        "submit_a": submit_a, "submit_b": submit_b, "submit_c": submit_c,
        "aggregate_unit": aggregate_unit,
        "audit_a": audit_a, "audit_b": audit_b, "audit_c": audit_c,
        "investigate_unit": investigate_unit,
        "trader_a_cid": trader_a_cid, "trader_b_cid": trader_b_cid,
        "trader_c_cid": trader_c_cid,
        "admin_submitter_cid": admin_submitter_cid,
        "fca_inspector_cid": fca_inspector_cid,
    }


def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _submit(scene, bank_key, submit_unit_key, trader_cid_key,
            submitted_rate, low, high, volume, window, label):
    op = scene[bank_key]
    submit_unit = scene[submit_unit_key]
    trader_cid = scene[trader_cid_key]
    bank_label = op.name
    r = op.runtime.invoke(
        submit_unit.content_id(),
        {
            "submitted_rate_bps": submitted_rate,
            "observed_rate_range_low_bps": low,
            "observed_rate_range_high_bps": high,
            "observed_volume_million_usd": volume,
            "submission_window": window,
            "bank_label": bank_label,
        },
        trader_cid,
    )
    if isinstance(r, Permit):
        out = r.output
        conf = out.get("confidence")
        conf_str = f"  confidence={conf:.3f}" if isinstance(conf, (int, float)) else ""
        print(f"  {label}: PERMIT  rate={out['submitted_rate_bps']}bps "
              f"divergence={out['divergence_from_activity_bps']:.2f}bps{conf_str}")
        return out
    else:
        print(f"  {label}: REFUSE  {r.rationale[:160]}")
        return None


def main() -> int:
    scene = build_scene()
    bank_a = scene["bank_a"]; bank_b = scene["bank_b"]; bank_c = scene["bank_c"]
    admin = scene["admin"]; fca = scene["fca"]

    _header("Setup")
    print(f"  Operators: BankA, BankB, BankC (panel), LIBORAdmin, FCA (regulator)")
    print(f"  Cooperative substrates:")
    print(f"    panel + admin   (aggregation witnessed jointly)")
    print(f"    panel + admin + FCA   (regulator audit)")
    print(f"  Per-submission policy: divergence_policy refuses if")
    print(f"    |submitted_rate - midpoint(observed_range)| > 5 bps")
    print(f"    OR observed_volume_million_usd == 0")
    print(f"  Drift criterion on submit_rate: mean divergence over last 5")
    print(f"    submissions must be in [0, 3] bps")

    # Each bank's "true" market activity for this window.
    activity = {
        "bank_a": {"low": 425.0, "high": 435.0, "volume": 500.0},
        "bank_b": {"low": 426.0, "high": 434.0, "volume": 480.0},
        "bank_c": {"low": 424.0, "high": 436.0, "volume": 520.0},
    }

    _header("Round 1: honest submissions, normal aggregation")
    outs = []
    for key, submit_key, trader_key in (
        ("bank_a", "submit_a", "trader_a_cid"),
        ("bank_b", "submit_b", "trader_b_cid"),
        ("bank_c", "submit_c", "trader_c_cid"),
    ):
        act = activity[key]
        out = _submit(
            scene, key, submit_key, trader_key,
            submitted_rate=(act["low"] + act["high"]) / 2.0 + 0.5,
            low=act["low"], high=act["high"], volume=act["volume"],
            window="2024-01-15", label=scene[key].name,
        )
        if out is not None:
            outs.append(out)

    print()
    print(f"  Admin invokes aggregate_libor under the panel cooperative substrate.")
    print(f"  Submitting banks contribute their permitted submissions.")
    r = admin.runtime.invoke(
        scene["aggregate_unit"].content_id(),
        {"submissions": outs, "submission_window": "2024-01-15"},
        scene["admin_submitter_cid"],
    )
    if isinstance(r, Permit):
        out = r.output
        agg_conf = out.get("confidence")
        print(f"    benchmark = {out['benchmark_rate_bps']} bps")
        print(f"    panel_size = {out['panel_size']}, trimmed = {out['trimmed_count']}")
        print(f"    contributing banks: {', '.join(out['contributing_banks'])}")
        if isinstance(agg_conf, (int, float)):
            print(f"    aggregate confidence (mean of submission confidences): {agg_conf:.3f}")

    _header("Round 2: BankA trader attempts a manipulated submission (10 bps off)")
    act = activity["bank_a"]
    _submit(
        scene, "bank_a", "submit_a", "trader_a_cid",
        submitted_rate=(act["low"] + act["high"]) / 2.0 + 10.0,
        low=act["low"], high=act["high"], volume=act["volume"],
        window="2024-01-16", label="BankA",
    )
    print(f"  divergence_policy refuses at submission time. The refusal is on")
    print(f"  BankA's ledger; the trader credential that attempted it is recorded.")

    _header("Round 3: BankA submits a systematic-bias pattern within the per-submission threshold")
    print(f"  Each submission is +4 bps off the activity midpoint (within the 5 bps")
    print(f"  policy threshold). The drift criterion (mean divergence over last 5")
    print(f"  submissions in [0, 3] bps) catches the systematic bias.")
    print()
    for i in range(5):
        _submit(
            scene, "bank_a", "submit_a", "trader_a_cid",
            submitted_rate=(act["low"] + act["high"]) / 2.0 + 4.0,
            low=act["low"], high=act["high"], volume=act["volume"],
            window=f"2024-01-{17 + i}", label=f"BankA submission {i + 1}",
        )
    drifted = bank_a.runtime.drift_monitor.is_drifted(scene["submit_a"].content_id())
    print()
    print(f"  drift_monitor.is_drifted(submit_rate_banka) = {drifted}")
    print()
    print(f"  BankA attempts another submission:")
    _submit(
        scene, "bank_a", "submit_a", "trader_a_cid",
        submitted_rate=(act["low"] + act["high"]) / 2.0 + 0.5,
        low=act["low"], high=act["high"], volume=act["volume"],
        window="2024-01-22", label="BankA submission 6",
    )
    print(f"  Drift refusal sticks until an authorised reset (administrative act).")

    _header("Round 4: FCA cross-operator audits BankA")
    r = fca.runtime.invoke(
        scene["investigate_unit"].content_id(),
        {
            "bank_operator_id": bank_a.content_id,
            "audit_unit_id": scene["audit_a"].content_id(),
            "audit_inputs": {
                "bank_label": "BankA",
                "submit_unit_id": scene["submit_a"].content_id(),
            },
        },
        scene["fca_inspector_cid"],
    )
    if isinstance(r, Permit):
        out = r.output
        print(f"  Audit completed.  bank_label={out['bank_label']}")
        print(f"  BankA ledger length: {out['ledger_length']}")
        print(f"  Permitted submissions: {len(out['permitted_submissions'])}")
        for s in out["permitted_submissions"]:
            print(f"    permit  rate={s['submitted_rate_bps']}bps  "
                  f"divergence={s.get('divergence_bps'):.2f}bps  by={s['by'][:16]}...")
        print(f"  Refused submissions: {len(out['refused_submissions'])}")
        for s in out["refused_submissions"]:
            print(f"    refuse  attempted_rate={s['submitted_rate_bps']}bps  by={s['by'][:16]}...")
            print(f"      rationale: {s.get('rationale', '')[:140]}")
    else:
        print(f"  Audit refused: {r.rationale[:200]}")

    _header("Ledger integrity")
    for op in (bank_a, bank_b, bank_c, admin, fca):
        print(f"  {op.name}: {len(op.substrate.ledger)} acts, verify={op.substrate.ledger.verify()}")

    _header("What the substrate would have prevented in LIBOR — summary")
    print()
    print(f"  CONTENT-ADDRESSED SUBMISSIONS. Every submission is a ledger event on")
    print(f"  the submitting bank's substrate, signed by the trader credential,")
    print(f"  carrying declared money-market activity context for the window.")
    print(f"  Anonymous or unattributed submissions are not possible.")
    print()
    print(f"  COOPERATIVE-SUBSTRATE AGGREGATION. The aggregate_libor unit is")
    print(f"  witnessed under the panel cooperative substrate's quorum custodian.")
    print(f"  The administrator cannot produce a valid benchmark unilaterally;")
    print(f"  the panel banks' joint witness is structural, not procedural.")
    print()
    print(f"  POINT-IN-TIME DIVERGENCE POLICY. divergence_policy refuses any")
    print(f"  submission whose rate is implausible given declared activity (or")
    print(f"  whose declared activity is zero). The bank cannot submit a rate")
    print(f"  unanchored to underlying money-market behaviour.")
    print()
    print(f"  DRIFT DETECTION over a window. Systematic bias within the")
    print(f"  per-submission threshold trips drift on the behaviour-characterised")
    print(f"  contract. The pattern of submissions catches the manipulation even")
    print(f"  when individual submissions look legitimate.")
    print()
    print(f"  CROSS-OPERATOR REGULATOR AUDIT. The FCA invokes audit_submissions")
    print(f"  on the bank's substrate via the audit cooperative substrate. The")
    print(f"  forensic report lands on the FCA's ledger. Audit invocations are")
    print(f"  themselves substrate events on both ledgers; no one can deny they")
    print(f"  happened.")
    print()
    print(f"  WHAT THE SUBSTRATE DOES NOT PREVENT: collusion conducted outside")
    print(f"  itself (the chatroom conversations and phone calls that")
    print(f"  characterised the actual scandal). What it does prevent is the")
    print(f"  ability to act on that collusion through the substrate without the")
    print(f"  attempt landing on the ledger with full attribution. Structural")
    print(f"  visibility of submissions is what the BBA's process lacked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
