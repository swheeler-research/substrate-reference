"""Tests for the author-side composition helper."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.composition import ComposedRefs, compose_refs
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    MutabilityDiscipline,
    StateUnit,
    TransferDiscipline,
)


def _cred(name, parent_cids=()):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("invoke:test",),
        credential_refs=tuple(parent_cids),
    )


def _func(name, *, credential_refs=(), functional_refs=(), state_refs=()):
    return FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": name},
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )


def test_compose_refs_includes_the_constituents_themselves():
    code = CodeArchive()
    creds = CredentialsArchive()

    a = _func("a")
    b = _func("b")
    code.put(a)
    code.put(b)

    refs = compose_refs(a, b, code_archive=code, credentials_archive=creds)
    assert isinstance(refs, ComposedRefs)
    assert set(refs.functional) == {a.content_id(), b.content_id()}
    assert refs.state == ()
    assert refs.credential == ()


def test_compose_refs_walks_transitively():
    """A constituent's own refs are pulled into the closure."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_cred("parliament"))
    dwp_cid = creds.put(_cred("dwp", parent_cids=(parliament_cid,)))
    policy_cid = creds.put(_cred("retention_policy"))

    leaf = _func("leaf", credential_refs=(dwp_cid, policy_cid))
    code.put(leaf)

    refs = compose_refs(leaf, code_archive=code, credentials_archive=creds)
    # The leaf itself, and every credential reachable through it
    # transitively (dwp -> parliament; plus retention_policy).
    assert set(refs.functional) == {leaf.content_id()}
    assert set(refs.credential) == {dwp_cid, parliament_cid, policy_cid}


def test_compose_refs_classifies_by_primitive_type():
    code = CodeArchive()
    creds = CredentialsArchive()

    func = _func("f")
    state = StateUnit(name="s", mutability=MutabilityDiscipline.IMMUTABLE, content={"x": 1})
    cred = _cred("c")
    code.put(func)
    code.put(state)
    creds.put(cred)

    refs = compose_refs(func, state, cred, code_archive=code, credentials_archive=creds)
    assert func.content_id() in refs.functional
    assert state.content_id() in refs.state
    assert cred.content_id() in refs.credential


def test_compose_refs_is_order_invariant_in_output():
    """Two orderings of the same constituents produce equal ComposedRefs."""
    code = CodeArchive()
    creds = CredentialsArchive()
    parliament_cid = creds.put(_cred("parliament"))

    a = _func("a", credential_refs=(parliament_cid,))
    b = _func("b", credential_refs=(parliament_cid,))
    code.put(a)
    code.put(b)

    refs_ab = compose_refs(a, b, code_archive=code, credentials_archive=creds)
    refs_ba = compose_refs(b, a, code_archive=code, credentials_archive=creds)
    assert refs_ab == refs_ba


def test_compose_refs_output_satisfies_wilful_inclusion():
    """A composing unit built from compose_refs's output compiles cleanly."""
    from substrate.compile import compile_unit
    from substrate.implementations import python_implementation

    code = CodeArchive()
    creds = CredentialsArchive()
    parliament_cid = creds.put(_cred("parliament"))

    # A policy is a functional unit. The binding credential references it
    # via policy_refs. compose_refs should pull both into the closure.
    policy_impl = python_implementation(
        "def implementation(inputs, runtime, cred_id):\n    return {}\n"
    )
    policy_impl_cid = code.put(policy_impl)
    policy_fn = FunctionalUnit(
        name="retention_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "retention"},
        implementation_ref=policy_impl_cid,
    )
    code.put(policy_fn)
    binding = CredentialUnit(
        name="retention_binding",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=(policy_fn.content_id(),),
    )
    binding_cid = creds.put(binding)

    leaf_a = _func("leaf_a", credential_refs=(parliament_cid, binding_cid))
    leaf_b = _func("leaf_b", credential_refs=(parliament_cid, binding_cid))
    code.put(leaf_a)
    code.put(leaf_b)

    refs = compose_refs(leaf_a, leaf_b, code_archive=code, credentials_archive=creds)
    composing = _func(
        "composing",
        functional_refs=refs.functional,
        credential_refs=refs.credential,
        state_refs=refs.state,
    )
    code.put(composing)

    # If compose_refs did its job, this compiles without WilfulInclusionFailure
    # AND the policy functional unit is in the compiled form's policies tuple.
    cf = compile_unit(composing, code, creds)
    assert policy_fn.content_id() in cf.policies


def test_compose_refs_rejects_non_primitive_input():
    code = CodeArchive()
    creds = CredentialsArchive()
    with pytest.raises(TypeError):
        compose_refs("not a unit", code_archive=code, credentials_archive=creds)
