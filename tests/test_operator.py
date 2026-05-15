"""Tests for the Substrate and Operator structural composition."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.federation import LocalCustodian
from substrate.ledger import FederatedLedger
from substrate.operator import Operator, Substrate, create_operator, create_substrate
from substrate.primitives import CredentialUnit, TransferDiscipline
from substrate.runtime import Runtime


def _root_cred(name="parliament_uk"):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("delegate:any",),
    )


def test_create_substrate_assembles_components():
    sub = create_substrate("dwp_custodian")
    assert isinstance(sub.code, CodeArchive)
    assert isinstance(sub.credentials, CredentialsArchive)
    assert isinstance(sub.ledger, FederatedLedger)
    assert isinstance(sub.custodian, LocalCustodian)
    assert isinstance(sub.runtime, Runtime)


def test_create_operator_attaches_a_substrate():
    op = create_operator("DWP", _root_cred("dwp_root"))
    assert isinstance(op.substrate, Substrate)
    assert op.name == "DWP"


def test_operator_identity_is_root_credential_content_id():
    root = _root_cred("dwp_root")
    op = create_operator("DWP", root)
    assert op.content_id == root.content_id()


def test_two_operators_with_different_roots_have_different_identities():
    op_a = create_operator("DWP", _root_cred("dwp_root"))
    op_b = create_operator("HomeOffice", _root_cred("ho_root"))
    assert op_a.content_id != op_b.content_id


def test_create_operator_puts_root_credential_in_substrate_credentials():
    """An operator must be able to reference its own identity in its substrate's archive."""
    root = _root_cred("dwp_root")
    op = create_operator("DWP", root)
    assert root.content_id() in op.substrate.credentials


def test_operator_convenience_properties_forward_to_substrate():
    """op.code, op.runtime, etc. forward to op.substrate.* for ergonomic access."""
    op = create_operator("DWP", _root_cred("dwp_root"))
    assert op.code is op.substrate.code
    assert op.credentials is op.substrate.credentials
    assert op.ledger is op.substrate.ledger
    assert op.runtime is op.substrate.runtime
    assert op.custodian is op.substrate.custodian


def test_substrate_can_be_constructed_directly_for_shared_archives():
    """For cooperative-substrate setups, build operator substrates manually
    so they can share archives."""
    code = CodeArchive()
    creds = CredentialsArchive()

    def make_sub(custodian_name):
        ledger = FederatedLedger()
        return Substrate(
            code=code, credentials=creds, ledger=ledger,
            custodian=LocalCustodian(name=custodian_name),
            runtime=Runtime(code, creds, ledger),
        )

    root_a = _root_cred("a")
    root_b = _root_cred("b")
    creds.put(root_a)
    creds.put(root_b)
    op_a = Operator(name="A", root_credential=root_a, substrate=make_sub("a_cust"))
    op_b = Operator(name="B", root_credential=root_b, substrate=make_sub("b_cust"))

    # Shared archives.
    assert op_a.code is op_b.code
    assert op_a.credentials is op_b.credentials
    # Separate ledgers.
    assert op_a.ledger is not op_b.ledger
