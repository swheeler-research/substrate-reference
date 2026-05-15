"""
The three functional sub-units of the advance payment decision.

Each unit takes its implementation_ref (the content_id of a state unit
holding the executable Python) and its credential_refs. The wilful-
inclusion check at compile time treats the implementation as a top-level
reference, so composing units that pull this unit in must also include
the implementation state unit in their state_refs (compose_refs takes
care of that).
"""

from substrate.primitives import ContractPattern, FunctionalUnit


def eligibility_check(*, implementation_ref, credential_refs, functional_refs=(), state_refs=()) -> FunctionalUnit:
    return FunctionalUnit(
        name="eligibility_check",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "household_income_pence": "int",
                "household_size": "int",
                "has_existing_claim": "bool",
            },
            "outputs": {"eligible": "bool", "reason": "str", "confidence": "float"},
            # Structured preconditions the compiler can analyse for type
            # mismatch against composing policies.
            "preconditions": [
                {"var": "household_income_pence", "op": "gte", "value": 0},
                {"var": "household_size", "op": "gte", "value": 1},
            ],
            # Eligibility check is a deterministic rule lookup; declared
            # confidence is high when the rule applies cleanly.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": "deterministic rule check; declared confidence 0.99 when applicable",
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=implementation_ref,
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )


def fraud_risk_assessment(*, implementation_ref, credential_refs, functional_refs=(), state_refs=()) -> FunctionalUnit:
    return FunctionalUnit(
        name="fraud_risk_assessment",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "inputs": {"applicant_id": "str", "claim_data": "dict"},
            "outputs": {"risk_score": "float", "confidence": "float"},
            "calibration": "ECE <= 0.05 on the DWP_2025_validation set",
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": "self-reported by classifier; ECE <= 0.05 on validation set",
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=implementation_ref,
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )


def identity_verification(*, implementation_ref, credential_refs, functional_refs=(), state_refs=()) -> FunctionalUnit:
    return FunctionalUnit(
        name="identity_verification",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"applicant_id": "str", "documents": "list"},
            "outputs": {"verified": "bool", "method": "str", "confidence": "float"},
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": "verification confidence varies by method (biometric > knowledge-based)",
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=implementation_ref,
        functional_refs=tuple(functional_refs),
        state_refs=tuple(state_refs),
        credential_refs=tuple(credential_refs),
    )
