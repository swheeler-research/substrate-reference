"""
CrowdStrike Channel File 291 outage modelled against the substrate.

Three operators:
  - CrowdStrike (vendor): publishes endpoint_detection unit versions.
  - CanaryCluster (small customer cohort): deploys updates first.
    Runs the unit; its drift monitor observes outputs.
  - ProductionCluster (large customer cohort): deploys updates ONLY
    after the canary has cleared.

Scenario:
  1. Vendor publishes v1 (good). Canary deploys, observes baseline events,
     no drift. Production deploys after canary clears.
  2. Vendor publishes v2 (faulty: causes every event to crash). Canary
     deploys, observes a few events, drift fires (system_crash rate
     above the calibration bound). Canary refuses to clear. Production
     refuses to deploy.
  3. Rollback to v1 proceeds normally: v1's content_id is still in the
     archive; production redeploys it.

Run with: python -m examples.crowdstrike.run
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

from examples.crowdstrike import _implementations as impls


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
    vendor_root = _cred("crowdstrike_root", parent_cids=(root_cid,))
    canary_root = _cred("canary_cluster_root", parent_cids=(root_cid,))
    prod_root = _cred("production_cluster_root", parent_cids=(root_cid,))
    vendor_root_cid = creds.put(vendor_root)
    canary_root_cid = creds.put(canary_root)
    prod_root_cid = creds.put(prod_root)

    coop_credential = CredentialUnit(
        name="cooperative_substrate_vendor_canary_production",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:vendor+canary+production",
        authorities=("cross_operator:deployment_gate",),
        credential_refs=(vendor_root_cid, canary_root_cid, prod_root_cid),
    )
    coop_cid = creds.put(coop_credential)

    canary_operator_cred = _cred("canary_operator", parent_cids=(canary_root_cid,))
    prod_operator_cred = _cred("production_operator", parent_cids=(prod_root_cid,))
    canary_operator_cid = creds.put(canary_operator_cred)
    prod_operator_cid = creds.put(prod_operator_cred)

    vendor = Operator(name="CrowdStrike", root_credential=vendor_root,
                      substrate=_make_substrate("crowdstrike_custodian", code, creds))
    canary = Operator(name="CanaryCluster", root_credential=canary_root,
                      substrate=_make_substrate("canary_custodian", code, creds))
    production = Operator(name="ProductionCluster", root_credential=prod_root,
                          substrate=_make_substrate("production_custodian", code, creds))
    coop = CooperativeSubstrate(cooperative_credential=coop_credential)
    coop.add(vendor)
    coop.add(canary)
    coop.add(production)

    # Vendor publishes two implementations.
    detection_v1_impl = python_implementation(impls.ENDPOINT_DETECTION_V1_GOOD, name="endpoint_detection_v1_impl")
    detection_v2_impl = python_implementation(impls.ENDPOINT_DETECTION_V2_FAULTY, name="endpoint_detection_v2_impl")
    detection_v1_cid = code.put(detection_v1_impl)
    detection_v2_cid = code.put(detection_v2_impl)

    # Endpoint detection units (one per version). Behaviour-characterised
    # with drift criteria: system_health should mostly be "ok"; a high
    # rate of "system_crash" pushes the rate-in criterion out of bounds.
    detection_v1 = FunctionalUnit(
        name="endpoint_detection_v1",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "inputs": {"event": "dict"},
            "outputs": {"verdict": "str", "system_health": "str"},
            "drift_criteria": [
                {"type": "rate_in", "field": "system_health", "value": "system_crash",
                 "bound": [0.0, 0.10], "window": 3},
            ],
        },
        implementation_ref=detection_v1_cid,
        credential_refs=(root_cid, vendor_root_cid),
    )
    detection_v2 = FunctionalUnit(
        name="endpoint_detection_v2",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "inputs": {"event": "dict"},
            "outputs": {"verdict": "str", "system_health": "str"},
            "drift_criteria": [
                {"type": "rate_in", "field": "system_health", "value": "system_crash",
                 "bound": [0.0, 0.10], "window": 3},
            ],
        },
        implementation_ref=detection_v2_cid,
        credential_refs=(root_cid, vendor_root_cid),
    )
    code.put(detection_v1)
    code.put(detection_v2)

    # Canary status check (canary side).
    canary_status_impl = python_implementation(impls.CANARY_STATUS_CHECK, name="canary_status_impl")
    canary_status_impl_cid = code.put(canary_status_impl)
    canary_status_unit = FunctionalUnit(
        name="canary_status_check",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"update_content_id": "str"}},
        implementation_ref=canary_status_impl_cid,
        credential_refs=(root_cid, canary_root_cid, vendor_root_cid, prod_root_cid, coop_cid),
    )
    code.put(canary_status_unit)

    # Production deployment unit.
    deploy_impl = python_implementation(impls.DEPLOY_UPDATE, name="deploy_update_impl")
    deploy_impl_cid = code.put(deploy_impl)
    deploy_unit = FunctionalUnit(
        name="deploy_update",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"update_content_id": "str",
                         "canary_operator_id": "str",
                         "canary_status_unit_id": "str"}},
        implementation_ref=deploy_impl_cid,
        credential_refs=(root_cid, prod_root_cid, canary_root_cid, vendor_root_cid, coop_cid),
        functional_refs=(canary_status_unit.content_id(),),
        state_refs=(canary_status_impl_cid,),
    )
    code.put(deploy_unit)

    # Register units on appropriate operators.
    # Canary runs both detection units (it deploys updates to assess them).
    canary.runtime.register_compiled(compile_unit(detection_v1, code, creds, custodian=canary.custodian))
    canary.runtime.register_compiled(compile_unit(detection_v2, code, creds, custodian=canary.custodian))
    canary.runtime.register_compiled(compile_unit(canary_status_unit, code, creds, custodian=coop.custodian))
    # Production has the deploy_update unit.
    production.runtime.register_compiled(compile_unit(deploy_unit, code, creds, custodian=coop.custodian))

    return {
        "vendor": vendor, "canary": canary, "production": production,
        "coop": coop,
        "canary_operator_cid": canary_operator_cid,
        "prod_operator_cid": prod_operator_cid,
        "detection_v1": detection_v1, "detection_v1_cid": detection_v1_cid,
        "detection_v2": detection_v2, "detection_v2_cid": detection_v2_cid,
        "canary_status_unit": canary_status_unit,
        "deploy_unit": deploy_unit,
    }


def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _canary_run_events(scene, detection_unit, label):
    """Canary cohort runs a few events through a detection unit to observe behaviour."""
    canary = scene["canary"]
    events = [
        {"indicators": ["benign_pattern"]},
        {"indicators": []},
        {"indicators": ["network_scan"]},
    ]
    print(f"  Canary runs {len(events)} events through {label}:")
    for i, ev in enumerate(events):
        r = canary.runtime.invoke(detection_unit.content_id(), {"event": ev}, scene["canary_operator_cid"])
        status = "PERMIT" if isinstance(r, Permit) else "REFUSE"
        detail = (r.output.get("system_health") if isinstance(r, Permit) else r.rationale[:80])
        print(f"    event {i}: {status}  system_health={detail}")
    print(f"  drift_monitor.is_drifted(detection_unit) = "
          f"{canary.runtime.drift_monitor.is_drifted(detection_unit.content_id())}")


def _attempt_production_deploy(scene, update_cid, label):
    production = scene["production"]
    result = production.runtime.invoke(
        scene["deploy_unit"].content_id(),
        {
            "update_content_id": update_cid,
            "canary_operator_id": scene["canary"].content_id,
            "canary_status_unit_id": scene["canary_status_unit"].content_id(),
        },
        scene["prod_operator_cid"],
    )
    print(f"  {label}")
    if isinstance(result, Permit):
        out = result.output
        print(f"    deployment={out.get('deployment').upper()}")
        if out.get("deployment") == "accepted":
            print(f"    update_content_id: {out.get('update_content_id')[:16]}...")
            print(f"    canary_observations: {out.get('canary_observations')}")
        else:
            print(f"    rationale: {out.get('rationale')}")
    else:
        print(f"    REFUSED: {result.rationale[:200]}")


def main() -> int:
    scene = build_scene()

    _header("Setup")
    print(f"  Operators: CrowdStrike (vendor), CanaryCluster, ProductionCluster")
    print(f"  Cooperative substrate: all three federated")
    print(f"  Production deploys ONLY after canary clears the update.")
    print(f"  Detection units have drift criteria:")
    print(f"    system_crash rate over last 3 observations must be <= 0.10")
    print(f"  Two vendor updates available:")
    print(f"    endpoint_detection_v1 (good)    content_id={scene['detection_v1_cid'][:16]}...")
    print(f"    endpoint_detection_v2 (faulty)  content_id={scene['detection_v2_cid'][:16]}...")

    _header("Round 1: vendor publishes v1 (good)")
    _canary_run_events(scene, scene["detection_v1"], "endpoint_detection_v1")

    _header("Production deploys v1 after canary clears")
    _attempt_production_deploy(scene, scene["detection_v1"].content_id(),
                                "production deploys endpoint_detection_v1:")

    _header("Round 2: vendor publishes v2 (faulty — Channel File 291 analogue)")
    _canary_run_events(scene, scene["detection_v2"], "endpoint_detection_v2")
    print(f"  Every event produced system_health=system_crash. The drift monitor")
    print(f"  fires on the rate_in criterion (rate of crashes exceeds 10%).")
    print(f"  Subsequent invocations of detection_v2 in the canary refuse.")

    _header("Production attempts to deploy v2 — refused")
    _attempt_production_deploy(scene, scene["detection_v2"].content_id(),
                                "production deploys endpoint_detection_v2:")
    print(f"  The canary status check reports drift; production refuses to deploy.")
    print(f"  Real Channel File 291 went to all customers simultaneously; here the")
    print(f"  cooperative substrate's canary gate prevented the mass outage.")

    _header("Rollback to v1")
    # The canary's v1 unit is still permitting (drift state is keyed by
    # unit content_id; v1 and v2 are different units, so v1's state is
    # unchanged). Production can redeploy v1 immediately.
    _attempt_production_deploy(scene, scene["detection_v1"].content_id(),
                                "production redeploys endpoint_detection_v1:")
    print(f"  Rollback is not a special operation — it's invoking the prior")
    print(f"  content_id. Previous compiled forms are retained; rollback is")
    print(f"  structural.")

    _header("Ledger integrity")
    print(f"  CrowdStrike: {len(scene['vendor'].substrate.ledger)} acts (vendor publishes; no invocations)")
    print(f"  CanaryCluster: {len(scene['canary'].substrate.ledger)} acts")
    print(f"  ProductionCluster: {len(scene['production'].substrate.ledger)} acts")
    print(f"  All ledgers verify: "
          f"{scene['vendor'].substrate.ledger.verify()}, "
          f"{scene['canary'].substrate.ledger.verify()}, "
          f"{scene['production'].substrate.ledger.verify()}")

    _header("What the substrate would have prevented in CrowdStrike — summary")
    print()
    print(f"  CODE/DATA UNIFICATION. The substrate does not distinguish 'code'")
    print(f"  from 'data' updates. Every behaviour-affecting artefact is a")
    print(f"  content-addressed state unit; every deployment goes through the")
    print(f"  same witnessing and policy checks. Channel File 291 would have")
    print(f"  been a state unit subject to the same validation as the binary.")
    print()
    print(f"  STAGED DEPLOYMENT via cooperative substrate. Production's deployment")
    print(f"  policy requires the canary to have cleared the update without drift.")
    print(f"  The vendor cannot force immediate deployment; the customer's")
    print(f"  policy is the gate.")
    print()
    print(f"  DRIFT DETECTION on behaviour-characterised units. The detection")
    print(f"  unit declares an expected behaviour distribution; an update that")
    print(f"  causes system_crash on every event pushes the rate out of bounds")
    print(f"  almost immediately. The canary's drift monitor invalidates v2")
    print(f"  before production sees it.")
    print()
    print(f"  STRUCTURAL ROLLBACK. v1's content_id is still in the archive;")
    print(f"  redeploying v1 is the same operation as deploying anything else.")
    print(f"  No special 'rollback' machinery is needed.")
    print()
    print(f"  WHAT THE SUBSTRATE DOES NOT PREVENT: a vendor publishing a faulty")
    print(f"  update. It prevents the faulty update reaching production at")
    print(f"  scale, and surfaces the failure on the canary's ledger where")
    print(f"  the vendor and customers can see it. The architectural property")
    print(f"  is that bad updates fail small and visibly rather than large and")
    print(f"  catastrophically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
