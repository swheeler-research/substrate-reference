"""
Implementation source strings for the Five Eyes / mass surveillance
demonstration.

All stylised. No claim about specific real systems or operations.
"""


# ===========================================================================
# Agency-side: query collected data
# ===========================================================================

# A stylised query against collected metadata. Returns matching records.
# Inputs include target_subject and target_jurisdiction; the substrate's
# policies on the unit will refuse queries that violate scope.
QUERY_COLLECTED_METADATA = """
def implementation(inputs, runtime, invoking_credential_id):
    subject = inputs["target_subject"]
    jurisdiction = inputs.get("target_jurisdiction", "")
    # Stub: returns a synthetic result. The architectural point is not
    # the data but the policies that gate the query.
    return {
        "query_id": inputs.get("query_id", "q_001"),
        "target_subject": subject,
        "target_jurisdiction": jurisdiction,
        "records_returned": 17,
        "queried_by": invoking_credential_id,
        "executed_in": inputs.get("collecting_agency", "?"),
    }
"""


# ===========================================================================
# Policy: jurisdiction scope
# ===========================================================================

# Each agency is structurally prohibited from querying its own citizens
# (the substrate's analogue of the legal restrictions). The policy
# refuses queries where the target's jurisdiction matches the executing
# agency's jurisdiction WITHOUT cooperative-substrate authorisation.
JURISDICTION_SCOPE_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    target_jurisdiction = inputs.get("target_jurisdiction", "")
    executing_agency = inputs.get("collecting_agency", "")
    cooperative_authorisation = inputs.get("cooperative_authorisation_credential", "")
    # If the target's jurisdiction matches the executing agency's,
    # the query is structurally restricted by domestic law.
    if target_jurisdiction == executing_agency:
        if not cooperative_authorisation:
            raise Exception(
                "jurisdiction_scope_policy refuses: " + executing_agency +
                " cannot query " + target_jurisdiction + " (own jurisdiction) " +
                "without cooperative-substrate authorisation from a partner agency"
            )
        # The authorisation is a credential, resolved here. Accepting any
        # non-empty string, which is what this policy did before, let the
        # agency authorise its own self-jurisdiction query by typing a value.
        resolved = runtime.resolve_credential(cooperative_authorisation)
        if not resolved.valid:
            raise Exception(
                "jurisdiction_scope_policy refuses: cooperative authorisation " +
                cooperative_authorisation[:12] + " is " + resolved.status
            )
        if not resolved.bears("cooperative:partner_authorisation"):
            raise Exception(
                "jurisdiction_scope_policy refuses: credential " +
                cooperative_authorisation[:12] + " does not bear partner "
                "authorisation for a self-jurisdiction query"
            )
        if invoking_credential_id in resolved.authority_chain:
            raise Exception(
                "jurisdiction_scope_policy refuses: the querying credential is "
                "in the authorisation's own chain, so the agency authorised itself"
            )
    return {}
"""


# ===========================================================================
# Policy: query justification
# ===========================================================================

# Every query must declare a justification credential (referencing a
# specific court order, FISC warrant, parliamentary authorisation, etc).
# The substrate makes the justification a credential whose content_id
# is recorded with the query.
JUSTIFICATION_REQUIRED_POLICY = """
def implementation(inputs, runtime, invoking_credential_id):
    # The policy resolves the justification through the runtime. Requiring
    # that the caller supply a non-empty string, which is what this policy
    # did before, requires nothing: an analyst who must declare a warrant
    # and may declare any value has not been constrained. A justification is
    # a credential issued by a judicial or parliamentary authority, and the
    # question the policy asks is whether this one is.
    justification_id = inputs.get("justification_credential_id", "")
    if not justification_id:
        raise Exception(
            "justification_required_policy refuses: query must declare a "
            "justification_credential_id (specific court order, warrant, or "
            "parliamentary authorisation). Anonymous queries are not permitted."
        )

    justification = runtime.resolve_credential(justification_id)
    if not justification.valid:
        raise Exception(
            "justification_required_policy refuses: justification credential " +
            justification_id[:12] + " is " + justification.status
        )

    # The authorising authority, named in the justification's own content and
    # therefore in its content identity. An expired warrant is revoked and a
    # relabelled one is a different credential.
    authorising_root = inputs.get("authorising_authority_credential_id", "")
    if authorising_root and not justification.descends_from(authorising_root):
        raise Exception(
            "justification_required_policy refuses: justification " +
            justification_id[:12] + " does not derive from the authorising "
            "judicial or parliamentary authority"
        )

    # The querying analyst must not be in the justification's own ancestry.
    # An agency that issues its own warrants has not been authorised by
    # anyone, and this is the whole substance of the oversight claim.
    if justification_id == invoking_credential_id:
        raise Exception(
            "justification_required_policy refuses: the invoking credential "
            "cannot be its own justification"
        )
    if invoking_credential_id in justification.authority_chain:
        if invoking_credential_id != authorising_root:
            raise Exception(
                "justification_required_policy refuses: the querying "
                "credential is in the justification's authority chain, so the "
                "justification is self-issued"
            )
    return {}
"""


# ===========================================================================
# Cross-jurisdictional gate (cooperative substrate)
# ===========================================================================

# When agency A wants to query agency B's data on agency A's citizens
# (circumventing A's domestic restrictions), the query must invoke
# this cross-operator gate. The gate refuses unless the cooperative
# substrate's bilateral arrangement permits the specific query category.
COOPERATIVE_CROSS_QUERY_GATE = """
def implementation(inputs, runtime, invoking_credential_id):
    target_jurisdiction = inputs.get("target_jurisdiction", "")
    requesting_agency = inputs.get("requesting_agency", "")
    query_category = inputs.get("query_category", "")
    # Stylised permission table: which agency pairs may exchange which
    # query categories under the cooperative substrate. A real
    # cooperative substrate would carry these as credential refs to
    # specific authorising statutes / treaties.
    PERMITTED_QUERIES = {
        # (requesting, target): set of permitted categories
        ("agency_a", "agency_b"): {"counter_terrorism_subjects"},
        ("agency_b", "agency_a"): {"counter_terrorism_subjects"},
        # Note: bulk_metadata is NOT in the permitted set for any pair.
    }
    permitted = PERMITTED_QUERIES.get((requesting_agency, target_jurisdiction), set())
    if query_category not in permitted:
        raise Exception(
            "cooperative_cross_query_gate refuses: " + requesting_agency +
            " querying " + target_jurisdiction + "'s data under category " +
            repr(query_category) + " is not in the cooperative substrate's " +
            "permitted query set " + repr(sorted(permitted))
        )
    return {
        "authorised": True,
        "requesting_agency": requesting_agency,
        "target_jurisdiction": target_jurisdiction,
        "query_category": query_category,
    }
"""


# ===========================================================================
# Oversight audit (cross-operator)
# ===========================================================================

# Parliamentary oversight committee invokes this on each agency's
# substrate. Returns query records visible under the cooperative
# substrate's audit terms.
AGENCY_AUDIT = """
def implementation(inputs, runtime, invoking_credential_id):
    # All query acts in this agency's ledger over the audit period.
    period_start_act = inputs.get("period_start_act", 0)
    queries = []
    administrative = []
    for i, act in enumerate(runtime.ledger):
        if i < period_start_act:
            continue
        if act.kind == "administrative":
            administrative.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "action": act.inputs.get("action"),
                "by": act.invoking_credential_id,
            })
            continue
        out = act.output_or_rationale
        ins = act.inputs if isinstance(act.inputs, dict) else {}
        # Only surface query-related acts.
        if isinstance(out, dict) and "target_subject" in out:
            queries.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "by": act.invoking_credential_id,
                "target_subject": out.get("target_subject"),
                "target_jurisdiction": out.get("target_jurisdiction"),
                "records_returned": out.get("records_returned"),
                "executed_in": out.get("executed_in"),
            })
        elif ins.get("target_subject"):
            # Refused queries (output_or_rationale is a string rationale).
            queries.append({
                "act_id": act.content_id(),
                "verdict": act.verdict,
                "by": act.invoking_credential_id,
                "target_subject": ins.get("target_subject"),
                "target_jurisdiction": ins.get("target_jurisdiction"),
                "rationale": out if isinstance(out, str) else "(structured)",
            })
    return {
        "queries": queries,
        "administrative": administrative,
        "ledger_length": len(runtime.ledger),
    }
"""


INVESTIGATE_AGENCY = """
def implementation(inputs, runtime, invoking_credential_id):
    agency_op_id = inputs["agency_operator_id"]
    audit_unit_id = inputs["audit_unit_id"]
    result = runtime.invoke_in(agency_op_id, audit_unit_id,
                                {"period_start_act": 0},
                                invoking_credential_id)
    if result.__class__.__name__ != "Permit":
        return {"verdict": "audit_refused", "rationale": result.rationale}
    data = result.output
    queries = data["queries"]
    refused = [q for q in queries if q["verdict"] != "permit"]
    permitted = [q for q in queries if q["verdict"] == "permit"]
    return {
        "verdict": "audit_completed",
        "agency_audit_act_id": result.act_id,
        "ledger_length": data["ledger_length"],
        "permitted_queries": permitted,
        "refused_queries": refused,
        "administrative_acts": data["administrative"],
    }
"""
