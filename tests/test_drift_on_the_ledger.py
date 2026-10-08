"""Drift detection is an act on the ledger, in the shape of a revocation, and
survives a restart; clearing it is an operator act.

The paper's remaining claim says a behavioural trigger and an authority
trigger are one kind of event on one surface, with the refusal recorded as
the same kind of act as the revocation. Before this, drift was a per-process
flag that no ledger entry recorded and a restart silently cleared.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.clock import FixedClock
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


def _cred(name, parent_cids=()):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=("invoke:any",), credential_refs=tuple(parent_cids),
    )


_ECHO = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"score": inputs["score"]}
"""


def _wire():
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("operator", parent_cids=(root_cid,)))
    unit = FunctionalUnit(
        name="scorer",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"drift_criteria": [{"type": "mean_in", "field": "score", "bound": [0.0, 1.0], "window": 3}]},
        implementation_ref=code.put(python_implementation(_ECHO)),
        credential_refs=(root_cid,),
    )
    unit_cid = code.put(unit)
    compiled = compile_unit(unit, code, creds, custodian=LocalCustodian("test"))
    runtime = Runtime(code, creds, led, clock=FixedClock())
    runtime.register_compiled(compiled)
    return code, creds, led, compiled, runtime, unit_cid, op_cid


def _drive_to_drift(runtime, unit_cid, op_cid):
    for v in (5.0, 5.0, 5.0):
        assert isinstance(runtime.invoke(unit_cid, {"score": v}, op_cid), Permit)
    assert runtime.drift_monitor.is_drifted(unit_cid)


def test_drift_detection_is_an_administrative_act_on_the_ledger():
    code, creds, led, compiled, runtime, unit_cid, op_cid = _wire()
    _drive_to_drift(runtime, unit_cid, op_cid)
    admin = [a for a in led if a.kind == "administrative"]
    assert len(admin) == 1
    act = admin[0]
    assert act.inputs == {"action": "drift_detected", "target_unit": unit_cid}
    assert act.verdict == "executed"
    assert act.output_or_rationale["criterion"]["type"] == "mean_in"
    assert act.governance_tick > 0
    # The next invocation refuses, and the refusal is an invocation act
    # after the drift act on the same chain.
    r = runtime.invoke(unit_cid, {"score": 0.5}, op_cid)
    assert isinstance(r, Refuse) and "drifted" in r.rationale
    assert led.verify()


def test_a_runtime_rebuilt_over_the_ledger_is_still_drifted():
    code, creds, led, compiled, runtime, unit_cid, op_cid = _wire()
    _drive_to_drift(runtime, unit_cid, op_cid)
    restarted = Runtime(code, creds, led, clock=FixedClock())
    restarted.register_compiled(compiled)
    assert restarted.drift_monitor.is_drifted(unit_cid)
    assert isinstance(restarted.invoke(unit_cid, {"score": 0.5}, op_cid), Refuse)


def test_a_recorded_reset_clears_drift_on_rebuild_but_a_restart_alone_does_not():
    from substrate.ledger import Act
    code, creds, led, compiled, runtime, unit_cid, op_cid = _wire()
    _drive_to_drift(runtime, unit_cid, op_cid)
    # A restart alone: still drifted (previous test). An operator's recorded
    # reset act, as operator.reset_drift writes it, clears it on rebuild.
    led.append(Act(
        kind="administrative", previous_act_id=led.latest(), compiled_form_id="",
        invoking_credential_id=op_cid,
        inputs={"action": "reset_drift", "target_unit": unit_cid},
        verdict="executed", output_or_rationale={"reset": unit_cid},
    ))
    restarted = Runtime(code, creds, led, clock=FixedClock())
    restarted.register_compiled(compiled)
    assert not restarted.drift_monitor.is_drifted(unit_cid)
    assert isinstance(restarted.invoke(unit_cid, {"score": 0.5}, op_cid), Permit)
