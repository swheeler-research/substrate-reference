"""
Backtest pattern: verifying calibration claims against realised behaviour.

A unit's `confidence.calibration` field is governance-load-bearing only
if it can be checked against actual ledger evidence. This module
provides the canonical building blocks for that check.

The pattern: a backtest is a regular functional unit whose impl walks
the ledger, pairs predictions made by a target unit with outcomes
recorded by an outcome unit, computes a declared calibration metric,
and refuses if the metric falls outside declared bounds. The backtest's
refusal is itself a ledger event. An authorised operator reads the
refusal and deprecates the target unit through the standard
administrative API; deprecation propagates via the substrate's uniform
invalidation surface.

This module provides:

- `pair_predictions_and_outcomes`: walks a runtime's ledger and joins
  acts of a target unit with acts of an outcome unit by a declared
  correlation field. Returns a list of (prediction, outcome) tuples.

- Reference metric functions: `coverage_rate`, `brier_score`,
  `bucket_calibration_error`, `exceedance_rate`. These are standard
  calibration metrics over (prediction, outcome) pairs. Unit authors
  inline them into backtest impls.

The substrate does NOT provide every possible calibration metric.
These four cover the common cases (classifier calibration, VaR
coverage, ECE). Domain libraries can build more sophisticated metrics
on top.

Operator-driven deprecation (rather than automatic invalidation on
backtest refusal) is the deliberate design: every invalidation
remains an explicit ledger event. This keeps the auditor's mental
model clean. See `docs/specification_gaps.md` entry "Backtest as a
substrate-recognised pattern".
"""

from typing import Any, Optional


def pair_predictions_and_outcomes(
    runtime,
    target_unit_id: str,
    outcome_unit_id: str,
    correlation_field: str,
    window: Optional[int] = None,
) -> list:
    """Walk the ledger and pair predictions with outcomes.

    Returns a list of (prediction_output, outcome_output) tuples. Both
    are dicts (the outputs of permitted acts). Pairs are joined on the
    value of `correlation_field`, which must be present in both
    prediction and outcome outputs (or in either inputs as fallback).

    `window`, if set, limits the walk to the most recent N acts.
    """
    predictions = {}
    outcomes = {}
    all_acts = list(runtime.ledger)
    if window is not None:
        all_acts = all_acts[-window:] if window > 0 else []
    for act in all_acts:
        if act.verdict != "permit":
            continue
        if act.kind == "administrative":
            continue
        try:
            cf = runtime.code.get_for_audit(act.compiled_form_id)
        except Exception:
            continue
        out = act.output_or_rationale
        ins = act.inputs if isinstance(act.inputs, dict) else {}
        if not isinstance(out, dict):
            continue
        key = out.get(correlation_field)
        if key is None:
            key = ins.get(correlation_field)
        if key is None:
            continue
        if cf.source_unit == target_unit_id:
            predictions[key] = out
        elif cf.source_unit == outcome_unit_id:
            outcomes[key] = out
    pairs = []
    for key, pred in predictions.items():
        if key in outcomes:
            pairs.append((pred, outcomes[key]))
    return pairs


def coverage_rate(
    pairs: list,
    confidence_field: str,
    correct_field: str,
    threshold: float,
) -> Optional[float]:
    """Of predictions with confidence >= threshold, fraction with correct=True.

    A well-calibrated unit's coverage rate should be >= threshold at every
    declared threshold level. Returns None if no predictions meet the
    threshold.
    """
    selected = [(p, o) for p, o in pairs if (p.get(confidence_field) or 0.0) >= threshold]
    if not selected:
        return None
    correct = sum(1 for _, o in selected if bool(o.get(correct_field)))
    return correct / len(selected)


def brier_score(
    pairs: list,
    confidence_field: str,
    correct_field: str,
) -> Optional[float]:
    """Mean squared error between confidence and 0/1 outcome.

    Lower is better. A perfectly-calibrated and perfectly-discriminating
    unit has Brier score 0. A unit that always predicts 0.5 has Brier
    score 0.25 on a balanced dataset.
    """
    if not pairs:
        return None
    total = 0.0
    for p, o in pairs:
        c = p.get(confidence_field) or 0.0
        y = 1.0 if bool(o.get(correct_field)) else 0.0
        total += (c - y) ** 2
    return total / len(pairs)


def bucket_calibration_error(
    pairs: list,
    confidence_field: str,
    correct_field: str,
    n_bins: int = 10,
) -> Optional[float]:
    """Expected Calibration Error (ECE) over confidence buckets.

    Bins predictions by confidence, computes |bucket_mean_confidence -
    bucket_accuracy| within each bin, weights by bin size, sums. Lower
    is better. A perfectly-calibrated unit has ECE 0 in the limit.
    """
    if not pairs:
        return None
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    buckets: list = [[] for _ in range(n_bins)]
    for p, o in pairs:
        c = max(0.0, min(1.0, p.get(confidence_field) or 0.0))
        idx = min(n_bins - 1, int(c * n_bins))
        y = 1.0 if bool(o.get(correct_field)) else 0.0
        buckets[idx].append((c, y))
    total_weight = 0
    total = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_c = sum(c for c, _ in bucket) / len(bucket)
        avg_y = sum(y for _, y in bucket) / len(bucket)
        total += abs(avg_c - avg_y) * len(bucket)
        total_weight += len(bucket)
    if total_weight == 0:
        return None
    return total / total_weight


def exceedance_rate(
    pairs: list,
    bound_field: str,
    realised_field: str,
) -> Optional[float]:
    """Fraction of cases where realised exceeded the unit's declared bound.

    For one-sided bounds like VaR: prediction declares a `bound_field`
    (e.g. predicted_var); outcome declares `realised_field` (e.g.
    realised_loss). Returns the rate at which realised > bound. A
    well-calibrated 99% VaR should have an exceedance rate close to 1%.
    """
    if not pairs:
        return None
    exceedances = 0
    for p, o in pairs:
        bound = p.get(bound_field)
        realised = o.get(realised_field)
        if bound is None or realised is None:
            continue
        if realised > bound:
            exceedances += 1
    return exceedances / len(pairs)
