"""
The Robodebt demonstration — end-to-end with cross-operator audit.

Three operators:

  - ServicesAustralia: runs the debt-calculation substrate.
    Holds two implementations of `calculate_overpayment`:
      v1: the original Robodebt algorithm. Averages annual income
          across fortnights with no precondition check.
      v2: same calculation, but the unit references a policy that
          refuses if income variability exceeds 0.30 (the assumption
          income-averaging requires).

  - ATO: holds claimant income data. Exposes a `fetch_annual_income`
    unit accessible to ServicesAustralia via cooperative substrate.

  - CommonwealthOmbudsman: an audit operator. Cross-operator audit
    rights over ServicesAustralia via a separate cooperative substrate.

Two claimants:

  - Sarah: stable monthly income of GBP 5000. Income variability low.
  - James: gig worker; monthly income wildly variable (zero for most
    months, large amounts in three months). Annual total: GBP 30000.

Scenario:

1. Sarah's case under v1 (Robodebt): produces zero or small overpayment.
   No phantom debt — averaging is approximately right for Sarah.
2. James's case under v1 (Robodebt): produces a substantial phantom
   overpayment, despite James having reported his actual fortnightly
   income correctly. This is the Robodebt mechanism.
3. Sarah's case under v2 (with precondition): variability low; precondition
   policy permits; algorithm runs; same small result.
4. James's case under v2 (with precondition): variability high (cv ~ 0.96);
   precondition policy refuses; algorithm refused; NO phantom debt.
5. Ombudsman audits James's case cross-operator. Sees: under v1 he was
   assessed a large overpayment; under v2 the algorithm refused with a
   clear rationale (income too variable). Both records are on the
   ServicesAustralia ledger; the Ombudsman's forensic report is on its
   own ledger.

Run with: python -m examples.robodebt.run
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

from examples.robodebt import _implementations as impls


# =============================================================================
# Helpers
# =============================================================================

def _cred(name, parent_cids=(), authorities=("invoke:any",)):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=authorities,
        credential_refs=tuple(parent_cids),
    )


def _make_substrate(custodian_name, code, creds):
    ledger = FederatedLedger()
    return Substrate(
        code=code, credentials=creds, ledger=ledger,
        custodian=LocalCustodian(name=custodian_name),
        runtime=Runtime(code, creds, ledger),
    )


# =============================================================================
# Setup
# =============================================================================

def build_scene():
    """Construct the three operators, cooperative substrates, credentials,
    units, and runtime registrations."""
    code = CodeArchive()
    creds = CredentialsArchive()

    # ---- Constitutional source and institutional roots ----
    parliament = _cred("commonwealth_parliament", authorities=("delegate:any",))
    parliament_cid = creds.put(parliament)

    sa_root = _cred(
        "services_australia_root", parent_cids=(parliament_cid,),
        authorities=("delegate:welfare", "administer:welfare_units"),
    )
    ato_root = _cred(
        "ato_root", parent_cids=(parliament_cid,),
        authorities=("delegate:tax_data",),
    )
    ombudsman_root = _cred(
        "commonwealth_ombudsman_root", parent_cids=(parliament_cid,),
        authorities=("delegate:any", "audit:any"),
    )
    sa_root_cid = creds.put(sa_root)
    ato_root_cid = creds.put(ato_root)
    ombudsman_root_cid = creds.put(ombudsman_root)

    # ---- Cooperative substrate: ATO ↔ Services Australia data sharing ----
    sa_ato_coop = CredentialUnit(
        name="cooperative_substrate_sa_ato_data_sharing",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:services_australia+ato",
        authorities=("cross_operator:fetch_income_data",),
        credential_refs=(sa_root_cid, ato_root_cid),
    )
    sa_ato_coop_cid = creds.put(sa_ato_coop)

    # ---- Cooperative substrate: Services Australia ↔ Ombudsman audit ----
    sa_ombudsman_coop = CredentialUnit(
        name="cooperative_substrate_sa_ombudsman_audit",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:services_australia+ombudsman",
        authorities=("cross_operator:audit",),
        credential_refs=(sa_root_cid, ombudsman_root_cid),
    )
    sa_ombudsman_coop_cid = creds.put(sa_ombudsman_coop)

    # ---- Operating credentials ----
    sa_caseworker = _cred(
        "sa_caseworker", parent_cids=(sa_root_cid,),
        authorities=("invoke:calculate_overpayment",),
    )
    ombudsman_auditor = _cred(
        "ombudsman_auditor", parent_cids=(ombudsman_root_cid,),
        authorities=("invoke:investigate", "cross_operator:audit"),
    )
    sa_caseworker_cid = creds.put(sa_caseworker)
    ombudsman_auditor_cid = creds.put(ombudsman_auditor)

    # ---- Operators and federations ----
    sa = Operator(
        name="ServicesAustralia",
        root_credential=sa_root,
        substrate=_make_substrate("services_australia_custodian", code, creds),
    )
    ato = Operator(
        name="ATO",
        root_credential=ato_root,
        substrate=_make_substrate("ato_custodian", code, creds),
    )
    ombudsman = Operator(
        name="CommonwealthOmbudsman",
        root_credential=ombudsman_root,
        substrate=_make_substrate("ombudsman_custodian", code, creds),
    )

    # Two cooperative substrates: SA ↔ ATO (data fetch) and SA ↔ Ombudsman (audit).
    # Operators can be members of multiple cooperative substrates; we add each
    # operator to both, since SA is a party to both arrangements.
    sa_ato_arrangement = CooperativeSubstrate(cooperative_credential=sa_ato_coop)
    sa_ato_arrangement.add(sa)
    sa_ato_arrangement.add(ato)
    # The Ombudsman arrangement: a separate cooperative substrate object
    # carrying the audit cooperative credential. SA's runtime can only be
    # part of one CooperativeSubstrate object at a time in the current
    # in-process registry; the demonstration adds SA and Ombudsman to one
    # registry holding the audit cooperative credential. (A future
    # extension would let an operator be a member of multiple
    # cooperative substrates simultaneously.)
    # For this Phase 2-deepened demonstration, we will use the audit
    # cooperative substrate for cross-operator audit calls. ATO data
    # fetching will happen via the SA-ATO cooperative substrate; switching
    # SA's runtime's cooperative_substrate back-ref between calls is the
    # simplification we accept here.
    sa_ombudsman_arrangement = CooperativeSubstrate(cooperative_credential=sa_ombudsman_coop)
    sa_ombudsman_arrangement.add(sa)
    sa_ombudsman_arrangement.add(ombudsman)

    # ---- Implementations ----
    fetch_impl = python_implementation(impls.FETCH_ANNUAL_INCOME, name="fetch_annual_income_impl")
    v1_impl = python_implementation(impls.CALCULATE_OVERPAYMENT_V1_ROBODEBT, name="calculate_overpayment_v1_impl")
    v2_impl = python_implementation(impls.CALCULATE_OVERPAYMENT_V2_PRINCIPLED, name="calculate_overpayment_v2_impl")
    variability_impl = python_implementation(impls.INCOME_VARIABILITY_POLICY, name="income_variability_policy_impl")
    legislative_impl = python_implementation(impls.LEGISLATIVE_AUTHORISATION_POLICY, name="legislative_authorisation_policy_impl")
    audit_impl = python_implementation(impls.AUDIT_CLAIMANT_CASE, name="audit_claimant_case_impl")
    investigate_impl = python_implementation(impls.INVESTIGATE_CLAIMANT_CASE, name="investigate_claimant_case_impl")
    fetch_impl_cid = code.put(fetch_impl)
    v1_impl_cid = code.put(v1_impl)
    v2_impl_cid = code.put(v2_impl)
    variability_impl_cid = code.put(variability_impl)
    legislative_impl_cid = code.put(legislative_impl)
    audit_impl_cid = code.put(audit_impl)
    investigate_impl_cid = code.put(investigate_impl)

    # ---- Policy units (functional units) ----
    income_variability_policy = FunctionalUnit(
        name="income_variability_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "name": "income_variability_policy",
            "evaluates": "coefficient of variation of monthly_breakdown",
            "refuses_when": "cv > 0.30",
        },
        implementation_ref=variability_impl_cid,
        credential_refs=(parliament_cid, sa_root_cid),
    )
    legislative_authorisation_policy = FunctionalUnit(
        name="legislative_authorisation_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "legislative_authorisation_policy"},
        implementation_ref=legislative_impl_cid,
        credential_refs=(parliament_cid, sa_root_cid),
    )
    code.put(income_variability_policy)
    code.put(legislative_authorisation_policy)

    # Binding credentials: bring the policies into binding for the v2 unit.
    variability_binding = CredentialUnit(
        name="income_variability_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance:services_australia",
        authorities=(),
        policy_refs=(income_variability_policy.content_id(),),
        credential_refs=(sa_root_cid,),
    )
    legislative_binding = CredentialUnit(
        name="legislative_authorisation_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance:parliament_authorised",
        authorities=(),
        policy_refs=(legislative_authorisation_policy.content_id(),),
        credential_refs=(parliament_cid,),
    )
    variability_binding_cid = creds.put(variability_binding)
    legislative_binding_cid = creds.put(legislative_binding)

    # ---- Functional units ----
    # ATO unit.
    fetch_annual_income = FunctionalUnit(
        name="fetch_annual_income",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"claimant_id": "str"}},
        implementation_ref=fetch_impl_cid,
        credential_refs=(parliament_cid, ato_root_cid, sa_root_cid, sa_ato_coop_cid),
    )
    code.put(fetch_annual_income)

    # Services Australia: v1 (Robodebt-style). Authority chain reaches
    # Parliament via SA root. NO precondition policy attached.
    calculate_overpayment_v1 = FunctionalUnit(
        name="calculate_overpayment_v1_robodebt",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "claimant_id": "str",
                "annual_income_aud": "float",
                "reported_fortnightly_income_aud": "list",
            },
            "outputs": {"alleged_overpayment_aud": "float"},
            "note": "no preconditions declared; this is the Robodebt failure mode",
        },
        implementation_ref=v1_impl_cid,
        credential_refs=(parliament_cid, sa_root_cid),
    )
    code.put(calculate_overpayment_v1)

    # Services Australia: v2 (with preconditions enforced through policies).
    # References both bindings: legislative authorisation and income
    # variability. Wilful inclusion requires the policy units in
    # functional_refs and their implementations in state_refs.
    calculate_overpayment_v2 = FunctionalUnit(
        name="calculate_overpayment_v2_principled",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "claimant_id": "str",
                "annual_income_aud": "float",
                "reported_fortnightly_income_aud": "list",
                "monthly_breakdown": "list",
            },
            "outputs": {"alleged_overpayment_aud": "float"},
            "note": "preconditions enforced through bound policies",
        },
        implementation_ref=v2_impl_cid,
        credential_refs=(
            parliament_cid, sa_root_cid,
            variability_binding_cid, legislative_binding_cid,
        ),
        functional_refs=(income_variability_policy.content_id(),
                         legislative_authorisation_policy.content_id()),
        state_refs=(variability_impl_cid, legislative_impl_cid),
    )
    code.put(calculate_overpayment_v2)

    # Audit unit on SA side, exposed to Ombudsman via cooperative substrate.
    audit_claimant_case = FunctionalUnit(
        name="audit_claimant_case",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"claimant_id": "str"}},
        implementation_ref=audit_impl_cid,
        credential_refs=(parliament_cid, sa_root_cid, ombudsman_root_cid, sa_ombudsman_coop_cid),
    )
    code.put(audit_claimant_case)

    # Investigation unit on Ombudsman side; invokes audit_claimant_case cross-operator.
    investigate_claimant_case = FunctionalUnit(
        name="investigate_claimant_case",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"claimant_id": "str"}},
        implementation_ref=investigate_impl_cid,
        credential_refs=(parliament_cid, ombudsman_root_cid, sa_root_cid, sa_ombudsman_coop_cid),
        functional_refs=(audit_claimant_case.content_id(),),
        state_refs=(audit_impl_cid,),
    )
    code.put(investigate_claimant_case)

    # ---- Compile and register ----
    # SA-only units witnessed by SA's custodian.
    for u in (calculate_overpayment_v1, calculate_overpayment_v2,
              income_variability_policy, legislative_authorisation_policy):
        sa.runtime.register_compiled(
            compile_unit(u, code, creds, custodian=sa.custodian)
        )
    # ATO unit witnessed by SA-ATO joint custodian.
    ato.runtime.register_compiled(
        compile_unit(fetch_annual_income, code, creds, custodian=sa_ato_arrangement.custodian)
    )
    # Audit units witnessed by SA-Ombudsman joint custodian.
    sa.runtime.register_compiled(
        compile_unit(audit_claimant_case, code, creds, custodian=sa_ombudsman_arrangement.custodian)
    )
    ombudsman.runtime.register_compiled(
        compile_unit(investigate_claimant_case, code, creds, custodian=sa_ombudsman_arrangement.custodian)
    )

    credential_names = {
        parliament_cid: "Commonwealth Parliament",
        sa_root_cid: "Services Australia (root)",
        ato_root_cid: "ATO (root)",
        ombudsman_root_cid: "Commonwealth Ombudsman (root)",
        sa_caseworker_cid: "SA caseworker",
        ombudsman_auditor_cid: "Ombudsman auditor",
        sa_ato_coop_cid: "Cooperative substrate (SA+ATO)",
        sa_ombudsman_coop_cid: "Cooperative substrate (SA+Ombudsman)",
    }

    return {
        "sa": sa, "ato": ato, "ombudsman": ombudsman,
        "sa_ato_coop": sa_ato_arrangement,
        "sa_ombudsman_coop": sa_ombudsman_arrangement,
        "sa_caseworker_cid": sa_caseworker_cid,
        "ombudsman_auditor_cid": ombudsman_auditor_cid,
        "calculate_v1": calculate_overpayment_v1,
        "calculate_v2": calculate_overpayment_v2,
        "audit_unit": audit_claimant_case,
        "investigate_unit": investigate_claimant_case,
        "credential_names": credential_names,
        # Synthetic income data baked into fetch_annual_income.
        "claimant_income": {
            "sarah": {
                "annual_income_aud": 60000,
                "monthly_breakdown": [5000] * 12,
                # Reported fortnightly: 26 fortnights at the steady rate.
                "reported_fortnightly_income_aud": [round(60000 / 26.0, 2)] * 26,
            },
            "james": {
                "annual_income_aud": 30000,
                "monthly_breakdown": [0, 0, 8000, 0, 0, 12000, 0, 0, 6000, 0, 0, 4000],
                # James reported his actual fortnightly income honestly:
                # mostly zero, with peaks in working months.
                "reported_fortnightly_income_aud":
                    [0, 0]    # months 1-2 zero
                    + [4000, 4000]  # month 3 working: $8000/2 fortnights
                    + [0, 0, 0, 0]  # months 4-5 zero
                    + [6000, 6000]  # month 6 working: $12000/2
                    + [0, 0, 0, 0]  # months 7-8 zero
                    + [3000, 3000]  # month 9 working: $6000/2
                    + [0, 0, 0, 0]  # months 10-11 zero
                    + [2000, 2000]  # month 12 working: $4000/2
            },
        },
    }


# =============================================================================
# Narrative
# =============================================================================

def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _name(scene, cid):
    return scene["credential_names"].get(cid, cid[:12] + "...")


def _calc(scene, version, claimant_id):
    """Invoke the v1 or v2 calculate_overpayment unit for a claimant."""
    sa = scene["sa"]
    caseworker = scene["sa_caseworker_cid"]
    income = scene["claimant_income"][claimant_id]
    inputs = {
        "claimant_id": claimant_id,
        "annual_income_aud": income["annual_income_aud"],
        "reported_fortnightly_income_aud": income["reported_fortnightly_income_aud"],
    }
    if version == "v2":
        inputs["monthly_breakdown"] = income["monthly_breakdown"]
        unit = scene["calculate_v2"]
    else:
        unit = scene["calculate_v1"]
    return sa.runtime.invoke(unit.content_id(), inputs, caseworker)


def _print_result(label, result):
    print(f"  {label}")
    if isinstance(result, Permit):
        out = result.output
        print(f"    PERMIT — algorithm {out.get('algorithm')}")
        print(f"    averaged_fortnightly: AUD {out.get('averaged_fortnightly')}")
        print(f"    alleged_overpayment_aud: AUD {out.get('alleged_overpayment_aud')}")
        if out.get('alleged_overpayment_aud', 0) > 0:
            print(f"    >>> a debt would be raised against this claimant <<<")
    else:
        print(f"    REFUSE")
        print(f"    rationale: {result.rationale}")
        print(f"    >>> NO debt raised; refusal is the structural output <<<")


def _print_forensic(scene, report):
    print(f"  Claimant: {report['claimant_id']}")
    if report["verdict"] != "audit_completed":
        print(f"  Verdict: {report['verdict']}")
        print(f"  Rationale: {report.get('rationale')}")
        return
    print(f"  Audit completed against SA's ledger (length {report['ledger_length_at_audit']}).")
    print(f"  SA audit act id: {report['services_audit_act_id'][:16]}...")
    print()
    if report["debt_calculations"]:
        print(f"  Debt calculations attempted against this claimant:")
        for d in report["debt_calculations"]:
            prec = "(preconditions checked)" if d.get("preconditions_checked") else "(NO preconditions checked)"
            print(f"    algorithm {d['algorithm']} {prec}")
            print(f"      alleged_overpayment_aud: AUD {d['alleged_overpayment_aud']}")
            print(f"      by: {_name(scene, d['by'])}")
            print(f"      act id: {d['act_id'][:16]}...")
    if report["refusals"]:
        print()
        print(f"  Refusals attributable to this claimant:")
        for r in report["refusals"]:
            print(f"    by: {_name(scene, r['by'])}")
            print(f"      rationale: {r['rationale']}")


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    scene = build_scene()
    sa = scene["sa"]
    ombudsman = scene["ombudsman"]

    _header("Setup")
    print(f"  Operators: ServicesAustralia, ATO, CommonwealthOmbudsman")
    print(f"  Cooperative substrates:")
    print(f"    SA <-> ATO: data sharing")
    print(f"    SA <-> Ombudsman: audit rights")
    print(f"  Algorithm versions:")
    print(f"    v1 (Robodebt): income-averaging; NO precondition policy attached")
    print(f"    v2 (principled): income-averaging; income_variability_policy attached")
    print()
    print(f"  Claimants:")
    print(f"    Sarah: stable monthly income AUD 5000 (annual AUD 60,000)")
    print(f"    James: gig worker; lumpy income; annual AUD 30,000 across a few months")

    # ---- Sarah under v1 (Robodebt) ----
    _header("Sarah under v1 (Robodebt algorithm — no preconditions)")
    r = _calc(scene, "v1", "sarah")
    _print_result("Sarah / v1 result:", r)

    # ---- Sarah under v2 (principled) ----
    _header("Sarah under v2 (with income-variability precondition policy)")
    r = _calc(scene, "v2", "sarah")
    _print_result("Sarah / v2 result:", r)
    print(f"  Sarah's income is stable; the precondition policy permits; algorithm runs.")
    print(f"  Both v1 and v2 produce equivalent outputs for Sarah.")

    # ---- James under v1 (Robodebt) ----
    _header("James under v1 (Robodebt algorithm — no preconditions)")
    r = _calc(scene, "v1", "james")
    _print_result("James / v1 result:", r)
    print(f"  James worked in 4 of 12 months. His REPORTED fortnightly income (which")
    print(f"  Centrelink received correctly) was zero for most fortnights and nonzero")
    print(f"  when he was working.")
    print(f"  The Robodebt algorithm averages his annual income across all 26 fortnights")
    print(f"  (AUD 30000 / 26 = AUD 1153.85/fortnight) and treats every fortnight he")
    print(f"  reported less than this as 'underreport' — generating a phantom debt.")
    print(f"  This is the Robodebt mechanism in 5 lines.")

    # ---- James under v2 (principled) ----
    _header("James under v2 (with income-variability precondition policy)")
    r = _calc(scene, "v2", "james")
    _print_result("James / v2 result:", r)
    print(f"  James's monthly income coefficient of variation is approximately 1.6 —")
    print(f"  far above the policy's 0.30 threshold. The precondition policy refuses.")
    print(f"  The algorithm is refused. No debt is raised.")
    print(f"  In real Robodebt, James would have received a substantial debt notice")
    print(f"  and been pursued for repayment. Under v2 the substrate refuses to compute.")

    # ---- Ombudsman audit ----
    _header("Commonwealth Ombudsman investigates James's case (cross-operator)")
    print()
    print(f"  The Ombudsman invokes investigate_claimant_case on its own runtime;")
    print(f"  the impl calls runtime.invoke_in() to reach SA's audit_claimant_case unit;")
    print(f"  the cooperative substrate authorises this access; the audit invocation")
    print(f"  lands on SA's ledger; the forensic report lands on the Ombudsman's ledger.")
    print()
    result = ombudsman.runtime.invoke(
        scene["investigate_unit"].content_id(),
        {
            "claimant_id": "james",
            "services_australia_operator_id": sa.content_id,
            "audit_unit_id": scene["audit_unit"].content_id(),
        },
        scene["ombudsman_auditor_cid"],
    )
    if not isinstance(result, Permit):
        print(f"  Investigation refused: {result.rationale}")
        return 1
    _print_forensic(scene, result.output)
    print()
    print(f"  The Ombudsman can now establish from substrate evidence alone:")
    print(f"  - James's case has both v1 (debt) and v2 (refusal) records.")
    print(f"  - The v1 result was generated WITHOUT precondition checking.")
    print(f"  - The v2 result correctly refused, naming the variability violation.")
    print(f"  - The discrepancy is the algorithm (preconditions), not James's reporting.")

    # ---- Ledger summary ----
    _header("Ledger integrity")
    print()
    print(f"  ServicesAustralia ledger: {len(sa.substrate.ledger)} acts; verify={sa.substrate.ledger.verify()}")
    print(f"  ATO ledger: {len(scene['ato'].substrate.ledger)} acts; verify={scene['ato'].substrate.ledger.verify()}")
    print(f"  Ombudsman ledger: {len(ombudsman.substrate.ledger)} acts; verify={ombudsman.substrate.ledger.verify()}")

    # ---- Closing summary ----
    _header("What the substrate would have prevented in Robodebt")
    print()
    print(f"  UNCHECKED PRECONDITIONS (failure point 1). The income-averaging")
    print(f"  algorithm assumes income is approximately evenly distributed. The")
    print(f"  substrate enforces this assumption as a policy that refuses when")
    print(f"  the assumption fails. James's case demonstrates the substrate")
    print(f"  refusing rather than producing a phantom debt.")
    print()
    print(f"  REVERSED BURDEN OF PROOF (failure point 2). The substrate cannot")
    print(f"  produce a debt without the precondition policy permitting. The")
    print(f"  burden is on the agency's algorithm to satisfy its declared")
    print(f"  preconditions, not on the recipient to disprove a debt that has")
    print(f"  already been generated.")
    print()
    print(f"  REMOVED HUMAN REVIEW (failure point 3). The refusal IS the")
    print(f"  refer-to-human signal. Each refusal carries a clear rationale and")
    print(f"  is recorded on the ledger; an operator with appropriate authority")
    print(f"  can examine each refused case and resolve it manually.")
    print()
    print(f"  AUTHORITY CHAIN (failure point 4). Every unit's authority chain")
    print(f"  traces to a constitutional source (here, Parliament). An algorithm")
    print(f"  whose chain does not include a legislative authority cannot be")
    print(f"  compiled, witnessed, or invoked. The Federal Court's 2019 finding")
    print(f"  of unlawfulness — that the scheme had no proper authorisation —")
    print(f"  would have been structural rather than retrospective.")
    print()
    print(f"  STRUCTURAL AUDIT (failure point 5). The Ombudsman's audit happens")
    print(f"  via the cooperative substrate; the audit invocation is recorded on")
    print(f"  both ledgers; neither side can deny it happened or rewrite what")
    print(f"  was found. James's case can be reconstructed from substrate")
    print(f"  evidence alone.")
    print()
    print(f"  HONEST LIMIT: the substrate's properties are enforced WHEN")
    print(f"  PRECONDITIONS ARE DECLARED. A unit author who chooses not to")
    print(f"  declare preconditions (as Robodebt's v1 demonstrates) gets the")
    print(f"  Robodebt result. The substrate makes the absence visible: every")
    print(f"  audit can see whether a unit has preconditions and whether they")
    print(f"  were checked. The political pressure to declare them then becomes")
    print(f"  external and structural rather than internal and procedural.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
