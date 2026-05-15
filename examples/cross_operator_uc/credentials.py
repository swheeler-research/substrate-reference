"""
Credentials for the cross-operator UC demonstration.

The authority structure:

    UK Parliament  (constitutional source for both operators)
        |
        |----> DWP root  (operator identity for DWP)
        |        |
        |        +----> DWP caseworker  (the invoking credential)
        |
        |----> Home Office root  (operator identity for Home Office)
        |
        |----> Cooperative substrate  (a credential with parents at both
                                       operator roots; cross-operator
                                       authority bridge)

The cooperative substrate is a normal credential. What makes it
"cooperative" is structural: its credential_refs include both operators'
roots. Walking upward from the cooperative substrate reaches both DWP
and Home Office (and through them, Parliament).

Units that participate in cross-operator composition reference the
cooperative substrate in their own credential_refs; that pulls both
operator roots into the unit's authority chain, enabling delegation from
either operator's credentials.
"""

from substrate.primitives import CredentialUnit, TransferDiscipline


def parliament() -> CredentialUnit:
    return CredentialUnit(
        name="parliament_uk",
        transfer=TransferDiscipline.DELEGATED,
        principal="parliament_uk",
        authorities=("delegate:any",),
    )


def dwp_root(parliament_cid: str) -> CredentialUnit:
    return CredentialUnit(
        name="dwp_root",
        transfer=TransferDiscipline.DELEGATED,
        principal="department_for_work_and_pensions",
        authorities=("delegate:welfare", "author:welfare_units"),
        credential_refs=(parliament_cid,),
    )


def home_office_root(parliament_cid: str) -> CredentialUnit:
    return CredentialUnit(
        name="home_office_root",
        transfer=TransferDiscipline.DELEGATED,
        principal="home_office",
        authorities=("delegate:immigration", "author:identity_units"),
        credential_refs=(parliament_cid,),
    )


def cooperative_substrate(dwp_root_cid: str, home_office_root_cid: str) -> CredentialUnit:
    """The cooperative substrate between DWP and Home Office.

    A credential whose parents are both operator roots. Walking upward
    from it reaches both DWP and Home Office; units that reference it
    inherit both operators' authority chains, enabling cross-operator
    delegation.

    Phase 2-deepened scope: no cross-operator policies attached
    (policy_refs is empty). Later phases could attach rate-limiting,
    purpose-restriction, or other policies governing cross-operator
    invocation.
    """
    return CredentialUnit(
        name="cooperative_substrate_dwp_home_office",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:dwp+home_office",
        authorities=(
            "cross_operator:invoke",
            "cross_operator:compose",
        ),
        credential_refs=(dwp_root_cid, home_office_root_cid),
    )


def dwp_caseworker(dwp_root_cid: str, caseworker_id: str = "caseworker_001") -> CredentialUnit:
    return CredentialUnit(
        name=f"dwp_caseworker:{caseworker_id}",
        transfer=TransferDiscipline.DELEGATED,
        principal=caseworker_id,
        authorities=("invoke:advance_payment_decision",),
        credential_refs=(dwp_root_cid,),
    )
