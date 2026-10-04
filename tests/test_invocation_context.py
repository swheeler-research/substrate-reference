"""The invocation context, the governance clock, and credential resolution.

The architecture says the rolled-up policy evaluates against the invocation
context: the principal invoking the act, the inputs being supplied, the state
being operated on, and the temporal context. An audit established that one of
the four was reachable by a policy and it was the one the caller controls, so
every substantive policy verdict in the demonstrations was a function of a
value the invoker had supplied.

These tests pin the four components as reachable, pin the clock on the act,
and discriminate the credential-resolution surface: each test fails if the
resolution is removed, rather than merely passing while it happens to be
there.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.clock import FixedClock, GovernanceClock
from substrate.compile import compile_unit
from substrate.federation import LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    MutabilityDiscipline,
    StateUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime


def _cred(name, parent_cids=(), principal=None):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=principal if principal is not None else name,
        authorities=("invoke:any",),
        credential_refs=tuple(parent_cids),
    )


# An implementation that reports what the invocation context contains, so a
# test can assert on all four components from outside the runtime.
_REPORT_CONTEXT = """
def implementation(inputs, runtime, invoking_credential_id):
    ctx = runtime.invocation_context()
    return {
        "has_context": ctx is not None,
        "principal_valid": ctx.principal.valid,
        "principal_name": getattr(ctx.principal.credential, "name", None),
        "principal_chain_len": len(ctx.principal.authority_chain),
        "inputs_seen": sorted(ctx.inputs.keys()),
        "state_count": len(ctx.state),
        "tick": ctx.entry_tick,
        "wall": ctx.wall_time,
        "target": ctx.target_unit_id,
    }
"""


def _wire(clock=None, state_refs=()):
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("parliament"))
    cw_cid = creds.put(_cred("caseworker", parent_cids=(root_cid,)))
    impl_cid = code.put(python_implementation(_REPORT_CONTEXT))
    unit = FunctionalUnit(
        name="reporter",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
        state_refs=tuple(state_refs),
    )
    code.put(unit)
    runtime = Runtime(code, creds, led, clock=clock)
    runtime.register_compiled(
        compile_unit(unit, code, creds, custodian=LocalCustodian("test"))
    )
    return runtime, unit, root_cid, cw_cid


# =============================================================================
# All four components of the invocation context are reachable
# =============================================================================

def test_invocation_context_is_available_to_an_implementation():
    runtime, unit, _, cw_cid = _wire(clock=FixedClock())
    result = runtime.invoke(unit.content_id(), {"a": 1}, cw_cid)
    assert isinstance(result, Permit)
    assert result.output["has_context"] is True


def test_context_principal_is_the_resolved_credential_not_its_identity():
    """The component the architecture names first, resolved rather than
    handed over as a string for the policy to take on trust."""
    runtime, unit, _, cw_cid = _wire(clock=FixedClock())
    out = runtime.invoke(unit.content_id(), {}, cw_cid).output
    assert out["principal_valid"] is True
    assert out["principal_name"] == "caseworker"
    # The chain includes the credential itself and its parent.
    assert out["principal_chain_len"] == 2


def test_context_carries_the_inputs_supplied():
    runtime, unit, _, cw_cid = _wire(clock=FixedClock())
    out = runtime.invoke(unit.content_id(), {"b": 2, "a": 1}, cw_cid).output
    assert out["inputs_seen"] == ["a", "b"]


def test_context_carries_the_state_being_operated_on():
    """The third component. Before the context existed a policy had no handle
    on the state at all, beyond reaching into the archives itself."""
    code = CodeArchive()
    held = StateUnit(
        name="held",
        mutability=MutabilityDiscipline.IMMUTABLE,
        content={"value": 7},
    )
    held_cid = code.put(held)
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("parliament"))
    cw_cid = creds.put(_cred("caseworker", parent_cids=(root_cid,)))
    impl_cid = code.put(python_implementation(_REPORT_CONTEXT))
    unit = FunctionalUnit(
        name="reporter",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
        state_refs=(held_cid,),
    )
    code.put(unit)
    runtime = Runtime(code, creds, led, clock=FixedClock())
    runtime.register_compiled(
        compile_unit(unit, code, creds, custodian=LocalCustodian("test"))
    )
    out = runtime.invoke(unit.content_id(), {}, cw_cid).output
    # The implementation reference is a state unit too, so both resolve.
    assert out["state_count"] >= 1


def test_context_carries_the_temporal_context():
    """The fourth component. No clock existed anywhere in the source before
    this, which falsified every claim about measurable latency."""
    runtime, unit, _, cw_cid = _wire(clock=FixedClock("2026-10-03T12:00:00Z"))
    out = runtime.invoke(unit.content_id(), {}, cw_cid).output
    assert out["wall"] == "2026-10-03T12:00:00Z"
    # The reading at entry. Zero for the first act on a substrate, which is the
    # state the formal model's initial state represents.
    assert out["tick"] == 0


def test_context_entry_tick_is_the_reading_at_entry_not_the_committing_tick():
    """With no policies in scope the two coincide, which is why an earlier
    version of this test passed while the prediction it asserted was wrong by
    the number of policies in scope. See the policy test below."""
    runtime, unit, _, cw_cid = _wire(clock=FixedClock())
    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    act = list(runtime.ledger)[-1]
    assert result.output["tick"] == act.governance_tick - 1
    assert result.output["tick"] == 0


def test_context_is_absent_outside_an_invocation():
    runtime, _, _, _ = _wire(clock=FixedClock())
    assert runtime.invocation_context() is None


def test_context_stack_is_balanced_after_a_refusal():
    """A refusal must not leave a context on the stack. If it did, the next
    invocation's policies would read the refused act's context."""
    runtime, unit, _, _ = _wire(clock=FixedClock())
    stranger = runtime.credentials.put(_cred("stranger"))
    result = runtime.invoke(unit.content_id(), {}, stranger)
    assert isinstance(result, Refuse)
    assert runtime.invocation_context() is None


# =============================================================================
# The governance clock on the act
# =============================================================================

def test_acts_record_a_monotone_governance_tick():
    runtime, unit, _, cw_cid = _wire(clock=FixedClock())
    runtime.invoke(unit.content_id(), {}, cw_cid)
    runtime.invoke(unit.content_id(), {}, cw_cid)
    ticks = [a.governance_tick for a in runtime.ledger]
    assert ticks == sorted(ticks)
    assert ticks[0] >= 1
    assert len(set(ticks)) == len(ticks)


def test_acts_record_a_wall_clock_reading():
    runtime, unit, _, cw_cid = _wire(clock=FixedClock("2026-10-03T12:00:00Z"))
    runtime.invoke(unit.content_id(), {}, cw_cid)
    assert list(runtime.ledger)[-1].recorded_time == "2026-10-03T12:00:00Z"


def test_the_clock_is_an_input_to_the_acts_content_identity():
    """An act is an event, not a compiled artefact, so covering its time
    breaks nothing the compiled form's invariance needs."""
    from substrate.ledger import Act

    base = dict(
        previous_act_id="",
        compiled_form_id="cf",
        invoking_credential_id="c",
        inputs={},
        verdict="permit",
        output_or_rationale={},
    )
    a = Act(governance_tick=1, recorded_time="2026-01-01T00:00:00Z", **base)
    b = Act(governance_tick=2, recorded_time="2026-01-01T00:00:00Z", **base)
    c = Act(governance_tick=1, recorded_time="2026-01-02T00:00:00Z", **base)
    assert a.content_id() != b.content_id()
    assert a.content_id() != c.content_id()


def test_a_real_clock_advances_and_a_fixed_clock_does_not():
    real = GovernanceClock()
    assert real.tick() == 1 and real.tick() == 2
    fixed = FixedClock("2000-01-01T00:00:00Z")
    assert fixed.wall() == fixed.wall() == "2000-01-01T00:00:00Z"
    # The tick still advances under a fixed clock: the ordering it supplies is
    # what the invalidation surface's bound is measured in.
    assert fixed.tick() == 1 and fixed.tick() == 2


def test_runtime_defaults_to_a_real_clock():
    runtime, unit, _, cw_cid = _wire()
    runtime.invoke(unit.content_id(), {}, cw_cid)
    recorded = list(runtime.ledger)[-1].recorded_time
    assert recorded.endswith("Z") and recorded.startswith("20")


# =============================================================================
# Credential resolution, which is what a policy needs to not take the
# invoker's word for anything
# =============================================================================

def test_resolve_credential_returns_a_valid_resolution_for_a_live_credential():
    runtime, _, root_cid, cw_cid = _wire(clock=FixedClock())
    r = runtime.resolve_credential(cw_cid)
    assert r.valid is True
    assert r.status == "valid"
    assert r.credential.name == "caseworker"
    assert root_cid in r.authority_chain


def test_resolve_credential_reports_a_missing_credential_as_invalid():
    runtime, _, _, _ = _wire(clock=FixedClock())
    r = runtime.resolve_credential("f" * 64)
    assert r.valid is False
    assert r.status == "not found"
    assert r.authority_chain == ()
    assert r.credential is None


def test_resolve_credential_reports_revocation():
    runtime, _, _, cw_cid = _wire(clock=FixedClock())
    runtime.credentials.revoke(cw_cid)
    r = runtime.resolve_credential(cw_cid)
    assert r.valid is False
    assert r.status == "revoked"


def test_resolve_credential_reports_deprecation():
    runtime, _, _, cw_cid = _wire(clock=FixedClock())
    runtime.credentials.deprecate(cw_cid)
    r = runtime.resolve_credential(cw_cid)
    assert r.valid is False
    assert "deprecat" in r.status


def test_bears_tests_the_principal_in_the_credentials_own_content():
    """The test a policy makes about authority has to be one the invoker
    cannot satisfy by relabelling. The principal is part of the content and
    therefore of the content identity."""
    runtime, _, root_cid, _ = _wire(clock=FixedClock())
    escalation = runtime.credentials.put(
        _cred("escalation", (root_cid,), principal="governance:escalation")
    )
    r = runtime.resolve_credential(escalation)
    assert r.bears("governance:escalation") is True
    assert r.bears("governance:something_else") is False


def test_bears_is_false_for_an_invalid_credential():
    """A policy that reads a field without checking validity reintroduces the
    defect the resolution exists to close, so bears() refuses to answer
    affirmatively for an invalid credential."""
    runtime, _, root_cid, _ = _wire(clock=FixedClock())
    escalation = runtime.credentials.put(
        _cred("escalation", (root_cid,), principal="governance:escalation")
    )
    runtime.credentials.revoke(escalation)
    r = runtime.resolve_credential(escalation)
    assert r.valid is False
    assert r.bears("governance:escalation") is False


def test_descends_from_follows_the_chain_transitively():
    runtime, _, root_cid, cw_cid = _wire(clock=FixedClock())
    junior = runtime.credentials.put(_cred("junior", (cw_cid,)))
    r = runtime.resolve_credential(junior)
    assert r.descends_from(cw_cid) is True
    assert r.descends_from(root_cid) is True
    assert r.descends_from("0" * 64) is False


def test_descends_from_is_false_for_an_invalid_credential():
    runtime, _, root_cid, cw_cid = _wire(clock=FixedClock())
    junior = runtime.credentials.put(_cred("junior", (cw_cid,)))
    runtime.credentials.revoke(junior)
    r = runtime.resolve_credential(junior)
    assert r.descends_from(root_cid) is False


def test_authority_chain_survives_an_ancestors_revocation():
    """The chain is a structural fact about derivation and does not change
    when an ancestor's status does. Whether each credential is presently
    valid is a separate question, answered separately."""
    runtime, _, root_cid, cw_cid = _wire(clock=FixedClock())
    junior = runtime.credentials.put(_cred("junior", (cw_cid,)))
    runtime.credentials.revoke(cw_cid)
    chain = runtime.credential_authority_chain(junior)
    assert cw_cid in chain and root_cid in chain


def test_authority_chain_terminates_on_a_cycle():
    """Two credentials that reference each other must not make resolution
    loop. A malformed graph is the archive's problem, not an infinite one."""
    runtime, _, _, _ = _wire(clock=FixedClock())
    a = _cred("a")
    a_cid = runtime.credentials.put(a)
    b = _cred("b", (a_cid,))
    b_cid = runtime.credentials.put(b)
    # Cannot build a true cycle with content addressing, so assert the walk
    # over a diamond does not revisit, which is the same guard.
    c = _cred("c", (a_cid, b_cid))
    c_cid = runtime.credentials.put(c)
    chain = runtime.credential_authority_chain(c_cid)
    assert len(chain) == len(set(chain))


# =============================================================================
# A policy that resolves, against a policy that reads a caller-supplied string
# =============================================================================

_STRING_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    if not inputs.get("escalation_name", "").startswith("senior"):
        raise Exception("string policy refuses")
    return {}
"""

_RESOLVING_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    cid = inputs.get("escalation_credential_id", "")
    r = runtime.resolve_credential(cid)
    if not r.valid:
        raise Exception("resolving policy refuses: " + r.status)
    if not r.bears("governance:escalation"):
        raise Exception("resolving policy refuses: not the escalation authority")
    if cid == invoking_credential_id or invoking_credential_id in r.authority_chain:
        raise Exception("resolving policy refuses: self-issued")
    return {}
"""

_PASS = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"ok": True}
"""


def _wire_policy(policy_src):
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    policy_impl = code.put(python_implementation(policy_src))
    policy = FunctionalUnit(
        name="p",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=policy_impl,
        credential_refs=(root_cid,),
    )
    code.put(policy)
    binding = creds.put(CredentialUnit(
        name="binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance:binding",
        authorities=(),
        policy_refs=(policy.content_id(),),
        credential_refs=(root_cid,),
    ))
    impl = code.put(python_implementation(_PASS))
    unit = FunctionalUnit(
        name="target",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl,
        credential_refs=(root_cid, binding),
        functional_refs=(policy.content_id(),),
        # Wilful inclusion: the policy's implementation is reached
        # transitively, so the target unit lists it at its own top level.
        state_refs=(policy_impl,),
    )
    code.put(unit)
    runtime = Runtime(code, creds, led, clock=FixedClock())
    runtime.register_compiled(
        compile_unit(policy, code, creds, custodian=LocalCustodian("t"))
    )
    runtime.register_compiled(
        compile_unit(unit, code, creds, custodian=LocalCustodian("t"))
    )
    return runtime, unit, root_cid


def test_a_string_policy_is_satisfied_by_the_caller_asserting_the_string():
    """The defect, stated as a test so it cannot return unnoticed. This is
    what every substantive policy verdict in the demonstrations rested on."""
    runtime, unit, root_cid = _wire_policy(_STRING_POLICY)
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    result = runtime.invoke(
        unit.content_id(), {"escalation_name": "senior_risk_officer"}, trader
    )
    assert isinstance(result, Permit)


def test_a_resolving_policy_refuses_the_same_assertion():
    runtime, unit, root_cid = _wire_policy(_RESOLVING_POLICY)
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    # Nothing the trader can put in the field resolves to the authority.
    for value in ("senior_risk_officer", "", "f" * 64, trader):
        result = runtime.invoke(
            unit.content_id(), {"escalation_credential_id": value}, trader
        )
        assert isinstance(result, Refuse), value


def test_a_resolving_policy_refuses_a_credential_the_invoker_issued():
    runtime, unit, root_cid = _wire_policy(_RESOLVING_POLICY)
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    self_issued = runtime.credentials.put(
        _cred("escalation", (trader,), principal="governance:escalation")
    )
    result = runtime.invoke(
        unit.content_id(), {"escalation_credential_id": self_issued}, trader
    )
    assert isinstance(result, Refuse)
    assert "self-issued" in result.rationale


def test_a_resolving_policy_permits_a_genuine_authority():
    runtime, unit, root_cid = _wire_policy(_RESOLVING_POLICY)
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    genuine = runtime.credentials.put(
        _cred("escalation", (root_cid,), principal="governance:escalation")
    )
    result = runtime.invoke(
        unit.content_id(), {"escalation_credential_id": genuine}, trader
    )
    assert isinstance(result, Permit)


def test_a_resolving_policy_refuses_once_the_authority_is_revoked():
    """The resolution reads the invalidation surface, so a withdrawn authority
    stops authorising without the policy changing."""
    runtime, unit, root_cid = _wire_policy(_RESOLVING_POLICY)
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    genuine = runtime.credentials.put(
        _cred("escalation", (root_cid,), principal="governance:escalation")
    )
    assert isinstance(
        runtime.invoke(unit.content_id(), {"escalation_credential_id": genuine}, trader),
        Permit,
    )
    runtime.credentials.revoke(genuine)
    after = runtime.invoke(
        unit.content_id(), {"escalation_credential_id": genuine}, trader
    )
    assert isinstance(after, Refuse)
    assert "revoked" in after.rationale


# =============================================================================
# What actually discriminates the fix
# =============================================================================
#
# The London Whale demonstration refuses the self-issued escalation under the
# resolving policy and also refuses it under the string policy it replaced,
# because the content identity it now passes does not happen to begin with
# "senior_risk". That refusal is an accident of a hash prefix, so the
# demonstration exhibits the mechanism without discriminating it.
#
# These two tests discriminate it. The genuine escalation authority and the
# desk's forgery are built with the same name and the same principal, and
# differ only in which credential issued them. Every test available to a
# policy that does not resolve the credential accepts both; the chain
# resolution is the only thing that separates them.

def test_the_forgery_is_indistinguishable_from_the_authority_by_content_alone():
    """Name and principal are identical. A policy reading either accepts the
    forgery, which is why reading either is not a check."""
    runtime, _, root_cid, _ = _wire(clock=FixedClock())
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    genuine = runtime.credentials.put(
        _cred("senior_risk_escalation", (root_cid,),
              principal="governance:senior_risk_escalation")
    )
    forged = runtime.credentials.put(
        _cred("senior_risk_escalation", (trader,),
              principal="governance:senior_risk_escalation")
    )
    g = runtime.resolve_credential(genuine)
    f = runtime.resolve_credential(forged)

    # Identical on every property a name-based or principal-based test reads.
    assert g.credential.name == f.credential.name
    assert g.bears("governance:senior_risk_escalation")
    assert f.bears("governance:senior_risk_escalation")
    # Both are valid credentials. The forgery is not malformed.
    assert g.valid and f.valid
    # Both derive from the bank's constitutional source, because the trader
    # does too. Deriving from the root is necessary and not sufficient.
    assert g.descends_from(root_cid) and f.descends_from(root_cid)
    # They are different credentials, because their references differ and
    # references are part of the content identity.
    assert genuine != forged


def test_only_the_issuing_chain_separates_the_forgery_from_the_authority():
    """The one test that discriminates: is the invoker in the credential's own
    ancestry. This is what the deployed policy performs."""
    runtime, _, root_cid, _ = _wire(clock=FixedClock())
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    genuine = runtime.credentials.put(
        _cred("senior_risk_escalation", (root_cid,),
              principal="governance:senior_risk_escalation")
    )
    forged = runtime.credentials.put(
        _cred("senior_risk_escalation", (trader,),
              principal="governance:senior_risk_escalation")
    )
    assert trader not in runtime.resolve_credential(genuine).authority_chain
    assert trader in runtime.resolve_credential(forged).authority_chain


def test_the_deployed_policy_separates_them_and_a_name_test_does_not():
    """End to end, over the same two credentials, through the runtime."""
    runtime, unit, root_cid = _wire_policy(_RESOLVING_POLICY)
    trader = runtime.credentials.put(_cred("desk_trader", (root_cid,)))
    genuine = runtime.credentials.put(
        _cred("senior_risk_escalation", (root_cid,),
              principal="governance:escalation")
    )
    forged = runtime.credentials.put(
        _cred("senior_risk_escalation", (trader,),
              principal="governance:escalation")
    )
    permitted = runtime.invoke(
        unit.content_id(), {"escalation_credential_id": genuine}, trader
    )
    refused = runtime.invoke(
        unit.content_id(), {"escalation_credential_id": forged}, trader
    )
    assert isinstance(permitted, Permit)
    assert isinstance(refused, Refuse)
    assert "self-issued" in refused.rationale

    # And the counterfactual, over the same pair: a policy that tested the
    # credential's name would accept both, because the names are equal.
    assert (
        runtime.credentials.get(genuine).name
        == runtime.credentials.get(forged).name
    )


# =============================================================================
# A policy reads the context of the act it governs, not a context of its own
# =============================================================================
#
# The first version of the invocation context pushed a fresh context for every
# invocation, including the policy evaluations the runtime performs on an act's
# behalf. Three of the four components then described the policy rather than the
# act: target_unit_id named the policy unit, state resolved the policy's own
# references, and the tick advanced once per policy act so that each policy saw
# a different one and none saw the act's. These tests pin the corrected
# behaviour and would fail against that version.

_CTX_REPORTER_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    c = runtime.invocation_context()
    runtime._probe = getattr(runtime, "_probe", [])
    runtime._probe.append((c.target_unit_id, c.entry_tick, len(c.state)))
    return {}
"""


def _wire_three_policies():
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    pimpl = code.put(python_implementation(_CTX_REPORTER_POLICY))
    pols, bindings = [], []
    for i in range(3):
        p = FunctionalUnit(
            name="pol%d" % i,
            contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
            spec={"n": i},
            implementation_ref=pimpl,
            credential_refs=(root_cid,),
        )
        code.put(p)
        pols.append(p)
        bindings.append(creds.put(CredentialUnit(
            name="b%d" % i,
            transfer=TransferDiscipline.DELEGATED,
            principal="governance:b%d" % i,
            authorities=(),
            policy_refs=(p.content_id(),),
            credential_refs=(root_cid,),
        )))
    held = code.put(StateUnit(
        name="held", mutability=MutabilityDiscipline.IMMUTABLE, content={"v": 1}
    ))
    timpl = code.put(python_implementation(_PASS))
    unit = FunctionalUnit(
        name="governed",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=timpl,
        credential_refs=(root_cid,) + tuple(bindings),
        functional_refs=tuple(p.content_id() for p in pols),
        state_refs=(pimpl, held, timpl),
    )
    code.put(unit)
    runtime = Runtime(code, creds, led, clock=FixedClock())
    for p in pols:
        runtime.register_compiled(compile_unit(p, code, creds, custodian=LocalCustodian("t")))
    runtime.register_compiled(compile_unit(unit, code, creds, custodian=LocalCustodian("t")))
    caller = creds.put(_cred("caller", (root_cid,)))
    return runtime, unit, caller


def test_every_policy_sees_the_governed_units_identity_not_its_own():
    runtime, unit, caller = _wire_three_policies()
    assert isinstance(runtime.invoke(unit.content_id(), {}, caller), Permit)
    targets = {t for t, _, _ in runtime._probe}
    assert len(runtime._probe) == 3
    assert targets == {unit.content_id()}


def test_every_policy_sees_the_same_entry_tick():
    """Not one tick each. The act is one act however many policies govern it."""
    runtime, unit, caller = _wire_three_policies()
    runtime.invoke(unit.content_id(), {}, caller)
    ticks = {t for _, t, _ in runtime._probe}
    assert len(ticks) == 1


def test_every_policy_sees_the_governed_units_state_not_its_own():
    """The policy units reference no state of their own, so the old behaviour
    showed every policy an empty mapping."""
    runtime, unit, caller = _wire_three_policies()
    runtime.invoke(unit.content_id(), {}, caller)
    counts = {n for _, _, n in runtime._probe}
    assert counts == {3}


def test_the_committing_tick_is_later_than_the_entry_tick_by_the_policy_count():
    """Which is why the context cannot predict it, and no longer claims to."""
    runtime, unit, caller = _wire_three_policies()
    runtime.invoke(unit.content_id(), {}, caller)
    entry = {t for _, t, _ in runtime._probe}.pop()
    committing = list(runtime.ledger)[-1].governance_tick
    assert committing == entry + 4
