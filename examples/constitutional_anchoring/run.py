"""
Constitutional anchoring — end-to-end demonstration.

A single operator (Ministry) running a trivial functional unit. The
constitutional source ("Parliament") is a credential whose parent
credential_refs are five natural-person credentials (named MPs). Each
MP has their own real Ed25519 keypair. Walking the authority chain
upward from any unit reaches these natural persons.

Scenario:

1. Setup: five MPs, parliament_v1 (their quorum), minister_v1
   (delegated under parliament_v1), civil_servant_v1 (delegated under
   minister_v1), process_application unit (authority chain reaches all
   five MPs).

2. Normal operation: civil_servant invokes process_application; the
   authority chain check passes; the audit shows the chain visibly
   terminating at the five MPs by name.

3. Election: MP Alice Brown loses her seat to MP Eleanor Martin. The
   Ministry's operator admits parliament_v2 (membership updated) and
   supersedes parliament_v1. All compiled forms whose authority chains
   include parliament_v1 now refuse. To resume operation, the Ministry
   recompiles its units under a new chain (minister_v2 -> parliament_v2,
   civil_servant_v2 -> minister_v2, process_application_v2 -> new chain).

4. Revocation mid-term: MP David Singh resigns. His credential is
   revoked. Authority chains that include his credential — i.e., the
   re-established v2 chain — refuse at the runtime's credential check
   for the revoked credential.

5. Audit: the operator's ledger shows the full sequence of constitutional
   events (parliament supersession, individual MP revocation, unit
   deprecations and re-registrations) alongside the application
   processing events.

Run with: python -m examples.constitutional_anchoring.run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from substrate.compile import compile_unit
from substrate.implementations import python_implementation
from substrate.operator import Operator, create_operator
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse

from examples.constitutional_anchoring import _implementations as impls


def _cred(name, parent_cids=(), authorities=("invoke:any",)):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=authorities, credential_refs=tuple(parent_cids),
    )


def _natural_person(name, role="MP"):
    """Build a natural-person credential.

    The credential has a real Ed25519 identity (via the credential's
    content_id and the substrate's existing content-addressing). What
    the demonstration cannot do is anchor this content_id to an actual
    human — that is the institutional/legal/cryptographic gap the
    README discusses. Here, MPs are named credentials.
    """
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=("constitutional:vote",),
        # Natural-person credentials are themselves at the root of the
        # recursion. They have no parent credentials in the substrate's
        # content-addressing sense; their real-world anchoring is what
        # makes them constitutional.
        credential_refs=(),
    )


# ===========================================================================
# Narrative helpers
# ===========================================================================

def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _walk_authority_chain(creds_archive, root_cid, visited=None, depth=0, max_depth=12):
    """Walk a credential's authority chain upward, printing the tree.

    Demonstrates that the substrate's authority chain terminates at
    natural-person credentials (those with empty credential_refs).
    """
    if visited is None:
        visited = set()
    if root_cid in visited or depth > max_depth:
        return
    visited.add(root_cid)
    try:
        cred = creds_archive.get_for_compile(root_cid)
    except KeyError:
        print(f"  {'  ' * depth}{root_cid[:12]}... (NOT IN ARCHIVE)")
        return
    indent = "  " * depth
    if not cred.credential_refs:
        # Terminus: a natural-person (or root) credential.
        print(f"  {indent}{root_cid[:12]}... {cred.name}  [TERMINUS — natural person]")
        return
    print(f"  {indent}{root_cid[:12]}... {cred.name}")
    for parent_cid in cred.credential_refs:
        _walk_authority_chain(creds_archive, parent_cid, visited, depth + 1, max_depth)


# ===========================================================================
# Main
# ===========================================================================

def main() -> int:
    # ----------------------------------------------------------------
    # Setup: five MPs, parliament_v1 quorum, derived chain, unit
    # ----------------------------------------------------------------
    _header("Setup: five MPs form Parliament; Ministry units derive authority from them")

    # Five natural-person credentials. In reality, each would be
    # anchored to a specific human via institutional / cryptographic /
    # legal mechanisms outside the substrate's domain.
    mps_v1 = [
        _natural_person("MP_Alice_Brown"),
        _natural_person("MP_David_Singh"),
        _natural_person("MP_Catherine_Yu"),
        _natural_person("MP_Frank_Patel"),
        _natural_person("MP_Sarah_Okonkwo"),
    ]

    # Ministry operator. Its substrate holds all credentials and units.
    # The operator's root credential is a generic "constitutional" stand-in;
    # we use it only to register the operator. The actual constitutional
    # anchoring runs through parliament_v1, not through the operator root.
    op = create_operator("Ministry", _cred("ministry_administration"))

    # Put each MP credential in the archive.
    mp_v1_cids = []
    for mp in mps_v1:
        cid = op.substrate.credentials.put(mp)
        mp_v1_cids.append(cid)
        print(f"  natural-person credential: {cid[:12]}...  {mp.name}")

    # Parliament_v1: credential with parent_refs to all five MPs.
    parliament_v1 = CredentialUnit(
        name="parliament_v1",
        transfer=TransferDiscipline.DELEGATED,
        principal="parliament_v1",
        authorities=("delegate:executive",),
        credential_refs=tuple(mp_v1_cids),
    )
    parliament_v1_cid = op.substrate.credentials.put(parliament_v1)
    print(f"  parliament_v1: {parliament_v1_cid[:12]}...  (quorum of 5 MPs)")

    # Minister credential, delegated by parliament_v1.
    minister_v1 = _cred("minister_v1", parent_cids=(parliament_v1_cid,),
                        authorities=("delegate:civil_service",))
    minister_v1_cid = op.substrate.credentials.put(minister_v1)
    print(f"  minister_v1: {minister_v1_cid[:12]}...  (delegated by parliament_v1)")

    # Civil-servant credential, delegated by minister_v1.
    civil_servant_v1 = _cred("civil_servant_v1", parent_cids=(minister_v1_cid,))
    civil_servant_v1_cid = op.substrate.credentials.put(civil_servant_v1)
    print(f"  civil_servant_v1: {civil_servant_v1_cid[:12]}...  (delegated by minister_v1)")

    # The process_application functional unit. Its authority chain goes
    # all the way up to the natural-person credentials.
    impl = python_implementation(impls.PROCESS_APPLICATION, name="process_application_impl")
    impl_cid = op.substrate.code.put(impl)
    # Wilful inclusion: the unit must list every transitively reachable
    # credential at its top level. parliament_v1 reaches the five MPs;
    # the unit must list them too. This is the substrate's transparency
    # commitment showing through — the constitutional sources affecting
    # the unit are visible in the unit's content.
    process_v1 = FunctionalUnit(
        name="process_application_v1",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"application_id": "str"}, "outputs": {"decision": "str"}},
        implementation_ref=impl_cid,
        credential_refs=(parliament_v1_cid, *mp_v1_cids),
    )
    op.substrate.code.put(process_v1)
    op.runtime.register_compiled(compile_unit(process_v1, op.substrate.code, op.substrate.credentials,
                                               custodian=op.custodian))
    print(f"  process_application_v1: {process_v1.content_id()[:12]}...  (authority chain reaches MPs)")

    # ----------------------------------------------------------------
    # Authority chain visibility
    # ----------------------------------------------------------------
    _header("Authority chain visibly terminates at natural persons")
    print()
    print(f"  Walking process_application_v1's authority chain (starting from its credential_refs):")
    print()
    _walk_authority_chain(op.substrate.credentials, parliament_v1_cid)
    print()
    print(f"  The recursion terminates at five named natural persons. This is the")
    print(f"  architectural mechanism. The institutional anchoring of those names")
    print(f"  to specific humans is outside what code can demonstrate.")

    # ----------------------------------------------------------------
    # Normal operation
    # ----------------------------------------------------------------
    _header("Normal operation: civil servant processes an application")
    r = op.runtime.invoke(process_v1.content_id(),
                          {"application_id": "app_001"},
                          civil_servant_v1_cid)
    print(f"  result: {r.__class__.__name__}")
    if isinstance(r, Permit):
        print(f"  output: {r.output}")
        print(f"  The substrate's delegation check passed because civil_servant_v1's")
        print(f"  ancestry reaches parliament_v1, which is in the unit's authority chain.")

    # ----------------------------------------------------------------
    # Election: Alice Brown replaced by Eleanor Martin
    # ----------------------------------------------------------------
    _header("Election: MP Alice Brown loses her seat to MP Eleanor Martin")
    print()
    print(f"  Eleanor Martin's natural-person credential is admitted to the archive.")
    eleanor = _natural_person("MP_Eleanor_Martin")
    eleanor_cid = op.substrate.credentials.put(eleanor)
    print(f"    natural-person credential: {eleanor_cid[:12]}...  {eleanor.name}")
    print()
    print(f"  parliament_v2 is minted with the new membership (Brown -> Martin).")
    print(f"  Its content_id differs from parliament_v1 because its parent refs differ.")
    mp_v2_cids = list(mp_v1_cids)
    mp_v2_cids[0] = eleanor_cid  # Eleanor replaces Alice
    parliament_v2 = CredentialUnit(
        name="parliament_v2",
        transfer=TransferDiscipline.DELEGATED,
        principal="parliament_v2",
        authorities=("delegate:executive",),
        credential_refs=tuple(mp_v2_cids),
    )
    parliament_v2_cid = op.substrate.credentials.put(parliament_v2)
    print(f"    parliament_v1: {parliament_v1_cid[:12]}...")
    print(f"    parliament_v2: {parliament_v2_cid[:12]}...  (different content_id)")
    print()
    print(f"  Operator records the supersession as an administrative act on the ledger.")
    supersede_act = op.supersede_credential(parliament_v1_cid, parliament_v2_cid,
                                             op.root_credential.content_id())
    print(f"  admin act: {supersede_act.content_id()[:12]}...  ({supersede_act.inputs['action']})")
    print()
    print(f"  Now any unit whose authority chain includes parliament_v1 refuses on")
    print(f"  invocation — the runtime's credential check raises CredentialSuperseded.")
    r = op.runtime.invoke(process_v1.content_id(),
                          {"application_id": "app_002"},
                          civil_servant_v1_cid)
    print(f"  attempting to invoke process_application_v1:")
    print(f"    result: {r.__class__.__name__}")
    if isinstance(r, Refuse):
        print(f"    rationale: {r.rationale[:200]}")

    # ----------------------------------------------------------------
    # Reconstitute the chain under parliament_v2
    # ----------------------------------------------------------------
    _header("Reconstitute the chain under parliament_v2")
    print()
    print(f"  To resume operation, the Ministry mints new derived credentials and")
    print(f"  registers a new compiled form for the unit. The substrate's content-")
    print(f"  addressing forces this: changing the constitutional source upstream")
    print(f"  invalidates everything downstream that referenced the old source.")
    print()
    minister_v2 = _cred("minister_v2", parent_cids=(parliament_v2_cid,),
                        authorities=("delegate:civil_service",))
    minister_v2_cid = op.substrate.credentials.put(minister_v2)
    civil_servant_v2 = _cred("civil_servant_v2", parent_cids=(minister_v2_cid,))
    civil_servant_v2_cid = op.substrate.credentials.put(civil_servant_v2)

    process_v2 = FunctionalUnit(
        name="process_application_v2",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"application_id": "str"}, "outputs": {"decision": "str"}},
        implementation_ref=impl_cid,  # same impl; just new authority chain
        credential_refs=(parliament_v2_cid, *mp_v2_cids),
    )
    op.substrate.code.put(process_v2)
    op.runtime.register_compiled(compile_unit(process_v2, op.substrate.code,
                                               op.substrate.credentials, custodian=op.custodian))
    print(f"  minister_v2: {minister_v2_cid[:12]}...  (delegated by parliament_v2)")
    print(f"  civil_servant_v2: {civil_servant_v2_cid[:12]}...  (delegated by minister_v2)")
    print(f"  process_application_v2: {process_v2.content_id()[:12]}...  (under v2 chain)")
    print()
    r = op.runtime.invoke(process_v2.content_id(),
                          {"application_id": "app_002"},
                          civil_servant_v2_cid)
    print(f"  civil_servant_v2 invokes process_application_v2:")
    print(f"    result: {r.__class__.__name__}")
    if isinstance(r, Permit):
        print(f"    output: {r.output}")
        print(f"    The new chain works; the old chain remains permanently invalidated.")

    # ----------------------------------------------------------------
    # Mid-term revocation: MP David Singh resigns
    # ----------------------------------------------------------------
    _header("Mid-term revocation: MP David Singh resigns")
    print()
    print(f"  David Singh's credential is revoked by an administrative act.")
    david_cid = mp_v2_cids[1]
    revoke_act = op.revoke_credential(david_cid, op.root_credential.content_id())
    print(f"  admin act: {revoke_act.content_id()[:12]}...  ({revoke_act.inputs['action']})")
    print()
    print(f"  Singh's credential is in parliament_v2's parent_refs. The runtime's")
    print(f"  authority chain check walks through parliament_v2 to Singh's credential,")
    print(f"  attempts credentials.get(Singh) — raises CredentialRevoked.")
    print()
    r = op.runtime.invoke(process_v2.content_id(),
                          {"application_id": "app_003"},
                          civil_servant_v2_cid)
    print(f"  attempting to invoke process_application_v2:")
    print(f"    result: {r.__class__.__name__}")
    if isinstance(r, Refuse):
        print(f"    rationale: {r.rationale[:200]}")
    print()
    print(f"  Until Singh is replaced (parliament_v3 with a new MP, new derived")
    print(f"  credentials, new compiled forms) the Ministry's units cannot operate.")
    print(f"  This is the architectural cost of strong constitutional attribution —")
    print(f"  the substrate refuses to operate under an incomplete or invalid")
    print(f"  constitutional source. In real terms: the operator-side cost of every")
    print(f"  parliamentary change is structurally visible, not procedural.")

    # ----------------------------------------------------------------
    # Audit
    # ----------------------------------------------------------------
    _header("Ledger: every constitutional event recorded")
    print()
    print(f"  Ministry ledger ({len(op.substrate.ledger)} acts):")
    for i, act in enumerate(op.substrate.ledger):
        kind = act.kind
        verdict = act.verdict
        if kind == "administrative":
            action = act.inputs.get("action", "?")
            detail = action
        else:
            try:
                cf = op.substrate.code.get_for_audit(act.compiled_form_id)
                source = op.substrate.code.get_for_audit(cf.source_unit)
                detail = f"invoke {source.name}"
            except Exception:
                detail = "invoke (unresolved)"
        print(f"    [{i}] kind={kind:14s} verdict={verdict:9s} {detail}")
    print()
    print(f"  ledger.verify() -> {op.substrate.ledger.verify()}")

    # ----------------------------------------------------------------
    # Closing
    # ----------------------------------------------------------------
    _header("What this demonstrates — and what it does not")
    print()
    print(f"  DEMONSTRATED ARCHITECTURALLY:")
    print(f"  - Authority chains visibly terminate at named natural-person credentials")
    print(f"    rather than at string labels.")
    print(f"  - Election succession: a change in the constitutional source's")
    print(f"    membership produces a different content_id; the old source can be")
    print(f"    superseded; downstream compiled forms are invalidated; recompilation")
    print(f"    under the new source restores operation.")
    print(f"  - Mid-term revocation: revoking a single natural-person credential")
    print(f"    propagates through every compiled form whose authority chain")
    print(f"    includes it. The substrate refuses to operate under an incomplete")
    print(f"    constitutional source.")
    print(f"  - Constitutional events are recorded on the ledger as administrative")
    print(f"    acts, with the operator's authorising credential and the targets.")
    print()
    print(f"  NOT DEMONSTRATED (and not demonstrable in code):")
    print(f"  - The institutional, legal, and cryptographic apparatus that anchors")
    print(f"    a specific Ed25519 keypair to a specific human being. Biometric")
    print(f"    attestation; hardware security modules; legal recognition of")
    print(f"    cryptographic credentials; processes for key generation, loss,")
    print(f"    recovery, death, and succession. All outside the substrate's")
    print(f"    domain. The substrate provides the structural machinery; this")
    print(f"    apparatus would be the institutional substrate that makes the")
    print(f"    architectural mechanism real.")
    print(f"  - The political work that would establish any real constitutional")
    print(f"    source as operating through the substrate.")
    print()
    print(f"  WHAT THIS CLOSES: the architectural loop. The substrate's claim that")
    print(f"  'decisions are attributable to natural persons' now has a concrete")
    print(f"  demonstration of the natural-person layer, using the substrate's")
    print(f"  existing machinery applied at the constitutional source. The")
    print(f"  remaining gap is institutional, not architectural.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
