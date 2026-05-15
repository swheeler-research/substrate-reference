"""
Implementation source strings for the 737 MAX demonstration.
"""


# ===========================================================================
# Boeing (vendor) — MCAS implementations
# ===========================================================================

# v1: the original MCAS. Single AOA sensor. No pilot-override path.
# Returns a nose-down command whenever the (single) sensor reads above
# a threshold. The architectural failure surfaces at the unit's
# credential_refs: this unit's authority chain does not include a
# pilot-override credential. The substrate's certification policy
# refuses to admit it.
MCAS_V1_SINGLE_SENSOR = """
def implementation(inputs, runtime, invoking_credential_id):
    aoa_left = inputs.get("aoa_left_degrees")
    # BUG: only reads left sensor. Right sensor's value is ignored.
    if aoa_left is None:
        raise Exception("aoa_left_degrees not provided")
    if aoa_left > 12.0:
        return {
            "command": "nose_down",
            "trim_units": 2.5,
            "sensors_used": ["aoa_left"],
            "pilot_notified": False,
        }
    return {
        "command": "no_action",
        "sensors_used": ["aoa_left"],
        "pilot_notified": False,
    }
"""


# v2: the revised MCAS. Reads both sensors. Confidence is declared in
# the unit's spec (produces=True, calibration based on sensor agreement)
# and computed on each invocation. When sensor disagreement is severe
# (confidence drops below 0.4 in this stylised model), refuse and defer
# to the pilot. The substrate-native confidence is what makes the
# refer-to-human signal structurally legible: the act records the
# confidence value at refusal time. Includes the pilot-override
# credential in its authority chain so certification policies permit it.
MCAS_V2_DUAL_SENSOR = """
def implementation(inputs, runtime, invoking_credential_id):
    aoa_left = inputs.get("aoa_left_degrees")
    aoa_right = inputs.get("aoa_right_degrees")
    if aoa_left is None or aoa_right is None:
        raise Exception("both aoa_left_degrees and aoa_right_degrees required")
    # Sensor-agreement-based confidence. 0 degrees disagreement -> 1.0;
    # 5+ degrees -> 0. The calibration claim is declared in the unit's
    # spec (see run.py).
    delta = abs(aoa_left - aoa_right)
    confidence = max(0.0, 1.0 - delta / 5.0)
    if confidence < 0.4:
        raise Exception(
            "AOA sensor disagreement: left=" + str(aoa_left) +
            ", right=" + str(aoa_right) +
            "; agreement_confidence=" + format(confidence, ".3f") +
            " below 0.40 actuation threshold; refusing to act; pilot override required"
        )
    aoa = (aoa_left + aoa_right) / 2.0
    if aoa > 12.0:
        return {
            "command": "nose_down",
            "trim_units": 0.6,  # less aggressive than v1
            "sensors_used": ["aoa_left", "aoa_right"],
            "pilot_notified": True,
            "confidence": confidence,
        }
    return {
        "command": "no_action",
        "sensors_used": ["aoa_left", "aoa_right"],
        "pilot_notified": True,
        "confidence": confidence,
    }
"""


# ===========================================================================
# FAA (certifier) — certification policies
# ===========================================================================

# Certification policy: refuses to admit a unit whose authority chain
# does not include a pilot-override credential. Architecturally, this
# means the unit's content explicitly references the pilot's authority
# to override; absent that reference, the unit cannot be certified.
PILOT_OVERRIDE_REQUIRED_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    pilot_credential_in_authority = inputs.get("pilot_credential_in_authority_chain", False)
    if not pilot_credential_in_authority:
        raise Exception(
            "certification refused: unit's authority chain does not include a "
            "pilot-override credential. Flight-control automation must structurally "
            "recognise pilot authority to override."
        )
    return {}
"""


# Certification policy: requires multi-sensor input declared in the unit's
# spec. Refuses if the unit's spec declares dependency on a single sensor
# for a safety-critical function.
MULTI_SENSOR_REQUIRED_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    sensors = inputs.get("sensors_declared", [])
    if len(sensors) < 2:
        raise Exception(
            "certification refused: safety-critical unit declares only " +
            str(len(sensors)) + " sensor(s) (" + repr(sensors) + "). " +
            "Multi-sensor redundancy required for flight-control automation."
        )
    return {}
"""


# ===========================================================================
# FAA (certifier) — the certification unit
# ===========================================================================

# Certification is its own substrate pattern, parallel to the backtest
# pattern. A backtest unit inspects ledger history; a certification unit
# inspects a candidate unit's structure. Neither is a new primitive;
# both are regular functional units that examine substrate artefacts and
# refuse.
#
# Certification cannot be done by binding the cert policies to the MCAS
# units as runtime policy_refs: runtime policies evaluate against an
# invocation's inputs (a flight's AOA readings), not against the unit's
# declared structure. The cert policies gate the unit's STRUCTURE (its
# declared sensor set, its authority chain). So certification is a unit
# that fetches the candidate, extracts its structure, and invokes the
# FAA's cert policy units as sub-units with the extracted structure as
# inputs. The candidate is a runtime input, not a compile-time reference
# (same as the backtest's target unit).
#
# certify_mcas is compiled under the cooperative substrate's quorum
# custodian: the certification unit itself requires Boeing + FAA +
# airlines to jointly witness. No single party can certify alone.
CERTIFY_MCAS = """
def implementation(inputs, runtime, invoking_credential_id):
    candidate_id = inputs["candidate_unit_id"]
    pilot_credential_id = inputs["pilot_credential_id"]
    multi_sensor_policy_id = inputs["multi_sensor_policy_id"]
    pilot_override_policy_id = inputs["pilot_override_policy_id"]

    # Fetch the candidate unit and inspect its declared structure.
    candidate = runtime.code.get_for_audit(candidate_id)
    spec = candidate.spec if isinstance(candidate.spec, dict) else {}
    sensors_declared = spec.get("sensors_declared", [])
    pilot_in_chain = pilot_credential_id in candidate.credential_refs

    # Invoke the FAA's certification policy units as sub-units. Each is a
    # functional unit; each sub-invocation is a ledger act. A policy that
    # refuses raises; the refusal propagates as a Refuse result, and this
    # certification act becomes a refusal on the FAA's ledger.
    sensor_check = runtime.invoke(
        multi_sensor_policy_id,
        {"sensors_declared": sensors_declared},
        invoking_credential_id,
    )
    if sensor_check.__class__.__name__ != "Permit":
        raise Exception(
            "certification refused by multi_sensor_required_policy: " +
            sensor_check.rationale
        )

    pilot_check = runtime.invoke(
        pilot_override_policy_id,
        {"pilot_credential_in_authority_chain": pilot_in_chain},
        invoking_credential_id,
    )
    if pilot_check.__class__.__name__ != "Permit":
        raise Exception(
            "certification refused by pilot_override_required_policy: " +
            pilot_check.rationale
        )

    return {
        "certified": True,
        "candidate_unit_id": candidate_id,
        "sensors_declared": sensors_declared,
        "pilot_override_credential_present": pilot_in_chain,
        "sensor_policy_act_id": sensor_check.act_id,
        "pilot_policy_act_id": pilot_check.act_id,
    }
"""


# ===========================================================================
# Cross-fleet drift propagation
# ===========================================================================

# Airline-side: invokes the in-flight MCAS unit and records observations.
# Returns the observation count and whether MCAS has drifted (per the
# unit's behaviour-characterised drift criteria).
FLEET_OBSERVATION_REPORT = """
def implementation(inputs, runtime, invoking_credential_id):
    mcas_unit_cid = inputs["mcas_unit_cid"]
    observations = 0
    nose_down_count = 0
    for act in runtime.ledger:
        if act.verdict != "permit":
            continue
        try:
            cf = runtime.code.get_for_audit(act.compiled_form_id)
        except Exception:
            continue
        if cf.source_unit != mcas_unit_cid:
            continue
        out = act.output_or_rationale
        if not isinstance(out, dict):
            continue
        observations += 1
        if out.get("command") == "nose_down":
            nose_down_count += 1
    return {
        "mcas_unit_cid": mcas_unit_cid,
        "observations": observations,
        "nose_down_count": nose_down_count,
        "nose_down_rate": (nose_down_count / observations) if observations else 0.0,
        "is_drifted": runtime.drift_monitor.is_drifted(mcas_unit_cid),
    }
"""
