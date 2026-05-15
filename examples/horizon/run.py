"""
The Horizon demonstration — end-to-end with cross-operator audit.

Two operators:

  - PostOffice: runs the accounting substrate where transactions and
    modifications happen. Holds the buggy balance_check_v1 implementation
    that produced phantom shortfalls in the actual Horizon case.

  - CourtOfAppeals: a separate operator with its own substrate, federated
    with PostOffice via a cooperative substrate that establishes the
    Court's audit rights. Holds the investigate_branch unit that performs
    cross-operator forensic audits.

The cooperative substrate is a credential with parent references to both
operator roots, jointly witnessed by both operators' custodians. Neither
party can unilaterally alter its terms. The audit invocation is itself
a substrate event recorded on both ledgers — the Court cannot fabricate
an audit; the Post Office cannot deny it happened.

Scenario:

1-4: Horizon failure mechanism reproduced. Postmaster Alice records 10
sales. Fujitsu engineer adds 5 credit_reversal entries. Buggy balance_check
reports a phantom shortfall of GBP 500.

5-7: Court of Appeals investigates via cross-operator audit. The audit
unit returns the relevant acts; investigate_branch analyses them; a
forensic report is produced on the Court's ledger. The audit invocation
itself lands on the Post Office's ledger, attributing the audit to the
Court auditor's credential.

8-10: Bug discovered. Post Office deprecates balance_check_v1 (admin act
on the ledger). Re-run with balance_check_v2 against the same data
produces the correct GBP 1000 figure. Anyone with either ledger can
verify this independently.

Run with: python -m examples.horizon.run
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

from examples.horizon import _implementations as impls


# =============================================================================
# Credentials and units
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
    """Build a substrate sharing the given archives but with its own
    ledger and custodian. The cross-operator demonstration uses shared
    archives as the Phase 2-deepened simplification; a real distributed
    setup would have per-operator archives plus a content-exchange
    protocol."""
    ledger = FederatedLedger()
    return Substrate(
        code=code, credentials=creds, ledger=ledger,
        custodian=LocalCustodian(name=custodian_name),
        runtime=Runtime(code, creds, ledger),
    )


def build_scene():
    """Construct the two operators, the cooperative substrate, all
    credentials, all units, and the runtime registrations.

    Returns a dict the orchestration can pull from by name.
    """
    # Shared archives (Phase 2-deepened simplification).
    code = CodeArchive()
    creds = CredentialsArchive()

    # ---- Constitutional source and institutional roots ----
    crown = _cred("crown", authorities=("delegate:any",))
    crown_cid = creds.put(crown)

    post_office_root = _cred(
        "post_office_root", parent_cids=(crown_cid,),
        authorities=("delegate:any", "administer:any"),
    )
    court_root = _cred(
        "court_of_appeals_root", parent_cids=(crown_cid,),
        authorities=("delegate:any", "audit:any"),
    )
    po_root_cid = creds.put(post_office_root)
    court_root_cid = creds.put(court_root)

    # ---- The cooperative audit substrate ----
    # A credential whose parents are both operator roots. Units that
    # reference it gain both operators' roots in their authority chains,
    # which is what enables Court credentials to be delegated under Post
    # Office units (and vice versa, if needed).
    cooperative_audit = CredentialUnit(
        name="cooperative_audit_substrate_po_court",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:post_office+court_of_appeals_audit",
        authorities=("cross_operator:audit",),
        credential_refs=(po_root_cid, court_root_cid),
    )
    coop_cid = creds.put(cooperative_audit)

    # ---- Operating credentials ----
    fujitsu_engineer = _cred(
        "fujitsu_engineer", parent_cids=(po_root_cid,),
        authorities=("invoke:modify_branch_account",),
    )
    postmaster_alice = _cred(
        "postmaster_alice", parent_cids=(po_root_cid,),
        authorities=("invoke:record_transaction", "invoke:balance_check"),
    )
    court_auditor = _cred(
        "court_auditor", parent_cids=(court_root_cid,),
        authorities=("invoke:investigate", "cross_operator:audit"),
    )
    fujitsu_cid = creds.put(fujitsu_engineer)
    alice_cid = creds.put(postmaster_alice)
    auditor_cid = creds.put(court_auditor)

    # ---- Operators and federation ----
    po = Operator(
        name="PostOffice",
        root_credential=post_office_root,
        substrate=_make_substrate("post_office_custodian", code, creds),
    )
    court = Operator(
        name="CourtOfAppeals",
        root_credential=court_root,
        substrate=_make_substrate("court_of_appeals_custodian", code, creds),
    )
    coop = CooperativeSubstrate(cooperative_credential=cooperative_audit)
    coop.add(po)
    coop.add(court)

    # ---- Implementations (content-addressed) ----
    record_impl = python_implementation(impls.RECORD_TRANSACTION, name="record_transaction_impl")
    modify_impl = python_implementation(impls.MODIFY_BRANCH_ACCOUNT, name="modify_branch_account_impl")
    balance_v1_impl = python_implementation(impls.BALANCE_CHECK_V1_BUGGY, name="balance_check_v1_impl")
    balance_v2_impl = python_implementation(impls.BALANCE_CHECK_V2_FIXED, name="balance_check_v2_impl")
    audit_impl = python_implementation(impls.AUDIT_BRANCH, name="audit_branch_impl")
    investigate_impl = python_implementation(impls.INVESTIGATE_BRANCH, name="investigate_branch_impl")
    record_impl_cid = code.put(record_impl)
    modify_impl_cid = code.put(modify_impl)
    balance_v1_impl_cid = code.put(balance_v1_impl)
    balance_v2_impl_cid = code.put(balance_v2_impl)
    audit_impl_cid = code.put(audit_impl)
    investigate_impl_cid = code.put(investigate_impl)

    # ---- Functional units ----
    # PO-side units: authority chain crown -> post_office_root.
    record_transaction = FunctionalUnit(
        name="record_transaction",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"branch_id": "str", "amount": "int", "type": "str"}},
        implementation_ref=record_impl_cid,
        credential_refs=(crown_cid, po_root_cid),
    )
    modify_branch_account = FunctionalUnit(
        name="modify_branch_account",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"branch_id": "str", "amount": "int", "type": "str"}},
        implementation_ref=modify_impl_cid,
        credential_refs=(crown_cid, po_root_cid),
    )
    balance_check_v1 = FunctionalUnit(
        name="balance_check_v1",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"branch_id": "str"}, "outputs": {"balance": "int"}},
        implementation_ref=balance_v1_impl_cid,
        credential_refs=(crown_cid, po_root_cid),
    )
    balance_check_v2 = FunctionalUnit(
        name="balance_check_v2",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"branch_id": "str"}, "outputs": {"balance": "int"}},
        implementation_ref=balance_v2_impl_cid,
        credential_refs=(crown_cid, po_root_cid),
    )

    # audit_branch: PO-side unit, exposed to the Court via the cooperative
    # substrate. Its credential_refs include the cooperative substrate so
    # the Court auditor's credentials are delegated under it.
    audit_branch = FunctionalUnit(
        name="audit_branch",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"branch_id": "str"}, "outputs": {"acts": "list"}},
        implementation_ref=audit_impl_cid,
        credential_refs=(crown_cid, po_root_cid, court_root_cid, coop_cid),
    )

    # investigate_branch: Court-side unit. References the cooperative
    # substrate so Court credentials are delegated. The implementation
    # calls runtime.invoke_in() to reach the PO audit_branch.
    investigate_branch = FunctionalUnit(
        name="investigate_branch",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {
            "branch_id": "str",
            "post_office_operator_id": "str",
            "audit_unit_id": "str",
        }},
        implementation_ref=investigate_impl_cid,
        credential_refs=(crown_cid, court_root_cid, po_root_cid, coop_cid),
        # Wilful inclusion: this unit transitively reaches audit_branch
        # and its implementation through the cross-operator call.
        functional_refs=(audit_branch.content_id(),),
        state_refs=(audit_impl_cid,),
    )

    for u in (record_transaction, modify_branch_account,
              balance_check_v1, balance_check_v2,
              audit_branch, investigate_branch):
        code.put(u)

    # ---- Compile and register ----
    # PO-only units witnessed by PO's custodian.
    for u in (record_transaction, modify_branch_account,
              balance_check_v1, balance_check_v2):
        po.runtime.register_compiled(
            compile_unit(u, code, creds, custodian=po.custodian)
        )

    # Cross-operator units (audit_branch, investigate_branch) are jointly
    # witnessed by both operators' custodians under the cooperative
    # substrate's quorum custodian.
    joint = coop.custodian
    po.runtime.register_compiled(compile_unit(audit_branch, code, creds, custodian=joint))
    court.runtime.register_compiled(compile_unit(investigate_branch, code, creds, custodian=joint))

    credential_names = {
        crown_cid: "Crown (constitutional source)",
        po_root_cid: "Post Office (root)",
        court_root_cid: "Court of Appeals (root)",
        coop_cid: "Cooperative audit substrate",
        fujitsu_cid: "Fujitsu engineer",
        alice_cid: "Postmaster Alice",
        auditor_cid: "Court auditor",
    }
    unit_index = {
        record_transaction.content_id(): "record_transaction",
        modify_branch_account.content_id(): "modify_branch_account",
        balance_check_v1.content_id(): "balance_check_v1",
        balance_check_v2.content_id(): "balance_check_v2",
        audit_branch.content_id(): "audit_branch",
        investigate_branch.content_id(): "investigate_branch",
    }

    return {
        "po": po, "court": court, "coop": coop,
        "alice_cid": alice_cid, "fujitsu_cid": fujitsu_cid, "auditor_cid": auditor_cid,
        "po_root_cid": po_root_cid, "court_root_cid": court_root_cid,
        "record_transaction": record_transaction,
        "modify_branch_account": modify_branch_account,
        "balance_check_v1": balance_check_v1,
        "balance_check_v2": balance_check_v2,
        "audit_branch": audit_branch,
        "investigate_branch": investigate_branch,
        "credential_names": credential_names,
        "unit_index": unit_index,
    }


# =============================================================================
# Narrative rendering
# =============================================================================

def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _name(scene, cid):
    return scene["credential_names"].get(cid, cid[:12] + "...")


def _print_forensic_report(scene, report):
    print(f"  Branch: {report['branch_id']}")
    print(f"  Verdict: {report['verdict']}")
    if report["verdict"] != "audit_completed":
        print(f"  Rationale: {report.get('rationale')}")
        return
    print(f"  Post Office audit act id: {report['po_audit_act_id'][:16]}...")
    print(f"  Ledger length at audit: {report['ledger_length_at_audit']}")
    print()
    print(f"  Activity by credential:")
    for cred, totals in report["by_credential"].items():
        name = _name(scene, cred)
        print(f"    {name}")
        if totals["sale"]:
            print(f"      sales: GBP {totals['sale']} ({totals['count']} acts)")
        if totals["refund"]:
            print(f"      refunds: GBP {totals['refund']}")
        if totals["credit_reversal"]:
            print(f"      credit reversals: GBP {totals['credit_reversal']}  <-- attributable to this credential")
    print()
    if report["balance_checks"]:
        print(f"  Balance check invocations:")
        for bc in report["balance_checks"]:
            print(f"    {_name(scene, bc['by'])}: balance = GBP {bc['balance']} (implementation {bc['implementation']})")
    if report["administrative_acts"]:
        print()
        print(f"  Administrative acts visible to the audit:")
        for a in report["administrative_acts"]:
            print(f"    {_name(scene, a['invoking_credential_id'])}: {a['inputs'].get('action')} ({a['verdict']})")
            print(f"      details: {a.get('output')}")


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    scene = build_scene()
    po = scene["po"]
    court = scene["court"]
    alice = scene["alice_cid"]
    fujitsu = scene["fujitsu_cid"]
    auditor = scene["auditor_cid"]
    branch = "alices_branch"

    _header("Setup")
    print(f"  Operators in the cooperative substrate:")
    for cid in scene["coop"].members():
        op = scene["coop"].operator(cid)
        print(f"    {cid[:12]}...  {op.name}")
    print(f"  Cooperative substrate id: {scene['coop'].content_id[:12]}...")
    print(f"  Joint custodian (quorum threshold {scene['coop'].custodian.threshold} of {len(scene['coop'].custodian.members)})")
    print(f"  Cross-operator units witnessed jointly:")
    print(f"    audit_branch (PostOffice)")
    print(f"    investigate_branch (CourtOfAppeals)")

    _header("Steps 1-2: Alice records 10 legitimate sales; balance correct")
    for _ in range(10):
        po.runtime.invoke(
            scene["record_transaction"].content_id(),
            {"branch_id": branch, "amount": 100, "type": "sale"},
            alice,
        )
    r = po.runtime.invoke(
        scene["balance_check_v1"].content_id(),
        {"branch_id": branch}, alice,
    )
    print(f"  After 10 sales, balance_check_v1 reports: {r.output}")

    _header("Step 3: Fujitsu engineer adds 5 credit_reversal entries")
    for _ in range(5):
        po.runtime.invoke(
            scene["modify_branch_account"].content_id(),
            {"branch_id": branch, "amount": 100, "type": "credit_reversal"},
            fujitsu,
        )
    print(f"  5 modifications recorded on the Post Office ledger.")
    print(f"  Each is signed by Fujitsu's credential, not Alice's.")

    _header("Step 4: balance_check_v1 reports phantom shortfall")
    r = po.runtime.invoke(
        scene["balance_check_v1"].content_id(),
        {"branch_id": branch}, alice,
    )
    print(f"  Output: {r.output}")
    print(f"  In the Horizon era, this is the figure used to prosecute postmasters.")

    _header("Steps 5-7: Court of Appeals investigates via cross-operator audit")
    print()
    print(f"  The Court auditor invokes investigate_branch on the Court's runtime.")
    print(f"  investigate_branch calls runtime.invoke_in() to reach the Post Office's")
    print(f"  audit_branch unit. The cooperative substrate authorises this access;")
    print(f"  the audit invocation lands on the Post Office's ledger; the forensic")
    print(f"  report lands on the Court's ledger.")
    print()
    result = court.runtime.invoke(
        scene["investigate_branch"].content_id(),
        {
            "branch_id": branch,
            "post_office_operator_id": po.content_id,
            "audit_unit_id": scene["audit_branch"].content_id(),
        },
        auditor,
    )
    if not isinstance(result, Permit):
        print(f"  Investigation refused: {result.rationale}")
        return 1
    print(f"  Investigation completed (act id {result.act_id[:16]}... on Court ledger).")
    print()
    _print_forensic_report(scene, result.output)
    print()
    print(f"  Conclusion (this is what the Court can assert from the audit):")
    print(f"  - Sales of GBP 1000 are attributable to Postmaster Alice.")
    print(f"  - Modifications of GBP 500 (credit_reversal) are attributable to")
    print(f"    Fujitsu's credential. Alice did not make them.")
    print(f"  - The shortfall figure came from balance_check_v1.")
    print(f"  - The shortfall is NOT attributable to Alice's behaviour.")

    _header("Audit-trail integrity: who knows what")
    print()
    print(f"  Post Office ledger ({len(po.substrate.ledger)} acts):")
    print(f"    - 10 record_transaction acts (by Alice)")
    print(f"    - 5 modify_branch_account acts (by Fujitsu)")
    print(f"    - 2 balance_check_v1 acts (by Alice)")
    print(f"    - 1 audit_branch act (by Court auditor; the audit itself)")
    print(f"    Post Office cannot deny the Court audited; the audit invocation is")
    print(f"    on their own hash-chained ledger.")
    print()
    print(f"  Court of Appeals ledger ({len(court.substrate.ledger)} acts):")
    print(f"    - 1 investigate_branch act (by Court auditor)")
    print(f"    The forensic report is preserved on the Court's own ledger.")
    print(f"    The Post Office cannot retroactively change what the Court found.")
    print()
    print(f"  Both ledgers verify: PO={po.substrate.ledger.verify()}, Court={court.substrate.ledger.verify()}")

    _header("Step 8: Bug discovered; Post Office deprecates balance_check_v1")
    print(f"  Operator records administrative act using its root credential as authorisation.")
    print(f"  The deprecation event is now on the Post Office ledger.")
    admin_act = po.deprecate_unit(
        scene["balance_check_v1"].content_id(),
        po.root_credential.content_id(),
    )
    print(f"  Admin act recorded: {admin_act.content_id()[:16]}...")
    print(f"  Cannot be hidden: any future audit will see this event.")

    _header("Step 9: Re-run balance_check_v2 against the same data")
    r = po.runtime.invoke(
        scene["balance_check_v2"].content_id(),
        {"branch_id": branch}, alice,
    )
    print(f"  Output: {r.output}")
    print(f"  Same data; same ledger; different implementation; correct figure.")
    print(f"  Anyone with the ledger and the v2 implementation can verify this")
    print(f"  themselves — no need to trust the Post Office's word.")

    _header("Step 10: Court re-investigates; sees both v1 and v2 outputs plus the deprecation")
    result2 = court.runtime.invoke(
        scene["investigate_branch"].content_id(),
        {
            "branch_id": branch,
            "post_office_operator_id": po.content_id,
            "audit_unit_id": scene["audit_branch"].content_id(),
        },
        auditor,
    )
    print()
    _print_forensic_report(scene, result2.output)
    print()
    print(f"  The Court can now see the full history: both v1 and v2 figures, the")
    print(f"  deprecation event, and which credentials produced which outputs. The")
    print(f"  forensic case is constructible from substrate evidence alone.")

    _header("What the substrate would have prevented in Horizon — summary")
    print()
    print(f"  ATTRIBUTION (failure point 2). Modifications are structurally signed")
    print(f"  by Fujitsu's credential, not Alice's. The Post Office could not")
    print(f"  honestly assert Alice made them.")
    print()
    print(f"  TRACEABILITY (failure point 1). Every balance figure records which")
    print(f"  implementation produced it. When v1 is identified as buggy, every")
    print(f"  figure produced by v1 is identifiable as such.")
    print()
    print(f"  NON-SUPPRESSION (failure point 4). Deprecation of v1 is itself an")
    print(f"  administrative act on the ledger. The fix is itself an event in the")
    print(f"  audit trail.")
    print()
    print(f"  RE-EXECUTABILITY. Same data through fixed v2 produces correct figure.")
    print(f"  Verifiable by anyone with the ledger and v2; no operator trust required.")
    print()
    print(f"  AUDIT REACHABILITY (failure point 5). Addressed structurally via the")
    print(f"  cooperative substrate. The Court of Appeals has audit rights bilaterally")
    print(f"  committed; the audit invocation produces records on both operators'")
    print(f"  ledgers; neither side can deny or unilaterally rewrite the audit.")
    print()
    print(f"  Political reachability (whether the Court has an arrangement with the")
    print(f"  Post Office in the first place) is not a substrate concern — it is the")
    print(f"  precondition for the cooperative substrate to exist. The substrate")
    print(f"  provides the structural place for it; the political work establishes it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
