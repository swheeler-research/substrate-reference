"""Tests for delegation enforcement and cross-operator composition.

Delegation: the invoking credential's ancestry must intersect the unit's
authority chain. Empty authority chain is permissionless (existing
single-operator policy units still work). Declared authority opts the
unit into delegation enforcement.

Cross-operator: two operators each running a substrate, federated. An
impl on one operator invokes a unit on the other via runtime.invoke_in().
Each invocation lands on its own operator's ledger; together they form
the joint audit trail.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.federation import CooperativeSubstrate, LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.operator import Operator, Substrate, create_operator
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


def _impl(source):
    return python_implementation(source)


_ECHO = "def implementation(inputs, runtime, invoking_credential_id):\n    return {'echo': inputs}\n"


# =============================================================================
# Delegation enforcement (single-operator)
# =============================================================================

def test_credential_delegated_under_unit_authority_permits():
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_cred("parliament"))
    caseworker_cid = creds.put(_cred("caseworker", parent_cids=(parliament_cid,)))

    impl_state = _impl(_ECHO)
    impl_cid = code.put(impl_state)
    unit = FunctionalUnit(
        name="welfare_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        credential_refs=(parliament_cid,),
    )
    code.put(unit)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(unit, code, creds))

    result = runtime.invoke(unit.content_id(), {"x": 1}, caseworker_cid)
    assert isinstance(result, Permit)


def test_credential_not_delegated_refuses():
    """A credential rooted elsewhere is refused under this unit's authority."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_uk_cid = creds.put(_cred("parliament_uk"))
    parliament_eu_cid = creds.put(_cred("parliament_eu"))
    # A credential rooted in a different parliament.
    foreign_cid = creds.put(_cred("foreign_caseworker", parent_cids=(parliament_eu_cid,)))

    impl_cid = code.put(_impl(_ECHO))
    unit = FunctionalUnit(
        name="uk_welfare_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        credential_refs=(parliament_uk_cid,),
    )
    code.put(unit)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(unit, code, creds))

    result = runtime.invoke(unit.content_id(), {}, foreign_cid)
    assert isinstance(result, Refuse)
    assert "not delegated" in result.rationale


def test_empty_authority_chain_is_permissionless():
    """A unit with no credential_refs admits any caller. This is the
    permissionless mode used by utility / library units."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    rando_cid = creds.put(_cred("random_caller"))

    impl_cid = code.put(_impl(_ECHO))
    unit = FunctionalUnit(
        name="permissionless_utility",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        # No credential_refs: empty authority chain.
    )
    code.put(unit)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(unit, code, creds))

    result = runtime.invoke(unit.content_id(), {}, rando_cid)
    assert isinstance(result, Permit)


def test_delegation_walks_transitive_ancestry():
    """A credential 3 hops up the chain is still delegated."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_cred("parliament"))
    dwp_cid = creds.put(_cred("dwp", parent_cids=(parliament_cid,)))
    region_cid = creds.put(_cred("dwp_region_north", parent_cids=(dwp_cid,)))
    caseworker_cid = creds.put(_cred("caseworker", parent_cids=(region_cid,)))

    impl_cid = code.put(_impl(_ECHO))
    unit = FunctionalUnit(
        name="welfare_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=impl_cid,
        credential_refs=(parliament_cid,),  # only references parliament
    )
    code.put(unit)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(unit, code, creds))

    result = runtime.invoke(unit.content_id(), {}, caseworker_cid)
    assert isinstance(result, Permit)


# =============================================================================
# Cross-operator invocation
# =============================================================================

def _build_dwp_homeoffice_federation():
    """Two operators (DWP and Home Office) federated under shared archives
    (for Phase 2-deepened simplicity — see specification_gaps.md). Each
    has its own ledger and custodian; both share archives so credentials
    propagate trivially. Returns (federation, dwp, home_office, parliament_cid,
    dwp_caseworker_cid)."""

    # Shared archives: both operators see the same code and credentials.
    code = CodeArchive()
    creds = CredentialsArchive()

    # The constitutional source (UK Parliament).
    parliament = _cred("parliament_uk")
    parliament_cid = creds.put(parliament)

    # Two institutional roots, both deriving from Parliament.
    dwp_root = _cred("dwp_root", parent_cids=(parliament_cid,))
    ho_root = _cred("home_office_root", parent_cids=(parliament_cid,))
    creds.put(dwp_root)
    creds.put(ho_root)

    # A caseworker credential rooted in DWP.
    caseworker = _cred("dwp_caseworker_1", parent_cids=(dwp_root.content_id(),))
    caseworker_cid = creds.put(caseworker)

    # Build each operator's substrate manually so they share archives but
    # have their own ledgers / custodians / runtimes.
    def _make_substrate(custodian_name: str) -> Substrate:
        ledger = FederatedLedger()
        return Substrate(
            code=code,
            credentials=creds,
            ledger=ledger,
            custodian=LocalCustodian(name=custodian_name),
            runtime=Runtime(code, creds, ledger),
        )

    dwp = Operator(name="DWP", root_credential=dwp_root, substrate=_make_substrate("dwp_custodian"))
    ho = Operator(name="HomeOffice", root_credential=ho_root, substrate=_make_substrate("ho_custodian"))

    coop = CooperativeSubstrate()
    coop.add(dwp)
    coop.add(ho)

    return coop, dwp, ho, parliament_cid, caseworker_cid


def test_operator_runtime_gets_cooperative_substrate_back_reference():
    coop, dwp, ho, parl_cid, cw_cid = _build_dwp_homeoffice_federation()
    assert dwp.runtime.cooperative_substrate is coop
    assert ho.runtime.cooperative_substrate is coop


def test_cross_operator_invocation_lands_on_target_operator_ledger():
    """When DWP's impl invokes a Home Office unit via invoke_in(), the
    invocation is processed by Home Office's runtime and recorded on
    Home Office's ledger. DWP's ledger does not get the entry directly;
    DWP's own act (if any) can record the cross-operator act_id in its
    output."""
    coop, dwp, ho, parl_cid, cw_cid = _build_dwp_homeoffice_federation()

    # A Home Office unit that any UK credential is delegated under.
    ho_unit_impl_cid = dwp.code.put(_impl(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'verified': True, 'by': 'home_office'}\n"
    ))
    ho_unit = FunctionalUnit(
        name="right_to_reside_check",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=ho_unit_impl_cid,
        credential_refs=(parl_cid,),  # delegated under UK Parliament
    )
    dwp.code.put(ho_unit)
    ho.runtime.register_compiled(compile_unit(ho_unit, ho.code, ho.credentials))

    # Now invoke from DWP's runtime into Home Office.
    result = dwp.runtime.invoke_in(
        ho.content_id, ho_unit.content_id(), {"applicant": "x"}, cw_cid,
    )
    assert isinstance(result, Permit)
    assert result.output == {"verified": True, "by": "home_office"}

    # The act landed on Home Office's ledger, not DWP's.
    assert len(ho.ledger) == 1
    assert len(dwp.ledger) == 0
    assert list(ho.ledger)[0].verdict == "permit"


def test_cross_operator_invocation_from_inside_python_impl():
    """The complete pattern: DWP runs an impl that invokes a Home Office
    unit via runtime.invoke_in(). Both operators record their acts on
    their respective ledgers; the DWP act references the HO act_id in
    its output."""
    coop, dwp, ho, parl_cid, cw_cid = _build_dwp_homeoffice_federation()

    # Home Office unit.
    ho_impl_cid = ho.code.put(_impl(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'right_to_reside': True}\n"
    ))
    ho_unit = FunctionalUnit(
        name="right_to_reside_check",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=ho_impl_cid,
        credential_refs=(parl_cid,),
    )
    ho.code.put(ho_unit)
    ho.runtime.register_compiled(compile_unit(ho_unit, ho.code, ho.credentials))

    # DWP unit whose impl invokes the HO unit.
    dwp_source = f"""
def implementation(inputs, runtime, invoking_credential_id):
    cross = runtime.invoke_in({ho.content_id!r}, {ho_unit.content_id()!r}, inputs, invoking_credential_id)
    return {{'eligibility': True, 'rtr_check': cross.output, 'rtr_act': cross.act_id}}
"""
    dwp_impl_cid = dwp.code.put(_impl(dwp_source))
    dwp_unit = FunctionalUnit(
        name="advance_payment",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=dwp_impl_cid,
        credential_refs=(parl_cid,),
        # The HO unit and its implementation are reachable transitively;
        # wilful inclusion requires us to list them.
        functional_refs=(ho_unit.content_id(),),
        state_refs=(ho_impl_cid,),
    )
    dwp.code.put(dwp_unit)
    dwp.runtime.register_compiled(compile_unit(dwp_unit, dwp.code, dwp.credentials))

    result = dwp.runtime.invoke(dwp_unit.content_id(), {"applicant": "x"}, cw_cid)
    assert isinstance(result, Permit)
    assert result.output["rtr_check"] == {"right_to_reside": True}

    # Both ledgers have entries. HO got 1 (the right_to_reside invocation);
    # DWP got 1 (the advance_payment invocation).
    assert len(ho.ledger) == 1
    assert len(dwp.ledger) == 1
    # The DWP act records the HO act's id, making the cross-operator
    # audit trail traceable.
    assert result.output["rtr_act"] == list(ho.ledger)[0].content_id()


def test_cross_operator_runtime_with_no_cooperative_substrate_raises():
    """A standalone runtime cannot do cross-operator invocations."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    rt = Runtime(code, creds, led)
    import pytest
    with pytest.raises(RuntimeError, match="not part of a cooperative substrate"):
        rt.invoke_in("any_op_cid", "any_unit_cid", {}, "any_cred_cid")
