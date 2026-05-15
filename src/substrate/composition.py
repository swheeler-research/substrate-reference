"""
Author-side helper for satisfying the wilful-inclusion check.

The substrate requires a composing unit to list every transitively reachable
unit at its top level (see docs/specification_gaps.md). Maintaining that
list by hand is feasible for small compositions but becomes a bookkeeping
chore as graphs grow.

`compose_refs` takes the constituent units of a composition and the two
archives, walks the transitive reference graph, and returns the three
typed reference tuples ready to spread into a composing unit's
construction. The wilful-inclusion contract at the substrate level is
unchanged; this helper just removes the manual enumeration.
"""

from __future__ import annotations

from dataclasses import dataclass

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.primitives import CredentialUnit, FunctionalUnit, StateUnit


@dataclass(frozen=True)
class ComposedRefs:
    """The three typed reference tuples a composing unit can pass straight in."""
    functional: tuple
    state: tuple
    credential: tuple


def compose_refs(
    *constituents,
    code_archive: CodeArchive,
    credentials_archive: CredentialsArchive,
) -> ComposedRefs:
    """Compute the transitive closure of references for a composition.

    Each constituent unit is itself added to the appropriate tuple (a
    FunctionalUnit constituent goes into `functional`, etc.). Then the
    walk follows every reference field of every reachable unit, looking
    up units in the appropriate archive.

    The returned tuples are sorted (matching the set-like semantics
    described in docs/specification_gaps.md), so two compositions of the
    same constituents in any order produce the same refs.
    """
    functional: set = set()
    state: set = set()
    credential: set = set()
    visited: set = set()
    queue: list = []

    for unit in constituents:
        if isinstance(unit, FunctionalUnit):
            functional.add(unit.content_id())
        elif isinstance(unit, StateUnit):
            state.add(unit.content_id())
        elif isinstance(unit, CredentialUnit):
            credential.add(unit.content_id())
        else:
            raise TypeError(
                f"compose_refs received {type(unit).__name__}; "
                f"expected FunctionalUnit, StateUnit, or CredentialUnit"
            )
        queue.append(unit)

    while queue:
        unit = queue.pop()
        cid = unit.content_id()
        if cid in visited:
            continue
        visited.add(cid)

        for fref in unit.functional_refs:
            functional.add(fref)
            if fref not in visited:
                queue.append(code_archive.get_for_audit(fref))
        for sref in unit.state_refs:
            state.add(sref)
            if sref not in visited:
                queue.append(code_archive.get_for_audit(sref))
        for cref in unit.credential_refs:
            credential.add(cref)
            if cref not in visited:
                queue.append(credentials_archive.get_for_compile(cref))

        # A functional unit's implementation_ref points at a state unit
        # holding the executable artefact. It is a direct dependency that
        # must satisfy wilful inclusion; pull it into the state closure.
        if isinstance(unit, FunctionalUnit) and unit.implementation_ref:
            state.add(unit.implementation_ref)
            if unit.implementation_ref not in visited:
                queue.append(code_archive.get_for_audit(unit.implementation_ref))

        # A credential's policy_refs point at policy functional units the
        # credential brings into binding. They must be top-level listed at
        # the composing unit too, so the wilful-inclusion check passes.
        policy_refs = getattr(unit, "policy_refs", ())
        for pref in policy_refs:
            functional.add(pref)
            if pref not in visited:
                queue.append(code_archive.get_for_audit(pref))

    return ComposedRefs(
        functional=tuple(sorted(functional)),
        state=tuple(sorted(state)),
        credential=tuple(sorted(credential)),
    )
