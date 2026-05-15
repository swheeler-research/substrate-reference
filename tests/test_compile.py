"""Tests for the compile-at-commit pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import (
    CompiledForm,
    CompilationRefused,
    UnresolvedReference,
    WilfulInclusionFailure,
    compile_unit,
)
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

def _constitutional_source(name="parliament"):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=f"{name}_principal",
        authorities=("delegate:any",),
    )


def _delegated(name, principal, parent_cid, authorities=("invoke:test",)):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=principal,
        authorities=authorities,
        credential_refs=(parent_cid,),
    )


def _functional(name, credential_refs=(), functional_refs=(), state_refs=()):
    return FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )


def _policy_fn(name: str, source: str, code: CodeArchive) -> FunctionalUnit:
    """Build a policy as a functional unit, putting impl + unit in the archive."""
    impl_state = python_implementation(source)
    impl_cid = code.put(impl_state)
    fn = FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": name},
        implementation_ref=impl_cid,
    )
    code.put(fn)
    return fn


def _binding_credential(name: str, policy_fn_cids) -> CredentialUnit:
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=tuple(policy_fn_cids),
    )


# =============================================================================
# Basic compilation
# =============================================================================

def test_simple_unit_compiles():
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_constitutional_source())
    caseworker_cid = creds.put(_delegated("caseworker", "person_1", parliament_cid))

    unit = _functional("simple", credential_refs=(parliament_cid, caseworker_cid))
    code.put(unit)

    cf = compile_unit(unit, code, creds)
    assert isinstance(cf, CompiledForm)
    assert cf.source_unit == unit.content_id()
    assert parliament_cid in cf.authority_chain
    assert caseworker_cid in cf.authority_chain
    # No policies in scope: empty tuple.
    assert cf.policies == ()
    assert cf.fused_form is None
    assert cf.cached_environment is None
    assert cf.dispatch_table is None
    assert cf.witness


# =============================================================================
# Policy collection
# =============================================================================

def test_compilation_collects_policy_refs_from_credentials():
    """A policy functional unit referenced via a credential's policy_refs
    ends up in compiled_form.policies."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_constitutional_source())
    policy_fn = _policy_fn(
        "retention_30",
        "def implementation(inputs, runtime, cred_id):\n    return {}\n",
        code,
    )
    binding = _binding_credential("retention_binding", (policy_fn.content_id(),))
    binding_cid = creds.put(binding)

    unit = _functional(
        "u",
        credential_refs=(parliament_cid, binding_cid),
        # Wilful inclusion: the policy functional unit at the top level
        # (functional_refs), plus its implementation state unit
        # (state_refs), because the source unit transitively reaches both.
        functional_refs=(policy_fn.content_id(),),
        state_refs=(policy_fn.implementation_ref,),
    )
    code.put(unit)

    cf = compile_unit(unit, code, creds)
    assert cf.policies == (policy_fn.content_id(),)


def test_compilation_collects_policies_from_transitively_reached_credentials():
    """Policies brought into binding via a sub-unit's credentials are also
    collected; this is the equivalent of the old strictest-binding-wins
    aggregation, now expressed as 'all policies in scope are evaluated at
    runtime'."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_constitutional_source())

    policy_a = _policy_fn(
        "policy_a",
        "def implementation(inputs, runtime, cred_id):\n    return {}\n",
        code,
    )
    policy_b = _policy_fn(
        "policy_b",
        "def implementation(inputs, runtime, cred_id):\n    return {}\n",
        code,
    )
    binding_a = _binding_credential("binding_a", (policy_a.content_id(),))
    binding_b = _binding_credential("binding_b", (policy_b.content_id(),))
    binding_a_cid = creds.put(binding_a)
    binding_b_cid = creds.put(binding_b)

    leaf = _functional(
        "leaf",
        credential_refs=(parliament_cid, binding_a_cid),
        functional_refs=(policy_a.content_id(),),
        state_refs=(policy_a.implementation_ref,),
    )
    code.put(leaf)

    composing = _functional(
        "composing",
        credential_refs=(parliament_cid, binding_a_cid, binding_b_cid),
        functional_refs=(leaf.content_id(), policy_a.content_id(), policy_b.content_id()),
        state_refs=(policy_a.implementation_ref, policy_b.implementation_ref),
    )
    code.put(composing)

    cf = compile_unit(composing, code, creds)
    assert set(cf.policies) == {policy_a.content_id(), policy_b.content_id()}


# =============================================================================
# Wilful inclusion
# =============================================================================

def test_wilful_inclusion_failure_when_composing_unit_omits_transitive_dep():
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(_constitutional_source())
    leaf = _functional("leaf", credential_refs=(parliament_cid,))
    code.put(leaf)

    # Composing unit references leaf but not parliament. Refuses.
    composing = _functional("composing", functional_refs=(leaf.content_id(),))
    code.put(composing)

    with pytest.raises(WilfulInclusionFailure) as exc:
        compile_unit(composing, code, creds)
    assert parliament_cid in str(exc.value)


def test_unresolved_reference_refuses():
    code = CodeArchive()
    creds = CredentialsArchive()
    bogus_cid = "0" * 64
    unit = _functional("u", credential_refs=(bogus_cid,))
    code.put(unit)

    with pytest.raises(UnresolvedReference):
        compile_unit(unit, code, creds)


# =============================================================================
# Determinism: stable compiled form
# =============================================================================

def test_compiled_form_is_stable_across_recompilation():
    """Compiling the same unit graph twice with the same custodian yields
    identical compiled forms. With Ed25519, the custodian's keypair is
    part of the witness; different custodians produce different
    witnesses (and thus different compiled forms). For stability we
    therefore share the custodian across calls — which is the realistic
    case anyway, since an operator's substrate has one custodian that
    witnesses every unit it admits."""
    from substrate.federation import LocalCustodian
    code = CodeArchive()
    creds = CredentialsArchive()
    parliament_cid = creds.put(_constitutional_source())
    policy_fn = _policy_fn(
        "noop",
        "def implementation(inputs, runtime, cred_id):\n    return {}\n",
        code,
    )
    binding_cid = creds.put(_binding_credential("noop_binding", (policy_fn.content_id(),)))

    unit = _functional(
        "u",
        credential_refs=(parliament_cid, binding_cid),
        functional_refs=(policy_fn.content_id(),),
        state_refs=(policy_fn.implementation_ref,),
    )
    code.put(unit)

    custodian = LocalCustodian("test_custodian")
    cf1 = compile_unit(unit, code, creds, custodian=custodian)
    cf2 = compile_unit(unit, code, creds, custodian=custodian)
    assert cf1.content_id() == cf2.content_id()
    assert cf1 == cf2


def test_compiled_form_has_content_id():
    code = CodeArchive()
    creds = CredentialsArchive()
    parliament_cid = creds.put(_constitutional_source())
    unit = _functional("u", credential_refs=(parliament_cid,))
    code.put(unit)

    cf = compile_unit(unit, code, creds)
    cid = cf.content_id()
    assert isinstance(cid, str)
    assert len(cid) == 64
    code.put(cf)
    assert code.get(cid) == cf


# =============================================================================
# Revocation does not influence compilation
# =============================================================================

def test_compilation_succeeds_even_if_a_referenced_credential_is_revoked():
    code = CodeArchive()
    creds = CredentialsArchive()
    parliament_cid = creds.put(_constitutional_source())
    creds.revoke(parliament_cid)

    unit = _functional("u", credential_refs=(parliament_cid,))
    code.put(unit)

    cf = compile_unit(unit, code, creds)
    assert cf.source_unit == unit.content_id()
    assert parliament_cid in cf.authority_chain
