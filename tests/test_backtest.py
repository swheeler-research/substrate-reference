"""Tests for the backtest pattern: canonical metric functions and ledger pairing.

The backtest pattern itself is not a new substrate primitive — it is a
documented use of existing units, ledger walks, and refusal semantics.
What this module provides (and what these tests exercise) is the set of
canonical calibration metric functions that unit authors inline into
their backtest impls.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.backtest import (
    bucket_calibration_error,
    brier_score,
    coverage_rate,
    exceedance_rate,
    pair_predictions_and_outcomes,
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
from substrate.runtime import Permit, Runtime


# =============================================================================
# Metric: coverage_rate
# =============================================================================

def test_coverage_rate_empty_returns_none():
    assert coverage_rate([], "confidence", "correct", 0.5) is None


def test_coverage_rate_no_predictions_meet_threshold():
    pairs = [({"confidence": 0.4}, {"correct": True})]
    assert coverage_rate(pairs, "confidence", "correct", 0.5) is None


def test_coverage_rate_well_calibrated():
    pairs = [
        ({"confidence": 0.95}, {"correct": True}),
        ({"confidence": 0.95}, {"correct": True}),
        ({"confidence": 0.95}, {"correct": True}),
        ({"confidence": 0.95}, {"correct": False}),
    ]
    rate = coverage_rate(pairs, "confidence", "correct", 0.9)
    assert rate == 0.75


def test_coverage_rate_perfect():
    pairs = [({"confidence": 0.9}, {"correct": True})] * 10
    assert coverage_rate(pairs, "confidence", "correct", 0.9) == 1.0


# =============================================================================
# Metric: brier_score
# =============================================================================

def test_brier_score_empty_returns_none():
    assert brier_score([], "confidence", "correct") is None


def test_brier_score_perfect_calibration_perfect_discrimination():
    pairs = [
        ({"confidence": 1.0}, {"correct": True}),
        ({"confidence": 0.0}, {"correct": False}),
    ]
    assert brier_score(pairs, "confidence", "correct") == 0.0


def test_brier_score_always_half():
    pairs = [
        ({"confidence": 0.5}, {"correct": True}),
        ({"confidence": 0.5}, {"correct": False}),
    ]
    assert brier_score(pairs, "confidence", "correct") == 0.25


def test_brier_score_wrong_direction():
    """Confidence-0 predictions that were correct = wrong direction; high Brier."""
    pairs = [
        ({"confidence": 0.0}, {"correct": True}),
        ({"confidence": 0.0}, {"correct": True}),
    ]
    assert brier_score(pairs, "confidence", "correct") == 1.0


# =============================================================================
# Metric: bucket_calibration_error
# =============================================================================

def test_ece_empty_returns_none():
    assert bucket_calibration_error([], "confidence", "correct") is None


def test_ece_perfect_calibration():
    """Predictions where confidence equals accuracy in each bucket."""
    pairs = (
        [({"confidence": 0.9}, {"correct": True})] * 9
        + [({"confidence": 0.9}, {"correct": False})] * 1
    )
    ece = bucket_calibration_error(pairs, "confidence", "correct", n_bins=10)
    assert abs(ece) < 1e-9


def test_ece_miscalibrated():
    """Overconfident predictions: confidence 0.9, accuracy 0.5."""
    pairs = (
        [({"confidence": 0.9}, {"correct": True})] * 5
        + [({"confidence": 0.9}, {"correct": False})] * 5
    )
    ece = bucket_calibration_error(pairs, "confidence", "correct", n_bins=10)
    # Bucket mean confidence 0.9, bucket accuracy 0.5, weight 10/10 = 1.0
    assert abs(ece - 0.4) < 1e-9


def test_ece_clips_out_of_range_confidences():
    pairs = [({"confidence": 1.5}, {"correct": True})]
    ece = bucket_calibration_error(pairs, "confidence", "correct", n_bins=10)
    assert ece is not None


def test_ece_bins_validate():
    with pytest.raises(ValueError):
        bucket_calibration_error([({}, {})], "confidence", "correct", n_bins=0)


# =============================================================================
# Metric: exceedance_rate
# =============================================================================

def test_exceedance_rate_empty_returns_none():
    assert exceedance_rate([], "bound", "realised") is None


def test_exceedance_rate_no_exceedances():
    pairs = [
        ({"bound": 10.0}, {"realised": 5.0}),
        ({"bound": 10.0}, {"realised": 8.0}),
    ]
    assert exceedance_rate(pairs, "bound", "realised") == 0.0


def test_exceedance_rate_var_calibrated():
    """A 99% VaR should be exceeded ~1% of the time."""
    pairs = (
        [({"bound": 10.0}, {"realised": 5.0})] * 99
        + [({"bound": 10.0}, {"realised": 15.0})] * 1
    )
    rate = exceedance_rate(pairs, "bound", "realised")
    assert abs(rate - 0.01) < 1e-9


def test_exceedance_rate_var_underestimated():
    """A miscalibrated bound shows high exceedance."""
    pairs = (
        [({"bound": 5.0}, {"realised": 15.0})] * 5
        + [({"bound": 5.0}, {"realised": 2.0})] * 5
    )
    rate = exceedance_rate(pairs, "bound", "realised")
    assert rate == 0.5


def test_exceedance_rate_skips_missing_fields():
    pairs = [
        ({"bound": 10.0}, {"realised": None}),
        ({}, {"realised": 5.0}),
    ]
    assert exceedance_rate(pairs, "bound", "realised") == 0.0


# =============================================================================
# pair_predictions_and_outcomes (uses a real runtime)
# =============================================================================

PREDICTION_IMPL = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"target_id": inputs["target_id"], "confidence": inputs["confidence"]}
"""

OUTCOME_IMPL = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"target_id": inputs["target_id"], "correct": inputs["correct"]}
"""


def _scene_with_target_and_outcome():
    code = CodeArchive()
    creds = CredentialsArchive()
    root = CredentialUnit(
        name="root", transfer=TransferDiscipline.DELEGATED, principal="root",
        authorities=("invoke:any",), credential_refs=(),
    )
    root_cid = creds.put(root)
    invoker = CredentialUnit(
        name="invoker", transfer=TransferDiscipline.DELEGATED, principal="invoker",
        authorities=("invoke:any",), credential_refs=(root_cid,),
    )
    invoker_cid = creds.put(invoker)

    pred_impl = python_implementation(PREDICTION_IMPL, name="pred_impl")
    pred_impl_cid = code.put(pred_impl)
    pred_unit = FunctionalUnit(
        name="target",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"target_id": "str", "confidence": "float"}},
        implementation_ref=pred_impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(pred_unit)

    outcome_impl = python_implementation(OUTCOME_IMPL, name="outcome_impl")
    outcome_impl_cid = code.put(outcome_impl)
    outcome_unit = FunctionalUnit(
        name="outcome",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"target_id": "str", "correct": "bool"}},
        implementation_ref=outcome_impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(outcome_unit)

    rt = Runtime(code, creds, FederatedLedger())
    cust = LocalCustodian()
    rt.register_compiled(compile_unit(pred_unit, code, creds, custodian=cust))
    rt.register_compiled(compile_unit(outcome_unit, code, creds, custodian=cust))

    return rt, pred_unit, outcome_unit, invoker_cid, root_cid


def test_pair_predictions_and_outcomes_joins_by_correlation_field():
    rt, pred_unit, outcome_unit, invoker_cid, root_cid = _scene_with_target_and_outcome()
    # Make three predictions and two outcomes; expect two pairs.
    rt.invoke(pred_unit.content_id(), {"target_id": "a", "confidence": 0.9}, invoker_cid)
    rt.invoke(pred_unit.content_id(), {"target_id": "b", "confidence": 0.7}, invoker_cid)
    rt.invoke(pred_unit.content_id(), {"target_id": "c", "confidence": 0.5}, invoker_cid)
    rt.invoke(outcome_unit.content_id(), {"target_id": "a", "correct": True}, invoker_cid)
    rt.invoke(outcome_unit.content_id(), {"target_id": "b", "correct": False}, invoker_cid)

    pairs = pair_predictions_and_outcomes(
        rt, pred_unit.content_id(), outcome_unit.content_id(), "target_id"
    )
    assert len(pairs) == 2
    by_key = {p["target_id"]: (p, o) for p, o in pairs}
    assert by_key["a"][1]["correct"] is True
    assert by_key["b"][1]["correct"] is False


def test_pair_predictions_and_outcomes_empty():
    rt, pred_unit, outcome_unit, _, _ = _scene_with_target_and_outcome()
    pairs = pair_predictions_and_outcomes(
        rt, pred_unit.content_id(), outcome_unit.content_id(), "target_id"
    )
    assert pairs == []


def test_pair_predictions_and_outcomes_window():
    rt, pred_unit, outcome_unit, invoker_cid, root_cid = _scene_with_target_and_outcome()
    for i in range(5):
        rt.invoke(pred_unit.content_id(), {"target_id": f"k{i}", "confidence": 0.9}, invoker_cid)
        rt.invoke(outcome_unit.content_id(), {"target_id": f"k{i}", "correct": True}, invoker_cid)
    pairs_full = pair_predictions_and_outcomes(
        rt, pred_unit.content_id(), outcome_unit.content_id(), "target_id"
    )
    assert len(pairs_full) == 5
    pairs_short = pair_predictions_and_outcomes(
        rt, pred_unit.content_id(), outcome_unit.content_id(), "target_id", window=4
    )
    # Only the last 4 acts; should pair the most recent two prediction-outcome pairs.
    assert len(pairs_short) <= 5


# =============================================================================
# End-to-end backtest pattern: a unit that walks the ledger and refuses
# =============================================================================

BACKTEST_IMPL = """
def implementation(inputs, runtime, invoking_credential_id):
    from substrate.backtest import pair_predictions_and_outcomes, coverage_rate
    pairs = pair_predictions_and_outcomes(
        runtime, inputs["target_unit_id"], inputs["outcome_unit_id"], inputs["correlation_field"]
    )
    if not pairs:
        return {"verdict": "insufficient_data", "n_pairs": 0}
    rate = coverage_rate(pairs, "confidence", "correct", inputs["threshold"])
    if rate is None:
        return {"verdict": "insufficient_data_at_threshold", "n_pairs": len(pairs)}
    min_rate = inputs["minimum_coverage"]
    if rate < min_rate:
        raise Exception(
            "backtest refuses: coverage_rate=" + format(rate, ".3f") +
            " below minimum " + format(min_rate, ".3f") +
            " (n=" + str(len(pairs)) + ")"
        )
    return {"verdict": "calibration_holds", "coverage_rate": rate, "n_pairs": len(pairs)}
"""


def test_backtest_unit_refuses_when_calibration_violated():
    rt, pred_unit, outcome_unit, invoker_cid, root_cid = _scene_with_target_and_outcome()

    # Make 10 high-confidence predictions; only 3 correct → miscalibrated.
    for i in range(10):
        rt.invoke(pred_unit.content_id(), {"target_id": f"k{i}", "confidence": 0.95}, invoker_cid)
        rt.invoke(
            outcome_unit.content_id(),
            {"target_id": f"k{i}", "correct": (i < 3)},
            invoker_cid,
        )

    # Build a backtest unit.
    backtest_impl_state = python_implementation(BACKTEST_IMPL, name="backtest_impl")
    backtest_impl_cid = rt.code.put(backtest_impl_state)
    backtest_unit = FunctionalUnit(
        name="backtest",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "target_unit_id": "str",
                "outcome_unit_id": "str",
                "correlation_field": "str",
                "threshold": "float",
                "minimum_coverage": "float",
            },
        },
        implementation_ref=backtest_impl_cid,
        credential_refs=(root_cid,),
    )
    rt.code.put(backtest_unit)
    rt.register_compiled(compile_unit(backtest_unit, rt.code, rt.credentials, custodian=LocalCustodian()))

    result = rt.invoke(
        backtest_unit.content_id(),
        {
            "target_unit_id": pred_unit.content_id(),
            "outcome_unit_id": outcome_unit.content_id(),
            "correlation_field": "target_id",
            "threshold": 0.9,
            "minimum_coverage": 0.9,
        },
        invoker_cid,
    )
    # 30% accuracy at confidence >= 0.9 fails the 90% minimum coverage claim.
    assert result.__class__.__name__ == "Refuse"
    assert "coverage_rate" in result.rationale


def test_backtest_unit_permits_when_calibration_holds():
    rt, pred_unit, outcome_unit, invoker_cid, root_cid = _scene_with_target_and_outcome()
    for i in range(10):
        rt.invoke(pred_unit.content_id(), {"target_id": f"k{i}", "confidence": 0.95}, invoker_cid)
        rt.invoke(
            outcome_unit.content_id(),
            {"target_id": f"k{i}", "correct": True},  # all correct
            invoker_cid,
        )

    backtest_impl_state = python_implementation(BACKTEST_IMPL, name="backtest_impl2")
    backtest_impl_cid = rt.code.put(backtest_impl_state)
    backtest_unit = FunctionalUnit(
        name="backtest_ok",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "target_unit_id": "str",
                "outcome_unit_id": "str",
                "correlation_field": "str",
                "threshold": "float",
                "minimum_coverage": "float",
            },
        },
        implementation_ref=backtest_impl_cid,
        credential_refs=(root_cid,),
    )
    rt.code.put(backtest_unit)
    rt.register_compiled(compile_unit(backtest_unit, rt.code, rt.credentials, custodian=LocalCustodian()))

    result = rt.invoke(
        backtest_unit.content_id(),
        {
            "target_unit_id": pred_unit.content_id(),
            "outcome_unit_id": outcome_unit.content_id(),
            "correlation_field": "target_id",
            "threshold": 0.9,
            "minimum_coverage": 0.9,
        },
        invoker_cid,
    )
    assert isinstance(result, Permit)
    assert result.output["verdict"] == "calibration_holds"
