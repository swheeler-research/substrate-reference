"""A confidence gate with source "act" reads the value from the act that
produced it, not from the invoker's inputs.

The paper's own test for a policy is whether it governs a value the invoker
proposes or establishes a fact; a gate that reads a calibration value from
the inputs takes the invoker's word for a fact. With source "act" the invoker
names the producing act and the gate reads the recorded value and its origin.
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


_PRODUCER = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"confidence": inputs["c"]}
"""
_CONSUMER = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"acted": True}
"""


def _wire(gate):
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("operator", parent_cids=(root_cid,)))
    producer = FunctionalUnit(
        name="producer", contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"confidence": {"produces": True}},
        implementation_ref=code.put(python_implementation(_PRODUCER)), credential_refs=(root_cid,),
    )
    producer_cid = code.put(producer)
    consumer = FunctionalUnit(
        name="consumer", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence_gate": gate},
        implementation_ref=code.put(python_implementation(_CONSUMER)), credential_refs=(root_cid,),
    )
    consumer_cid = code.put(consumer)
    runtime = Runtime(code, creds, led, clock=FixedClock())
    custodian = LocalCustodian("test")
    runtime.register_compiled(compile_unit(producer, code, creds, custodian=custodian))
    runtime.register_compiled(compile_unit(consumer, code, creds, custodian=custodian))
    return runtime, producer_cid, consumer_cid, op_cid


GATE = {"minimum_confidence": 0.9, "applies_to_field": "confidence", "source": "act", "act_field": "producing_act"}


def test_the_gate_reads_the_recorded_value_not_the_inputs():
    runtime, producer_cid, consumer_cid, op_cid = _wire(GATE)
    low = runtime.invoke(producer_cid, {"c": 0.5}, op_cid)
    assert isinstance(low, Permit)
    # The invoker claims 0.99 in the inputs; the act says 0.5.
    r = runtime.invoke(consumer_cid, {"confidence": 0.99, "producing_act": low.act_id}, op_cid)
    assert isinstance(r, Refuse) and "below" in r.rationale or isinstance(r, Refuse)
    high = runtime.invoke(producer_cid, {"c": 0.95}, op_cid)
    r2 = runtime.invoke(consumer_cid, {"confidence": 0.1, "producing_act": high.act_id}, op_cid)
    assert isinstance(r2, Permit)


def test_a_forged_or_missing_act_identity_refuses():
    runtime, producer_cid, consumer_cid, op_cid = _wire(GATE)
    r = runtime.invoke(consumer_cid, {"producing_act": "0" * 64}, op_cid)
    assert isinstance(r, Refuse) and "not on this ledger" in r.rationale
    r2 = runtime.invoke(consumer_cid, {}, op_cid)
    assert isinstance(r2, Refuse) and "must name the producing act" in r2.rationale


def test_require_origin_composed_refuses_an_asserted_value():
    gate = dict(GATE, require_origin="composed")
    runtime, producer_cid, consumer_cid, op_cid = _wire(gate)
    asserted = runtime.invoke(producer_cid, {"c": 0.99}, op_cid)
    assert list(runtime.ledger)[-1].confidence_origin == "asserted"
    r = runtime.invoke(consumer_cid, {"producing_act": asserted.act_id}, op_cid)
    assert isinstance(r, Refuse) and "recorded origin 'asserted'" in r.rationale


def test_an_unknown_gate_source_refuses_compilation():
    with pytest.raises(CompilationRefused):
        _wire(dict(GATE, source="ledger"))


def test_an_implementation_cannot_detach_a_sub_invocation_from_its_lineage():
    """invoke() takes no flags; the detaching flag is internal."""
    runtime, producer_cid, consumer_cid, op_cid = _wire(GATE)
    with pytest.raises(TypeError):
        runtime.invoke(producer_cid, {"c": 0.5}, op_cid, _observe_for_propagation=False)
