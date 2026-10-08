"""Tests for sub-invocation references on the parent act.

PP 3.6.1 requires that a parent act record its sub-invocations as
references in its ledger entry, so that lineage queries reconstruct the
call tree from the ledger alone. Each sub-invocation is already its own
act; what these tests pin is the parent-to-child edge.

The central case is the one `probe_lineage.py` exhibited against the
unmodified implementation: two independent parents over a shared
sub-unit. Commit order does not recover the relation, because a parent's
act is appended after its implementation returns and nothing marks where
a subtree begins or ends. With the references recorded, the attribution
is read off the ledger directly.

The references are direct, not transitive: the tree is recovered by
following the edges at each level. They are recorded on refusals as well
as permits, because an act that refused after invoking sub-units has a
lineage worth keeping.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.federation import CooperativeSubstrate, LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import Act, FederatedLedger
from substrate.operator import Operator, Substrate
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

def _cred(name, parent_cids=()):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("invoke:test",),
        credential_refs=tuple(parent_cids),
    )


def _unit(
    code: CodeArchive,
    name: str,
    source: str,
    *,
    credential_refs=(),
    functional_refs=(),
    state_refs=(),
):
    """Put a Python-implemented functional unit in the code archive."""
    impl_state = python_implementation(source)
    impl_cid = code.put(impl_state)
    unit = FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )
    code.put(unit)
    return unit


_LEAF = "def implementation(inputs, runtime, invoking_credential_id):\n    return {'leaf': True}\n"


def _stack():
    """A code archive, credentials archive, ledger, runtime and credential."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    parliament_cid = creds.put(_cred("parliament"))
    cw_cid = creds.put(_cred("caseworker", parent_cids=(parliament_cid,)))
    return code, creds, led, Runtime(code, creds, led), parliament_cid, cw_cid


def _by_id(ledger) -> dict:
    """Index the ledger by act content_id, as a lineage query would."""
    return {act.content_id(): act for act in ledger}


# =============================================================================
# The field exists
# =============================================================================

def test_act_carries_sub_invocation_references():
    """Act has a field capable of holding sub-invocation references, and it
    defaults to empty so an act that invoked nothing says so."""
    act = Act(
        previous_act_id="",
        compiled_form_id="cf_" + "0" * 60,
        invoking_credential_id="cred_" + "0" * 58,
        inputs={},
        verdict="permit",
        output_or_rationale={},
    )
    assert act.sub_invocations == ()


def test_sub_invocation_references_are_covered_by_the_content_hash():
    """The references are inside the hash chain, so they are as
    tamper-evident as the verdict. An act claiming different children is a
    different act."""
    def _act(subs):
        return Act(
            previous_act_id="",
            compiled_form_id="cf_" + "0" * 60,
            invoking_credential_id="cred_" + "0" * 58,
            inputs={},
            verdict="permit",
            output_or_rationale={},
            sub_invocations=subs,
        )

    assert _act(()).content_id() != _act(("a" * 64,)).content_id()
    assert _act(("a" * 64,)).content_id() != _act(("b" * 64,)).content_id()


# =============================================================================
# Direct sub-invocations on a permit
# =============================================================================

def test_parent_records_the_acts_its_implementation_caused():
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    leaf = _unit(code, "leaf", _LEAF, credential_refs=(parl_cid,))
    parent = _unit(
        code, "parent",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    a = runtime.invoke({leaf.content_id()!r}, {{'n': 1}}, invoking_credential_id)\n"
        f"    b = runtime.invoke({leaf.content_id()!r}, {{'n': 2}}, invoking_credential_id)\n"
        "    return {'acts': [a.act_id, b.act_id]}\n",
        credential_refs=(parl_cid,),
        functional_refs=(leaf.content_id(),),
        state_refs=(leaf.implementation_ref,),
    )
    runtime.register_compiled(compile_unit(leaf, code, creds))
    runtime.register_compiled(compile_unit(parent, code, creds))

    result = runtime.invoke(parent.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)

    acts = list(led)
    parent_act = acts[-1]
    # Recorded in invocation order, which is the order the implementation
    # made the calls, not an incidental sort.
    assert parent_act.sub_invocations == tuple(result.output["acts"])
    assert parent_act.sub_invocations == (acts[0].content_id(), acts[1].content_id())


def test_a_leaf_records_no_sub_invocations():
    code, creds, led, runtime, parl_cid, cw_cid = _stack()
    leaf = _unit(code, "leaf", _LEAF, credential_refs=(parl_cid,))
    runtime.register_compiled(compile_unit(leaf, code, creds))

    assert isinstance(runtime.invoke(leaf.content_id(), {}, cw_cid), Permit)
    assert list(led)[0].sub_invocations == ()


def test_sub_invocations_are_direct_not_the_transitive_closure():
    """Grandparent -> parent -> leaf. The grandparent records the parent
    only; the leaf is reached by following the parent's own edge. Recording
    the closure would duplicate what the chain already holds."""
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    leaf = _unit(code, "leaf", _LEAF, credential_refs=(parl_cid,))
    parent = _unit(
        code, "parent",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    s = runtime.invoke({leaf.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    return {'sub': s.act_id}\n",
        credential_refs=(parl_cid,),
        functional_refs=(leaf.content_id(),),
        state_refs=(leaf.implementation_ref,),
    )
    grandparent = _unit(
        code, "grandparent",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    s = runtime.invoke({parent.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    return {'sub': s.act_id}\n",
        credential_refs=(parl_cid,),
        functional_refs=(parent.content_id(), leaf.content_id()),
        state_refs=(parent.implementation_ref, leaf.implementation_ref),
    )
    for u in (leaf, parent, grandparent):
        runtime.register_compiled(compile_unit(u, code, creds))

    result = runtime.invoke(grandparent.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)

    leaf_act, parent_act, grandparent_act = list(led)
    assert leaf_act.sub_invocations == ()
    assert parent_act.sub_invocations == (leaf_act.content_id(),)
    assert grandparent_act.sub_invocations == (parent_act.content_id(),)
    # The whole tree, three levels deep, from the ledger alone.
    index = _by_id(led)
    reached = index[grandparent_act.sub_invocations[0]]
    assert index[reached.sub_invocations[0]] is leaf_act


# =============================================================================
# The probe: two parents over a shared sub-unit
# =============================================================================

def test_two_parents_over_a_shared_sub_unit_are_attributable():
    """The case `probe_lineage.py` exhibited.

    Two independent parents invoke the same leaf with the same inputs, so
    the two leaf acts are indistinguishable in every field the unmodified
    implementation recorded: same compiled form, same credential, same
    inputs, same verdict. Only their position in the chain differed, and
    position does not encode parenthood. The references settle it.
    """
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    leaf = _unit(code, "leaf", _LEAF, credential_refs=(parl_cid,))
    shared_call = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    s = runtime.invoke({leaf.content_id()!r}, {{'tag': 'same'}}, invoking_credential_id)\n"
        "    return {'sub': s.act_id}\n"
    )
    first = _unit(
        code, "first_parent", shared_call,
        credential_refs=(parl_cid,),
        functional_refs=(leaf.content_id(),),
        state_refs=(leaf.implementation_ref,),
    )
    # A distinct unit with the same body, so the two parents are different
    # units invoking one shared leaf under identical inputs.
    second = _unit(
        code, "second_parent", shared_call + "# second\n",
        credential_refs=(parl_cid,),
        functional_refs=(leaf.content_id(),),
        state_refs=(leaf.implementation_ref,),
    )
    for u in (leaf, first, second):
        runtime.register_compiled(compile_unit(u, code, creds))

    assert isinstance(runtime.invoke(first.content_id(), {}, cw_cid), Permit)
    assert isinstance(runtime.invoke(second.content_id(), {}, cw_cid), Permit)

    leaf_a, parent_a, leaf_b, parent_b = list(led)
    # The two leaf acts agree on everything the entry recorded before the
    # references existed.
    assert leaf_a.compiled_form_id == leaf_b.compiled_form_id
    assert leaf_a.inputs == leaf_b.inputs
    assert leaf_a.verdict == leaf_b.verdict
    assert leaf_a.output_or_rationale == leaf_b.output_or_rationale

    # And are nonetheless attributed to the right parent, from the ledger
    # alone, with no out-of-band knowledge of the call structure.
    assert parent_a.sub_invocations == (leaf_a.content_id(),)
    assert parent_b.sub_invocations == (leaf_b.content_id(),)
    assert leaf_b.content_id() not in parent_a.sub_invocations
    assert leaf_a.content_id() not in parent_b.sub_invocations


# =============================================================================
# Refusals
# =============================================================================

def test_an_act_that_refuses_after_invoking_sub_units_records_them():
    """The implementation invokes a sub-unit and then raises. The refusal
    is the act whose lineage the architecture most wants auditable, so the
    references are recorded on it."""
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    leaf = _unit(code, "leaf", _LEAF, credential_refs=(parl_cid,))
    parent = _unit(
        code, "parent_that_refuses",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    runtime.invoke({leaf.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    raise Exception('refusing after the sub-invocation')\n",
        credential_refs=(parl_cid,),
        functional_refs=(leaf.content_id(),),
        state_refs=(leaf.implementation_ref,),
    )
    runtime.register_compiled(compile_unit(leaf, code, creds))
    runtime.register_compiled(compile_unit(parent, code, creds))

    result = runtime.invoke(parent.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)

    leaf_act, parent_act = list(led)
    assert parent_act.verdict == "refuse"
    assert parent_act.sub_invocations == (leaf_act.content_id(),)


def test_a_refused_sub_invocation_is_observable_in_the_parents_entry():
    """PP 3.6.1: refusal of a sub-invocation is observable in the parent's
    entry. The parent here catches the refusal and permits; the edge is
    what connects the two entries."""
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    refuser = _unit(
        code, "refuser",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    raise Exception('leaf refuses')\n",
        credential_refs=(parl_cid,),
    )
    parent = _unit(
        code, "tolerant_parent",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    s = runtime.invoke({refuser.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    return {'handled': True}\n",
        credential_refs=(parl_cid,),
        functional_refs=(refuser.content_id(),),
        state_refs=(refuser.implementation_ref,),
    )
    runtime.register_compiled(compile_unit(refuser, code, creds))
    runtime.register_compiled(compile_unit(parent, code, creds))

    result = runtime.invoke(parent.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)

    sub_act, parent_act = list(led)
    assert parent_act.verdict == "permit"
    assert parent_act.sub_invocations == (sub_act.content_id(),)
    # An auditor holding the parent's entry reaches the refusal from it.
    assert _by_id(led)[parent_act.sub_invocations[0]].verdict == "refuse"


def test_an_act_refusing_before_its_implementation_records_nothing():
    """A refusal at the credential check performed no sub-invocations, and
    says so rather than inheriting its caller's."""
    code, creds, led, runtime, parl_cid, cw_cid = _stack()
    leaf = _unit(code, "leaf", _LEAF, credential_refs=(parl_cid,))
    runtime.register_compiled(compile_unit(leaf, code, creds))
    runtime.credentials.revoke(cw_cid)

    assert isinstance(runtime.invoke(leaf.content_id(), {}, cw_cid), Refuse)
    assert list(led)[0].sub_invocations == ()


# =============================================================================
# Policy acts
# =============================================================================

def test_policy_acts_are_not_recorded_as_the_governed_acts_sub_invocations():
    """A policy invocation is a sub-invocation of the runtime's own policy
    evaluation, not of the governed unit's implementation. It is reached
    through `policy_refusals` on a refusal; recording it as a
    sub-invocation would misdescribe the call tree, and would attribute it
    to the governed act's caller rather than to the governed act."""
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    policy = _unit(
        code, "permitting_policy",
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {}\n",
    )
    binding_cid = creds.put(CredentialUnit(
        name="policy_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=(policy.content_id(),),
    ))
    governed = _unit(
        code, "governed", _LEAF,
        credential_refs=(parl_cid, binding_cid),
        functional_refs=(policy.content_id(),),
        state_refs=(policy.implementation_ref,),
    )
    runtime.register_compiled(compile_unit(policy, code, creds))
    runtime.register_compiled(compile_unit(governed, code, creds))

    assert isinstance(runtime.invoke(governed.content_id(), {}, cw_cid), Permit)

    policy_act, governed_act = list(led)
    assert governed_act.sub_invocations == ()
    assert policy_act.sub_invocations == ()


def test_a_policy_act_does_not_steal_its_callers_frame():
    """Regression guard. The governed act's policy runs while the calling
    implementation's frame is on top of the stack; the policy act must not
    read that frame, and must not be deposited in it."""
    code, creds, led, runtime, parl_cid, cw_cid = _stack()

    policy = _unit(
        code, "permitting_policy",
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {}\n",
    )
    binding_cid = creds.put(CredentialUnit(
        name="policy_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=(policy.content_id(),),
    ))
    governed = _unit(
        code, "governed", _LEAF,
        credential_refs=(parl_cid, binding_cid),
        functional_refs=(policy.content_id(),),
        state_refs=(policy.implementation_ref,),
    )
    caller = _unit(
        code, "caller",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    s = runtime.invoke({governed.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    return {'sub': s.act_id}\n",
        credential_refs=(parl_cid, binding_cid),
        functional_refs=(governed.content_id(), policy.content_id()),
        state_refs=(governed.implementation_ref, policy.implementation_ref),
    )
    for u in (policy, governed, caller):
        runtime.register_compiled(compile_unit(u, code, creds))

    result = runtime.invoke(caller.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)

    acts = list(led)
    caller_act = acts[-1]
    # The caller's own policy act and the governed unit's policy act are on
    # the ledger; the caller's single edge is to the governed act.
    assert caller_act.sub_invocations == (result.output["sub"],)
    governed_act = _by_id(led)[result.output["sub"]]
    assert governed_act.sub_invocations == ()


# =============================================================================
# Cross-operator
# =============================================================================

def _two_operator_federation():
    """Two operators sharing archives, each with its own ledger and runtime."""
    code = CodeArchive()
    creds = CredentialsArchive()
    parliament = _cred("parliament")
    parliament_cid = creds.put(parliament)
    a_root_cid = creds.put(_cred("a_root", parent_cids=(parliament_cid,)))
    b_root_cid = creds.put(_cred("b_root", parent_cids=(parliament_cid,)))
    cw_cid = creds.put(_cred("caseworker", parent_cids=(parliament_cid,)))

    def _substrate(name):
        ledger = FederatedLedger()
        return Substrate(
            code=code,
            credentials=creds,
            ledger=ledger,
            custodian=LocalCustodian(name=name),
            runtime=Runtime(code, creds, ledger),
        )

    a = Operator(
        name="A",
        root_credential=creds.get_for_compile(a_root_cid),
        substrate=_substrate("a_custodian"),
    )
    b = Operator(
        name="B",
        root_credential=creds.get_for_compile(b_root_cid),
        substrate=_substrate("b_custodian"),
    )
    coop = CooperativeSubstrate()
    coop.add(a)
    coop.add(b)
    return coop, a, b, code, creds, parliament_cid, cw_cid


def test_cross_operator_sub_invocation_is_recorded_on_the_callers_act():
    """PP 3.6.1 wants the cross-operator sub-invocation joinable across the
    two ledgers. The callee's act lands on operator B's ledger; operator
    A's act references it, so the join is by reference rather than by
    content and timing."""
    coop, a, b, code, creds, parl_cid, cw_cid = _two_operator_federation()

    remote = _unit(
        code, "remote_check",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'checked': True}\n",
        credential_refs=(parl_cid,),
    )
    b.runtime.register_compiled(compile_unit(remote, code, creds, custodian=b.custodian))

    local = _unit(
        code, "local_caller",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    cross = runtime.invoke_in({b.content_id!r}, {remote.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    return {'cross_act': cross.act_id}\n",
        credential_refs=(parl_cid,),
        functional_refs=(remote.content_id(),),
        state_refs=(remote.implementation_ref,),
    )
    a.runtime.register_compiled(compile_unit(local, code, creds, custodian=a.custodian))

    result = a.runtime.invoke(local.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)

    assert len(a.ledger) == 1
    assert len(b.ledger) == 1
    a_act = list(a.ledger)[0]
    b_act = list(b.ledger)[0]
    assert a_act.sub_invocations == (b_act.content_id(),)
    assert result.output["cross_act"] == b_act.content_id()


def test_a_self_targeted_cross_operator_call_records_one_reference():
    """invoke_in resolving to the calling runtime is a local invocation. It
    deposits its reference once, through invoke(), rather than once there
    and once again as a cross-operator call."""
    coop, a, b, code, creds, parl_cid, cw_cid = _two_operator_federation()

    local_leaf = _unit(code, "local_leaf", _LEAF, credential_refs=(parl_cid,))
    a.runtime.register_compiled(compile_unit(local_leaf, code, creds, custodian=a.custodian))

    caller = _unit(
        code, "self_targeting_caller",
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        f"    s = runtime.invoke_in({a.content_id!r}, {local_leaf.content_id()!r}, {{}}, invoking_credential_id)\n"
        "    return {'sub': s.act_id}\n",
        credential_refs=(parl_cid,),
        functional_refs=(local_leaf.content_id(),),
        state_refs=(local_leaf.implementation_ref,),
    )
    a.runtime.register_compiled(compile_unit(caller, code, creds, custodian=a.custodian))

    result = a.runtime.invoke(caller.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)

    leaf_act, caller_act = list(a.ledger)
    assert caller_act.sub_invocations == (leaf_act.content_id(),)
