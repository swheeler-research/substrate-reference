"""Tests for drift detection of behaviour-characterised units.

A behaviour-characterised unit declares `drift_criteria` in its spec.
The runtime maintains a DriftMonitor that tracks recent outputs; when a
criterion is violated, the unit is marked drifted and subsequent
invocations refuse with a clear rationale naming the criterion and
evidence.

Drift is the sixth and final invalidation trigger from v1.4 §2.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.drift import DriftMonitor
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
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("invoke:test",),
        credential_refs=tuple(parent_cids),
    )


# =============================================================================
# DriftMonitor unit tests
# =============================================================================

def test_drift_monitor_empty_starts_undrifted():
    m = DriftMonitor()
    assert m.is_drifted("any") is False
    assert m.drift_reason("any") is None


def test_drift_monitor_no_criteria_never_drifts():
    m = DriftMonitor()
    for _ in range(100):
        m.observe("u", {"x": 1.0}, drift_criteria=[])
    assert m.is_drifted("u") is False


def test_drift_monitor_mean_in_undersampled_does_not_drift():
    """Below window size, the criterion is not enforced."""
    m = DriftMonitor()
    criterion = {"type": "mean_in", "field": "x", "bound": [0.9, 1.0], "window": 5}
    # Only 3 observations; below window of 5.
    for _ in range(3):
        m.observe("u", {"x": 0.0}, drift_criteria=[criterion])
    assert m.is_drifted("u") is False


def test_drift_monitor_mean_in_violation_marks_drifted():
    m = DriftMonitor()
    criterion = {"type": "mean_in", "field": "confidence", "bound": [0.9, 1.0], "window": 5}
    # 5 observations all at 0.5; mean is 0.5, below 0.9.
    for _ in range(5):
        m.observe("u", {"confidence": 0.5}, drift_criteria=[criterion])
    assert m.is_drifted("u") is True
    criterion_out, evidence = m.drift_reason("u")
    assert criterion_out is criterion
    assert evidence["observed_mean"] == 0.5
    assert evidence["bound"] == [0.9, 1.0]


def test_drift_monitor_mean_in_within_bound_does_not_drift():
    m = DriftMonitor()
    criterion = {"type": "mean_in", "field": "confidence", "bound": [0.9, 1.0], "window": 5}
    for _ in range(10):
        m.observe("u", {"confidence": 0.95}, drift_criteria=[criterion])
    assert m.is_drifted("u") is False


def test_drift_monitor_rate_in_violation_marks_drifted():
    m = DriftMonitor()
    criterion = {"type": "rate_in", "field": "verdict", "value": "refused", "bound": [0.0, 0.2], "window": 10}
    # 8 of 10 are "refused"; rate is 0.8, above 0.2.
    for i in range(10):
        verdict = "refused" if i < 8 else "permitted"
        m.observe("u", {"verdict": verdict}, drift_criteria=[criterion])
    assert m.is_drifted("u") is True


def test_drift_monitor_is_sticky():
    """Once drifted, subsequent observations do not clear the drift flag."""
    m = DriftMonitor()
    criterion = {"type": "mean_in", "field": "x", "bound": [0.9, 1.0], "window": 3}
    for _ in range(3):
        m.observe("u", {"x": 0.0}, drift_criteria=[criterion])
    assert m.is_drifted("u")
    # Observe with values that would otherwise satisfy the criterion.
    m.observe("u", {"x": 1.0}, drift_criteria=[criterion])
    assert m.is_drifted("u")  # still drifted


def test_drift_monitor_reset_clears_state():
    m = DriftMonitor()
    criterion = {"type": "mean_in", "field": "x", "bound": [0.9, 1.0], "window": 3}
    for _ in range(3):
        m.observe("u", {"x": 0.0}, drift_criteria=[criterion])
    assert m.is_drifted("u")
    m.reset("u")
    assert m.is_drifted("u") is False


def test_drift_monitor_unknown_criterion_type_is_not_enforced():
    """A criterion with an unrecognised type is silently accepted (so
    units can declare experimental criteria without breaking)."""
    m = DriftMonitor()
    criterion = {"type": "experimental_ks_test", "field": "x", "window": 3}
    for _ in range(10):
        m.observe("u", {"x": 0.0}, drift_criteria=[criterion])
    assert m.is_drifted("u") is False


# =============================================================================
# Runtime integration: drift detected through invocations
# =============================================================================

def _make_runtime_with_drift_unit(impl_source: str, drift_criteria: list):
    """Build a runtime and a behaviour-characterised unit that declares
    the given drift_criteria. Returns (runtime, unit, caseworker_cid)."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    parl_cid = creds.put(_cred("parliament"))
    cw_cid = creds.put(_cred("caseworker", parent_cids=(parl_cid,)))
    impl_cid = code.put(python_implementation(impl_source))
    unit = FunctionalUnit(
        name="behaviour_unit",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"drift_criteria": drift_criteria},
        implementation_ref=impl_cid,
        credential_refs=(parl_cid,),
    )
    code.put(unit)
    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(unit, code, creds, custodian=LocalCustodian("t")))
    return runtime, unit, cw_cid


def test_runtime_permits_invocations_until_drift_detected_then_refuses():
    """An implementation whose output drifts below the calibration floor
    is initially permitted; once the running mean drops below the bound,
    the unit refuses subsequent invocations."""
    impl_source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'confidence': inputs['c']}\n"
    )
    drift_criteria = [
        {"type": "mean_in", "field": "confidence", "bound": [0.9, 1.0], "window": 3},
    ]
    runtime, unit, cw = _make_runtime_with_drift_unit(impl_source, drift_criteria)

    # Three invocations at 0.95 — mean stays above 0.9; permits.
    for _ in range(3):
        result = runtime.invoke(unit.content_id(), {"c": 0.95}, cw)
        assert isinstance(result, Permit)
    assert runtime.drift_monitor.is_drifted(unit.content_id()) is False

    # An invocation at 0.5: the drift check (before execution) passes
    # because no drift yet; the impl runs and returns 0.5; the
    # observation pushes the running mean below the bound; drift is
    # detected post-execution. This invocation still permits — its
    # output is part of the data that established drift.
    result = runtime.invoke(unit.content_id(), {"c": 0.5}, cw)
    assert isinstance(result, Permit)
    assert runtime.drift_monitor.is_drifted(unit.content_id())

    # Subsequent invocations refuse with the drift rationale; the input
    # value is irrelevant because the drift check is on the unit, not
    # the new inputs.
    result = runtime.invoke(unit.content_id(), {"c": 0.95}, cw)
    assert isinstance(result, Refuse)
    assert "drifted" in result.rationale
    assert "observed_mean" in result.rationale


def test_runtime_no_drift_criteria_never_refuses_for_drift():
    """Specification-bounded units, or units without drift_criteria, are
    never refused for drift even if their outputs are wildly variable."""
    impl_source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'value': inputs['v']}\n"
    )
    # No drift_criteria in the spec.
    runtime, unit, cw = _make_runtime_with_drift_unit(impl_source, [])
    for v in (0.0, 1.0, -100.0, 1e10, 0.0):
        result = runtime.invoke(unit.content_id(), {"v": v}, cw)
        assert isinstance(result, Permit)


def test_drifted_unit_refuses_with_rationale_naming_the_criterion():
    impl_source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'refused_rate': 1.0 if inputs['r'] else 0.0, 'verdict': inputs['v']}\n"
    )
    drift_criteria = [
        {"type": "rate_in", "field": "verdict", "value": "refused", "bound": [0.0, 0.3], "window": 5},
    ]
    runtime, unit, cw = _make_runtime_with_drift_unit(impl_source, drift_criteria)
    # Five in a row with "refused" — rate of 1.0 violates [0.0, 0.3].
    for _ in range(5):
        runtime.invoke(unit.content_id(), {"r": True, "v": "refused"}, cw)
    assert runtime.drift_monitor.is_drifted(unit.content_id())

    result = runtime.invoke(unit.content_id(), {"r": False, "v": "permitted"}, cw)
    assert isinstance(result, Refuse)
    assert "rate_in" in result.rationale
    assert "verdict" in result.rationale
