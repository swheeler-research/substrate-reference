"""
Implementation source strings for the CrowdStrike Channel File 291
demonstration.
"""


# ===========================================================================
# Endpoint detection unit (the analogue of Falcon's threat detection)
# ===========================================================================

# A behaviour-characterised unit: examines a system event and returns
# a verdict. Drift criteria in the unit's spec declare what "normal"
# behaviour looks like. A faulty rule-update would push the unit's
# output distribution out of calibration, triggering drift.

ENDPOINT_DETECTION_V1_GOOD = """
def implementation(inputs, runtime, invoking_credential_id):
    event = inputs.get("event", {})
    # Stub: returns "ok" for events that look benign, "alert" for ones
    # matching synthetic indicators. Importantly, returns "ok" the vast
    # majority of the time on baseline data.
    indicators = event.get("indicators", [])
    if "known_malware_hash" in indicators:
        return {"verdict": "alert", "indicators_matched": ["known_malware_hash"],
                "system_health": "ok"}
    return {"verdict": "ok", "indicators_matched": [], "system_health": "ok"}
"""


# v2: the FAULTY update. Has a logic error that causes the unit to
# return "system_crash" for virtually every input. In real Falcon this
# manifested as kernel-level crashes (BSOD); here we model the visible
# effect as the unit declaring "system_crash" in its output.
ENDPOINT_DETECTION_V2_FAULTY = """
def implementation(inputs, runtime, invoking_credential_id):
    event = inputs.get("event", {})
    # BUG: a logic error introduced in the update. The new content-rule
    # field is referenced without bounds checking; the impl returns
    # crash for every event.
    return {"verdict": "alert", "indicators_matched": ["UNVALIDATED_RULE"],
            "system_health": "system_crash"}
"""


# ===========================================================================
# Customer deployment unit — gated by canary clearance
# ===========================================================================

# A customer's deployment unit. Invokes a canary-status check cross-operator
# before accepting an update. If the canary reports drift, the deployment
# refuses.
DEPLOY_UPDATE = """
def implementation(inputs, runtime, invoking_credential_id):
    update_content_id = inputs["update_content_id"]
    canary_op_id = inputs["canary_operator_id"]
    canary_status_unit_id = inputs["canary_status_unit_id"]

    status = runtime.invoke_in(
        canary_op_id, canary_status_unit_id,
        {"update_content_id": update_content_id},
        invoking_credential_id,
    )
    if status.__class__.__name__ != "Permit":
        return {
            "deployment": "refused",
            "rationale": "canary status check refused: " + status.rationale,
            "canary_status_act_id": status.act_id,
        }
    info = status.output
    if not info.get("clear_for_production"):
        return {
            "deployment": "refused",
            "rationale": "canary not clear: " + info.get("reason", "drift detected"),
            "canary_status_act_id": status.act_id,
        }
    return {
        "deployment": "accepted",
        "update_content_id": update_content_id,
        "canary_status_act_id": status.act_id,
        "canary_cohort_size": info.get("cohort_size"),
        "canary_observations": info.get("observations"),
    }
"""


# ===========================================================================
# Canary status check — exposed via cooperative substrate
# ===========================================================================

# Reports whether the canary cohort has observed this update without
# the endpoint_detection unit drifting. If drift fired, the canary
# refuses to clear; production refuses to deploy.

# Implementation walks the canary's runtime ledger to find recent
# observations of this update, and checks drift status. This is
# read-only: the canary's runtime state is the source of truth.
CANARY_STATUS_CHECK = """
def implementation(inputs, runtime, invoking_credential_id):
    # The 'update' being deployed is identified by the functional unit's
    # content_id (NOT its implementation's content_id). The status check
    # is per-unit: previous versions remain deployable even if a newer
    # version drifted.
    unit_cid = inputs["update_content_id"]
    observations = 0
    crashes = 0
    for act in runtime.ledger:
        if act.verdict != "permit":
            continue
        out = act.output_or_rationale
        if not isinstance(out, dict):
            continue
        if "verdict" not in out and "indicators_matched" not in out:
            continue
        # Was this act an invocation of the specific unit being audited?
        try:
            cf = runtime.code.get_for_audit(act.compiled_form_id)
            if cf.source_unit != unit_cid:
                continue
        except Exception:
            continue
        observations += 1
        if out.get("system_health") == "system_crash":
            crashes += 1
    is_drifted = runtime.drift_monitor.is_drifted(unit_cid)
    MIN_OBSERVATIONS = 3
    if observations < MIN_OBSERVATIONS:
        return {
            "clear_for_production": False,
            "reason": "insufficient observations (" + str(observations) +
                      " / " + str(MIN_OBSERVATIONS) + " required)",
            "cohort_size": "canary",
            "observations": observations,
        }
    if is_drifted or crashes > 0:
        return {
            "clear_for_production": False,
            "reason": "canary detected anomalies (crashes=" + str(crashes) +
                      "; drift=" + str(is_drifted) + ")",
            "cohort_size": "canary",
            "observations": observations,
        }
    return {
        "clear_for_production": True,
        "cohort_size": "canary",
        "observations": observations,
    }
"""
