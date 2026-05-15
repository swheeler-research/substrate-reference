"""
SolarWinds supply-chain compromise modelled against the substrate.

Three operators:
  - SolarWinds (vendor): produces Orion builds.
  - BuildVerifier (independent auditor): inspects artefacts before witnessing.
  - FederalAgency (customer): deploys vendor artefacts ONLY when both
    vendor and auditor witness under the cooperative substrate's quorum.

Scenario:
  1. SolarWinds publishes the legitimate Orion build (content_id A).
     BuildVerifier inspects, witnesses. Customer deploys via the
     cooperative substrate. Deployment accepted.
  2. Build pipeline compromised. SolarWinds publishes the trojanised
     Orion build (content_id B != A). Vendor's signature would be valid
     on B (the vendor signs whatever is produced). BUT the BuildVerifier's
     inspection refuses to witness because the artefact contains
     known-bad indicators. The cross-operator deployment refuses.
  3. Audit shows: deployment 1 (legitimate) accepted; deployment 2
     (trojanised) refused, with the inspection refusal recorded on both
     ledgers.

Run with: python -m examples.solarwinds.run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.federation import CooperativeSubstrate, LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.operator import Operator, Substrate
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime

from examples.solarwinds import _implementations as impls


def _cred(name, parent_cids=(), authorities=("invoke:any",)):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=authorities, credential_refs=tuple(parent_cids),
    )


def _make_substrate(custodian_name, code, creds):
    ledger = FederatedLedger()
    return Substrate(
        code=code, credentials=creds, ledger=ledger,
        custodian=LocalCustodian(name=custodian_name),
        runtime=Runtime(code, creds, ledger),
    )


def build_scene():
    code = CodeArchive()
    creds = CredentialsArchive()

    root = _cred("constitutional", authorities=("delegate:any",))
    root_cid = creds.put(root)
    vendor_root = _cred("solarwinds_root", parent_cids=(root_cid,),
                        authorities=("publish:builds",))
    auditor_root = _cred("build_verifier_root", parent_cids=(root_cid,),
                         authorities=("witness:builds",))
    customer_root = _cred("federal_agency_root", parent_cids=(root_cid,),
                          authorities=("deploy:vendor_artefacts",))
    vendor_root_cid = creds.put(vendor_root)
    auditor_root_cid = creds.put(auditor_root)
    customer_root_cid = creds.put(customer_root)

    # Cooperative substrate: vendor + auditor + customer.
    coop_credential = CredentialUnit(
        name="cooperative_substrate_solarwinds_auditor_customer",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:solarwinds+auditor+customer",
        authorities=("cross_operator:verify_and_deploy",),
        credential_refs=(vendor_root_cid, auditor_root_cid, customer_root_cid),
    )
    coop_cid = creds.put(coop_credential)

    deployer = _cred("agency_deployer", parent_cids=(customer_root_cid,))
    inspector = _cred("auditor_inspector", parent_cids=(auditor_root_cid,))
    deployer_cid = creds.put(deployer)
    inspector_cid = creds.put(inspector)

    vendor = Operator(name="SolarWinds", root_credential=vendor_root,
                      substrate=_make_substrate("solarwinds_custodian", code, creds))
    auditor = Operator(name="BuildVerifier", root_credential=auditor_root,
                       substrate=_make_substrate("build_verifier_custodian", code, creds))
    customer = Operator(name="FederalAgency", root_credential=customer_root,
                        substrate=_make_substrate("federal_agency_custodian", code, creds))
    coop = CooperativeSubstrate(cooperative_credential=coop_credential)
    coop.add(vendor)
    coop.add(auditor)
    coop.add(customer)

    # Two vendor builds (legitimate and trojanised) as content-addressed
    # state units. Their content_ids differ because their source bytes
    # differ.
    orion_legit_impl = python_implementation(impls.ORION_LEGITIMATE, name="orion_legitimate_impl")
    orion_trojan_impl = python_implementation(impls.ORION_TROJANISED, name="orion_trojanised_impl")
    orion_legit_cid = code.put(orion_legit_impl)
    orion_trojan_cid = code.put(orion_trojan_impl)

    # Auditor's inspector unit.
    inspect_impl = python_implementation(impls.INSPECT_BUILD, name="inspect_build_impl")
    inspect_impl_cid = code.put(inspect_impl)
    inspect_build = FunctionalUnit(
        name="inspect_build",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"artefact_source": "str"}},
        implementation_ref=inspect_impl_cid,
        credential_refs=(root_cid, auditor_root_cid, vendor_root_cid, customer_root_cid, coop_cid),
    )
    code.put(inspect_build)

    # Customer's deployment unit.
    deploy_impl = python_implementation(impls.DEPLOY_VENDOR_UNIT, name="deploy_vendor_unit_impl")
    deploy_impl_cid = code.put(deploy_impl)
    deploy_vendor_unit = FunctionalUnit(
        name="deploy_vendor_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"artefact_source": "str", "artefact_content_id": "str",
                         "auditor_operator_id": "str", "inspector_unit_id": "str"}},
        implementation_ref=deploy_impl_cid,
        credential_refs=(root_cid, customer_root_cid, vendor_root_cid, auditor_root_cid, coop_cid),
        functional_refs=(inspect_build.content_id(),),
        state_refs=(inspect_impl_cid,),
    )
    code.put(deploy_vendor_unit)

    # Audit unit on customer side.
    audit_impl = python_implementation(impls.AUDIT_DEPLOYMENTS, name="audit_deployments_impl")
    audit_impl_cid = code.put(audit_impl)
    audit_deployments = FunctionalUnit(
        name="audit_deployments",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}},
        implementation_ref=audit_impl_cid,
        credential_refs=(root_cid, customer_root_cid),
    )
    code.put(audit_deployments)

    joint = coop.custodian
    auditor.runtime.register_compiled(compile_unit(inspect_build, code, creds, custodian=joint))
    customer.runtime.register_compiled(compile_unit(deploy_vendor_unit, code, creds, custodian=joint))
    customer.runtime.register_compiled(compile_unit(audit_deployments, code, creds, custodian=customer.custodian))

    return {
        "vendor": vendor, "auditor": auditor, "customer": customer,
        "coop": coop,
        "deployer_cid": deployer_cid,
        "orion_legit_impl": orion_legit_impl, "orion_legit_cid": orion_legit_cid,
        "orion_trojan_impl": orion_trojan_impl, "orion_trojan_cid": orion_trojan_cid,
        "inspect_build": inspect_build,
        "deploy_vendor_unit": deploy_vendor_unit,
        "audit_deployments": audit_deployments,
    }


def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _attempt_deployment(scene, artefact_impl, artefact_cid, label):
    customer = scene["customer"]
    result = customer.runtime.invoke(
        scene["deploy_vendor_unit"].content_id(),
        {
            "artefact_source": artefact_impl.content["source"],
            "artefact_content_id": artefact_cid,
            "auditor_operator_id": scene["auditor"].content_id,
            "inspector_unit_id": scene["inspect_build"].content_id(),
        },
        scene["deployer_cid"],
    )
    print(f"  {label}")
    print(f"    artefact content_id: {artefact_cid[:16]}...")
    if isinstance(result, Permit):
        out = result.output
        print(f"    {out.get('deployment').upper()}")
        if out.get("deployment") == "accepted":
            print(f"    verified_by: {out.get('verified_by')}")
            print(f"    inspection_act_id: {(out.get('inspection_act_id') or '')[:16]}...")
        else:
            print(f"    rationale: {out.get('rationale')[:200]}")
    else:
        print(f"    REFUSED: {result.rationale[:200]}")


def main() -> int:
    scene = build_scene()
    customer = scene["customer"]

    _header("Setup")
    print(f"  Operators: SolarWinds (vendor), BuildVerifier (auditor), FederalAgency (customer)")
    print(f"  Cooperative substrate: all three operators federated")
    print(f"  Quorum: deployment requires both vendor AND auditor to witness the build")
    print(f"  Two vendor artefacts available:")
    print(f"    orion_legitimate    content_id={scene['orion_legit_cid'][:16]}...")
    print(f"    orion_trojanised    content_id={scene['orion_trojan_cid'][:16]}...")
    print(f"  (Note: their content_ids differ because their source bytes differ.")
    print(f"  This is the Horizon correction applied to build artefacts.)")

    _header("Deployment 1: legitimate build")
    _attempt_deployment(scene, scene["orion_legit_impl"], scene["orion_legit_cid"],
                        "deploy orion_legitimate:")
    print(f"  Build verifier inspected the artefact (no known-bad indicators).")
    print(f"  Customer deployment accepted.")

    _header("Deployment 2: trojanised build (build pipeline compromised)")
    _attempt_deployment(scene, scene["orion_trojan_impl"], scene["orion_trojan_cid"],
                        "deploy orion_trojanised:")
    print(f"  Build verifier inspected the artefact, found known-bad indicators")
    print(f"  (the SUNBURST-style C2 hostnames). Refused to witness.")
    print(f"  Customer's deployment refused: the cooperative substrate's quorum")
    print(f"  could not be met without the auditor's signature.")

    _header("Audit (customer ledger walk)")
    result = customer.runtime.invoke(
        scene["audit_deployments"].content_id(), {}, scene["deployer_cid"],
    )
    if isinstance(result, Permit):
        for a in result.output["acts"]:
            out = a["output"]
            decision = out.get("deployment") if isinstance(out, dict) else "?"
            print(f"  deployment={decision}  act_id={a['act_id'][:16]}...")
            if isinstance(out, dict):
                cid = out.get('deployed_content_id') or 'n/a'
                print(f"    artefact: {cid[:16] if cid != 'n/a' else 'n/a'}...")
                if out.get("verified_by"):
                    print(f"    verified_by: {out['verified_by']}")
                if out.get("rationale"):
                    print(f"    rationale: {out['rationale'][:150]}")
    print()
    print(f"  Ledger lengths: SolarWinds={len(scene['vendor'].substrate.ledger)},"
          f" BuildVerifier={len(scene['auditor'].substrate.ledger)},"
          f" FederalAgency={len(customer.substrate.ledger)}")
    print(f"  All ledgers verify: {scene['vendor'].substrate.ledger.verify()},"
          f" {scene['auditor'].substrate.ledger.verify()},"
          f" {customer.substrate.ledger.verify()}")

    _header("What the substrate would have prevented in SolarWinds — summary")
    print()
    print(f"  CONTENT-ADDRESSING. The trojanised build has a different content_id")
    print(f"  from the legitimate one. The vendor's signature on the trojanised")
    print(f"  artefact does not change its content_id. Customers who deploy by")
    print(f"  content_id reject the substitution structurally; signature alone")
    print(f"  cannot validate a build.")
    print()
    print(f"  MULTI-CUSTODIAN QUORUM. The cooperative substrate requires both")
    print(f"  vendor and independent auditor to witness any deployment. A")
    print(f"  compromised vendor alone cannot produce a quorum-valid witness")
    print(f"  without also compromising the auditor. The auditor's independent")
    print(f"  inspection becomes the structural choke point.")
    print()
    print(f"  CROSS-OPERATOR AUDIT. The deployment refusal is recorded on both")
    print(f"  customer and auditor ledgers. Other customers (under the same")
    print(f"  cooperative substrate) can query the auditor's ledger and see")
    print(f"  the refusal — evidence sharing is structural, not voluntary.")
    print()
    print(f"  WHAT THE SUBSTRATE DOES NOT PREVENT: a vendor compromise plus an")
    print(f"  auditor compromise. The substrate raises the bar to compromise N")
    print(f"  independent custodians simultaneously rather than 1; the bar's")
    print(f"  height is the quorum threshold (here, 3 of 3 — vendor + auditor +")
    print(f"  customer must all witness). The architectural property is that")
    print(f"  the bar exists structurally rather than as a procedural")
    print(f"  recommendation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
