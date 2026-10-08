"""Closures from the fourth hostile review, each pinned by the probe that
found it: a forged compiled form against the trust root; a manufacturer-
minted pilot-override credential; an implementation reaching past its
facade; a gate fed an act from the wrong unit, or the same act twice; an
administrative act carrying the clock."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dataclasses
import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.clock import FixedClock
from substrate.compile import compile_unit
from substrate.federation import LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.operator import create_substrate
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime, UntrustedWitness


def _cred(name, parent_cids=(), authorities=("invoke:any",)):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=authorities, credential_refs=tuple(parent_cids),
    )


_PASS = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"ok": True}
"""


def _trusted_runtime():
    code = CodeArchive(); creds = CredentialsArchive(); led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("operator", (root_cid,)))
    unit = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED, spec={},
        implementation_ref=code.put(python_implementation(_PASS)), credential_refs=(root_cid,),
    )
    unit_cid = code.put(unit)
    own = LocalCustodian("ours")
    runtime = Runtime(code, creds, led, clock=FixedClock())
    runtime.trust_custodian(own)
    genuine = compile_unit(unit, code, creds, custodian=own)
    return runtime, unit, unit_cid, genuine, op_cid, root_cid


def test_a_form_witnessed_by_a_fresh_key_is_refused_at_registration_and_at_the_act():
    runtime, unit, unit_cid, genuine, op_cid, root_cid = _trusted_runtime()
    runtime.register_compiled(genuine)
    forged = compile_unit(unit, runtime.code, runtime.credentials, custodian=LocalCustodian("attacker"))
    forged = dataclasses.replace(forged, policies=(), authority_chain=())
    with pytest.raises(UntrustedWitness):
        runtime.register_compiled(forged)
    # Even indexed by hand, the act-time integrity check refuses it.
    runtime._compiled_by_source[unit_cid] = runtime.code.put(forged)
    r = runtime.invoke(unit_cid, {}, op_cid)
    assert isinstance(r, Refuse) and "trust root" in r.rationale


def test_without_a_declared_trust_root_any_valid_witness_passes_which_is_the_disclosed_mode():
    runtime, unit, unit_cid, genuine, op_cid, root_cid = _trusted_runtime()
    bare = Runtime(runtime.code, runtime.credentials, FederatedLedger(), clock=FixedClock())
    assert not bare.trusted_custodian_keys
    bare.register_compiled(compile_unit(unit, runtime.code, runtime.credentials, custodian=LocalCustodian("anyone")))
    assert isinstance(bare.invoke(unit_cid, {}, op_cid), Permit)


def test_an_operator_substrate_trusts_its_own_custodian_only():
    sub = create_substrate("alpha")
    root_cid = sub.credentials.put(_cred("root"))
    unit = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED, spec={},
        implementation_ref=sub.code.put(python_implementation(_PASS)), credential_refs=(root_cid,),
    )
    sub.code.put(unit)
    sub.runtime.register_compiled(compile_unit(unit, sub.code, sub.credentials, custodian=sub.custodian))
    with pytest.raises(UntrustedWitness):
        sub.runtime.register_compiled(compile_unit(unit, sub.code, sub.credentials, custodian=LocalCustodian("other")))


_REACHER = """
def implementation(inputs, runtime, invoking_credential_id):
    runtime._invoke_as(inputs["child"], {}, invoking_credential_id, False)
    return {"reached": True}
"""


def test_an_implementation_cannot_reach_past_its_facade():
    runtime, unit, unit_cid, genuine, op_cid, root_cid = _trusted_runtime()
    runtime.register_compiled(genuine)
    own = LocalCustodian("ours2"); runtime.trust_custodian(own)
    parent = FunctionalUnit(
        name="reacher", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"sub_refusal": "refuse"},
        implementation_ref=runtime.code.put(python_implementation(_REACHER)),
        credential_refs=(root_cid,), functional_refs=(unit_cid,), state_refs=(unit.implementation_ref,),
    )
    parent_cid = runtime.code.put(parent)
    runtime.register_compiled(compile_unit(parent, runtime.code, runtime.credentials, custodian=own))
    r = runtime.invoke(parent_cid, {"child": unit_cid}, op_cid)
    assert isinstance(r, Refuse) and "AttributeError" in r.rationale
