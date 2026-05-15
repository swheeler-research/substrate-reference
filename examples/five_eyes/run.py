"""
Five Eyes / mass surveillance demonstration against the substrate.

Three operators (stylised; not modelled on any specific agency):
  - AgencyA (a signals-intelligence agency in jurisdiction A)
  - AgencyB (a signals-intelligence agency in jurisdiction B)
  - OversightCommittee (parliamentary oversight; cross-operator audit)

Two cooperative substrates:
  - AgencyA + AgencyB: cross-jurisdictional query exchange (the bilateral
    "Five Eyes"-style arrangement)
  - AgencyA + AgencyB + OversightCommittee: audit rights

Scenario:
  1. AgencyA's analyst queries AgencyA's data on a subject in AgencyA's
     own jurisdiction WITHOUT cooperative authorisation — refused
     (jurisdiction_scope_policy).
  2. AgencyA's analyst queries AgencyA's data on a subject in AgencyA's
     own jurisdiction WITH cooperative-authorisation credential for
     counter-terrorism category — permitted.
  3. AgencyA's analyst queries AgencyB's data on a subject in AgencyA's
     jurisdiction via the cross-jurisdictional gate, declared category
     "counter_terrorism_subjects" — permitted (in the cooperative
     substrate's permission table).
  4. AgencyA's analyst attempts the same cross-jurisdictional query
     under category "bulk_metadata" — refused (not in permitted set).
  5. AgencyA's analyst attempts to query without a justification
     credential — refused (justification_required_policy).
  6. OversightCommittee invokes investigate_agency cross-operator on
     AgencyA. Forensic report shows every query attempt with verdict,
     justification, and target jurisdiction.

Run with: python -m examples.five_eyes.run
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

from examples.five_eyes import _implementations as impls


def _cred(name, parent_cids=(), authorities=("invoke:any",)):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=authorities, credential_refs=tuple(parent_cids),
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

    constitutional = _cred("constitutional_authority", authorities=("delegate:any",))
    constitutional_cid = creds.put(constitutional)

    agency_a_root = _cred("agency_a_root", parent_cids=(constitutional_cid,))
    agency_b_root = _cred("agency_b_root", parent_cids=(constitutional_cid,))
    oversight_root = _cred("oversight_committee_root", parent_cids=(constitutional_cid,))
    agency_a_root_cid = creds.put(agency_a_root)
    agency_b_root_cid = creds.put(agency_b_root)
    oversight_root_cid = creds.put(oversight_root)

    # Cross-jurisdictional cooperative substrate (the bilateral
    # arrangement between agencies).
    bilateral_coop = CredentialUnit(
        name="cooperative_substrate_bilateral_agencies",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:agency_a+agency_b",
        authorities=("cross_operator:cross_jurisdictional_query",),
        credential_refs=(agency_a_root_cid, agency_b_root_cid),
    )
    bilateral_coop_cid = creds.put(bilateral_coop)

    # Oversight cooperative substrate (all three operators).
    oversight_coop = CredentialUnit(
        name="cooperative_substrate_oversight",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:agencies+oversight",
        authorities=("cross_operator:audit",),
        credential_refs=(agency_a_root_cid, agency_b_root_cid, oversight_root_cid),
    )
    oversight_coop_cid = creds.put(oversight_coop)

    # Operating credentials.
    analyst_a = _cred("analyst_agency_a", parent_cids=(agency_a_root_cid,))
    analyst_b = _cred("analyst_agency_b", parent_cids=(agency_b_root_cid,))
    oversight_chair = _cred("oversight_chair", parent_cids=(oversight_root_cid,))
    analyst_a_cid = creds.put(analyst_a)
    analyst_b_cid = creds.put(analyst_b)
    oversight_chair_cid = creds.put(oversight_chair)

    # Synthetic justification credentials (court orders, parliamentary
    # authorisations). In a real substrate these would themselves trace
    # to constitutional sources (judiciary, parliament).
    court_order_1 = _cred("court_order_2024_1234", parent_cids=(constitutional_cid,))
    court_order_2 = _cred("court_order_2024_1235", parent_cids=(constitutional_cid,))
    court_order_1_cid = creds.put(court_order_1)
    court_order_2_cid = creds.put(court_order_2)

    # Operators.
    agency_a = Operator(name="AgencyA", root_credential=agency_a_root,
                        substrate=_make_substrate("agency_a_custodian", code, creds))
    agency_b = Operator(name="AgencyB", root_credential=agency_b_root,
                        substrate=_make_substrate("agency_b_custodian", code, creds))
    oversight = Operator(name="OversightCommittee", root_credential=oversight_root,
                         substrate=_make_substrate("oversight_custodian", code, creds))
    # Use the oversight cooperative substrate as the federation registry
    # (it includes all three operators).
    oversight_arrangement = CooperativeSubstrate(cooperative_credential=oversight_coop)
    oversight_arrangement.add(agency_a)
    oversight_arrangement.add(agency_b)
    oversight_arrangement.add(oversight)

    # Implementations.
    query_impl = python_implementation(impls.QUERY_COLLECTED_METADATA, name="query_impl")
    jurisdiction_policy_impl = python_implementation(impls.JURISDICTION_SCOPE_POLICY, name="jurisdiction_policy_impl")
    justification_policy_impl = python_implementation(impls.JUSTIFICATION_REQUIRED_POLICY, name="justification_policy_impl")
    cross_gate_impl = python_implementation(impls.COOPERATIVE_CROSS_QUERY_GATE, name="cross_gate_impl")
    audit_impl = python_implementation(impls.AGENCY_AUDIT, name="agency_audit_impl")
    investigate_impl = python_implementation(impls.INVESTIGATE_AGENCY, name="investigate_agency_impl")
    query_impl_cid = code.put(query_impl)
    jurisdiction_policy_impl_cid = code.put(jurisdiction_policy_impl)
    justification_policy_impl_cid = code.put(justification_policy_impl)
    cross_gate_impl_cid = code.put(cross_gate_impl)
    audit_impl_cid = code.put(audit_impl)
    investigate_impl_cid = code.put(investigate_impl)

    # Policy units.
    jurisdiction_scope_policy = FunctionalUnit(
        name="jurisdiction_scope_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "jurisdiction_scope_policy"},
        implementation_ref=jurisdiction_policy_impl_cid,
        credential_refs=(constitutional_cid, agency_a_root_cid, agency_b_root_cid),
    )
    justification_required_policy = FunctionalUnit(
        name="justification_required_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "justification_required_policy"},
        implementation_ref=justification_policy_impl_cid,
        credential_refs=(constitutional_cid, agency_a_root_cid, agency_b_root_cid),
    )
    code.put(jurisdiction_scope_policy)
    code.put(justification_required_policy)

    def _binding(name, policy_cid, root):
        return CredentialUnit(
            name=name, transfer=TransferDiscipline.DELEGATED,
            principal="governance", authorities=(),
            policy_refs=(policy_cid,), credential_refs=(root,),
        )
    a_jurisdiction_binding_cid = creds.put(_binding("a_jurisdiction_binding",
                                                     jurisdiction_scope_policy.content_id(),
                                                     agency_a_root_cid))
    a_justification_binding_cid = creds.put(_binding("a_justification_binding",
                                                      justification_required_policy.content_id(),
                                                      agency_a_root_cid))

    # Cross-jurisdictional gate (cooperative-substrate-authorised).
    cross_gate = FunctionalUnit(
        name="cooperative_cross_query_gate",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"target_jurisdiction": "str", "requesting_agency": "str",
                         "query_category": "str"}},
        implementation_ref=cross_gate_impl_cid,
        credential_refs=(constitutional_cid, agency_a_root_cid, agency_b_root_cid,
                         oversight_root_cid, bilateral_coop_cid),
    )
    code.put(cross_gate)

    # Query unit on AgencyA. References both policies. Wilful inclusion
    # requires every transitively reachable credential to be listed at
    # the top level; both policies reference agency_b_root in their
    # credential_refs, so it must appear here too.
    query_unit_a = FunctionalUnit(
        name="query_collected_metadata_a",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"target_subject": "str", "target_jurisdiction": "str",
                         "collecting_agency": "str", "query_id": "str",
                         "justification_credential_id": "str",
                         "cooperative_authorisation_credential": "str"}},
        implementation_ref=query_impl_cid,
        credential_refs=(constitutional_cid, agency_a_root_cid, agency_b_root_cid,
                         a_jurisdiction_binding_cid, a_justification_binding_cid),
        functional_refs=(jurisdiction_scope_policy.content_id(),
                         justification_required_policy.content_id()),
        state_refs=(jurisdiction_policy_impl_cid, justification_policy_impl_cid),
    )
    code.put(query_unit_a)

    # Audit unit on each agency (here, just AgencyA for the demo).
    agency_audit = FunctionalUnit(
        name="agency_audit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"period_start_act": "int"}},
        implementation_ref=audit_impl_cid,
        credential_refs=(constitutional_cid, agency_a_root_cid, agency_b_root_cid,
                         oversight_root_cid, oversight_coop_cid),
    )
    code.put(agency_audit)

    # OversightCommittee's investigation unit.
    investigate_agency = FunctionalUnit(
        name="investigate_agency",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"agency_operator_id": "str", "audit_unit_id": "str"}},
        implementation_ref=investigate_impl_cid,
        credential_refs=(constitutional_cid, oversight_root_cid, agency_a_root_cid,
                         agency_b_root_cid, oversight_coop_cid),
        functional_refs=(agency_audit.content_id(),),
        state_refs=(audit_impl_cid,),
    )
    code.put(investigate_agency)

    # Register units on operators. Policy units must also be registered
    # so the runtime can invoke them during policy evaluation.
    for u in (jurisdiction_scope_policy, justification_required_policy, query_unit_a):
        agency_a.runtime.register_compiled(
            compile_unit(u, code, creds, custodian=agency_a.custodian)
        )
    # Cross-gate runs on the federation; we register it on AgencyB for
    # the demonstration (it represents AgencyB granting the cross-query
    # authorisation).
    agency_b.runtime.register_compiled(
        compile_unit(cross_gate, code, creds, custodian=oversight_arrangement.custodian)
    )
    agency_a.runtime.register_compiled(
        compile_unit(agency_audit, code, creds, custodian=oversight_arrangement.custodian)
    )
    oversight.runtime.register_compiled(
        compile_unit(investigate_agency, code, creds, custodian=oversight_arrangement.custodian)
    )

    return {
        "agency_a": agency_a, "agency_b": agency_b, "oversight": oversight,
        "coop": oversight_arrangement,
        "analyst_a_cid": analyst_a_cid,
        "analyst_b_cid": analyst_b_cid,
        "oversight_chair_cid": oversight_chair_cid,
        "court_order_1_cid": court_order_1_cid,
        "court_order_2_cid": court_order_2_cid,
        "bilateral_coop_cid": bilateral_coop_cid,
        "query_unit_a": query_unit_a,
        "cross_gate": cross_gate,
        "agency_audit": agency_audit,
        "investigate_agency": investigate_agency,
    }


def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _try_query(scene, label, *,
               target_subject, target_jurisdiction, collecting_agency,
               justification_credential_id="",
               cooperative_authorisation_credential="",
               query_id="q_001"):
    print(f"  {label}")
    print(f"    target_subject={target_subject}  target_jurisdiction={target_jurisdiction}")
    print(f"    collecting_agency={collecting_agency}  justification={justification_credential_id[:12] or '(none)'}...")
    if cooperative_authorisation_credential:
        print(f"    coop_authorisation={cooperative_authorisation_credential[:12]}...")
    r = scene["agency_a"].runtime.invoke(
        scene["query_unit_a"].content_id(),
        {
            "target_subject": target_subject,
            "target_jurisdiction": target_jurisdiction,
            "collecting_agency": collecting_agency,
            "query_id": query_id,
            "justification_credential_id": justification_credential_id,
            "cooperative_authorisation_credential": cooperative_authorisation_credential,
        },
        scene["analyst_a_cid"],
    )
    if isinstance(r, Permit):
        print(f"    PERMIT  records_returned={r.output.get('records_returned')}")
    else:
        print(f"    REFUSE  rationale: {r.rationale[:200]}")
    return r


def main() -> int:
    scene = build_scene()
    oversight = scene["oversight"]

    _header("Setup")
    print(f"  Operators: AgencyA, AgencyB, OversightCommittee")
    print(f"  Cooperative substrates:")
    print(f"    bilateral (AgencyA + AgencyB): cross-jurisdictional query exchange")
    print(f"    oversight (AgencyA + AgencyB + OversightCommittee): audit rights")
    print(f"  Policies on query_collected_metadata:")
    print(f"    jurisdiction_scope_policy: agencies cannot query their own jurisdiction without cooperative authorisation")
    print(f"    justification_required_policy: every query must declare a justification credential")
    print(f"  Cross-jurisdictional cooperative gate (cooperative_cross_query_gate):")
    print(f"    permitted query categories per (requesting, target) pair are credentialed")

    _header("Query 1: AgencyA analyst queries AgencyA's data on own-jurisdiction subject; no cooperative authorisation")
    _try_query(scene, "query 1:",
               target_subject="subject_001", target_jurisdiction="agency_a",
               collecting_agency="agency_a",
               justification_credential_id=scene["court_order_1_cid"])

    _header("Query 2: AgencyA tries to obtain cooperative authorisation for self-jurisdiction query")
    # First obtain cooperative authorisation by invoking the cross-gate.
    print(f"  AgencyA first invokes cooperative_cross_query_gate on AgencyB to obtain authorisation:")
    gate_result = scene["agency_a"].runtime.invoke_in(
        scene["agency_b"].content_id,
        scene["cross_gate"].content_id(),
        {
            "target_jurisdiction": "agency_a",
            "requesting_agency": "agency_a",
            "query_category": "counter_terrorism_subjects",
        },
        scene["analyst_a_cid"],
    )
    if isinstance(gate_result, Permit):
        print(f"    cooperative authorisation GRANTED  (act_id={gate_result.act_id[:12]}...)")
        # In a real system, the authorisation would be a credential
        # issued by the cooperative substrate; here we use the
        # cooperative substrate credential itself as a stand-in.
        coop_auth = scene["bilateral_coop_cid"]
    else:
        print(f"    cooperative authorisation REFUSED  ({gate_result.rationale[:120]})")
        coop_auth = ""
    print()
    _try_query(scene, "query 2 with cooperative authorisation:",
               target_subject="subject_001", target_jurisdiction="agency_a",
               collecting_agency="agency_a",
               justification_credential_id=scene["court_order_1_cid"],
               cooperative_authorisation_credential=coop_auth,
               query_id="q_002")

    _header("Query 3: cross-jurisdictional query — AgencyA querying AgencyB's data; category counter_terrorism_subjects")
    print(f"  AgencyA invokes cooperative_cross_query_gate on AgencyB:")
    gate_result_3 = scene["agency_a"].runtime.invoke_in(
        scene["agency_b"].content_id,
        scene["cross_gate"].content_id(),
        {
            "target_jurisdiction": "agency_b",
            "requesting_agency": "agency_a",
            "query_category": "counter_terrorism_subjects",
        },
        scene["analyst_a_cid"],
    )
    if isinstance(gate_result_3, Permit):
        print(f"    PERMIT — counter_terrorism_subjects is in the permitted set for (agency_a, agency_b)")
        print(f"    act_id={gate_result_3.act_id[:12]}... (on AgencyB's ledger)")
    else:
        print(f"    REFUSE  ({gate_result_3.rationale[:150]})")

    _header("Query 4: same as 3, but category 'bulk_metadata' — NOT in permitted set")
    print(f"  AgencyA attempts cooperative_cross_query_gate with category 'bulk_metadata':")
    gate_result_4 = scene["agency_a"].runtime.invoke_in(
        scene["agency_b"].content_id,
        scene["cross_gate"].content_id(),
        {
            "target_jurisdiction": "agency_b",
            "requesting_agency": "agency_a",
            "query_category": "bulk_metadata",
        },
        scene["analyst_a_cid"],
    )
    if isinstance(gate_result_4, Permit):
        print(f"    PERMIT (unexpected)")
    else:
        print(f"    REFUSE  ({gate_result_4.rationale[:200]})")
    print(f"  Bulk-metadata cross-querying is structurally outside the cooperative")
    print(f"  substrate's permitted set. Adding it would require updating the")
    print(f"  cooperative substrate credential — a substrate event recorded on both")
    print(f"  agencies' ledgers and audit-visible to OversightCommittee.")

    _header("Query 5: query with no justification credential")
    _try_query(scene, "query 5 (no justification):",
               target_subject="subject_002", target_jurisdiction="agency_b",
               collecting_agency="agency_a",
               justification_credential_id="",  # empty
               query_id="q_005")

    _header("OversightCommittee investigates AgencyA cross-operator")
    print()
    investigation = oversight.runtime.invoke(
        scene["investigate_agency"].content_id(),
        {
            "agency_operator_id": scene["agency_a"].content_id,
            "audit_unit_id": scene["agency_audit"].content_id(),
        },
        scene["oversight_chair_cid"],
    )
    if isinstance(investigation, Permit):
        r = investigation.output
        print(f"  Audit completed (act_id={investigation.act_id[:12]}... on Oversight ledger)")
        print(f"  AgencyA ledger length at audit: {r['ledger_length']}")
        print()
        print(f"  Permitted queries:")
        for q in r["permitted_queries"]:
            print(f"    {q.get('query_id','?')}: target={q['target_subject']} ({q['target_jurisdiction']})  records={q.get('records_returned')}")
        print()
        print(f"  Refused queries:")
        for q in r["refused_queries"]:
            print(f"    target={q['target_subject']} ({q['target_jurisdiction']})  rationale: {(q.get('rationale','') or '')[:150]}")

    _header("Ledger integrity")
    print()
    print(f"  AgencyA ledger: {len(scene['agency_a'].substrate.ledger)} acts; verify={scene['agency_a'].substrate.ledger.verify()}")
    print(f"  AgencyB ledger: {len(scene['agency_b'].substrate.ledger)} acts; verify={scene['agency_b'].substrate.ledger.verify()}")
    print(f"  Oversight ledger: {len(oversight.substrate.ledger)} acts; verify={oversight.substrate.ledger.verify()}")

    _header("What the substrate would have structurally changed about Five Eyes — summary")
    print()
    print(f"  SCOPE VISIBILITY. Every query is a ledger event with declared")
    print(f"  target, target jurisdiction, requesting agency, justification")
    print(f"  credential, and verdict. OversightCommittee invokes audit cross-")
    print(f"  operator and sees the complete query history.")
    print()
    print(f"  CROSS-JURISDICTIONAL CIRCUMVENTION REFUSED BY DEFAULT. AgencyA")
    print(f"  cannot query AgencyA's own jurisdiction without cooperative")
    print(f"  authorisation from a partner agency; the structural prohibition")
    print(f"  cannot be silently waived. The cooperative substrate's permitted")
    print(f"  query categories are themselves credentialed and audit-visible.")
    print()
    print(f"  AUTHORITY CHAIN VISIBILITY. Every query records the analyst")
    print(f"  credential, the justification credential (specific court order /")
    print(f"  warrant), and the cooperative authorisation credential. There")
    print(f"  is no anonymous query.")
    print()
    print(f"  STRUCTURAL POLICIES ON COLLECTION SCOPE. The cooperative substrate's")
    print(f"  permitted query categories (counter_terrorism_subjects yes;")
    print(f"  bulk_metadata no) are policy units whose content_id is the")
    print(f"  authorisation. Substituting more permissive policies requires")
    print(f"  substituting new policy units — visible in any audit.")
    print()
    print(f"  STRUCTURAL OVERSIGHT REPLACES WHISTLEBLOWING. OversightCommittee")
    print(f"  has structurally-committed audit rights via the cooperative")
    print(f"  substrate. Auditing the surveillance machinery does not require")
    print(f"  removing classified documents and giving them to journalists; it")
    print(f"  requires the oversight body invoking its audit unit and reading")
    print(f"  the result.")
    print()
    print(f"  WHAT THE SUBSTRATE CANNOT PREVENT:")
    print(f"  - A constitutional source choosing to deploy more permissive policies.")
    print(f"    The substrate makes the choice visible; it cannot make the choice.")
    print(f"  - Oversight bodies choosing not to invoke their audit rights, or")
    print(f"    being structurally denied the credentials needed to do so. The")
    print(f"    substrate provides the structural place for oversight; it cannot")
    print(f"    force oversight to look.")
    print(f"  - The fundamental political question of whether mass-collection")
    print(f"    capabilities should exist at all. The substrate makes the")
    print(f"    capabilities and their use structurally legible; what to do")
    print(f"    about them is downstream of the architecture.")
    print()
    print(f"  The substrate's claim against Five Eyes is therefore conditional and")
    print(f"  bounded. The architectural mechanism turns the question from 'are")
    print(f"  the constitutional authorities being deceived about scope?' into 'are")
    print(f"  the constitutional authorities looking at the ledger?'. The second")
    print(f"  question is a political question. The first becomes structurally")
    print(f"  unanswerable in the affirmative.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
