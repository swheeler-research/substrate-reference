"""A calibration value's origin is on the act, and a contract may forbid
assertion.

The paper names four origins for a calibration value and admits that an
implementation permitted to assert the value that governs whether it is
trusted can inflate it. These tests pin the two remedies the runtime now
provides: the origin is recorded on the act, and a unit whose contract
declares assertion: forbid is refused if its implementation sets the value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.clock import FixedClock
from substrate.compile import CompilationRefused, compile_unit
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


def _cred(name, parent_cids=()):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=("invoke:any",), credential_refs=tuple(parent_cids),
    )


_CHILD = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"value": 1, "confidence": 0.8}
"""
_PARENT_COMPOSES = """
def implementation(inputs, runtime, invoking_credential_id):
    runtime.invoke(inputs["child"], {}, invoking_credential_id)
    runtime.invoke(inputs["child"], {}, invoking_credential_id)
    return {"value": 2}
"""
_PARENT_ASSERTS = """
def implementation(inputs, runtime, invoking_credential_id):
    runtime.invoke(inputs["child"], {}, invoking_credential_id)
    return {"value": 2, "confidence": 0.99}
"""


def _wire(parent_src, parent_conf):
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("operator", parent_cids=(root_cid,)))
    child_impl = code.put(python_implementation(_CHILD))
    child = FunctionalUnit(
        name="child", contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"confidence": {"produces": True}},
        implementation_ref=child_impl, credential_refs=(root_cid,),
    )
    child_cid = code.put(child)
    parent = FunctionalUnit(
        name="parent", contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"confidence": parent_conf},
        implementation_ref=code.put(python_implementation(parent_src)),
        credential_refs=(root_cid,), functional_refs=(child_cid,), state_refs=(child_impl,),
    )
    parent_cid = code.put(parent)
    custodian = LocalCustodian("test")
    runtime = Runtime(code, creds, led, clock=FixedClock())
    runtime.register_compiled(compile_unit(child, code, creds, custodian=custodian))
    runtime.register_compiled(compile_unit(parent, code, creds, custodian=custodian))
    return runtime, parent_cid, child_cid, op_cid


def test_a_composed_value_is_recorded_as_composed():
    runtime, parent_cid, child_cid, op_cid = _wire(_PARENT_COMPOSES, {"produces": True, "propagation": "minimum"})
    r = runtime.invoke(parent_cid, {"child": child_cid}, op_cid)
    assert isinstance(r, Permit) and r.output["confidence"] == 0.8
    assert list(runtime.ledger)[-1].confidence_origin == "composed"


def test_an_asserted_value_is_recorded_as_asserted_and_permitted_by_default():
    runtime, parent_cid, child_cid, op_cid = _wire(_PARENT_ASSERTS, {"produces": True, "propagation": "minimum"})
    r = runtime.invoke(parent_cid, {"child": child_cid}, op_cid)
    assert isinstance(r, Permit) and r.output["confidence"] == 0.99
    assert list(runtime.ledger)[-1].confidence_origin == "asserted"


def test_a_contract_that_forbids_assertion_refuses_an_asserting_implementation():
    runtime, parent_cid, child_cid, op_cid = _wire(_PARENT_ASSERTS, {"produces": True, "propagation": "minimum", "assertion": "forbid"})
    r = runtime.invoke(parent_cid, {"child": child_cid}, op_cid)
    assert isinstance(r, Refuse) and "assertion forbidden" in r.rationale


def test_a_contract_that_forbids_assertion_still_composes():
    runtime, parent_cid, child_cid, op_cid = _wire(_PARENT_COMPOSES, {"produces": True, "propagation": "minimum", "assertion": "forbid"})
    r = runtime.invoke(parent_cid, {"child": child_cid}, op_cid)
    assert isinstance(r, Permit) and r.output["confidence"] == 0.8


def test_an_unknown_assertion_value_refuses_compilation():
    with pytest.raises(CompilationRefused):
        _wire(_PARENT_COMPOSES, {"produces": True, "propagation": "minimum", "assertion": "maybe"})
