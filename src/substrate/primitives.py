"""
The three primitive types.

The substrate has three primitive types. Every architectural element is one
of these. Composition is through typed references: each unit carries
functional_refs, state_refs, and credential_refs pointing at other units in
the archives.

- Functional units implement operations.
- State units hold content.
- Credential units encode authorisation.

Policies are functional units, not a special kind of credential. A
credential brings a policy into binding by referencing the policy
functional unit through its `policy_refs` field. The credential supplies
authority; the policy supplies behaviour. See docs/specification_gaps.md
"Policies are functional units" for the rationale.

Each primitive has internal typings:

- Functional units by contract pattern: specification-bounded,
  behaviour-characterised, or hybrid.
- State units by mutability discipline: immutable, mutable, or append-only.
- Credential units by transfer discipline: bearer, delegated, or capability.

Units are content-addressed. A unit's identity is the hash of its content
(including its references). This implements the architectural commitment
that compiled forms (and units generally) are immutable and content-
addressed.

Reference fields are semantically unordered sets. The field type is `tuple`
for immutability, but content_id() sorts the references before hashing;
two units listing the same references in different orders produce the same
content_id. See docs/specification_gaps.md for the rationale.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# =============================================================================
# Internal typings
# =============================================================================

class ContractPattern(Enum):
    """How a functional unit's contract is expressed.

    - SPECIFICATION_BOUNDED: the unit's behaviour is bounded by a precise
      specification of inputs and outputs. Confidence is 1.0 conditional on
      the specification's preconditions being met.

    - BEHAVIOUR_CHARACTERISED: the unit's behaviour is characterised by a
      probability distribution. AI inference units typically fall here.

    - HYBRID: the unit combines specification-bounded and
      behaviour-characterised aspects.
    """
    SPECIFICATION_BOUNDED = "specification_bounded"
    BEHAVIOUR_CHARACTERISED = "behaviour_characterised"
    HYBRID = "hybrid"


class MutabilityDiscipline(Enum):
    """How a state unit's content can change.

    - IMMUTABLE: never changes after commit.
    - MUTABLE: can be replaced under credential authorisation.
    - APPEND_ONLY: can be extended but not modified or truncated.
    """
    IMMUTABLE = "immutable"
    MUTABLE = "mutable"
    APPEND_ONLY = "append_only"


class TransferDiscipline(Enum):
    """How a credential unit's authority transfers.

    - BEARER: holder of the credential exercises the authority.
    - DELEGATED: authority is delegated to a specific principal.
    - CAPABILITY: authority is bound to specific operations on specific
      objects, transferable under explicit rules.
    """
    BEARER = "bearer"
    DELEGATED = "delegated"
    CAPABILITY = "capability"


# =============================================================================
# Content addressing
# =============================================================================

def content_hash(payload: dict) -> str:
    """Compute the content hash of a unit's payload.

    Canonical JSON serialisation, then SHA-256. The hash is the unit's
    content-addressable identity.

    Phase 1 uses SHA-256 for simplicity. The architecture commits to
    post-quantum cryptography (NIST FIPS 203/204/205) in production; for
    a single-machine prototype, SHA-256 is sufficient to demonstrate the
    content-addressing pattern.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# =============================================================================
# The three primitive types
# =============================================================================

@dataclass(frozen=True)
class FunctionalUnit:
    """A unit that implements an operation.

    A functional unit holds its specification, a contract pattern, a
    reference to its implementation (a content-addressed state unit
    holding the actual executable code), and references to other units it
    depends on at invocation.

    Fields:
        name: human-readable identifier (for debugging; not the unit's identity)
        contract_pattern: how the unit's behaviour is bounded
        spec: a dictionary describing what the unit does (inputs, outputs,
            preconditions, calibration commitments). The spec is the
            contract; it is human-readable and part of what is auditable
            before the unit is admitted to the substrate.
        implementation_ref: content_id of the StateUnit holding the unit's
            implementation. Empty string means the unit has no
            implementation (it cannot be executed; useful for
            specification-only units used in composition or documentation).
            The implementation is part of the unit's content_id by
            reference, so changing the implementation requires creating
            a new functional unit. See docs/specification_gaps.md "The
            Horizon correction" for the architectural rationale.
        functional_refs: content_ids of functional units this unit depends on.
        state_refs: content_ids of state units this unit depends on. The
            implementation state unit is referenced via implementation_ref
            and is NOT listed here as well (the runtime knows where to
            find it; the wilful-inclusion check treats it as a direct ref).
        credential_refs: content_ids of credential units this unit references
            (identity credential, plus any credentials in policy role).
        Reference fields are unordered sets; see module docstring.
    """
    name: str
    contract_pattern: ContractPattern
    spec: dict
    implementation_ref: str = ""
    functional_refs: tuple = ()
    state_refs: tuple = ()
    credential_refs: tuple = ()

    def content_id(self) -> str:
        """Content-addressable identity. Hash of the unit's content including the implementation reference."""
        return content_hash({
            "type": "functional",
            "name": self.name,
            "contract_pattern": self.contract_pattern.value,
            "spec": self.spec,
            "implementation_ref": self.implementation_ref,
            "functional_refs": sorted(self.functional_refs),
            "state_refs": sorted(self.state_refs),
            "credential_refs": sorted(self.credential_refs),
        })


@dataclass(frozen=True)
class StateUnit:
    """A unit that holds content.

    Fields:
        name: human-readable identifier
        mutability: how the state can change
        content: the actual content held by this unit. For immutable units,
            this is fixed at creation. For mutable units, this represents
            the current value (and mutation produces a new content_id).
            For append-only units, content is a list and extension produces
            a new content_id.
        functional_refs: content_ids of functional units that derived this
            state, or that the state depends on.
        state_refs: content_ids of state units in this state's lineage.
        credential_refs: content_ids of credential units this state
            references (identity credential, plus any credentials in policy
            role governing access).
        Reference fields are unordered sets; see module docstring.
    """
    name: str
    mutability: MutabilityDiscipline
    content: Any
    functional_refs: tuple = ()
    state_refs: tuple = ()
    credential_refs: tuple = ()

    def content_id(self) -> str:
        return content_hash({
            "type": "state",
            "name": self.name,
            "mutability": self.mutability.value,
            "content": _make_jsonable(self.content),
            "functional_refs": sorted(self.functional_refs),
            "state_refs": sorted(self.state_refs),
            "credential_refs": sorted(self.credential_refs),
        })


@dataclass(frozen=True)
class CredentialUnit:
    """A unit that encodes authorisation.

    Fields:
        name: human-readable identifier
        transfer: how the credential's authority transfers
        principal: who or what the credential authorises (a string identifier
            in Phase 1; later phases use full constitutional-source references)
        authorities: the set of operations the credential authorises
            (a list of strings naming permitted operations in Phase 1)
        constraints: a dictionary of conditions (e.g. expiry, scope, jurisdiction).
            Phase 1 keeps this simple.
        policy_refs: content_ids of functional units this credential brings
            into binding as policies. A policy is itself a functional unit
            (see docs/specification_gaps.md "Policies are functional units").
            The credential supplies authority; the policy supplies behaviour.
        For credential units, credential_refs typically carries the
        authority chain (the parent credentials this credential derives
        from). Functional and state refs are present for symmetry.
    """
    name: str
    transfer: TransferDiscipline
    principal: str
    authorities: tuple  # immutable tuple of strings
    constraints: dict = field(default_factory=dict)
    policy_refs: tuple = ()
    functional_refs: tuple = ()
    state_refs: tuple = ()
    credential_refs: tuple = ()

    def content_id(self) -> str:
        return content_hash({
            "type": "credential",
            "name": self.name,
            "transfer": self.transfer.value,
            "principal": self.principal,
            "authorities": list(self.authorities),
            "constraints": _make_jsonable(self.constraints),
            "policy_refs": sorted(self.policy_refs),
            "functional_refs": sorted(self.functional_refs),
            "state_refs": sorted(self.state_refs),
            "credential_refs": sorted(self.credential_refs),
        })


# =============================================================================
# Helpers
# =============================================================================

def _make_jsonable(obj: Any) -> Any:
    """Convert an object into a JSON-serialisable form for hashing.

    Handles tuples (to lists), dataclasses (to dicts), nested structures.
    """
    if isinstance(obj, tuple):
        return [_make_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_make_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _make_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return _make_jsonable(asdict(obj))
    return obj
