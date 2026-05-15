"""Tests for the runtime."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime


# =============================================================================
# Helpers
# =============================================================================

def _constitutional_source():
    return CredentialUnit(
        name="parliament",
        transfer=TransferDiscipline.DELEGATED,
        principal="parliament",
        authorities=("delegate:any",),
    )


def _caseworker(parent_cid):
    return CredentialUnit(
        name="caseworker",
        transfer=TransferDiscipline.DELEGATED,
        principal="caseworker_1",
        authorities=("invoke:test",),
        credential_refs=(parent_cid,),
    )


def _retention_policy(code: CodeArchive, max_days: int):
    """Build a policy as a functional unit: it raises (refuses) when the
    inputs request a retention period above max_days.

    Returns (binding_credential, policy_functional_unit). The caller is
    responsible for putting the credential in the credentials archive and
    listing both at the source unit's top level."""
    source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    if inputs.get('max_retention_days', 0) > {max_days}:\n"
        f"        raise Exception('max_retention_days exceeds cap of {max_days}')\n"
        "    return {}\n"
    )
    impl_state = python_implementation(source)
    code.put(impl_state)
    fn = FunctionalUnit(
        name=f"retention_<= {max_days}",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": f"retention_max_{max_days}"},
        implementation_ref=impl_state.content_id(),
    )
    code.put(fn)
    binding = CredentialUnit(
        name=f"retention_binding_{max_days}",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=(fn.content_id(),),
    )
    return binding, fn


def _confidence_policy(code: CodeArchive, min_confidence: float):
    source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    cv = inputs.get('min_confidence')\n"
        "    if cv is None:\n"
        f"        raise Exception('input min_confidence missing; floor is {min_confidence}')\n"
        f"    if cv < {min_confidence}:\n"
        f"        raise Exception('min_confidence below floor {min_confidence}')\n"
        "    return {}\n"
    )
    impl_state = python_implementation(source)
    code.put(impl_state)
    fn = FunctionalUnit(
        name=f"confidence_>= {min_confidence}",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": f"confidence_min_{min_confidence}"},
        implementation_ref=impl_state.content_id(),
    )
    code.put(fn)
    binding = CredentialUnit(
        name=f"confidence_binding_{min_confidence}",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=(fn.content_id(),),
    )
    return binding, fn


def _put_unit_with_impl(
    code: CodeArchive,
    name: str,
    source: str,
    *,
    credential_refs=(),
    functional_refs=(),
    state_refs=(),
):
    """Create a FunctionalUnit with a content-addressed Python implementation.

    Puts the implementation state unit and the functional unit into the
    code archive. Returns the FunctionalUnit.
    """
    impl_state = python_implementation(source)
    impl_cid = code.put(impl_state)
    unit = FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
        implementation_ref=impl_cid,
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )
    code.put(unit)
    return unit


_ECHO_IMPL = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"ran": True, "echo": inputs}
"""


def _wire_up_with_policies(build_policies=None):
    """Build an end-to-end stack with optional policy functional units.

    build_policies(code) -> list of (binding_credential, policy_fn) pairs.
    The helper puts each binding credential in the credentials archive,
    lists each policy functional unit in the source unit's functional_refs
    (wilful inclusion), and compiles every unit the runtime may invoke.

    Returns (runtime, source_cid, cw_cid, ledger).
    """
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_constitutional_source())
    cw_cid = creds.put(_caseworker(parliament_cid))

    pairs = build_policies(code) if build_policies else []
    policy_binding_cids = [creds.put(b) for b, _ in pairs]
    policy_fn_cids = [p.content_id() for _, p in pairs]
    policy_impl_cids = [p.implementation_ref for _, p in pairs]

    unit = _put_unit_with_impl(
        code, "doer", _ECHO_IMPL,
        credential_refs=(parliament_cid, cw_cid, *policy_binding_cids),
        functional_refs=tuple(policy_fn_cids),
        state_refs=tuple(policy_impl_cids),
    )

    runtime = Runtime(code, creds, led)
    for u in (unit, *(p for _, p in pairs)):
        runtime.register_compiled(compile_unit(u, code, creds))

    return runtime, unit.content_id(), cw_cid, led


def _wire_up():
    """Convenience: a stack with no policies."""
    return _wire_up_with_policies()


# =============================================================================
# Permits
# =============================================================================

def test_permitted_invocation_returns_permit_and_records_ledger_entry():
    runtime, src_cid, cw_cid, led = _wire_up()
    result = runtime.invoke(src_cid, inputs={"hello": "world"}, invoking_credential_id=cw_cid)
    assert isinstance(result, Permit)
    assert result.output == {"ran": True, "echo": {"hello": "world"}}
    assert len(led) == 1
    assert list(led)[0].verdict == "permit"
    assert list(led)[0].content_id() == result.act_id


# =============================================================================
# Refusals
# =============================================================================

def test_revoked_invoking_credential_refuses():
    runtime, src_cid, cw_cid, led = _wire_up()
    runtime.credentials.revoke(cw_cid)
    result = runtime.invoke(src_cid, inputs={}, invoking_credential_id=cw_cid)
    assert isinstance(result, Refuse)
    assert "revoked" in result.rationale
    assert len(led) == 1
    assert list(led)[0].verdict == "refuse"


def test_unknown_invoking_credential_refuses():
    runtime, src_cid, cw_cid, led = _wire_up()
    bogus = "0" * 64
    result = runtime.invoke(src_cid, inputs={}, invoking_credential_id=bogus)
    assert isinstance(result, Refuse)
    assert "not found" in result.rationale


def test_revoked_credential_in_authority_chain_refuses():
    runtime, src_cid, cw_cid, led = _wire_up()
    parliament_cid = None
    for cid in runtime.compiled_for(src_cid).authority_chain:
        cred = runtime.credentials.get_for_compile(cid)
        if not cred.credential_refs:
            parliament_cid = cid
            break
    assert parliament_cid is not None
    runtime.credentials.revoke(parliament_cid)

    result = runtime.invoke(src_cid, inputs={}, invoking_credential_id=cw_cid)
    assert isinstance(result, Refuse)
    assert parliament_cid in result.rationale


def test_policy_constraint_violation_refuses():
    """A policy functional unit that raises (refuses) causes the parent to refuse."""
    runtime, src_cid, cw_cid, led = _wire_up_with_policies(
        lambda code: [_retention_policy(code, max_days=30)]
    )
    result = runtime.invoke(
        src_cid,
        inputs={"max_retention_days": 60},
        invoking_credential_id=cw_cid,
    )
    assert isinstance(result, Refuse)
    # The parent's rationale names the refusing policy and includes the
    # policy's own rationale.
    assert "cap of 30" in result.rationale
    assert "policy" in result.rationale


def test_missing_required_context_value_refuses():
    runtime, src_cid, cw_cid, led = _wire_up_with_policies(
        lambda code: [_confidence_policy(code, min_confidence=0.95)]
    )
    result = runtime.invoke(src_cid, inputs={}, invoking_credential_id=cw_cid)
    assert isinstance(result, Refuse)
    assert "min_confidence" in result.rationale


def test_strictest_binding_wins_emerges_from_refuse_wins():
    """Three retention policies (30/60/90 day caps) compose as: the strictest
    one is the first to refuse anything above its cap. The behaviour is
    identical to the old dict-based rollup but the mechanism is just
    refuse-wins across policy invocations."""
    runtime, src_cid, cw_cid, led = _wire_up_with_policies(
        lambda code: [
            _retention_policy(code, max_days=30),
            _retention_policy(code, max_days=60),
            _retention_policy(code, max_days=90),
        ]
    )
    # Inputs at 30 days: all three policies permit.
    result_at_cap = runtime.invoke(
        src_cid, inputs={"max_retention_days": 30}, invoking_credential_id=cw_cid,
    )
    assert isinstance(result_at_cap, Permit)

    # Inputs at 31 days: the 30-day policy refuses; the parent refuses
    # with its rationale (the strictest binding cap wins).
    result_over = runtime.invoke(
        src_cid, inputs={"max_retention_days": 31}, invoking_credential_id=cw_cid,
    )
    assert isinstance(result_over, Refuse)
    assert "cap of 30" in result_over.rationale


# =============================================================================
# Implementation as content-addressed artefact
# =============================================================================

def test_unit_without_implementation_ref_refuses():
    """A specification-only unit cannot be executed; invoking it refuses."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_constitutional_source())
    cw_cid = creds.put(_caseworker(parliament_cid))

    # Construct a functional unit with no implementation_ref.
    spec_only = FunctionalUnit(
        name="spec_only",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
        credential_refs=(parliament_cid, cw_cid),
    )
    code.put(spec_only)
    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(spec_only, code, creds))

    result = runtime.invoke(spec_only.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "no implementation_ref" in result.rationale


def test_substituting_implementation_produces_a_different_unit():
    """Changing the implementation changes its content_id, which changes the
    unit's content_id. This is the Horizon guarantee: behaviour cannot drift
    without producing a structurally different unit."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_constitutional_source())
    cw_cid = creds.put(_caseworker(parliament_cid))

    impl_a = python_implementation(
        "def implementation(inputs, runtime, cred_id):\n    return {'answer': 42}\n"
    )
    impl_b = python_implementation(
        "def implementation(inputs, runtime, cred_id):\n    return {'answer': 99}\n"
    )
    assert impl_a.content_id() != impl_b.content_id()

    code.put(impl_a)
    code.put(impl_b)

    unit_a = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"x": "y"}, implementation_ref=impl_a.content_id(),
        credential_refs=(parliament_cid,),
    )
    unit_b = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"x": "y"}, implementation_ref=impl_b.content_id(),
        credential_refs=(parliament_cid,),
    )
    # Same name, same spec, same credentials, different implementation:
    # different unit. The substrate cannot mistake one for the other.
    assert unit_a.content_id() != unit_b.content_id()


def test_wilful_inclusion_pulls_in_implementation_ref():
    """A unit's implementation_ref counts as a top-level reference for the
    wilful-inclusion check. A composing unit that references a unit with
    an implementation must include that implementation in its own state_refs."""
    from substrate.compile import compile_unit, WilfulInclusionFailure
    import pytest

    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_constitutional_source())

    leaf = _put_unit_with_impl(
        code, "leaf",
        "def implementation(inputs, runtime, cred_id):\n    return {}\n",
        credential_refs=(parliament_cid,),
    )

    # A composing unit references the leaf but not the leaf's implementation.
    # Compilation should refuse because the implementation is transitively
    # reachable from the leaf but not listed at the composing unit's top level.
    composing = FunctionalUnit(
        name="composing",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        functional_refs=(leaf.content_id(),),
        credential_refs=(parliament_cid,),
    )
    code.put(composing)

    with pytest.raises(WilfulInclusionFailure) as exc:
        compile_unit(composing, code, creds)
    assert leaf.implementation_ref in str(exc.value)


# =============================================================================
# Runtime sub-unit composition
# =============================================================================

def test_implementation_can_invoke_a_sub_unit_through_the_runtime():
    """Runtime composition: a unit's implementation invokes another unit."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_constitutional_source())
    cw_cid = creds.put(_caseworker(parliament_cid))

    adder = _put_unit_with_impl(
        code, "adder",
        "def implementation(inputs, runtime, cred_id):\n"
        "    return {'sum': inputs['x'] + inputs['y']}\n",
        credential_refs=(parliament_cid, cw_cid),
    )
    parent_source = f"""
def implementation(inputs, runtime, invoking_credential_id):
    sub = runtime.invoke({adder.content_id()!r}, {{'x': 2, 'y': 3}}, invoking_credential_id)
    return {{'sub_output': sub.output, 'sub_act': sub.act_id}}
"""
    parent_impl = python_implementation(parent_source)
    parent_impl_cid = code.put(parent_impl)
    parent = FunctionalUnit(
        name="parent",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
        implementation_ref=parent_impl_cid,
        functional_refs=(adder.content_id(),),
        state_refs=(adder.implementation_ref,),
        credential_refs=(parliament_cid, cw_cid),
    )
    code.put(parent)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(adder, code, creds))
    runtime.register_compiled(compile_unit(parent, code, creds))

    result = runtime.invoke(parent.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)
    assert result.output["sub_output"] == {"sum": 5}

    # Two acts: sub-invocation permit, then parent permit.
    assert len(led) == 2
    acts = list(led)
    assert acts[0].verdict == "permit"
    assert acts[1].verdict == "permit"
    assert led.verify() is True
    assert result.output["sub_act"] == acts[0].content_id()


# =============================================================================
# Ledger invariants
# =============================================================================

def test_every_invocation_records_a_ledger_entry():
    runtime, src_cid, cw_cid, led = _wire_up_with_policies(
        lambda code: [_retention_policy(code, max_days=30)]
    )

    # First call: at-cap permit. The policy is invoked (its own ledger
    # entry) and then the source unit is invoked (another entry). Two
    # acts per top-level permit when there is one policy.
    runtime.invoke(src_cid, inputs={"max_retention_days": 30}, invoking_credential_id=cw_cid)
    # Second call: over-cap. The policy is invoked and refuses (one
    # entry); the parent then refuses (one entry). Two acts.
    runtime.invoke(src_cid, inputs={"max_retention_days": 60}, invoking_credential_id=cw_cid)
    # Third call: revoked credential. The parent refuses immediately on
    # credential check before any policy is invoked. One act.
    runtime.credentials.revoke(cw_cid)
    runtime.invoke(src_cid, inputs={"max_retention_days": 30}, invoking_credential_id=cw_cid)

    # 2 + 2 + 1 = 5 acts total.
    assert len(led) == 5
    verdicts = [a.verdict for a in led]
    assert verdicts == ["permit", "permit", "refuse", "refuse", "refuse"]
    assert led.verify() is True
