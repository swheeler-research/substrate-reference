"""
Content-addressed archives.

Phase 1 has two archives, both in-memory:

- CodeArchive stores functional and state units (and, once they exist,
  compiled forms). Lookup is by content_id.
- CredentialsArchive stores credential units and tracks which have been
  revoked. Lookup is by content_id; revoked credentials raise rather than
  returning silently, so propagation of revocation happens automatically at
  every reference site that calls get(). See docs/specification_gaps.md for
  the rationale.

The two archives are separate classes rather than a shared base because
they have different semantics (revocation belongs only on credentials) and
Phase 1 resists premature abstraction. Phase 3 may revisit when the
federation layer arrives.
"""

from __future__ import annotations

from typing import Any

from substrate.primitives import CredentialUnit


class CredentialRevoked(Exception):
    """Raised when a revoked credential is looked up through get().

    The credential is attached to the exception so audit code that needs the
    revoked credential's content (for example, to verify a historical ledger
    entry that referenced it while it was still active) can recover it
    without bypassing the archive's safety check.
    """

    def __init__(self, credential: CredentialUnit):
        self.credential = credential
        super().__init__(
            f"Credential {credential.content_id()} ({credential.name}) is revoked"
        )


class CredentialSuperseded(Exception):
    """Raised when a credential has been superseded by a successor credential.

    Policy supersession and constitutional source credential update both
    surface through this mechanism: an old credential is marked superseded
    by a new one, and any invocation depending on the old credential
    refuses until the dependent units recompile against the new credential.

    The exception carries both the superseded credential and the successor's
    content_id so callers can surface the supersession in the refusal
    rationale and (in a future phase) trigger recompilation.
    """

    def __init__(self, credential: CredentialUnit, successor_cid: str):
        self.credential = credential
        self.successor_cid = successor_cid
        super().__init__(
            f"Credential {credential.content_id()} ({credential.name}) "
            f"superseded by {successor_cid}"
        )


class CredentialDeprecated(Exception):
    """Raised when a deprecated credential is looked up through get().

    Deprecation is an explicit signal that a credential should no longer
    be used. Distinct from revocation (which signals "authority withdrawn")
    and supersession (which signals "replaced by a successor"). All three
    refuse invocations; the rationale differs.
    """

    def __init__(self, credential: CredentialUnit):
        self.credential = credential
        super().__init__(
            f"Credential {credential.content_id()} ({credential.name}) is deprecated"
        )


class UnitDeprecated(Exception):
    """Raised when a deprecated unit (functional or state) is looked up.

    Deprecation of executable units is the unit-level counterpart to
    credential deprecation. A deprecated unit's compiled forms refuse
    invocation; new compositions cannot reference it.
    """

    def __init__(self, unit):
        self.unit = unit
        super().__init__(
            f"Unit {unit.content_id()} ({getattr(unit, 'name', '?')}) is deprecated"
        )


class CodeArchive:
    """In-memory content-addressed store for functional and state units.

    Putting the same unit twice is a no-op: the unit's content_id is
    deterministic, so the second put writes the same key with the same
    value. Lookup of a missing content_id raises KeyError, the same way a
    dict does, so the failure cannot be silently swallowed.

    Units can be deprecated. `get()` raises `UnitDeprecated` for
    deprecated units; the deprecation set is archive state, not unit
    content, so deprecating a unit does not change its content_id (audit
    trails referencing the unit remain resolvable via `get_for_audit()`).
    """

    def __init__(self):
        self._store: dict = {}
        self._deprecated: set = set()

    def put(self, unit: Any) -> str:
        cid = unit.content_id()
        self._store[cid] = unit
        return cid

    def get(self, content_id: str) -> Any:
        if content_id not in self._store:
            raise KeyError(content_id)
        unit = self._store[content_id]
        if content_id in self._deprecated:
            raise UnitDeprecated(unit)
        return unit

    def get_for_audit(self, content_id: str) -> Any:
        """Return the unit regardless of deprecation status. For audit
        and for compile-time graph walking, where the unit's content is
        the source of truth even if its operational status has changed."""
        if content_id not in self._store:
            raise KeyError(content_id)
        return self._store[content_id]

    def deprecate(self, content_id: str) -> None:
        """Mark a unit as deprecated. Idempotent. Unit must exist."""
        if content_id not in self._store:
            raise KeyError(content_id)
        self._deprecated.add(content_id)

    def status(self, content_id: str) -> str:
        """Return 'active' or 'deprecated'. Raises KeyError if unit not present."""
        if content_id not in self._store:
            raise KeyError(content_id)
        return "deprecated" if content_id in self._deprecated else "active"

    def __contains__(self, content_id: str) -> bool:
        return content_id in self._store

    def __len__(self) -> int:
        return len(self._store)


class CredentialsArchive:
    """In-memory content-addressed store for credential units, with revocation.

    Credentials themselves are immutable; revocation is recorded in a
    separate set held by the archive. get() is the runtime path: it raises
    CredentialRevoked for revoked credentials. status() is the diagnostic
    path: it reports active/revoked without raising and is intended for the
    invalidation-surface code in later phases.
    """

    def __init__(self):
        self._store: dict = {}
        self._revoked: set = set()
        # old_cid -> new_cid: the credential at old_cid has been superseded
        # by the credential at new_cid.
        self._superseded: dict = {}
        self._deprecated: set = set()

    def put(self, credential: CredentialUnit) -> str:
        cid = credential.content_id()
        self._store[cid] = credential
        return cid

    def get(self, content_id: str) -> CredentialUnit:
        if content_id not in self._store:
            raise KeyError(content_id)
        credential = self._store[content_id]
        if content_id in self._revoked:
            raise CredentialRevoked(credential)
        if content_id in self._superseded:
            raise CredentialSuperseded(credential, self._superseded[content_id])
        if content_id in self._deprecated:
            raise CredentialDeprecated(credential)
        return credential

    def get_for_compile(self, content_id: str) -> CredentialUnit:
        """Return the credential regardless of revocation status.

        This is the compile-time accessor. Compilation must produce an
        immutable artefact as a function of unit content alone, so it
        cannot let runtime revocation status influence compilation;
        otherwise the same source unit would compile to different compiled
        forms over time, breaking content-addressing. Revocation propagates
        at the runtime path via get() instead.

        Raises KeyError if the credential was never put.
        """
        if content_id not in self._store:
            raise KeyError(content_id)
        return self._store[content_id]

    def revoke(self, content_id: str) -> None:
        """Mark a credential as revoked. Idempotent. The credential must exist."""
        if content_id not in self._store:
            raise KeyError(content_id)
        self._revoked.add(content_id)

    def supersede(self, old_content_id: str, new_content_id: str) -> None:
        """Mark old_content_id as superseded by new_content_id. Both must exist.

        Idempotent only if the recorded successor is the same; reassigning
        a different successor raises ValueError so accidental rewriting of
        the supersession map is caught.
        """
        if old_content_id not in self._store:
            raise KeyError(old_content_id)
        if new_content_id not in self._store:
            raise KeyError(new_content_id)
        if old_content_id in self._superseded and self._superseded[old_content_id] != new_content_id:
            raise ValueError(
                f"credential {old_content_id} already superseded by "
                f"{self._superseded[old_content_id]}; cannot reassign to {new_content_id}"
            )
        self._superseded[old_content_id] = new_content_id

    def deprecate(self, content_id: str) -> None:
        """Mark a credential as deprecated. Idempotent. Credential must exist."""
        if content_id not in self._store:
            raise KeyError(content_id)
        self._deprecated.add(content_id)

    def status(self, content_id: str) -> str:
        """Diagnostic accessor: returns 'active', 'revoked', 'superseded_by:<cid>',
        or 'deprecated'.

        Raises KeyError if the credential was never put. This is not the
        runtime path; use get() on the runtime path so invalidation
        propagates automatically.
        """
        if content_id not in self._store:
            raise KeyError(content_id)
        if content_id in self._revoked:
            return "revoked"
        if content_id in self._superseded:
            return f"superseded_by:{self._superseded[content_id]}"
        if content_id in self._deprecated:
            return "deprecated"
        return "active"

    def __contains__(self, content_id: str) -> bool:
        return content_id in self._store

    def __len__(self) -> int:
        return len(self._store)
