"""Tests for the content-addressed archives."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive, CredentialRevoked
from substrate.primitives import (
    FunctionalUnit, StateUnit, CredentialUnit,
    ContractPattern, MutabilityDiscipline, TransferDiscipline,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_functional(name="f"):
    return FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
    )


def _make_state(name="s", content=None):
    return StateUnit(
        name=name,
        mutability=MutabilityDiscipline.IMMUTABLE,
        content=content if content is not None else {"value": 1},
    )


def _make_credential(name="c", principal="someone"):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=principal,
        authorities=("invoke:test",),
    )


# =============================================================================
# CodeArchive
# =============================================================================

def test_code_archive_round_trips_a_unit():
    archive = CodeArchive()
    unit = _make_functional()
    cid = archive.put(unit)
    assert archive.get(cid) is unit


def test_code_archive_round_trips_a_state_unit():
    """The archive holds functional and state units alike."""
    archive = CodeArchive()
    unit = _make_state()
    cid = archive.put(unit)
    assert archive.get(cid) is unit


def test_code_archive_put_is_idempotent():
    """Putting the same unit twice returns the same content_id; no duplicate."""
    archive = CodeArchive()
    unit = _make_functional()
    cid1 = archive.put(unit)
    cid2 = archive.put(unit)
    assert cid1 == cid2
    assert len(archive) == 1


def test_code_archive_missing_id_raises_key_error():
    archive = CodeArchive()
    with pytest.raises(KeyError):
        archive.get("0" * 64)


def test_code_archive_contains():
    archive = CodeArchive()
    unit = _make_functional()
    cid = archive.put(unit)
    assert cid in archive
    assert "0" * 64 not in archive


# =============================================================================
# CredentialsArchive
# =============================================================================

def test_credentials_archive_round_trips():
    archive = CredentialsArchive()
    cred = _make_credential()
    cid = archive.put(cred)
    assert archive.get(cid) is cred


def test_credentials_archive_put_is_idempotent():
    archive = CredentialsArchive()
    cred = _make_credential()
    cid1 = archive.put(cred)
    cid2 = archive.put(cred)
    assert cid1 == cid2
    assert len(archive) == 1


def test_credentials_archive_missing_id_raises_key_error():
    archive = CredentialsArchive()
    with pytest.raises(KeyError):
        archive.get("0" * 64)


def test_revoking_a_credential_does_not_change_its_content():
    """Revocation is archive state, not credential state.

    The credential's content_id must remain stable across revocation, so
    historical ledger entries that reference it still resolve.
    """
    archive = CredentialsArchive()
    cred = _make_credential()
    cid_before = archive.put(cred)
    archive.revoke(cid_before)
    # The credential object itself is unchanged, and its content_id is
    # unchanged. We confirm by recomputing the content_id from the same
    # credential object.
    assert cred.content_id() == cid_before


def test_get_on_revoked_credential_raises():
    """The runtime path faults at lookup time, automatically propagating revocation."""
    archive = CredentialsArchive()
    cred = _make_credential()
    cid = archive.put(cred)
    archive.revoke(cid)
    with pytest.raises(CredentialRevoked) as exc_info:
        archive.get(cid)
    # The exception carries the credential so audit code can still inspect it.
    assert exc_info.value.credential is cred


def test_status_reports_active_and_revoked():
    archive = CredentialsArchive()
    cred = _make_credential()
    cid = archive.put(cred)
    assert archive.status(cid) == "active"
    archive.revoke(cid)
    assert archive.status(cid) == "revoked"


def test_revoke_is_idempotent():
    archive = CredentialsArchive()
    cred = _make_credential()
    cid = archive.put(cred)
    archive.revoke(cid)
    archive.revoke(cid)  # second revoke does not raise or change anything
    assert archive.status(cid) == "revoked"


def test_revoke_unknown_credential_raises():
    """Cannot revoke a credential that was never put."""
    archive = CredentialsArchive()
    with pytest.raises(KeyError):
        archive.revoke("0" * 64)
