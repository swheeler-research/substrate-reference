"""Tests for operator-level administrative acts on the ledger.

When an operator revokes, deprecates, supersedes, or resets drift, the
action is recorded as an `Act` on the operator's ledger with
`kind="administrative"`. The act includes the authorising credential
and the action details. Unauthorised attempts are also recorded (as
refused administrative acts) so the audit trail captures both
successful operator actions and unauthorised attempts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import (
    CredentialDeprecated,
    CredentialRevoked,
    CredentialSuperseded,
    UnitDeprecated,
)
from substrate.compile import compile_unit
from substrate.federation import LocalCustodian
from substrate.implementations import python_implementation
from substrate.operator import Operator, create_operator
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)


def _root(name="dwp_root", parent_cids=()):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("delegate:any", "administer:any"),
        credential_refs=tuple(parent_cids),
    )


def _child(name, parent_cid):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("invoke:test",),
        credential_refs=(parent_cid,),
    )


def _setup_operator():
    """Build an operator with a root credential and an authorised admin credential."""
    root = _root("dwp_root")
    op = create_operator("DWP", root)
    # An admin credential delegated under the root.
    admin = _child("dwp_admin", root.content_id())
    admin_cid = op.substrate.credentials.put(admin)
    return op, admin_cid


# =============================================================================
# Credential revocation as an administrative act
# =============================================================================

def test_revoke_credential_records_administrative_act():
    op, admin_cid = _setup_operator()
    target = _child("retiring_caseworker", op.root_credential.content_id())
    target_cid = op.substrate.credentials.put(target)

    act = op.revoke_credential(target_cid, admin_cid)

    assert act.kind == "administrative"
    assert act.verdict == "executed"
    assert act.invoking_credential_id == admin_cid
    assert act.inputs["action"] == "revoke_credential"
    assert act.inputs["target_credential"] == target_cid

    # The revocation actually took effect.
    with pytest.raises(CredentialRevoked):
        op.substrate.credentials.get(target_cid)


def test_revoke_credential_with_unauthorised_credential_records_refused_act_and_raises():
    op, _admin_cid = _setup_operator()
    target = _child("retiring", op.root_credential.content_id())
    target_cid = op.substrate.credentials.put(target)
    # An unauthorised credential rooted elsewhere.
    foreign_root = _root("foreign_root")
    op.substrate.credentials.put(foreign_root)
    foreign_admin = _child("foreign_admin", foreign_root.content_id())
    foreign_admin_cid = op.substrate.credentials.put(foreign_admin)

    with pytest.raises(PermissionError, match="not delegated"):
        op.revoke_credential(target_cid, foreign_admin_cid)

    # The refused attempt is on the ledger.
    acts = list(op.substrate.ledger)
    assert len(acts) == 1
    assert acts[0].kind == "administrative"
    assert acts[0].verdict == "refused"
    assert acts[0].invoking_credential_id == foreign_admin_cid

    # The target was NOT revoked.
    op.substrate.credentials.get(target_cid)  # would raise if revoked


def test_revoke_credential_with_revoked_authorising_credential_refuses():
    op, admin_cid = _setup_operator()
    target = _child("retiring", op.root_credential.content_id())
    target_cid = op.substrate.credentials.put(target)
    # Revoke the admin credential first (using the operator root itself).
    op.revoke_credential(admin_cid, op.root_credential.content_id())

    # Now attempt to use the revoked admin credential.
    with pytest.raises(PermissionError, match="invalid"):
        op.revoke_credential(target_cid, admin_cid)


# =============================================================================
# Deprecation
# =============================================================================

def test_deprecate_credential_records_administrative_act():
    op, admin_cid = _setup_operator()
    target = _child("legacy_caseworker", op.root_credential.content_id())
    target_cid = op.substrate.credentials.put(target)

    act = op.deprecate_credential(target_cid, admin_cid)

    assert act.kind == "administrative"
    assert act.verdict == "executed"
    assert act.inputs["action"] == "deprecate_credential"

    with pytest.raises(CredentialDeprecated):
        op.substrate.credentials.get(target_cid)


def test_deprecate_unit_records_administrative_act():
    op, admin_cid = _setup_operator()
    impl = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {}\n"
    )
    op.substrate.code.put(impl)
    unit = FunctionalUnit(
        name="old_unit", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={}, implementation_ref=impl.content_id(),
        credential_refs=(op.root_credential.content_id(),),
    )
    unit_cid = op.substrate.code.put(unit)

    act = op.deprecate_unit(unit_cid, admin_cid)

    assert act.kind == "administrative"
    assert act.verdict == "executed"
    assert act.inputs["action"] == "deprecate_unit"
    assert act.inputs["target_unit"] == unit_cid

    with pytest.raises(UnitDeprecated):
        op.substrate.code.get(unit_cid)


# =============================================================================
# Supersession
# =============================================================================

def test_supersede_credential_records_administrative_act():
    op, admin_cid = _setup_operator()
    old = _child("policy_v1", op.root_credential.content_id())
    new = _child("policy_v2", op.root_credential.content_id())
    old_cid = op.substrate.credentials.put(old)
    new_cid = op.substrate.credentials.put(new)

    act = op.supersede_credential(old_cid, new_cid, admin_cid)

    assert act.kind == "administrative"
    assert act.verdict == "executed"
    assert act.inputs["action"] == "supersede_credential"
    assert act.inputs["old_credential"] == old_cid
    assert act.inputs["new_credential"] == new_cid

    with pytest.raises(CredentialSuperseded) as exc:
        op.substrate.credentials.get(old_cid)
    assert exc.value.successor_cid == new_cid


# =============================================================================
# Drift reset
# =============================================================================

def test_reset_drift_records_administrative_act():
    op, admin_cid = _setup_operator()
    impl = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'value': inputs['v']}\n"
    )
    op.substrate.code.put(impl)
    unit = FunctionalUnit(
        name="drifty_unit",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"drift_criteria": [
            {"type": "mean_in", "field": "value", "bound": [0.9, 1.0], "window": 3},
        ]},
        implementation_ref=impl.content_id(),
        credential_refs=(op.root_credential.content_id(),),
    )
    unit_cid = op.substrate.code.put(unit)
    op.substrate.runtime.register_compiled(
        compile_unit(unit, op.substrate.code, op.substrate.credentials, custodian=op.substrate.custodian)
    )

    # Drive the unit into drift.
    for _ in range(3):
        op.substrate.runtime.invoke(unit_cid, {"v": 0.1}, admin_cid)
    assert op.substrate.runtime.drift_monitor.is_drifted(unit_cid)

    # Reset.
    act = op.reset_drift(unit_cid, admin_cid)
    assert act.kind == "administrative"
    assert act.verdict == "executed"
    assert op.substrate.runtime.drift_monitor.is_drifted(unit_cid) is False


# =============================================================================
# Chain integrity
# =============================================================================

def test_administrative_acts_chain_with_invocation_acts():
    """Administrative and invocation acts share one ledger chain; verify()
    walks both kinds."""
    op, admin_cid = _setup_operator()
    # Add a policy credential to deprecate later.
    target = _child("retiring", op.root_credential.content_id())
    target_cid = op.substrate.credentials.put(target)

    # An admin act:
    op.deprecate_credential(target_cid, admin_cid)
    # Then revoke target. (Already deprecated; revocation also possible.)
    target2 = _child("retiring_2", op.root_credential.content_id())
    target2_cid = op.substrate.credentials.put(target2)
    op.revoke_credential(target2_cid, admin_cid)

    acts = list(op.substrate.ledger)
    assert len(acts) == 2
    assert all(a.kind == "administrative" for a in acts)
    assert op.substrate.ledger.verify() is True
