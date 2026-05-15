"""
End-to-end cross-operator Universal Credit advance payment.

Two operators (DWP and Home Office), each running a substrate instance,
federated. A cooperative substrate credential bridges their authorities.
DWP's advance_payment_decision invokes Home Office's right_to_reside_check
via runtime.invoke_in(); each operator records its acts on its own ledger.

Run with: python -m examples.cross_operator_uc.run
"""

import sys
from pathlib import Path

# Allow running directly from the repository without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.federation import CooperativeSubstrate, LocalCustodian
from substrate.ledger import FederatedLedger
from substrate.operator import Operator, Substrate
from substrate.runtime import Permit, Refuse, Runtime

from examples.cross_operator_uc import credentials as creds_mod
from examples.cross_operator_uc import units as units_mod


def build_federation():
    """Build DWP and Home Office operators, federate them, return everything."""
    # Phase 2-deepened simplification: shared archives between operators.
    # Each operator has its own ledger and custodian and runtime; the
    # archives are shared so credentials and units propagate trivially.
    # A real distributed federation would have per-operator archives plus
    # a content-exchange protocol; that machinery is deferred.
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament = creds_mod.parliament()
    parliament_cid = creds.put(parliament)
    dwp_root = creds_mod.dwp_root(parliament_cid)
    dwp_root_cid = creds.put(dwp_root)
    ho_root = creds_mod.home_office_root(parliament_cid)
    ho_root_cid = creds.put(ho_root)
    coop = creds_mod.cooperative_substrate(dwp_root_cid, ho_root_cid)
    coop_cid = creds.put(coop)
    caseworker = creds_mod.dwp_caseworker(dwp_root_cid)
    caseworker_cid = creds.put(caseworker)

    # Home Office RTR unit.
    rtr_impl = units_mod.right_to_reside_impl()
    rtr_impl_cid = code.put(rtr_impl)
    rtr_unit = units_mod.right_to_reside_check(
        implementation_ref=rtr_impl_cid,
        cooperative_substrate_cid=coop_cid,
        parliament_cid=parliament_cid,
        home_office_root_cid=ho_root_cid,
        dwp_root_cid=dwp_root_cid,
    )
    code.put(rtr_unit)

    # DWP advance payment unit. Its impl closes over Home Office's
    # operator content_id and the RTR unit content_id so it can do the
    # cross-operator invocation.
    advance_impl = units_mod.advance_payment_impl(
        home_office_op_cid=ho_root_cid,  # Home Office operator id
        rtr_unit_cid=rtr_unit.content_id(),
    )
    advance_impl_cid = code.put(advance_impl)
    advance_unit = units_mod.advance_payment_decision(
        implementation_ref=advance_impl_cid,
        cooperative_substrate_cid=coop_cid,
        parliament_cid=parliament_cid,
        dwp_root_cid=dwp_root_cid,
        home_office_root_cid=ho_root_cid,
        rtr_unit_cid=rtr_unit.content_id(),
        rtr_impl_cid=rtr_impl_cid,
    )
    code.put(advance_unit)

    # Build each operator's substrate manually so they share archives but
    # have their own ledgers and custodians. (Phase 2-deepened
    # simplification: shared archives in-process. A real distributed
    # cooperative substrate would have per-operator archives and a
    # content-exchange protocol; deferred.)
    def _make_substrate(custodian_name: str) -> Substrate:
        ledger = FederatedLedger()
        return Substrate(
            code=code, credentials=creds, ledger=ledger,
            custodian=LocalCustodian(name=custodian_name),
            runtime=Runtime(code, creds, ledger),
        )

    dwp = Operator(name="DWP", root_credential=dwp_root, substrate=_make_substrate("dwp_custodian"))
    ho = Operator(name="HomeOffice", root_credential=ho_root, substrate=_make_substrate("ho_custodian"))

    # The cooperative substrate composes DWP and Home Office under the
    # cooperative credential established earlier.
    coop_substrate = CooperativeSubstrate(cooperative_credential=coop)
    coop_substrate.add(dwp)
    coop_substrate.add(ho)

    # Compile and register units on the right operators. Both advance_unit
    # and rtr_unit reference the cooperative substrate credential in their
    # authority chains; they are cross-operator units and should be
    # witnessed by the cooperative substrate's quorum custodian (a
    # composition of both operators' custodians) rather than by either
    # operator's local custodian alone. This is the substrate-as-unit
    # pattern at the witness layer.
    joint_custodian = coop_substrate.custodian
    dwp.runtime.register_compiled(compile_unit(advance_unit, code, creds, custodian=joint_custodian))
    ho.runtime.register_compiled(compile_unit(rtr_unit, code, creds, custodian=joint_custodian))

    return coop_substrate, dwp, ho, advance_unit.content_id(), caseworker_cid


def _print_header(title: str):
    print("=" * 70)
    print(title)
    print("=" * 70)


def _print_compiled_form(label: str, runtime, source_cid):
    cf = runtime.compiled_for(source_cid)
    print(f"  {label}")
    print(f"    source unit:       {cf.source_unit[:16]}...")
    print(f"    compiled form id:  {cf.content_id()[:16]}...")
    print(f"    authority chain:   {len(cf.authority_chain)} credentials")
    for cid in cf.authority_chain:
        cred = runtime.credentials.get_for_compile(cid)
        print(f"      {cid[:16]}...  ({cred.name})")
    print(f"    policies in scope: {len(cf.policies)}")
    print(f"    witness:           {cf.witness[:16]}...")
    print()


def _print_result(label, result):
    print(f"--- {label} ---")
    if isinstance(result, Permit):
        print(f"  PERMIT")
        print(f"  output:   {result.output}")
        print(f"  act id:   {result.act_id[:16]}...")
    elif isinstance(result, Refuse):
        print(f"  REFUSE")
        print(f"  rationale: {result.rationale}")
        print(f"  act id:    {result.act_id[:16]}...")
    print()


def _print_ledger(label, ledger):
    print(f"  {label}: {len(ledger)} acts; verify={ledger.verify()}")
    for i, act in enumerate(ledger):
        prev = act.previous_act_id[:16] if act.previous_act_id else "(genesis)"
        print(f"    [{i}] verdict={act.verdict}  act={act.content_id()[:16]}...  prev={prev}")
        if isinstance(act.output_or_rationale, dict):
            d = act.output_or_rationale
            if "decision" in d:
                print(f"        decision: {d['decision']} — {d.get('rationale', '')}")
            else:
                print(f"        output:   {d}")
        else:
            print(f"        rationale: {act.output_or_rationale}")


def main() -> int:
    coop, dwp, ho, advance_cid, caseworker_cid = build_federation()

    _print_header("Cooperative substrate")
    if coop.content_id:
        print(f"  identity (cooperative credential): {coop.content_id[:16]}...")
    print(f"  member operators: {len(coop)}")
    for cid in coop.members():
        op = coop.operator(cid)
        print(f"    {cid[:16]}...  ({op.name})")
    print()

    _print_header("DWP advance_payment_decision compiled form")
    _print_compiled_form("advance_payment_decision (on DWP)", dwp.runtime, advance_cid)

    _print_header("Home Office right_to_reside_check compiled form")
    rtr_source_cids = list(ho.runtime._compiled_by_source.keys())
    _print_compiled_form("right_to_reside_check (on Home Office)", ho.runtime, rtr_source_cids[0])

    _print_header("Cases")

    # Case 1: UK applicant; right to reside; advance approved.
    result1 = dwp.runtime.invoke(
        advance_cid,
        {
            "applicant_id": "uk_person_001",
            "household_income_pence": 80_000,
            "requested_amount_pence": 50_00,
        },
        caseworker_cid,
    )
    _print_result("Case 1: UK applicant; RTR confirmed at HO; DWP approves", result1)

    # Case 2: EU applicant; HO refuses RTR; DWP refers to human citing HO.
    result2 = dwp.runtime.invoke(
        advance_cid,
        {
            "applicant_id": "eu_person_002",
            "household_income_pence": 80_000,
            "requested_amount_pence": 50_00,
        },
        caseworker_cid,
    )
    _print_result("Case 2: EU applicant; HO refuses; DWP refers to human", result2)

    # Case 3: revoke the caseworker; DWP's runtime refuses before cross-operator call.
    dwp.credentials.revoke(caseworker_cid)
    result3 = dwp.runtime.invoke(
        advance_cid,
        {
            "applicant_id": "uk_person_001",
            "household_income_pence": 80_000,
            "requested_amount_pence": 50_00,
        },
        caseworker_cid,
    )
    _print_result("Case 3: caseworker revoked; DWP refuses before cross-operator call", result3)

    _print_header("Ledgers (operators retain sovereignty over their own records)")
    _print_ledger("DWP ledger", dwp.ledger)
    print()
    _print_ledger("Home Office ledger", ho.ledger)
    print()
    print("Joint audit trail: walk each ledger; cross-operator acts on the")
    print("DWP side carry the matching HO act_id in their output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
