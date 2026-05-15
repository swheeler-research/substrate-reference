"""
Implementation source strings for the Horizon demonstration units.

Kept separate from run.py so the orchestration narrative stays readable;
these are reference material the reader can inspect when they want to
see exactly what each unit does.
"""


# ===========================================================================
# Post Office side
# ===========================================================================

RECORD_TRANSACTION = """
def implementation(inputs, runtime, invoking_credential_id):
    return {
        "branch_id": inputs["branch_id"],
        "amount": inputs["amount"],
        "type": inputs["type"],
        "recorded_by": invoking_credential_id,
    }
"""


MODIFY_BRANCH_ACCOUNT = """
def implementation(inputs, runtime, invoking_credential_id):
    return {
        "branch_id": inputs["branch_id"],
        "amount": inputs["amount"],
        "type": inputs["type"],
        "modified_by": invoking_credential_id,
        "note": "back-office adjustment",
    }
"""


# v1: BUGGY. Treats credit_reversal as a debit; under correct accounting
# a credit_reversal cancels a previously-issued credit and nets to zero.
BALANCE_CHECK_V1_BUGGY = """
def implementation(inputs, runtime, invoking_credential_id):
    branch_id = inputs["branch_id"]
    balance = 0
    for act in runtime.ledger:
        if act.verdict != "permit":
            continue
        out = act.output_or_rationale
        if not isinstance(out, dict):
            continue
        if out.get("branch_id") != branch_id:
            continue
        t = out.get("type")
        amt = out.get("amount", 0)
        if t == "sale":
            balance += amt
        elif t == "refund":
            balance -= amt
        elif t == "credit_reversal":
            balance -= amt  # BUG: should be no-op
    return {"branch_id": branch_id, "balance": balance, "implementation": "v1"}
"""


# v2: FIXED. credit_reversal nets to zero.
BALANCE_CHECK_V2_FIXED = """
def implementation(inputs, runtime, invoking_credential_id):
    branch_id = inputs["branch_id"]
    balance = 0
    for act in runtime.ledger:
        if act.verdict != "permit":
            continue
        out = act.output_or_rationale
        if not isinstance(out, dict):
            continue
        if out.get("branch_id") != branch_id:
            continue
        t = out.get("type")
        amt = out.get("amount", 0)
        if t == "sale":
            balance += amt
        elif t == "refund":
            balance -= amt
        elif t == "credit_reversal":
            pass  # FIXED: credit_reversal nets to zero
    return {"branch_id": branch_id, "balance": balance, "implementation": "v2"}
"""


# audit_branch: exposed to the Court of Appeals via the cooperative
# substrate. Returns serialised acts for the given branch so the Court's
# investigation can analyse them. The invocation itself lands on the
# Post Office's ledger (because the unit runs on the Post Office's
# runtime), recording WHO audited WHAT and WHEN — so the audit is itself
# auditable.
AUDIT_BRANCH = """
def implementation(inputs, runtime, invoking_credential_id):
    branch_id = inputs["branch_id"]
    acts = []
    for act in runtime.ledger:
        # Administrative acts (deprecations, supersessions, revocations)
        # are always included so the auditor can see operator-level
        # actions affecting this branch's processing.
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
        # Invocation acts: include only those whose output references
        # this branch.
        out = act.output_or_rationale
        if isinstance(out, dict) and out.get("branch_id") == branch_id:
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
        "branch_id": branch_id,
        "acts": acts,
        "ledger_length_at_audit": len(runtime.ledger),
    }
"""


# ===========================================================================
# Court of Appeals side
# ===========================================================================

# investigate_branch: invoked by the Court auditor. Calls the Post Office's
# audit_branch unit cross-operator via the cooperative substrate. Analyses
# the returned data and produces a forensic report recorded on the Court's
# own ledger.
INVESTIGATE_BRANCH = """
def implementation(inputs, runtime, invoking_credential_id):
    branch_id = inputs["branch_id"]
    po_operator_id = inputs["post_office_operator_id"]
    audit_unit_id = inputs["audit_unit_id"]

    # Cross-operator: invoke the Post Office's audit unit through the
    # cooperative substrate. The cooperative substrate authorises this
    # credential to be delegated under the Post Office's audit unit.
    result = runtime.invoke_in(po_operator_id, audit_unit_id,
                                {"branch_id": branch_id},
                                invoking_credential_id)
    if result.__class__.__name__ != "Permit":
        return {
            "branch_id": branch_id,
            "verdict": "audit_refused_by_post_office",
            "rationale": result.rationale,
        }

    acts = result.output["acts"]
    ledger_len = result.output["ledger_length_at_audit"]

    # Group by invoking credential. Each credential's totals are
    # independently attributable; the Court can see who did what.
    by_credential = {}
    balance_checks = []
    administrative_acts = []
    for a in acts:
        cred = a["invoking_credential_id"]
        if a["kind"] == "administrative":
            administrative_acts.append(a)
            continue
        out = a.get("output", {})
        if not isinstance(out, dict):
            continue
        t = out.get("type")
        if t in ("sale", "refund", "credit_reversal"):
            bucket = by_credential.setdefault(cred, {"sale": 0, "refund": 0, "credit_reversal": 0, "count": 0})
            bucket[t] += out.get("amount", 0)
            bucket["count"] += 1
        elif "balance" in out:
            balance_checks.append({
                "by": cred,
                "balance": out["balance"],
                "implementation": out.get("implementation", "?"),
                "compiled_form_id": a["compiled_form_id"],
            })

    return {
        "branch_id": branch_id,
        "verdict": "audit_completed",
        "po_audit_act_id": result.act_id,
        "ledger_length_at_audit": ledger_len,
        "by_credential": by_credential,
        "balance_checks": balance_checks,
        "administrative_acts": administrative_acts,
    }
"""
