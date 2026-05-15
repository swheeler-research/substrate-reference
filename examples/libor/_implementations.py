"""
Implementation source strings for the LIBOR manipulation demonstration.

All stylised. The synthetic money-market activity figures are
illustrative; no claim about specific real bank submissions.
"""


# ===========================================================================
# Bank-side: submit_rate
# ===========================================================================

# Each bank submits a daily rate. The submission carries the declared
# money-market activity context (observed_volume, observed_rate_range)
# for the relevant submission window. The substrate's divergence_policy,
# brought into binding via the bank's submission credential, refuses
# submissions where the rate is implausible given the declared activity.
# The behaviour-characterised contract additionally declares a drift
# criterion on the divergence-from-activity metric: systematic bias
# trips drift over a window even when individual submissions stay
# within the per-submission threshold.
SUBMIT_RATE = """
def implementation(inputs, runtime, invoking_credential_id):
    submitted_rate = inputs["submitted_rate_bps"]
    observed_low = inputs.get("observed_rate_range_low_bps", 0)
    observed_high = inputs.get("observed_rate_range_high_bps", 0)
    midpoint = (observed_low + observed_high) / 2.0
    divergence = abs(submitted_rate - midpoint)
    # Confidence-as-architectural-property. Confidence reflects how
    # well-anchored the submitted rate is to declared activity. 1.0 at
    # the midpoint; drops linearly to 0 as divergence reaches 5 bps
    # (the per-submission policy threshold). The substrate runtime
    # propagates this into the administrator's aggregate.
    confidence = max(0.0, 1.0 - divergence / 5.0)
    return {
        "submitted_rate_bps": submitted_rate,
        "observed_volume_million_usd": inputs.get("observed_volume_million_usd", 0),
        "observed_rate_range_low_bps": observed_low,
        "observed_rate_range_high_bps": observed_high,
        "submission_window": inputs.get("submission_window", ""),
        "submitting_bank": inputs.get("bank_label", ""),
        "submitted_by": invoking_credential_id,
        "divergence_from_activity_bps": divergence,
        "confidence": confidence,
    }
"""


# ===========================================================================
# Policy: divergence at submission time
# ===========================================================================

# Refuses a submission whose rate diverges from the implied midpoint of
# declared activity by more than a threshold. Also refuses a submission
# whose declared volume is zero (there is no activity to anchor the
# rate to).
DIVERGENCE_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    submitted_rate = inputs.get("submitted_rate_bps")
    if submitted_rate is None:
        return {}
    observed_low = inputs.get("observed_rate_range_low_bps", 0)
    observed_high = inputs.get("observed_rate_range_high_bps", 0)
    observed_volume = inputs.get("observed_volume_million_usd", 0)
    THRESHOLD_BPS = 5.0
    if observed_volume <= 0:
        raise Exception(
            "divergence_policy refuses: submission declared zero observed "
            "volume; rate cannot be anchored to underlying activity"
        )
    midpoint = (observed_low + observed_high) / 2.0
    divergence = abs(submitted_rate - midpoint)
    if divergence > THRESHOLD_BPS:
        raise Exception(
            "divergence_policy refuses: submitted_rate " + str(submitted_rate) +
            "bps diverges by " + str(round(divergence, 2)) +
            "bps from observed activity midpoint " + str(midpoint) +
            "bps (threshold " + str(THRESHOLD_BPS) + "bps)"
        )
    return {}
"""


# ===========================================================================
# Admin-side: aggregate_libor (witnessed by the panel cooperative substrate)
# ===========================================================================

# Trim-and-average across panel bank submissions. The architectural point
# is that this unit is witnessed under the cooperative substrate's quorum
# custodian — the benchmark cannot be produced without a quorum of the
# panel banks plus the administrator.
AGGREGATE_LIBOR = """
def implementation(inputs, runtime, invoking_credential_id):
    submissions = inputs["submissions"]
    window = inputs.get("submission_window", "")
    rates = sorted([s["submitted_rate_bps"] for s in submissions])
    n = len(rates)
    if n < 3:
        raise Exception(
            "aggregate_libor refuses: " + str(n) +
            " submissions provided; need at least 3"
        )
    trim = max(1, n // 4)
    middle = rates[trim:n - trim] if (n - 2 * trim) > 0 else rates
    benchmark = sum(middle) / len(middle)
    # Aggregate confidence: mean of the per-submission confidences. The
    # substrate's propagation injects this into the unit's output unless
    # the impl explicitly sets it. Here we set it explicitly because the
    # submissions arrive as data inputs (not as sub-invocations of this
    # call), so propagation has no sub-confidences to combine. This is
    # the impl-override case.
    confidences = [s.get("confidence") for s in submissions if s.get("confidence") is not None]
    if confidences:
        agg_conf = sum(confidences) / len(confidences)
    else:
        agg_conf = None
    return {
        "submission_window": window,
        "panel_size": n,
        "trimmed_count": 2 * trim,
        "benchmark_rate_bps": round(benchmark, 4),
        "contributing_banks": [s.get("submitting_bank", "?") for s in submissions],
        "confidence": agg_conf,
    }
"""


# ===========================================================================
# Cross-operator audit
# ===========================================================================

# Admin or regulator can invoke this on a panel bank's substrate via the
# audit cooperative substrate. Walks the bank's ledger and reports every
# submission act (permitted and refused) with attribution.
AUDIT_BANK_SUBMISSIONS = """
def implementation(inputs, runtime, invoking_credential_id):
    bank_label = inputs.get("bank_label", "")
    submit_unit_id = inputs.get("submit_unit_id", "")
    submissions = []
    for act in runtime.ledger:
        if act.kind == "administrative":
            continue
        # Filter to acts that were invocations of the bank's specific
        # submit_rate unit (not the policy unit invoked during evaluation).
        try:
            cf = runtime.code.get_for_audit(act.compiled_form_id)
            if cf.source_unit != submit_unit_id:
                continue
        except Exception:
            continue
        out = act.output_or_rationale
        ins = act.inputs if isinstance(act.inputs, dict) else {}
        if isinstance(out, dict):
            submissions.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "by": act.invoking_credential_id,
                "submitted_rate_bps": out.get("submitted_rate_bps"),
                "divergence_bps": out.get("divergence_from_activity_bps"),
                "observed_volume": out.get("observed_volume_million_usd"),
                "window": out.get("submission_window"),
            })
        else:
            submissions.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "by": act.invoking_credential_id,
                "submitted_rate_bps": ins.get("submitted_rate_bps"),
                "observed_volume": ins.get("observed_volume_million_usd"),
                "window": ins.get("submission_window"),
                "rationale": out if isinstance(out, str) else "(structured)",
            })
    return {
        "bank_label": bank_label,
        "submissions": submissions,
        "ledger_length": len(runtime.ledger),
    }
"""


# ===========================================================================
# Regulator-side investigation
# ===========================================================================

INVESTIGATE_PANEL = """
def implementation(inputs, runtime, invoking_credential_id):
    bank_op_id = inputs["bank_operator_id"]
    audit_unit_id = inputs["audit_unit_id"]
    audit_inputs = inputs.get("audit_inputs", {})
    result = runtime.invoke_in(bank_op_id, audit_unit_id, audit_inputs,
                                invoking_credential_id)
    if result.__class__.__name__ != "Permit":
        return {"verdict": "audit_refused", "rationale": result.rationale}
    data = result.output
    submissions = data["submissions"]
    refused = [s for s in submissions if s["verdict"] != "permit"]
    permitted = [s for s in submissions if s["verdict"] == "permit"]
    return {
        "verdict": "audit_completed",
        "bank_label": data["bank_label"],
        "audit_act_id": result.act_id,
        "ledger_length": data["ledger_length"],
        "permitted_submissions": permitted,
        "refused_submissions": refused,
    }
"""
