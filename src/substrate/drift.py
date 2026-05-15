"""
Drift detection for behaviour-characterised units.

A behaviour-characterised unit declares its calibration in its spec
through a `drift_criteria` list. Each criterion is a structured check
over a sliding window of recent outputs; when a check is violated,
the unit is marked drifted and subsequent invocations refuse with
`UnitDrifted` propagating through the runtime's standard refusal path.

This is the sixth and final invalidation trigger from v1.4 §2. The
other five (revocation, supersession, deprecation, constitutional source
update, compilation integrity) are archive-state or compile-time
properties. Drift is a runtime-state property: it depends on observed
outputs over time, so it lives on the runtime (a `DriftMonitor`
attached to each Runtime instance) rather than on the archives. When a
substrate restarts, drift state is rebuilt from new observations. This
is intentional — drift is about live behaviour, and history-window
based; restarting the process is effectively a "give it fresh data" act
that the operator can do explicitly if drift detection was wrong or if
the underlying implementation has been replaced.

Supported criterion types (Phase 3 minimum):

- `mean_in`: the mean of a numeric output field over the last `window`
  observations must lie within `[lo, hi]`. Out-of-range marks drift.

- `rate_in`: the fraction of observations whose `field` equals `value`,
  over the last `window` observations, must lie within `[lo, hi]`.

Both criteria are evaluated lazily: when fewer than `window`
observations have been collected for a unit, the check is satisfied
(insufficient data). This avoids false drift signals during warm-up.

Additional criterion types (KS-test, distributional checks,
multi-variable correlation) are deferred. The same shape applies: a
type tag, parameters, evaluation against a recent-window of
observations.
"""

from __future__ import annotations


class UnitDrifted(Exception):
    """A behaviour-characterised unit has been observed leaving its
    declared calibration bounds. Subsequent invocations refuse until
    the unit is replaced with a recalibrated implementation (which
    produces a new content_id and a fresh drift-monitor entry).
    """

    def __init__(self, unit_content_id: str, violated_criterion: dict, evidence: dict):
        self.unit_content_id = unit_content_id
        self.violated_criterion = violated_criterion
        self.evidence = evidence
        super().__init__(
            f"Unit {unit_content_id} drifted; criterion {violated_criterion!r} "
            f"violated; evidence: {evidence}"
        )


class DriftMonitor:
    """Tracks per-unit running output observations and detects drift.

    The monitor holds two pieces of state per unit content_id:
    a bounded list of recent observations (one per permitted invocation),
    and a drift flag (set once a criterion is violated, cleared only by
    explicit reset). Drift is sticky within a process: once detected,
    the unit refuses for the lifetime of the substrate instance.
    """

    def __init__(self):
        # unit_cid -> list of recent output dicts.
        self._observations: dict = {}
        # unit_cid -> the (criterion_dict, evidence_dict) tuple if drifted.
        self._drifted: dict = {}

    def is_drifted(self, unit_content_id: str) -> bool:
        return unit_content_id in self._drifted

    def drift_reason(self, unit_content_id: str):
        """Return the (criterion, evidence) tuple for a drifted unit, or None."""
        return self._drifted.get(unit_content_id)

    def reset(self, unit_content_id: str) -> None:
        """Clear observations and drift state for a unit (operator action;
        used when a replacement implementation is registered or when
        drift detection was a false alarm)."""
        self._observations.pop(unit_content_id, None)
        self._drifted.pop(unit_content_id, None)

    def observe(
        self,
        unit_content_id: str,
        output: dict,
        drift_criteria: list,
    ):
        """Record an output and check every criterion.

        If any criterion is violated, mark the unit drifted and return
        the violating (criterion, evidence) pair. Returns None if all
        criteria pass (or were under-sampled).
        """
        if not drift_criteria:
            return None
        if unit_content_id in self._drifted:
            # Already drifted; no further observation needed (the
            # runtime's drift check happens before observation, so we
            # are only called when permitted invocations occur; if
            # already drifted we should never reach here, but defend).
            return self._drifted[unit_content_id]

        history = self._observations.setdefault(unit_content_id, [])
        history.append(output if isinstance(output, dict) else {"_raw": output})

        # Trim to the largest window any criterion needs.
        max_window = max((int(c.get("window", 1)) for c in drift_criteria), default=1)
        if len(history) > max_window:
            del history[: len(history) - max_window]

        for criterion in drift_criteria:
            evidence = _evaluate_criterion(criterion, history)
            if evidence is not None:
                self._drifted[unit_content_id] = (criterion, evidence)
                return (criterion, evidence)
        return None


def _evaluate_criterion(criterion: dict, history: list):
    """Return None if the criterion is satisfied (or under-sampled);
    return an evidence dict if the criterion is violated.

    Parsing is permissive: unknown criterion types are treated as
    satisfied (so a unit can declare experimental criterion types
    without breaking compilation; only known types are enforced).
    """
    ctype = criterion.get("type")
    window = int(criterion.get("window", len(history)))
    if window <= 0:
        return None
    recent = history[-window:]
    if len(recent) < window:
        return None  # under-sampled; not yet enforceable

    if ctype == "mean_in":
        field = criterion.get("field")
        bound = criterion.get("bound")
        if field is None or not _valid_bound(bound):
            return None
        values = [obs[field] for obs in recent if isinstance(obs, dict) and field in obs and isinstance(obs[field], (int, float))]
        if not values:
            return None  # no numeric data
        mean = sum(values) / len(values)
        lo, hi = bound
        if lo <= mean <= hi:
            return None
        return {"observed_mean": mean, "bound": [lo, hi], "field": field, "window": window}

    if ctype == "rate_in":
        field = criterion.get("field")
        value = criterion.get("value")
        bound = criterion.get("bound")
        if field is None or not _valid_bound(bound):
            return None
        matches = sum(1 for obs in recent if isinstance(obs, dict) and obs.get(field) == value)
        rate = matches / len(recent)
        lo, hi = bound
        if lo <= rate <= hi:
            return None
        return {"observed_rate": rate, "bound": [lo, hi], "field": field, "value": value, "window": window}

    # Unknown criterion type: skip (not enforced).
    return None


def _valid_bound(bound) -> bool:
    return (
        isinstance(bound, (list, tuple))
        and len(bound) == 2
        and all(isinstance(b, (int, float)) for b in bound)
        and bound[0] <= bound[1]
    )
