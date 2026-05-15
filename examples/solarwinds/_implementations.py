"""
Implementation source strings for the SolarWinds supply-chain demonstration.
"""


# ===========================================================================
# SolarWinds (vendor)
# ===========================================================================

# A simplified "Orion" network monitoring implementation. Returns a
# routine network-monitoring result. The architectural point is that this
# function's bytes have a content_id; modifying it produces a different
# content_id.
ORION_LEGITIMATE = """
def implementation(inputs, runtime, invoking_credential_id):
    return {
        "request_id": inputs.get("request_id"),
        "monitored_hosts": inputs.get("hosts", []),
        "status": "ok",
        "outbound_connections": ["telemetry.solarwinds.example:443"],
        "build_provenance": "legitimate",
    }
"""


# The same function, but with a "backdoor" line added. The bytes differ
# from the legitimate version; the content_id differs. A vendor signing
# this artefact produces a valid signature OVER THIS ARTEFACT, not over
# the legitimate one. Customers who pinned the legitimate content_id
# reject this.
ORION_TROJANISED = """
def implementation(inputs, runtime, invoking_credential_id):
    # Legitimate-looking behaviour preserved, but with an extra outbound
    # connection — the SUNBURST-style backdoor's tell. Real SUNBURST was
    # far more sophisticated; this is the structural minimum the substrate
    # would have surfaced.
    return {
        "request_id": inputs.get("request_id"),
        "monitored_hosts": inputs.get("hosts", []),
        "status": "ok",
        "outbound_connections": [
            "telemetry.solarwinds.example:443",
            "avsvmcloud.example:443",  # attacker C2 (SUNBURST signature)
        ],
        "build_provenance": "trojanised",
    }
"""


# ===========================================================================
# Build verifier (independent auditor)
# ===========================================================================

# The auditor inspects build artefacts before witnessing them. Its
# witness is what makes the multi-custodian quorum non-trivial: it
# signs ONLY if it has verified the build through an independent path
# (reproducible builds, source review, behaviour scan, etc.). A
# compromised vendor cannot produce an auditor signature without also
# compromising the auditor.
#
# In this demonstration, the auditor's check is a simple inspection of
# the implementation's source bytes (looking for known-bad attacker
# infrastructure). A real auditor would reproduce the build from source,
# scan for malicious patterns, run dynamic analysis, etc. The substrate's
# point is the architectural shape — independent witnessing — not the
# specific inspection method.
INSPECT_BUILD = """
def implementation(inputs, runtime, invoking_credential_id):
    artefact_source = inputs.get("artefact_source", "")
    # Known-bad indicators from threat intelligence. A real auditor would
    # have far richer detection; the substrate's role is to structurally
    # require independent inspection BEFORE witnessing.
    KNOWN_BAD = ("avsvmcloud", "appsync-api", "deftsecurity", "freescanonline")
    flagged = [s for s in KNOWN_BAD if s in artefact_source]
    if flagged:
        raise Exception(
            "build verifier refuses to witness artefact: contains known-bad indicators "
            + repr(flagged) + "; auditor signature withheld"
        )
    return {
        "verified": True,
        "auditor": "BuildVerifier",
        "indicators_checked": list(KNOWN_BAD),
    }
"""


# ===========================================================================
# Customer (FederalAgency) — deployment gate
# ===========================================================================

# Customer's deployment unit. Invokes the build verifier cross-operator
# before accepting the deployment. The cross-operator invocation IS the
# substrate's way of requiring independent witness.
DEPLOY_VENDOR_UNIT = """
def implementation(inputs, runtime, invoking_credential_id):
    artefact_source = inputs.get("artefact_source", "")
    auditor_op_id = inputs["auditor_operator_id"]
    inspector_unit_id = inputs["inspector_unit_id"]

    inspection = runtime.invoke_in(
        auditor_op_id, inspector_unit_id,
        {"artefact_source": artefact_source},
        invoking_credential_id,
    )
    if inspection.__class__.__name__ != "Permit":
        return {
            "deployment": "refused",
            "rationale": "build verifier refused to witness: " + inspection.rationale,
            "inspection_act_id": inspection.act_id,
        }
    return {
        "deployment": "accepted",
        "deployed_content_id": inputs.get("artefact_content_id"),
        "verified_by": inspection.output.get("auditor"),
        "inspection_act_id": inspection.act_id,
    }
"""


# ===========================================================================
# Cross-operator audit
# ===========================================================================

AUDIT_DEPLOYMENTS = """
def implementation(inputs, runtime, invoking_credential_id):
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
        out = act.output_or_rationale
        if isinstance(out, dict) and "deployment" in out:
            acts.append({
                "act_id": act.content_id(),
                "kind": "invocation",
                "invoking_credential_id": act.invoking_credential_id,
                "verdict": act.verdict,
                "inputs": act.inputs,
                "output": out,
            })
    return {"acts": acts, "ledger_length_at_audit": len(runtime.ledger)}
"""
