"""
Implementation source strings for the London Whale demonstration.

All stylised. Synthetic position and P&L figures are illustrative;
no claim about specific real positions or model parameters.
"""


# ===========================================================================
# VaR model (behaviour-characterised, produces confidence)
# ===========================================================================

# The VaR model returns a daily VaR figure and a confidence value
# reflecting calibration on this invocation: how close the realised P&L
# volatility is to the predicted volatility. confidence = 1.0 when ratio
# is 1.0; drops linearly toward 0 as the ratio diverges.
#
# This is the substrate-native confidence — produced at runtime, declared
# in the unit's spec, propagated by the runtime into parent units that
# invoke this one.
#
# The drift criterion on `realised_over_predicted_ratio` remains as a
# backstop: systematic miscalibration over a window invalidates the
# unit regardless of per-invocation confidence.
VAR_MODEL = """
def implementation(inputs, runtime, invoking_credential_id):
    notional = inputs["position_notional_million_usd"]
    risk_factor = inputs["risk_factor"]
    actual_vol = inputs.get("actual_realised_volatility_million_usd", 0.0)
    predicted = notional * risk_factor
    if predicted > 0 and actual_vol > 0:
        ratio = actual_vol / predicted
        # Calibration-based confidence: peaks at ratio=1.0; drops
        # linearly to 0 as the ratio diverges from 1.0 by 2x or more.
        confidence = max(0.0, 1.0 - 0.5 * abs(ratio - 1.0))
    else:
        ratio = 1.0
        # No realised data on this invocation; fall back to the unit's
        # declared calibration prior.
        confidence = 0.9
    return {
        "position_notional_million_usd": notional,
        "model_risk_factor": risk_factor,
        "predicted_var_million_usd": predicted,
        "actual_realised_volatility_million_usd": actual_vol,
        "realised_over_predicted_ratio": ratio,
        "confidence": confidence,
        "computed_by": invoking_credential_id,
    }
"""


# ===========================================================================
# Trade clearance (confidence gate)
# ===========================================================================

# A downstream unit invoked by authorise_position after the VaR computation.
# Its spec declares a confidence_gate that refuses the invocation if the
# incoming confidence is below a declared threshold. The threshold is
# part of the unit's content_id, so substituting a more lenient threshold
# produces a new unit content_id and is visible in audit.
#
# This is the architectural difference between "a threshold constant in
# a policy impl" and "a compiled policy threshold". The mechanism is
# functionally the same; the visibility is different.
TRADE_CLEARANCE = """
def implementation(inputs, runtime, invoking_credential_id):
    return {
        "cleared": True,
        "confidence": inputs.get("confidence"),
        "predicted_var_million_usd": inputs.get("predicted_var_million_usd"),
    }
"""


# ===========================================================================
# Position limit policy
# ===========================================================================

# Refuses positions whose notional exceeds the declared desk limit unless
# the invoking credential is an escalation credential (the senior risk
# officer). Escalation as a structural credential, not a procedure.
POSITION_LIMIT_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    notional = inputs.get("position_notional_million_usd", 0.0)
    declared_limit = inputs.get("declared_desk_limit_million_usd", 0.0)
    escalation_name = inputs.get("escalation_credential_name", "")
    if notional <= declared_limit:
        return {}
    if not escalation_name:
        raise Exception(
            "position_limit_policy refuses: notional " + str(notional) +
            "m USD exceeds declared desk limit " + str(declared_limit) +
            "m USD with no escalation credential declared"
        )
    if not escalation_name.startswith("senior_risk_officer"):
        raise Exception(
            "position_limit_policy refuses: escalation credential '" +
            escalation_name + "' is not recognised as a senior-risk " +
            "escalation principal"
        )
    return {}
"""


# ===========================================================================
# Authorise position (composing unit; propagates confidence)
# ===========================================================================

# Invokes the VaR model, then invokes trade_clearance with the VaR
# confidence as input. The parent unit declares `confidence` propagation
# (minimum), so the runtime collects the sub-unit confidences and
# computes the propagated value into authorise_position's output
# automatically. The impl does not need to compute confidence itself;
# the substrate machinery handles it.
AUTHORISE_POSITION = """
def implementation(inputs, runtime, invoking_credential_id):
    var_unit_id = inputs["var_unit_id"]
    clearance_unit_id = inputs["clearance_unit_id"]
    notional = inputs["position_notional_million_usd"]
    risk_factor = inputs["risk_factor"]
    actual_vol = inputs.get("actual_realised_volatility_million_usd", 0.0)
    position_id = inputs.get("position_id", "")

    var_result = runtime.invoke(
        var_unit_id,
        {
            "position_notional_million_usd": notional,
            "risk_factor": risk_factor,
            "actual_realised_volatility_million_usd": actual_vol,
        },
        invoking_credential_id,
    )
    if var_result.__class__.__name__ != "Permit":
        return {
            "position_authorised": False,
            "position_id": position_id,
            "rationale": "var model refused: " + var_result.rationale,
            "var_act_id": var_result.act_id,
        }

    var_confidence = var_result.output["confidence"]
    predicted_var = var_result.output["predicted_var_million_usd"]

    clearance_result = runtime.invoke(
        clearance_unit_id,
        {
            "confidence": var_confidence,
            "predicted_var_million_usd": predicted_var,
        },
        invoking_credential_id,
    )
    if clearance_result.__class__.__name__ != "Permit":
        return {
            "position_authorised": False,
            "position_id": position_id,
            "rationale": "trade clearance refused: " + clearance_result.rationale,
            "var_act_id": var_result.act_id,
            "clearance_act_id": clearance_result.act_id,
        }

    # Note: the substrate runtime injects the propagated confidence into
    # this output automatically (this unit declares confidence propagation
    # in its spec). We do not need to set it here.
    return {
        "position_authorised": True,
        "position_id": position_id,
        "position_notional_million_usd": notional,
        "predicted_var_million_usd": predicted_var,
        "var_model_act_id": var_result.act_id,
        "trade_clearance_act_id": clearance_result.act_id,
        "authorised_by": invoking_credential_id,
        "escalation_credential_name": inputs.get("escalation_credential_name", ""),
    }
"""


# ===========================================================================
# Outcome report (records realised P&L for a position)
# ===========================================================================

# Records the realised loss for a position, keyed by position_id. The
# backtest unit pairs these outcomes with the authorise_position
# predictions by position_id.
REPORT_REALISED_PNL = """
def implementation(inputs, runtime, invoking_credential_id):
    return {
        "position_id": inputs["position_id"],
        "realised_loss_million_usd": inputs["realised_loss_million_usd"],
        "reported_by": invoking_credential_id,
    }
"""


# ===========================================================================
# Backtest: VaR calibration against realised P&L
# ===========================================================================

# Walks the ledger; pairs authorise_position acts with
# report_realised_pnl acts by position_id; computes the exceedance rate
# (realised_loss > predicted_var). A well-calibrated 99% VaR should be
# exceeded around 1% of the time. The backtest refuses if the exceedance
# rate is above a declared bound — the calibration claim is structurally
# violated.
#
# Refusal is a ledger event. An authorised operator (e.g., risk_officer)
# would then deprecate the target VaR model via an administrative act;
# deprecation propagates through the substrate's uniform invalidation
# surface.
BACKTEST_VAR_CALIBRATION = """
def implementation(inputs, runtime, invoking_credential_id):
    from substrate.backtest import pair_predictions_and_outcomes, exceedance_rate
    target_id = inputs["authorise_unit_id"]
    outcome_id = inputs["outcome_unit_id"]
    max_exceedance_rate = inputs["max_exceedance_rate"]

    pairs = pair_predictions_and_outcomes(
        runtime, target_id, outcome_id, "position_id"
    )
    # Only include pairs whose prediction was actually authorised AND has a numeric predicted_var.
    pairs = [
        (p, o) for p, o in pairs
        if p.get("position_authorised") and p.get("predicted_var_million_usd") is not None
    ]
    if not pairs:
        return {"verdict": "insufficient_data", "n_pairs": 0}

    rate = exceedance_rate(
        pairs, "predicted_var_million_usd", "realised_loss_million_usd"
    )
    if rate is None:
        return {"verdict": "insufficient_data", "n_pairs": len(pairs)}
    if rate > max_exceedance_rate:
        raise Exception(
            "backtest refuses: VaR exceedance_rate=" + format(rate, ".3f") +
            " exceeds declared maximum " + format(max_exceedance_rate, ".3f") +
            " (n=" + str(len(pairs)) + " paired observations); " +
            "VaR calibration claim is structurally violated; " +
            "deprecate the target unit via administrative act"
        )
    return {
        "verdict": "calibration_holds",
        "exceedance_rate": rate,
        "max_exceedance_rate": max_exceedance_rate,
        "n_pairs": len(pairs),
    }
"""


# ===========================================================================
# Cross-operator audit
# ===========================================================================

AUDIT_POSITIONS = """
def implementation(inputs, runtime, invoking_credential_id):
    authorise_unit_id = inputs.get("authorise_unit_id", "")
    positions = []
    for act in runtime.ledger:
        if act.kind == "administrative":
            continue
        try:
            cf = runtime.code.get_for_audit(act.compiled_form_id)
            if cf.source_unit != authorise_unit_id:
                continue
        except Exception:
            continue
        out = act.output_or_rationale
        ins = act.inputs if isinstance(act.inputs, dict) else {}
        if isinstance(out, dict):
            positions.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "by": act.invoking_credential_id,
                "position_notional_million_usd": out.get("position_notional_million_usd")
                    or ins.get("position_notional_million_usd"),
                "predicted_var_million_usd": out.get("predicted_var_million_usd"),
                "propagated_confidence": out.get("confidence"),
                "position_authorised": out.get("position_authorised"),
                "escalation": out.get("escalation_credential_name"),
                "rationale": out.get("rationale"),
            })
        else:
            positions.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "by": act.invoking_credential_id,
                "position_notional_million_usd": ins.get("position_notional_million_usd"),
                "rationale": out if isinstance(out, str) else "(structured)",
            })
    return {
        "positions": positions,
        "ledger_length": len(runtime.ledger),
    }
"""


INVESTIGATE_BANK = """
def implementation(inputs, runtime, invoking_credential_id):
    bank_op_id = inputs["bank_operator_id"]
    audit_unit_id = inputs["audit_unit_id"]
    audit_inputs = inputs.get("audit_inputs", {})
    result = runtime.invoke_in(bank_op_id, audit_unit_id, audit_inputs,
                                invoking_credential_id)
    if result.__class__.__name__ != "Permit":
        return {"verdict": "audit_refused", "rationale": result.rationale}
    data = result.output
    positions = data["positions"]
    refused = [p for p in positions if p["verdict"] != "permit"]
    permitted = [p for p in positions if p["verdict"] == "permit"]
    return {
        "verdict": "audit_completed",
        "audit_act_id": result.act_id,
        "ledger_length": data["ledger_length"],
        "permitted_positions": permitted,
        "refused_positions": refused,
    }
"""
