"""
The advance payment decision: the composing unit, and the implementations
of every functional unit in the example.

Implementations are returned as `python_implementation(...)` state units;
they are content-addressed artefacts in the code archive. The composing
unit's implementation invokes its three sub-units through the runtime;
that source text is written here as a single Python string because the
runtime exec()s it at invocation time. This is the Phase 1 shape of
content-addressed implementations; later phases will use a sandboxed
executor or a constrained DSL.
"""

from substrate.implementations import python_implementation
from substrate.primitives import ContractPattern, FunctionalUnit


def advance_payment_decision(
    *,
    implementation_ref: str,
    constituent_unit_cids,
    state_cids,
    credential_cids,
) -> FunctionalUnit:
    """Build the composing unit.

    implementation_ref is the content_id of the StateUnit holding the
    composing unit's implementation (see make_advance_payment_implementation
    below).

    constituent_unit_cids: content_ids of the three sub-units, plus any
        other functional units pulled in transitively by compose_refs.
    state_cids: content_ids of every state unit reachable through the
        constituents (which includes the sub-units' implementations).
    credential_cids: content_ids of every credential reachable through
        the constituents.
    """
    return FunctionalUnit(
        name="advance_payment_decision",
        contract_pattern=ContractPattern.HYBRID,
        spec={
            "inputs": {
                "applicant_id": "str",
                "household_income_pence": "int",
                "household_size": "int",
                "requested_amount_pence": "int",
                "min_confidence": "float",
                "max_retention_days": "int",
                "allowed_purposes": "str",
            },
            "outputs": {
                "decision": "str",
                "amount_pence": "int",
                "rationale": "str",
                "confidence": "float",
            },
            # Composing-unit confidence: minimum across sub-unit confidences.
            # The substrate runtime captures eligibility_check, fraud_risk,
            # and identity_verification confidences during impl execution
            # and injects the minimum into this unit's output. The impl
            # does not compute confidence itself; the substrate does.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": (
                    "minimum across eligibility, fraud risk, and identity verification "
                    "confidences; AND-composition"
                ),
                "acceptance_band": [0.0, 1.0],
                "propagation": "minimum",
            },
        },
        implementation_ref=implementation_ref,
        functional_refs=tuple(constituent_unit_cids),
        state_refs=tuple(state_cids),
        credential_refs=tuple(credential_cids),
    )


def make_advance_payment_implementation(
    eligibility_source_cid: str,
    fraud_source_cid: str,
    identity_source_cid: str,
):
    """Build the composing unit's implementation as a content-addressed state unit.

    The source text closes over the three sub-units' source content_ids by
    template substitution. The result is a StateUnit holding the Python
    source; its content_id is what the composing unit's implementation_ref
    will point at.
    """
    source = f"""
def implementation(inputs, runtime, invoking_credential_id):
    applicant_id = inputs["applicant_id"]

    eligibility = runtime.invoke({eligibility_source_cid!r}, inputs, invoking_credential_id)
    if eligibility.__class__.__name__ != "Permit":
        return {{
            "decision": "refer_to_human",
            "amount_pence": 0,
            "rationale": "eligibility check refused: " + eligibility.rationale,
            "applicant_id": applicant_id,
            "refused_sub_act_id": eligibility.act_id,
        }}

    fraud = runtime.invoke({fraud_source_cid!r}, inputs, invoking_credential_id)
    if fraud.__class__.__name__ != "Permit":
        return {{
            "decision": "refer_to_human",
            "amount_pence": 0,
            "rationale": "fraud check refused: " + fraud.rationale,
            "applicant_id": applicant_id,
            "refused_sub_act_id": fraud.act_id,
        }}

    identity = runtime.invoke({identity_source_cid!r}, inputs, invoking_credential_id)
    if identity.__class__.__name__ != "Permit":
        return {{
            "decision": "refer_to_human",
            "amount_pence": 0,
            "rationale": "identity check refused: " + identity.rationale,
            "applicant_id": applicant_id,
            "refused_sub_act_id": identity.act_id,
        }}

    requested = inputs["requested_amount_pence"]
    income = inputs["household_income_pence"]
    if requested > 10000 and income < 100000:
        decision = "refer_to_human"
        amount = 0
        rationale = "amount above 100 GBP with very low household income; manual review"
    else:
        decision = "approved"
        amount = requested
        rationale = "all sub-unit checks permitted; within automated approval envelope"

    return {{
        "decision": decision,
        "amount_pence": amount,
        "rationale": rationale,
        "applicant_id": applicant_id,
        "sub_act_ids": [eligibility.act_id, fraud.act_id, identity.act_id],
    }}
"""
    return python_implementation(source, name="advance_payment_impl")


def trivial_eligibility_impl():
    return python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    eligible = inputs['household_income_pence'] < 200000\n"
        "    return {'eligible': eligible,\n"
        "            'reason': 'below_income_threshold' if eligible else 'above_threshold',\n"
        "            'confidence': 0.99 if eligible else 0.99}\n",
        name="eligibility_impl",
    )


def trivial_fraud_impl():
    return python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'risk_score': 0.05, 'confidence': inputs.get('min_confidence', 0.95)}\n",
        name="fraud_impl",
    )


def trivial_identity_impl():
    return python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    return {'verified': True, 'method': 'stub', 'confidence': 0.97}\n",
        name="identity_impl",
    )
