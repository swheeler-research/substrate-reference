"""
Policies for the Universal Credit example.

A policy is a functional unit. Each helper here returns a pair:

    (policy_functional_unit, binding_credential)

The functional unit's implementation evaluates the constraint and raises
if it should refuse, returning normally otherwise. The runtime catches
the raise, converts it to a Refuse, and propagates that to the parent
unit's policy evaluation (which then refuses the parent with the
policy's rationale).

The binding credential holds the policy in its `policy_refs` field; it
is the authority that brings the policy into force. Different binding
credentials can carry the same policy with different authority claims.

Strictest-binding-wins emerges from refuse-wins: when retention_30,
retention_60, and retention_90 are all in scope, the 30-day policy is
the first to refuse anything over 30, so the effective cap is 30.
"""

from substrate.implementations import python_implementation
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)


# =============================================================================
# Helpers
# =============================================================================

def _build_policy(
    name: str,
    source: str,
    preconditions=(),
    contract=ContractPattern.SPECIFICATION_BOUNDED,
):
    """Return (policy_functional_unit, implementation_state_unit).

    `preconditions` is the structural contract the policy declares. The
    compiler analyses these for joint satisfiability across all policies
    in scope; an incomposable composition is a TypeMismatch caught at
    compile-at-commit (see docs/specification_gaps.md "Non-reconcilability
    is a type mismatch"). The implementation is trusted to honour the
    declared contract; the substrate verifies structure, not faithfulness.
    """
    impl = python_implementation(source, name=f"{name}_impl")
    unit = FunctionalUnit(
        name=name,
        contract_pattern=contract,
        spec={
            "name": name,
            "evaluates": "inputs at runtime; raises to refuse",
            "preconditions": list(preconditions),
        },
        implementation_ref=impl.content_id(),
    )
    return unit, impl


def _binding(name: str, policy_unit_cid: str) -> CredentialUnit:
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal="governance:dwp",
        authorities=(),
        policy_refs=(policy_unit_cid,),
    )


# =============================================================================
# Retention policies (deterministic; max-wins emerges from refuse-wins).
# =============================================================================

def retention_policy_pair(max_days: int):
    """A policy that refuses if inputs request retention above max_days.

    Declares the structural precondition `max_retention_days <= max_days`;
    if a composition combines this with a policy requiring
    `max_retention_days >= N` for N > max_days, the compiler catches the
    type mismatch at compile-at-commit.
    """
    source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    if inputs.get('max_retention_days', 0) > {max_days}:\n"
        f"        raise Exception('requested retention exceeds policy cap of {max_days} days')\n"
        "    return {}\n"
    )
    unit, impl = _build_policy(
        f"retention_max_{max_days}_days", source,
        preconditions=[{"var": "max_retention_days", "op": "lte", "value": max_days}],
    )
    binding = _binding(f"retention_max_{max_days}_days_binding", unit.content_id())
    return unit, impl, binding


# =============================================================================
# Confidence policies (deterministic floor; would be behaviour-characterised
# in a fuller realisation where the policy interprets uncertainty rather
# than reading a declared value).
# =============================================================================

def confidence_policy_pair(min_confidence: float):
    """A policy that refuses if inputs declare confidence below the floor."""
    source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    declared = inputs.get('min_confidence')\n"
        "    if declared is None:\n"
        f"        raise Exception('input min_confidence missing; policy requires >= {min_confidence}')\n"
        f"    if declared < {min_confidence}:\n"
        f"        raise Exception('declared confidence below policy floor {min_confidence}')\n"
        "    return {}\n"
    )
    unit, impl = _build_policy(
        f"min_confidence_{min_confidence}", source,
        preconditions=[{"var": "min_confidence", "op": "gte", "value": min_confidence}],
    )
    binding = _binding(f"min_confidence_{min_confidence}_binding", unit.content_id())
    return unit, impl, binding


# =============================================================================
# Purpose policies (allowed-set intersection emerges from refuse-wins).
# =============================================================================

def purpose_policy_pair(allowed_purposes):
    """A policy that refuses if inputs request a purpose outside the allowed set."""
    allowed_list = sorted(set(allowed_purposes))
    source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    allowed = {allowed_list!r}\n"
        "    requested = inputs.get('allowed_purposes')\n"
        "    if requested is None:\n"
        "        raise Exception('input allowed_purposes missing; policy requires one of ' + repr(allowed))\n"
        "    if requested not in allowed:\n"
        "        raise Exception('purpose ' + repr(requested) + ' not in allowed set ' + repr(allowed))\n"
        "    return {}\n"
    )
    purposes_tag = "_".join(allowed_list)
    unit, impl = _build_policy(
        f"purposes_{purposes_tag}", source,
        preconditions=[{"var": "allowed_purposes", "op": "in", "value": allowed_list}],
    )
    binding = _binding(f"purposes_{purposes_tag}_binding", unit.content_id())
    return unit, impl, binding
