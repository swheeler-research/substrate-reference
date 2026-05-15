"""Tests for the remaining invalidation triggers.

Six total per v1.4. This file covers:

- Policy supersession / constitutional source update (one mechanism:
  credential supersession; the archive marks one credential as
  superseded by another)
- Versioning and deprecation (credential and unit deprecation)
- Compilation integrity failure (witness no longer verifies)

Credential revocation is tested in test_archives.py. Drift detection
for behaviour-characterised units is a separate machinery deferred to a
later pass.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import (
    CodeArchive,
    CredentialDeprecated,
    CredentialRevoked,
    CredentialSuperseded,
    CredentialsArchive,
    UnitDeprecated,
)
from substrate.compile import compile_unit
from substrate.federation import LocalCustodian
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

def _cred(name, parent_cids=()):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("invoke:test",),
        credential_refs=tuple(parent_cids),
    )


_ECHO = "def implementation(inputs, runtime, invoking_credential_id):\n    return {'echo': inputs}\n"


def _wire():
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    parl_cid = creds.put(_cred("parliament"))
    cw_cid = creds.put(_cred("caseworker", parent_cids=(parl_cid,)))
    impl_cid = code.put(python_implementation(_ECHO))
    unit = FunctionalUnit(
        name="doer",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={}, implementation_ref=impl_cid,
        credential_refs=(parl_cid,),
    )
    code.put(unit)
    runtime = Runtime(code, creds, led)
    custodian = LocalCustodian("test")
    runtime.register_compiled(compile_unit(unit, code, creds, custodian=custodian))
    return runtime, unit, parl_cid, cw_cid, custodian


# =============================================================================
# Credential supersession
# =============================================================================

def test_credentials_archive_can_record_supersession():
    creds = CredentialsArchive()
    old_cid = creds.put(_cred("old_policy"))
    new_cid = creds.put(_cred("new_policy"))
    creds.supersede(old_cid, new_cid)
    assert creds.status(old_cid) == f"superseded_by:{new_cid}"
    assert creds.status(new_cid) == "active"


def test_get_on_superseded_credential_raises():
    creds = CredentialsArchive()
    old_cid = creds.put(_cred("old_policy"))
    new_cid = creds.put(_cred("new_policy"))
    creds.supersede(old_cid, new_cid)
    with pytest.raises(CredentialSuperseded) as exc:
        creds.get(old_cid)
    assert exc.value.successor_cid == new_cid


def test_supersede_unknown_credential_raises_key_error():
    creds = CredentialsArchive()
    cid = creds.put(_cred("x"))
    with pytest.raises(KeyError):
        creds.supersede(cid, "0" * 64)
    with pytest.raises(KeyError):
        creds.supersede("0" * 64, cid)


def test_supersede_idempotent_with_same_successor():
    creds = CredentialsArchive()
    old_cid = creds.put(_cred("old"))
    new_cid = creds.put(_cred("new"))
    creds.supersede(old_cid, new_cid)
    creds.supersede(old_cid, new_cid)  # idempotent
    assert creds.status(old_cid).startswith("superseded_by:")


def test_supersede_rejects_reassignment():
    creds = CredentialsArchive()
    old_cid = creds.put(_cred("old"))
    new1_cid = creds.put(_cred("new1"))
    new2_cid = creds.put(_cred("new2"))
    creds.supersede(old_cid, new1_cid)
    with pytest.raises(ValueError, match="already superseded"):
        creds.supersede(old_cid, new2_cid)


def test_runtime_refuses_invocation_with_superseded_invoking_credential():
    runtime, unit, parl_cid, cw_cid, _ = _wire()
    new_cw_cid = runtime.credentials.put(_cred("caseworker_new", parent_cids=(parl_cid,)))
    runtime.credentials.supersede(cw_cid, new_cw_cid)

    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "superseded by" in result.rationale
    assert new_cw_cid in result.rationale


def test_runtime_refuses_invocation_when_authority_chain_credential_superseded():
    """Constitutional source supersession is just credential supersession at the root."""
    runtime, unit, parl_cid, cw_cid, _ = _wire()
    new_parl_cid = runtime.credentials.put(_cred("parliament_new"))
    runtime.credentials.supersede(parl_cid, new_parl_cid)

    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "superseded by" in result.rationale


# =============================================================================
# Credential deprecation
# =============================================================================

def test_credentials_archive_can_record_deprecation():
    creds = CredentialsArchive()
    cid = creds.put(_cred("retiring"))
    creds.deprecate(cid)
    assert creds.status(cid) == "deprecated"


def test_get_on_deprecated_credential_raises():
    creds = CredentialsArchive()
    cid = creds.put(_cred("retiring"))
    creds.deprecate(cid)
    with pytest.raises(CredentialDeprecated):
        creds.get(cid)


def test_runtime_refuses_invocation_with_deprecated_invoking_credential():
    runtime, unit, parl_cid, cw_cid, _ = _wire()
    runtime.credentials.deprecate(cw_cid)
    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "deprecated" in result.rationale


# =============================================================================
# Unit deprecation
# =============================================================================

def test_code_archive_can_record_unit_deprecation():
    code = CodeArchive()
    impl = python_implementation(_ECHO)
    cid = code.put(impl)
    code.deprecate(cid)
    assert code.status(cid) == "deprecated"


def test_get_on_deprecated_unit_raises():
    code = CodeArchive()
    impl = python_implementation(_ECHO)
    cid = code.put(impl)
    code.deprecate(cid)
    with pytest.raises(UnitDeprecated):
        code.get(cid)


def test_get_for_audit_returns_deprecated_unit():
    """Audit access bypasses deprecation; the unit's content is still recoverable."""
    code = CodeArchive()
    impl = python_implementation(_ECHO)
    cid = code.put(impl)
    code.deprecate(cid)
    assert code.get_for_audit(cid) is impl


def test_runtime_refuses_invocation_when_source_unit_deprecated():
    runtime, unit, parl_cid, cw_cid, _ = _wire()
    runtime.code.deprecate(unit.content_id())
    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "source unit" in result.rationale
    assert "deprecated" in result.rationale


# =============================================================================
# Compilation integrity failure
# =============================================================================

def test_runtime_refuses_when_compiled_form_witness_does_not_verify():
    """Tampering with a compiled form invalidates its witness; the runtime
    detects this at invocation time and refuses."""
    from dataclasses import replace
    runtime, unit, parl_cid, cw_cid, custodian = _wire()
    # Pull the original compiled form, tamper with one field, and reinstall.
    cf = runtime.compiled_for(unit.content_id())
    tampered = replace(cf, authority_chain=("0" * 64,) + cf.authority_chain)
    runtime._compiled_by_source[unit.content_id()] = runtime.code.put(tampered)

    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "compilation integrity" in result.rationale


def test_runtime_permits_when_witness_verifies():
    """Sanity: the normal case still permits."""
    runtime, unit, parl_cid, cw_cid, _ = _wire()
    result = runtime.invoke(unit.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)
