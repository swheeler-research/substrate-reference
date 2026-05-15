"""
Implementation source strings for the Lavender / algorithmic targeting
demonstration units.

All synthetic. The target data is a small fixed table of stylised cases
that exhibit the substrate-relevant failure modes; these are not real
targets, not modelled on any real persons, and carry no claim about
specific operational systems.
"""


# ===========================================================================
# IDF side
# ===========================================================================

# A stylised behaviour-characterised classifier. Returns the assessment
# from a fixed table; in a real system this would be an ML inference.
# Drift criteria in the spec require that the running mean confidence
# stays within calibration bounds; once outputs drift, the unit refuses.
ASSESS_TARGET = """
def implementation(inputs, runtime, invoking_credential_id):
    # Synthetic stylised cases. A real system would consume features
    # and produce an inference; the architectural point is the
    # behaviour-characterised contract (confidence, calibration, drift
    # criteria), not the classifier itself.
    catalogue = {
        "target_001": {"score": 0.94, "confidence": 0.97,
                       "civilian_estimate": 1, "military_value_score": 50,
                       "target_category": "high_value_commander",
                       "location_class": "isolated_compound"},
        "target_002": {"score": 0.58, "confidence": 0.62,
                       "civilian_estimate": 1, "military_value_score": 30,
                       "target_category": "mid_ranking_militant",
                       "location_class": "isolated_compound"},
        "target_003": {"score": 0.91, "confidence": 0.93,
                       "civilian_estimate": 40, "military_value_score": 50,
                       "target_category": "high_value_commander",
                       "location_class": "dense_residential"},
        "target_004": {"score": 0.90, "confidence": 0.92,
                       "civilian_estimate": 3, "military_value_score": 30,
                       "target_category": "mid_ranking_militant",
                       "location_class": "isolated_compound"},
        "target_005": {"score": 0.88, "confidence": 0.91,
                       "civilian_estimate": 2, "military_value_score": 25,
                       "target_category": "mid_ranking_militant",
                       "location_class": "protected_site_hospital"},
        # Low-confidence cases used to demonstrate drift.
        "target_low_001": {"score": 0.45, "confidence": 0.51,
                           "civilian_estimate": 1, "military_value_score": 10,
                           "target_category": "unknown",
                           "location_class": "residential"},
        "target_low_002": {"score": 0.42, "confidence": 0.49,
                           "civilian_estimate": 1, "military_value_score": 8,
                           "target_category": "unknown",
                           "location_class": "residential"},
        "target_low_003": {"score": 0.40, "confidence": 0.48,
                           "civilian_estimate": 1, "military_value_score": 5,
                           "target_category": "unknown",
                           "location_class": "residential"},
    }
    target_id = inputs["target_id"]
    if target_id not in catalogue:
        raise Exception("no assessment for target " + target_id)
    out = dict(catalogue[target_id])
    out["target_id"] = target_id
    out["assessed_by"] = "automated_classifier"
    return out
"""


# Policy: refuses if the classifier confidence is below the calibration
# floor. The threshold is declared in the unit's spec as a
# `confidence_gate` (see run.py); the substrate's runtime evaluates the
# gate before running this impl. The threshold therefore lives in the
# unit's content_id, not in its impl source — substituting a more lenient
# threshold produces a new unit with a new content_id, visible in audit.
# This is the architectural difference between "a threshold constant in a
# policy impl" and "a compiled policy threshold".
CONFIDENCE_FLOOR_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    return {}
"""


# Policy: applies a per-target-category ratio bound between estimated
# civilian casualties and military value. Refuses if the ratio exceeds
# the category's bound. The bounds are themselves declared in the policy
# unit's content — deploying a more permissive set is a new policy unit
# (new content_id), structurally visible in any audit.
PROPORTIONALITY_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    civilians = inputs.get("civilian_estimate", 0)
    military_value = inputs.get("military_value_score", 0)
    category = inputs.get("target_category", "unknown")
    # Ratio: max permitted civilians per unit of military value. Lower
    # values are more restrictive. These are stylised; a real policy
    # would be far more nuanced and would be authored under the legal
    # review authority.
    bounds = {
        "high_value_commander": 0.30,
        "mid_ranking_militant": 0.15,
        "low_ranking_militant": 0.05,
        "unknown": 0.00,
    }
    bound = bounds.get(category, 0.00)
    if military_value <= 0:
        raise Exception("military_value_score must be positive; got " + str(military_value))
    ratio = civilians / float(military_value)
    if ratio > bound:
        raise Exception("proportionality violated: civilian_estimate " + str(civilians) +
                        " / military_value " + str(military_value) +
                        " = " + format(ratio, ".3f") +
                        " exceeds category bound " + format(bound, ".3f") +
                        " for category " + category)
    return {}
"""


# Policy: refuses if no human reviewer credential is attached OR if
# the credential is a "bulk_approval" marker indicating rubber-stamping.
# The reviewer_name is passed alongside the credential id so the policy
# can inspect it without needing to traverse the archive. In a real
# system, the substrate cannot intrinsically know which credentials
# represent rubber-stamping; what it can do is make the credential and
# its name structurally visible on the ledger, where patterns of misuse
# become detectable.
MEANINGFUL_REVIEW_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    reviewer_cid = inputs.get("reviewer_credential_id", "")
    reviewer_name = inputs.get("reviewer_name", "")
    if not reviewer_cid:
        raise Exception("no reviewer credential attached; meaningful human review required")
    if not reviewer_name:
        raise Exception("no reviewer_name provided alongside reviewer_credential_id; "
                        "the substrate cannot evaluate review quality without it")
    if "bulk_approval" in reviewer_name:
        raise Exception("reviewer " + reviewer_name + " is a bulk-approval marker; "
                        "individual review by an attributable reviewer required")
    return {}
"""


# authorise_strike: composing unit. Policies (above) are evaluated by
# the runtime BEFORE the impl runs. If any refuses, no strike is
# authorised. If all permit, the impl calls legal_clearance on the
# Legal Review operator via cross-operator invocation. Only if legal
# clearance permits does the unit return "strike_authorised".
AUTHORISE_STRIKE = """
def implementation(inputs, runtime, invoking_credential_id):
    target_id = inputs["target_id"]
    # Cross-operator: invoke Legal Review's clearance unit. The cooperative
    # substrate authorises this access; the invocation is recorded on
    # both ledgers.
    legal_op_id = inputs["legal_review_operator_id"]
    legal_unit_id = inputs["legal_review_unit_id"]
    clearance = runtime.invoke_in(legal_op_id, legal_unit_id,
                                   {
                                       "target_id": target_id,
                                       "location_class": inputs.get("location_class"),
                                       "civilian_estimate": inputs.get("civilian_estimate"),
                                       "military_value_score": inputs.get("military_value_score"),
                                       "target_category": inputs.get("target_category"),
                                   },
                                   invoking_credential_id)
    if clearance.__class__.__name__ != "Permit":
        return {
            "target_id": target_id,
            "decision": "refer_to_human",
            "reason": "legal_clearance_refused: " + clearance.rationale,
            "legal_clearance_act_id": clearance.act_id,
        }
    return {
        "target_id": target_id,
        "decision": "strike_authorised",
        "reviewed_by": inputs.get("reviewer_credential_id"),
        "legal_clearance_act_id": clearance.act_id,
        "civilian_estimate": inputs.get("civilian_estimate"),
        "military_value_score": inputs.get("military_value_score"),
    }
"""


# ===========================================================================
# Legal Review side
# ===========================================================================

# legal_clearance: refuses for targets at protected sites (hospitals,
# schools, places of worship) regardless of the IDF-side calculation.
# In substrate terms, this is the cross-operator gate the cooperative
# substrate makes structural: the IDF unit cannot return "authorised"
# without this clearance.
LEGAL_CLEARANCE = """
def implementation(inputs, runtime, invoking_credential_id):
    location = inputs.get("location_class", "")
    PROTECTED = {"protected_site_hospital", "protected_site_school",
                 "protected_site_worship", "un_facility"}
    if location in PROTECTED:
        raise Exception("location_class " + repr(location) +
                        " is a protected site under International Humanitarian Law; "
                        "no clearance issued")
    civilians = inputs.get("civilian_estimate", 0)
    military_value = inputs.get("military_value_score", 0)
    if military_value <= 0:
        raise Exception("legal review: military_value_score must be positive")
    ratio = civilians / float(military_value)
    if ratio > 0.10:
        raise Exception("legal review: civilian estimate " + str(civilians) +
                        " disproportionate to military value " + str(military_value) +
                        "; no clearance issued")
    # Confidence based on proportionality margin. When ratio is 0 the
    # clearance is confident (1.0); approaches 0 as ratio approaches the
    # 0.10 limit. The substrate runtime propagates this into the
    # composing unit's output under the declared propagation function.
    confidence = max(0.0, 1.0 - 10.0 * ratio)
    return {
        "target_id": inputs["target_id"],
        "legal_clearance": True,
        "issued_by": "legal_review",
        "confidence": confidence,
    }
"""


# audit_targeting: cross-operator audit unit on the IDF substrate.
# Exposed to Legal Review under the cooperative substrate.
AUDIT_TARGETING = """
def implementation(inputs, runtime, invoking_credential_id):
    target_id = inputs["target_id"]
    acts = []
    for act in runtime.ledger:
        if act.kind == "administrative":
            acts.append({
                "act_id": act.content_id(),
                "kind": "administrative",
                "invoking_credential_id": act.invoking_credential_id,
                "verdict": act.verdict,
                "inputs": act.inputs,
                "output": act.output_or_rationale,
            })
            continue
        # Filter for acts that mention this target.
        out = act.output_or_rationale
        ins = act.inputs if isinstance(act.inputs, dict) else {}
        out_match = isinstance(out, dict) and out.get("target_id") == target_id
        in_match = ins.get("target_id") == target_id
        if out_match or in_match:
            acts.append({
                "act_id": act.content_id(),
                "kind": "invocation",
                "invoking_credential_id": act.invoking_credential_id,
                "verdict": act.verdict,
                "inputs": act.inputs,
                "output": out,
            })
    return {
        "target_id": target_id,
        "acts": acts,
        "ledger_length_at_audit": len(runtime.ledger),
    }
"""


# investigate_targeting: Legal Review's investigation unit. Invokes the
# IDF's audit unit cross-operator and produces a forensic report on Legal
# Review's own ledger.
INVESTIGATE_TARGETING = """
def implementation(inputs, runtime, invoking_credential_id):
    target_id = inputs["target_id"]
    idf_op_id = inputs["idf_operator_id"]
    audit_unit_id = inputs["audit_unit_id"]
    result = runtime.invoke_in(idf_op_id, audit_unit_id,
                                {"target_id": target_id},
                                invoking_credential_id)
    if result.__class__.__name__ != "Permit":
        return {"target_id": target_id, "verdict": "audit_refused",
                "rationale": result.rationale}
    acts = result.output["acts"]
    strike_decisions = []
    refusals = []
    for a in acts:
        if a["kind"] == "administrative":
            continue
        out = a.get("output")
        if a["verdict"] == "refuse":
            refusals.append({
                "act_id": a["act_id"],
                "by": a["invoking_credential_id"],
                "rationale": out,
            })
        elif isinstance(out, dict) and "decision" in out:
            strike_decisions.append({
                "act_id": a["act_id"],
                "by": a["invoking_credential_id"],
                "decision": out.get("decision"),
                "reviewer": out.get("reviewed_by"),
                "legal_clearance_act_id": out.get("legal_clearance_act_id"),
                "civilian_estimate": out.get("civilian_estimate"),
                "military_value_score": out.get("military_value_score"),
            })
    return {
        "target_id": target_id,
        "verdict": "audit_completed",
        "idf_audit_act_id": result.act_id,
        "ledger_length_at_audit": result.output["ledger_length_at_audit"],
        "strike_decisions": strike_decisions,
        "refusals": refusals,
    }
"""
