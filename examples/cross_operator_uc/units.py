"""
The two cross-operator units: DWP's advance_payment_decision and
Home Office's right_to_reside_check.

Both reference the cooperative substrate in their credential_refs so
their authority chains include both operators' roots; this is what
permits a DWP credential to be delegated under a Home Office unit and
vice versa. The composing unit's implementation invokes the
right_to_reside_check via runtime.invoke_in(), which routes through the
federation to Home Office's runtime.
"""

from substrate.implementations import python_implementation
from substrate.primitives import ContractPattern, FunctionalUnit


def right_to_reside_impl() -> "StateUnit":
    """Home Office's RTR check implementation.

    The applicant must have an entry in a (notional) right-to-reside
    register. Phase 2-deepened: a simple check on the applicant_id; a
    real implementation would consult Home Office systems.

    Refuses by raising. Permits by returning a dict.
    """
    source = (
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    applicant_id = inputs.get('applicant_id', '')\n"
        "    # Stub: applicant_ids starting with 'eu_' have no RTR; others do.\n"
        "    if applicant_id.startswith('eu_'):\n"
        "        raise Exception('applicant ' + applicant_id + ' has no right to reside')\n"
        "    return {'right_to_reside': True, 'applicant_id': applicant_id, 'verified_by': 'home_office'}\n"
    )
    return python_implementation(source, name="right_to_reside_impl")


def right_to_reside_check(
    *,
    implementation_ref: str,
    cooperative_substrate_cid: str,
    parliament_cid: str,
    home_office_root_cid: str,
    dwp_root_cid: str,
) -> FunctionalUnit:
    """Home Office's RTR functional unit.

    Authority chain includes Parliament, Home Office root, DWP root,
    and the cooperative substrate (which transitively reaches both
    roots). Wilful inclusion requires every transitively reachable unit
    at the top level; since the cooperative substrate has both operator
    roots as parents, both must be listed here too.

    The presence of DWP root in the authority chain is precisely what
    enables DWP credentials (caseworker -> DWP root -> Parliament) to be
    delegated under this Home Office unit.
    """
    return FunctionalUnit(
        name="right_to_reside_check",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"applicant_id": "str"},
            "outputs": {"right_to_reside": "bool", "verified_by": "str"},
            "preconditions": [],
        },
        implementation_ref=implementation_ref,
        credential_refs=(
            parliament_cid,
            home_office_root_cid,
            dwp_root_cid,
            cooperative_substrate_cid,
        ),
    )


def advance_payment_impl(home_office_op_cid: str, rtr_unit_cid: str) -> "StateUnit":
    """DWP's advance_payment_decision implementation.

    Calls Home Office's right_to_reside_check via runtime.invoke_in()
    (cross-operator). If RTR refuses, advance payment refers to human.
    If RTR permits, applies DWP's local decision rule.
    """
    source = f"""
def implementation(inputs, runtime, invoking_credential_id):
    applicant_id = inputs.get('applicant_id', '')

    # Cross-operator: invoke Home Office's RTR check.
    rtr = runtime.invoke_in(
        {home_office_op_cid!r},
        {rtr_unit_cid!r},
        {{'applicant_id': applicant_id}},
        invoking_credential_id,
    )
    if rtr.__class__.__name__ != 'Permit':
        return {{
            'decision': 'refer_to_human',
            'amount_pence': 0,
            'rationale': 'right_to_reside refused: ' + rtr.rationale,
            'applicant_id': applicant_id,
            'rtr_act_id': rtr.act_id,
        }}

    # Local DWP decision: simple income/amount check.
    requested = inputs.get('requested_amount_pence', 0)
    income = inputs.get('household_income_pence', 0)
    if requested > 10000 and income < 100000:
        return {{
            'decision': 'refer_to_human',
            'amount_pence': 0,
            'rationale': 'amount above 100 GBP with very low household income; manual review',
            'applicant_id': applicant_id,
            'rtr_act_id': rtr.act_id,
        }}

    return {{
        'decision': 'approved',
        'amount_pence': requested,
        'rationale': 'right_to_reside confirmed by home_office; within approval envelope',
        'applicant_id': applicant_id,
        'rtr_act_id': rtr.act_id,
    }}
"""
    return python_implementation(source, name="advance_payment_impl")


def advance_payment_decision(
    *,
    implementation_ref: str,
    cooperative_substrate_cid: str,
    parliament_cid: str,
    dwp_root_cid: str,
    home_office_root_cid: str,
    rtr_unit_cid: str,
    rtr_impl_cid: str,
) -> FunctionalUnit:
    """DWP's composing unit.

    Authority chain includes Parliament, DWP root, Home Office root (via
    the cooperative substrate), and the cooperative substrate itself.

    Wilful inclusion: every transitively reachable unit must be listed
    at the top level. That includes the RTR unit and the RTR
    implementation state unit.
    """
    return FunctionalUnit(
        name="advance_payment_decision",
        contract_pattern=ContractPattern.HYBRID,
        spec={
            "inputs": {
                "applicant_id": "str",
                "household_income_pence": "int",
                "requested_amount_pence": "int",
            },
            "outputs": {
                "decision": "str",
                "amount_pence": "int",
                "rationale": "str",
            },
        },
        implementation_ref=implementation_ref,
        functional_refs=(rtr_unit_cid,),
        state_refs=(rtr_impl_cid,),
        credential_refs=(
            parliament_cid,
            dwp_root_cid,
            home_office_root_cid,
            cooperative_substrate_cid,
        ),
    )
