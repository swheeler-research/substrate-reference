"""
Compile-at-commit.

Expensive structural work happens once per unit version at commit time,
producing the unit's compiled form. Cheap operational work happens at every
act. The compiled form is what the runtime evaluates; it is content-
addressed and immutable.

The compilation pipeline, in order:

1. Resolve the full transitive reference graph and verify wilful inclusion.
   Every unit reachable through any chain of references from the source
   unit must also be listed at the source unit's top level. See
   docs/specification_gaps.md for the rationale.

2. Walk the authority chain. Start from the source unit's credential
   references; for each credential, follow its own credential references
   upward until reaching credentials with no parent (constitutional
   sources). Collect every credential content_id encountered.

3. Collect policies. A policy is a functional unit; a credential brings a
   policy into binding via its `policy_refs` field. The compiler walks
   every credential reachable through the source unit's reference graph
   and collects the union of their policy_refs. The result is a tuple of
   policy functional unit content_ids that the runtime will invoke before
   executing the source unit's implementation. Strictest-binding-wins
   emerges from refuse-wins semantics at runtime; the compiler does not
   need to combine policies statically.

4. Produce the execution-optimisation artefacts. Phase 1 stubs all three
   as None. The fields exist in the compiled form's schema so later phases
   can populate them without a schema change.

5. Witness the compiled form. Phase 1's witness is a deterministic
   SHA-256 of the compiled form's other fields. Later phases use real
   custodian signatures under multi-custodian quorum.

The compiled form itself has a content_id (the hash of its full content
including the witness). It can therefore be stored in CodeArchive and
referenced like any other content-addressed artefact.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.confidence import (
    ConfidenceSpecError,
    validate_confidence_gate,
    validate_confidence_spec,
)
from substrate.contracts import TypeMismatch, check_satisfiable, parse_constraint
from substrate.federation import LocalCustodian, WitnessRequest
from substrate.primitives import FunctionalUnit, content_hash, _make_jsonable


# =============================================================================
# Errors
# =============================================================================

class CompilationRefused(Exception):
    """A unit cannot be admitted to the substrate.

    The rationale string names what went wrong in terms a human or an
    auditing process can act on.
    """


class UnresolvedReference(CompilationRefused):
    """A declared reference does not resolve in the relevant archive."""


class WilfulInclusionFailure(CompilationRefused):
    """A unit is reached through transitive walking but is not listed at
    the source unit's top level."""


# TypeMismatch is defined in contracts.py and re-exported here for the
# convenience of callers that import everything from compile. The two
# classes are siblings (both signal compilation refusal) but not in an
# inheritance relationship; catch one or the other or both depending on
# what failure mode you care about.
__all__ = [
    "CompiledForm", "CompilationRefused", "TypeMismatch",
    "UnresolvedReference", "WilfulInclusionFailure", "compile_unit",
]


# =============================================================================
# The compiled form
# =============================================================================

@dataclass(frozen=True)
class CompiledForm:
    """The artefact the runtime evaluates at every act.

    Fields:
        source_unit: content_id of the unit this compiled form represents.
        authority_chain: content_ids of every credential walked from the
            source unit's credential references up to constitutional source
            credentials. Sorted for determinism.
        policies: content_ids of the policy functional units that govern
            this unit. Collected at compile time from the credential
            graph's policy_refs. The runtime invokes each policy before
            executing the source unit's implementation. Sorted for
            determinism.
        fused_form: content_id of a state unit holding a fused execution
            artefact, or None if the compiler did not produce one. Phase 1
            always None.
        cached_environment: content_id of a state unit holding a cached
            execution environment, or None. Phase 1 always None.
        dispatch_table: content_id of a state unit holding a dispatch
            table, or None. Phase 1 always None.
        witness: stub custodian signature. Phase 1 computes this as the
            SHA-256 of the compiled form's other fields; later phases use
            real signatures under multi-custodian quorum.
    """
    source_unit: str
    authority_chain: tuple
    policies: tuple
    fused_form: Optional[str] = None
    cached_environment: Optional[str] = None
    dispatch_table: Optional[str] = None
    witness: str = ""
    # The full verification data for the witness signature: custodian
    # public key(s), the payload hash that was signed, the signature(s),
    # and the witness type. Carried on the compiled form so it is
    # self-contained for verification — a third party can verify the
    # compiled form's witness without holding any private key and
    # without needing out-of-band custodian metadata.
    witness_payload: dict = field(default_factory=dict)

    def content_id(self) -> str:
        """Content-addressable identity of the full compiled form, witness included."""
        return content_hash(self._payload(include_witness=True))

    def unwitnessed_payload(self) -> dict:
        """The compiled form's content excluding the witness, for the witness computation."""
        return self._payload(include_witness=False)

    def _payload(self, *, include_witness: bool) -> dict:
        out = {
            "type": "compiled_form",
            "source_unit": self.source_unit,
            "authority_chain": list(self.authority_chain),
            "policies": list(self.policies),
            "fused_form": self.fused_form,
            "cached_environment": self.cached_environment,
            "dispatch_table": self.dispatch_table,
        }
        if include_witness:
            out["witness"] = self.witness
            out["witness_payload"] = _make_jsonable(self.witness_payload)
        return out


# =============================================================================
# Compilation pipeline
# =============================================================================

def compile_unit(
    unit,
    code_archive: CodeArchive,
    credentials_archive: CredentialsArchive,
    custodian: Optional[LocalCustodian] = None,
) -> CompiledForm:
    """Produce the compiled form for a unit.

    Raises CompilationRefused (or a subclass) if the unit cannot be
    admitted to the substrate.

    The custodian witnesses the compiled form. If not supplied, a default
    LocalCustodian is constructed. Phase 3 will pass a custodian that
    coordinates a multi-custodian quorum; the compile pipeline does not
    need to change.
    """
    if custodian is None:
        custodian = LocalCustodian()
    source_cid = unit.content_id()

    # 1. Resolve transitive references and check wilful inclusion. The
    #    implementation_ref of a functional unit counts as a direct ref
    #    (the unit explicitly listed it; it is what the runtime will
    #    execute). It is treated separately from state_refs so the runtime
    #    can find it without scanning, but for wilful-inclusion purposes
    #    it is no different from any other top-level reference.
    transitive = _transitive_refs(unit, code_archive, credentials_archive)
    direct = _direct_refs(unit)
    implicit = transitive - direct - {source_cid}
    if implicit:
        raise WilfulInclusionFailure(
            f"unit {unit.name} ({source_cid[:12]}) reaches references transitively "
            f"that are not listed at its top level: {sorted(implicit)}"
        )

    # 2. Walk the authority chain.
    authority_chain = _walk_authority_chain(unit, credentials_archive)

    # 3. Collect policy functional unit content_ids from the credential graph.
    policies = _collect_policies(unit, code_archive, credentials_archive)

    # 3b. Structural type check: gather every declared precondition across
    #     the source unit and every policy unit in scope; verify joint
    #     satisfiability per variable. Preconditions are opt-in; units
    #     without them simply contribute nothing to the check and rely on
    #     runtime refusal instead. See contracts.py and
    #     docs/specification_gaps.md "Non-reconcilability is a type mismatch".
    constraints = _collect_constraints(unit, policies, code_archive)
    check_satisfiable(constraints)

    # 3c. Confidence spec validation. If the unit declares a `confidence`
    #     section in its spec, validate the structure at compile time so
    #     malformed declarations are rejected before the unit is admitted.
    #     Likewise for `confidence_gate`. Both sections are optional.
    if isinstance(unit.spec, dict):
        try:
            validate_confidence_spec(unit.spec.get("confidence"))
            validate_confidence_gate(unit.spec.get("confidence_gate"))
        except ConfidenceSpecError as exc:
            raise CompilationRefused(
                f"unit {unit.name} ({source_cid[:12]}) confidence spec invalid: {exc}"
            ) from exc

    # 4. Execution-optimisation artefacts (Phase 1: all stubbed None).
    fused_form = None
    cached_environment = None
    dispatch_table = None

    # 5. Witness the compiled form. The custodian signs the payload hash.
    draft = CompiledForm(
        source_unit=source_cid,
        authority_chain=authority_chain,
        policies=policies,
        fused_form=fused_form,
        cached_environment=cached_environment,
        dispatch_table=dispatch_table,
        witness="",
    )
    response = custodian.witness(WitnessRequest(payload_hash=content_hash(draft.unwitnessed_payload())))
    return replace(draft, witness=response.signature, witness_payload=response.witness_payload)


# =============================================================================
# Reference graph walking
# =============================================================================

def _lookup(cid: str, code_archive: CodeArchive, credentials_archive: CredentialsArchive):
    """Return the unit at cid from whichever archive holds it.

    Raises UnresolvedReference if the cid is in neither archive.
    Invalidation status (revocation, supersession, deprecation) does not
    influence compilation: the unit's content is the source of truth at
    compile time. Invalidation propagates at runtime (the runtime's
    archive accessors raise on invalidated units). This keeps content-
    addressing of compiled forms a pure function of unit content.
    """
    if cid in code_archive:
        return code_archive.get_for_audit(cid)
    if cid in credentials_archive:
        return credentials_archive.get_for_compile(cid)
    raise UnresolvedReference(f"reference {cid} not found in any archive")


def _direct_refs(unit) -> set:
    """Return the set of content_ids directly referenced at a unit's top level.

    For a FunctionalUnit, this includes the implementation_ref alongside
    the three reference tuples. For a CredentialUnit, this includes the
    policy_refs (which point at policy functional units the credential
    brings into binding). Both are top-level references the unit's author
    explicitly listed.
    """
    refs = set(unit.functional_refs) | set(unit.state_refs) | set(unit.credential_refs)
    if isinstance(unit, FunctionalUnit) and unit.implementation_ref:
        refs.add(unit.implementation_ref)
    policy_refs = getattr(unit, "policy_refs", ())
    if policy_refs:
        refs.update(policy_refs)
    return refs


def _transitive_refs(source_unit, code_archive, credentials_archive) -> set:
    """Collect every content_id reachable from the source unit by following
    any reference type. The source unit itself is not included.
    """
    found: set = set()
    queue: list = []
    for cid in _direct_refs(source_unit):
        queue.append(cid)

    while queue:
        cid = queue.pop()
        if cid in found:
            continue
        found.add(cid)
        unit = _lookup(cid, code_archive, credentials_archive)
        for child in _direct_refs(unit):
            if child not in found:
                queue.append(child)
    return found


def _walk_authority_chain(source_unit, credentials_archive: CredentialsArchive) -> tuple:
    """Collect every credential reachable upward via credential_refs.

    Starts from the source unit's credential_refs; follows each credential's
    own credential_refs; terminates at credentials with no parent
    (constitutional sources). Returns a sorted tuple for determinism.
    """
    found: set = set()
    queue = list(source_unit.credential_refs)
    while queue:
        cid = queue.pop()
        if cid in found:
            continue
        if cid not in credentials_archive:
            raise UnresolvedReference(
                f"credential {cid} referenced in authority chain not in credentials archive"
            )
        found.add(cid)
        credential = credentials_archive.get_for_compile(cid)
        for parent in credential.credential_refs:
            if parent not in found:
                queue.append(parent)
    return tuple(sorted(found))


def _collect_constraints(source_unit, policy_cids, code_archive) -> list:
    """Gather every structured precondition contributed by the source unit
    and by each policy unit in scope.

    Reads each unit's spec["preconditions"] (a list of dicts) and parses
    each into a Constraint tagged with the source unit's content_id, so
    error messages can name the contributing units. Units without a
    preconditions field contribute nothing (preconditions are opt-in).
    Malformed preconditions surface as a ValueError raised by
    parse_constraint; we surface them rather than silently ignore.
    """
    constraints: list = []
    units = [source_unit] + [code_archive.get_for_audit(cid) for cid in policy_cids]
    for u in units:
        if not isinstance(u, FunctionalUnit):
            continue
        pcs = u.spec.get("preconditions", ()) if isinstance(u.spec, dict) else ()
        for raw in pcs:
            constraints.append(parse_constraint(u.content_id(), raw))
    return constraints


def _collect_policies(source_unit, code_archive, credentials_archive) -> tuple:
    """Collect every policy functional unit content_id brought into binding
    by credentials reachable through the source unit's reference graph.

    The walk visits every unit reachable from the source. When a credential
    is encountered, its `policy_refs` are added to the result; the
    credentials themselves are not descended into (the authority chain
    walk handles that). The walk does descend through functional and state
    units (and a functional unit's implementation_ref) so policies brought
    into binding through sub-units are also picked up.

    Returns a sorted tuple for determinism. Each entry is the content_id
    of a policy functional unit, which the runtime invokes before executing
    the source unit's implementation.
    """
    seen: set = set()
    policies: set = set()

    def visit(cid: str):
        if cid in seen:
            return
        seen.add(cid)
        if cid in credentials_archive:
            credential = credentials_archive.get_for_compile(cid)
            for policy_cid in credential.policy_refs:
                policies.add(policy_cid)
            # Do not descend through a credential's own credential_refs;
            # that is the authority chain. Policy refs are the binding,
            # not the inheritance.
            return
        if cid in code_archive:
            unit = code_archive.get_for_audit(cid)
            for sub in _direct_refs(unit):
                visit(sub)
            return
        raise UnresolvedReference(f"reference {cid} not found in any archive")

    for cid in _direct_refs(source_unit):
        visit(cid)

    return tuple(sorted(policies))
