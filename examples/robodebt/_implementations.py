"""
Implementation source strings for the Robodebt demonstration units.
"""


# ===========================================================================
# ATO side
# ===========================================================================

FETCH_ANNUAL_INCOME = """
def implementation(inputs, runtime, invoking_credential_id):
    # Stub: looks up a fixed table of synthetic claimants. A real ATO
    # would query its tax records system. The point is that the data is
    # fetched cross-operator, with the fetch itself recorded on both
    # operators' ledgers.
    table = {
        "sarah": {"annual_income_aud": 60000, "monthly_breakdown": [5000]*12},
        "james": {"annual_income_aud": 30000,
                  "monthly_breakdown": [0, 0, 8000, 0, 0, 12000, 0, 0, 6000, 0, 0, 4000]},
    }
    claimant = inputs["claimant_id"]
    if claimant not in table:
        raise Exception("no income record for claimant " + claimant)
    record = table[claimant]
    return {
        "claimant_id": claimant,
        "annual_income_aud": record["annual_income_aud"],
        "monthly_breakdown": record["monthly_breakdown"],
        "fetched_from": "ATO",
    }
"""


# ===========================================================================
# Services Australia side — v1 (original Robodebt algorithm)
# ===========================================================================

# v1: ROBODEBT-STYLE. Averages annual income across fortnights without
# checking whether the assumption (income evenly distributed) holds.
# Declares NO structural preconditions — this is the architectural
# failure: the algorithm runs regardless of whether it applies.
CALCULATE_OVERPAYMENT_V1_ROBODEBT = """
def implementation(inputs, runtime, invoking_credential_id):
    annual = inputs["annual_income_aud"]
    reported_fortnightly = inputs["reported_fortnightly_income_aud"]
    # 26 fortnights per year in the Robodebt model.
    averaged_fortnightly = annual / 26.0
    # For each of the 26 fortnights, the discrepancy between averaged
    # (assumed) and reported (actual) is treated as overpayment.
    overpayment = 0
    for reported in reported_fortnightly:
        if averaged_fortnightly > reported:
            # Welfare was paid based on the lower reported income.
            # Treat the difference as overpaid welfare to recover.
            # (Rate: 0.5 dollar of welfare recovered per dollar of "underreport".)
            overpayment += (averaged_fortnightly - reported) * 0.5
    return {
        "claimant_id": inputs["claimant_id"],
        "algorithm": "v1_robodebt",
        "averaged_fortnightly": round(averaged_fortnightly, 2),
        "alleged_overpayment_aud": round(overpayment, 2),
        "method": "income_averaging",
        "preconditions_checked": False,
    }
"""


# ===========================================================================
# Services Australia side — v2 (algorithm with preconditions and
# refer-to-human policy enforcement)
# ===========================================================================

# v2: same averaging logic, but only runs after the income-variability
# precondition policy has permitted. The unit is otherwise identical;
# the architectural difference is that v2 references a policy that
# refuses the invocation when the income is too variable for averaging
# to be valid.
CALCULATE_OVERPAYMENT_V2_PRINCIPLED = """
def implementation(inputs, runtime, invoking_credential_id):
    annual = inputs["annual_income_aud"]
    reported_fortnightly = inputs["reported_fortnightly_income_aud"]
    averaged_fortnightly = annual / 26.0
    overpayment = 0
    for reported in reported_fortnightly:
        if averaged_fortnightly > reported:
            overpayment += (averaged_fortnightly - reported) * 0.5
    return {
        "claimant_id": inputs["claimant_id"],
        "algorithm": "v2_principled",
        "averaged_fortnightly": round(averaged_fortnightly, 2),
        "alleged_overpayment_aud": round(overpayment, 2),
        "method": "income_averaging",
        "preconditions_checked": True,
    }
"""


# A policy unit attached to v2. Computes the coefficient of variation
# of the monthly income breakdown; refuses if above 0.30 (the algorithm's
# averaging assumption fails). Refusal carries a clear rationale naming
# the variability and the threshold.
INCOME_VARIABILITY_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    monthly = inputs.get("monthly_breakdown", [])
    if not monthly:
        raise Exception("income variability policy requires monthly_breakdown in inputs")
    n = len(monthly)
    mean = sum(monthly) / n
    if mean == 0:
        # Income is zero throughout; variability check vacuously fails
        # (the algorithm would compute zero overpayment anyway, but the
        # policy is the principled point: refuse to compute when the
        # data is statistically inappropriate for the method).
        raise Exception("zero mean income; averaging method not applicable")
    variance = sum((x - mean) ** 2 for x in monthly) / n
    cv = (variance ** 0.5) / mean
    THRESHOLD = 0.30
    if cv > THRESHOLD:
        raise Exception(
            "income variability (coefficient of variation = " + format(cv, ".2f") +
            ") exceeds policy threshold " + format(THRESHOLD, ".2f") +
            "; income-averaging assumption violated; manual review required"
        )
    return {}
"""


# A "must be authorised by Parliament" policy. Returns immediately;
# its architectural significance is that its credential_refs trace to
# an Act of Parliament credential, so the algorithm's authority chain
# is grounded. (The actual scheme had a real authorisation gap; the
# Federal Court ruled it unlawful in 2019.)
LEGISLATIVE_AUTHORISATION_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    # The policy itself is satisfied trivially; its significance is in
    # WHICH credential brings it into binding (an Act of Parliament
    # credential). If the algorithm is composed under a binding credential
    # that does not trace to a legislative source, the substrate would
    # have refused at compile-at-commit (the authority chain would not
    # include Parliament).
    return {}
"""


# ===========================================================================
# Commonwealth Ombudsman side — cross-operator audit
# ===========================================================================

# Services Australia exposes an audit unit. The Ombudsman invokes it
# cross-operator through the cooperative substrate.
AUDIT_CLAIMANT_CASE = """
def implementation(inputs, runtime, invoking_credential_id):
    claimant = inputs["claimant_id"]
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
                "compiled_form_id": act.compiled_form_id,
            })
            continue
        # Filter for acts relating to this claimant.
        out = act.output_or_rationale
        if isinstance(out, dict) and out.get("claimant_id") == claimant:
            acts.append({
                "act_id": act.content_id(),
                "kind": "invocation",
                "invoking_credential_id": act.invoking_credential_id,
                "verdict": act.verdict,
                "inputs": act.inputs,
                "output": out,
                "compiled_form_id": act.compiled_form_id,
            })
        elif isinstance(act.inputs, dict) and act.inputs.get("claimant_id") == claimant:
            # Catch refused acts whose output_or_rationale is a string
            # rationale rather than a dict.
            acts.append({
                "act_id": act.content_id(),
                "kind": "invocation",
                "invoking_credential_id": act.invoking_credential_id,
                "verdict": act.verdict,
                "inputs": act.inputs,
                "output": out,
                "compiled_form_id": act.compiled_form_id,
            })
    return {
        "claimant_id": claimant,
        "acts": acts,
        "ledger_length_at_audit": len(runtime.ledger),
    }
"""


# The Ombudsman's investigation unit. Invokes the audit unit cross-operator
# and produces a forensic report on the Ombudsman's own ledger.
INVESTIGATE_CLAIMANT_CASE = """
def implementation(inputs, runtime, invoking_credential_id):
    claimant = inputs["claimant_id"]
    services_op_id = inputs["services_australia_operator_id"]
    audit_unit_id = inputs["audit_unit_id"]

    result = runtime.invoke_in(services_op_id, audit_unit_id,
                                {"claimant_id": claimant},
                                invoking_credential_id)
    if result.__class__.__name__ != "Permit":
        return {
            "claimant_id": claimant,
            "verdict": "audit_refused",
            "rationale": result.rationale,
        }

    acts = result.output["acts"]
    debt_calculations = []
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
        elif isinstance(out, dict) and "alleged_overpayment_aud" in out:
            debt_calculations.append({
                "act_id": a["act_id"],
                "by": a["invoking_credential_id"],
                "algorithm": out.get("algorithm"),
                "alleged_overpayment_aud": out.get("alleged_overpayment_aud"),
                "preconditions_checked": out.get("preconditions_checked"),
            })

    return {
        "claimant_id": claimant,
        "verdict": "audit_completed",
        "services_audit_act_id": result.act_id,
        "ledger_length_at_audit": result.output["ledger_length_at_audit"],
        "debt_calculations": debt_calculations,
        "refusals": refusals,
    }
"""
