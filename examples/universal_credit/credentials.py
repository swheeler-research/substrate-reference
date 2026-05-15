"""
The authority chain for the Universal Credit example.

Parliament is the constitutional source: it has no parent credential
references; the recursion of the authority chain terminates here. (In a
fuller realisation, Parliament would itself trace to credentials held by
natural persons, the architecture's deepest termination point. Phase 1
stops at the institutional source.)

The Department for Work and Pensions (DWP) is delegated from Parliament.
A specific caseworker credential is delegated from DWP. The caseworker is
the credential a runtime invocation presents.
"""

from substrate.primitives import CredentialUnit, TransferDiscipline


def parliament() -> CredentialUnit:
    return CredentialUnit(
        name="parliament",
        transfer=TransferDiscipline.DELEGATED,
        principal="parliament",
        authorities=("delegate:any",),
    )


def dwp(parliament_cid: str) -> CredentialUnit:
    return CredentialUnit(
        name="department_for_work_and_pensions",
        transfer=TransferDiscipline.DELEGATED,
        principal="dwp",
        authorities=(
            "delegate:welfare_administration",
            "author:welfare_units",
        ),
        credential_refs=(parliament_cid,),
    )


def caseworker(dwp_cid: str, caseworker_id: str = "caseworker_001") -> CredentialUnit:
    return CredentialUnit(
        name=f"caseworker:{caseworker_id}",
        transfer=TransferDiscipline.DELEGATED,
        principal=caseworker_id,
        authorities=("invoke:advance_payment_decision",),
        credential_refs=(dwp_cid,),
    )
