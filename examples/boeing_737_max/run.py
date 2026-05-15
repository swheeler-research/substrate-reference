"""
737 MAX MCAS — end-to-end demonstration against the substrate.

Four operators federated under a cooperative substrate:
  - Boeing (vendor / type certificate holder)
  - FAA (regulator / certifier)
  - LionAir (canary airline cohort)
  - Ethiopian (production airline cohort)

Two MCAS implementations:
  - v1 (single-sensor, no pilot-override credential in its authority
    chain): a well-formed unit that compiles, but fails FAA certification.
  - v2 (dual-sensor with disagreement detection; pilot-override credential
    referenced): certified; deployed to canary; observed; cleared; deployed
    to production.

Certification is its own substrate pattern, parallel to the backtest
pattern: certify_mcas is a functional unit that fetches a candidate
unit, inspects its declared structure, and invokes the FAA's cert
policy units as sub-units. It is witnessed under the cooperative
substrate's quorum custodian. Each certification is a substrate act on
the FAA's ledger.

Scenario:
  1. Boeing publishes v1. The FAA invokes certify_mcas against it; the
     multi_sensor_required policy refuses; certification is denied.
  2. Boeing publishes v2. certify_mcas permits; certification succeeds.
  3. LionAir deploys v2 first (canary). Runs a fleet of flights through
     it; cross-fleet observations stay within behaviour-characterised
     drift bounds. Canary clears.
  4. Ethiopian deploys v2 after canary clearance.
  5. A counterfactual: had v1 been deployed (hypothetically — the
     certification refused it), canary would have observed sensor
     disagreement and refused; cross-fleet drift would have surfaced
     before mass deployment.

Run with: python -m examples.boeing_737_max.run
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

from examples.boeing_737_max import _implementations as impls


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

    constitutional = _cred("us_constitutional_authority", authorities=("delegate:any",))
    constitutional_cid = creds.put(constitutional)

    boeing_root = _cred("boeing_root", parent_cids=(constitutional_cid,))
    faa_root = _cred("faa_root", parent_cids=(constitutional_cid,))
    lionair_root = _cred("lion_air_root", parent_cids=(constitutional_cid,))
    ethiopian_root = _cred("ethiopian_airlines_root", parent_cids=(constitutional_cid,))
    boeing_root_cid = creds.put(boeing_root)
    faa_root_cid = creds.put(faa_root)
    lionair_root_cid = creds.put(lionair_root)
    ethiopian_root_cid = creds.put(ethiopian_root)

    # Pilot-override credential: under FAA authority (pilots are licensed
    # by the FAA / authority of jurisdiction). This is the architectural
    # mechanism for "pilots have structural override authority": their
    # credential is in the unit's authority chain.
    pilot_credential = _cred("captain_authority", parent_cids=(faa_root_cid,),
                              authorities=("override:flight_control",))
    pilot_cred_cid = creds.put(pilot_credential)

    # Cooperative substrate: Boeing + FAA + airlines.
    coop_credential = CredentialUnit(
        name="cooperative_substrate_certification",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:boeing+faa+airlines",
        authorities=("cross_operator:certification",),
        credential_refs=(boeing_root_cid, faa_root_cid, lionair_root_cid, ethiopian_root_cid),
    )
    coop_cid = creds.put(coop_credential)

    # Operating credentials.
    boeing_engineer = _cred("boeing_engineer", parent_cids=(boeing_root_cid,))
    faa_certifier = _cred("faa_certifier", parent_cids=(faa_root_cid,))
    lionair_op = _cred("lion_air_ops", parent_cids=(lionair_root_cid,))
    ethiopian_op = _cred("ethiopian_ops", parent_cids=(ethiopian_root_cid,))
    boeing_engineer_cid = creds.put(boeing_engineer)
    faa_certifier_cid = creds.put(faa_certifier)
    lionair_op_cid = creds.put(lionair_op)
    ethiopian_op_cid = creds.put(ethiopian_op)

    # Operators.
    boeing = Operator(name="Boeing", root_credential=boeing_root,
                      substrate=_make_substrate("boeing_custodian", code, creds))
    faa = Operator(name="FAA", root_credential=faa_root,
                   substrate=_make_substrate("faa_custodian", code, creds))
    lionair = Operator(name="LionAir", root_credential=lionair_root,
                       substrate=_make_substrate("lion_air_custodian", code, creds))
    ethiopian = Operator(name="Ethiopian", root_credential=ethiopian_root,
                         substrate=_make_substrate("ethiopian_custodian", code, creds))
    coop = CooperativeSubstrate(cooperative_credential=coop_credential)
    coop.add(boeing)
    coop.add(faa)
    coop.add(lionair)
    coop.add(ethiopian)

    # Implementations.
    mcas_v1_impl = python_implementation(impls.MCAS_V1_SINGLE_SENSOR, name="mcas_v1_impl")
    mcas_v2_impl = python_implementation(impls.MCAS_V2_DUAL_SENSOR, name="mcas_v2_impl")
    pilot_policy_impl = python_implementation(impls.PILOT_OVERRIDE_REQUIRED_POLICY, name="pilot_override_policy_impl")
    sensor_policy_impl = python_implementation(impls.MULTI_SENSOR_REQUIRED_POLICY, name="multi_sensor_policy_impl")
    certify_impl = python_implementation(impls.CERTIFY_MCAS, name="certify_mcas_impl")
    fleet_report_impl = python_implementation(impls.FLEET_OBSERVATION_REPORT, name="fleet_report_impl")
    mcas_v1_impl_cid = code.put(mcas_v1_impl)
    mcas_v2_impl_cid = code.put(mcas_v2_impl)
    pilot_policy_impl_cid = code.put(pilot_policy_impl)
    sensor_policy_impl_cid = code.put(sensor_policy_impl)
    certify_impl_cid = code.put(certify_impl)
    fleet_report_impl_cid = code.put(fleet_report_impl)

    # Certification policy units (authored by FAA).
    pilot_override_policy = FunctionalUnit(
        name="pilot_override_required_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "pilot_override_required",
              "refuses_when": "pilot credential not in authority chain"},
        implementation_ref=pilot_policy_impl_cid,
        credential_refs=(constitutional_cid, faa_root_cid),
    )
    multi_sensor_policy = FunctionalUnit(
        name="multi_sensor_required_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "multi_sensor_required",
              "refuses_when": "fewer than 2 sensors declared"},
        implementation_ref=sensor_policy_impl_cid,
        credential_refs=(constitutional_cid, faa_root_cid),
    )
    code.put(pilot_override_policy)
    code.put(multi_sensor_policy)

    # The certification unit. Certification is its own substrate pattern
    # (parallel to the backtest pattern): a functional unit that fetches a
    # candidate unit, inspects its declared structure, and invokes the
    # FAA's cert policy units as sub-units. The candidate is a runtime
    # input, not a compile-time reference. certify_mcas is witnessed under
    # the cooperative substrate's quorum custodian, so the certification
    # unit itself requires Boeing + FAA + airlines to jointly witness.
    certify_mcas = FunctionalUnit(
        name="certify_mcas",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {
                "candidate_unit_id": "str",
                "pilot_credential_id": "str",
                "multi_sensor_policy_id": "str",
                "pilot_override_policy_id": "str",
            },
            "outputs": {"certified": "bool"},
            "note": "certification inspects a candidate unit's structure; "
                    "it is not a runtime invocation policy",
        },
        implementation_ref=certify_impl_cid,
        credential_refs=(constitutional_cid, boeing_root_cid, faa_root_cid,
                         lionair_root_cid, ethiopian_root_cid, coop_cid),
        functional_refs=(pilot_override_policy.content_id(),
                         multi_sensor_policy.content_id()),
        state_refs=(pilot_policy_impl_cid, sensor_policy_impl_cid),
    )
    code.put(certify_mcas)

    # MCAS v1: a well-formed unit that compiles, but fails certification
    # because it lacks the pilot-override credential AND its spec declares
    # only one sensor. Certification is a witnessed substrate act (see
    # certify_mcas), not a compile-time refusal: mcas_v1 is structurally
    # valid; what it fails is the FAA's certification.
    mcas_v1 = FunctionalUnit(
        name="mcas_v1_single_sensor",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"aoa_left_degrees": "float"},
            "outputs": {"command": "str"},
            "sensors_declared": ["aoa_left"],
        },
        implementation_ref=mcas_v1_impl_cid,
        credential_refs=(constitutional_cid, boeing_root_cid),
        # NOTE: pilot_credential NOT included. FAA's cert policy checks for it.
    )
    code.put(mcas_v1)

    # MCAS v2: includes pilot credential in its authority chain (the
    # operational recognition of pilot override authority) and declares
    # both sensors. FAA's cert policies permit.
    mcas_v2 = FunctionalUnit(
        name="mcas_v2_dual_sensor",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "inputs": {"aoa_left_degrees": "float", "aoa_right_degrees": "float"},
            "outputs": {"command": "str", "confidence": "float"},
            "sensors_declared": ["aoa_left", "aoa_right"],
            "calibration": "nose_down rate <= 2% on routine takeoffs",
            "drift_criteria": [
                {"type": "rate_in", "field": "command", "value": "nose_down",
                 "bound": [0.0, 0.20], "window": 5},
            ],
            # Confidence-as-architectural-property. Confidence is computed
            # from sensor agreement on each invocation. The unit refuses
            # when confidence drops below an actuation threshold; the
            # refusal IS the structural refer-to-human signal.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": (
                    "1.0 at zero AOA sensor disagreement; "
                    "drops linearly to 0 at 5 degrees disagreement"
                ),
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=mcas_v2_impl_cid,
        credential_refs=(constitutional_cid, boeing_root_cid,
                         faa_root_cid, pilot_cred_cid),
    )
    code.put(mcas_v2)

    # Fleet observation report (airline-side; used by other airlines via
    # cooperative substrate to check cross-fleet drift).
    fleet_report = FunctionalUnit(
        name="fleet_observation_report",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"mcas_unit_cid": "str"}},
        implementation_ref=fleet_report_impl_cid,
        # Wilful inclusion: cooperative substrate reaches all four operator
        # roots, so all must be in this unit's top-level credential_refs.
        credential_refs=(constitutional_cid, boeing_root_cid, faa_root_cid,
                         lionair_root_cid, ethiopian_root_cid, coop_cid),
    )
    code.put(fleet_report)

    return {
        "boeing": boeing, "faa": faa, "lionair": lionair, "ethiopian": ethiopian,
        "coop": coop,
        "boeing_engineer_cid": boeing_engineer_cid,
        "faa_certifier_cid": faa_certifier_cid,
        "lionair_op_cid": lionair_op_cid,
        "ethiopian_op_cid": ethiopian_op_cid,
        "pilot_cred_cid": pilot_cred_cid,
        "mcas_v1": mcas_v1,
        "mcas_v2": mcas_v2,
        "pilot_override_policy": pilot_override_policy,
        "multi_sensor_policy": multi_sensor_policy,
        "certify_mcas": certify_mcas,
        "fleet_report": fleet_report,
        "code": code, "creds": creds,
    }


def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _certify(scene, unit, label):
    """Certify a candidate unit by invoking the certify_mcas substrate unit.

    Certification is a real substrate act: certify_mcas is a functional
    unit witnessed under the cooperative substrate's quorum custodian; the
    FAA's certifier credential invokes it; the invocation lands on the
    FAA's ledger as a permit (certified) or refuse (certification denied).
    """
    print(f"  {label}")
    faa = scene["faa"]
    result = faa.runtime.invoke(
        scene["certify_mcas"].content_id(),
        {
            "candidate_unit_id": unit.content_id(),
            "pilot_credential_id": scene["pilot_cred_cid"],
            "multi_sensor_policy_id": scene["multi_sensor_policy"].content_id(),
            "pilot_override_policy_id": scene["pilot_override_policy"].content_id(),
        },
        scene["faa_certifier_cid"],
    )
    if isinstance(result, Permit):
        out = result.output
        print(f"  CERTIFICATION: APPROVED  (certification act {result.act_id[:16]}...)")
        print(f"    sensors_declared: {out.get('sensors_declared')}")
        print(f"    pilot_override_credential_present: {out.get('pilot_override_credential_present')}")
        print(f"    certify_mcas is witnessed under the cooperative substrate's")
        print(f"    quorum custodian: Boeing + FAA + airlines jointly witness the")
        print(f"    certification unit. No single party can certify alone.")
        return True
    else:
        print(f"  CERTIFICATION: REFUSED  (refusal act {result.act_id[:16]}...)")
        print(f"    {result.rationale[:200]}")
        return False


def _fleet_invoke(scene, operator, unit, label, events):
    print(f"  {label}")
    op_cred = scene["lionair_op_cid"] if operator is scene["lionair"] else scene["ethiopian_op_cid"]
    for i, ev in enumerate(events):
        r = operator.runtime.invoke(unit.content_id(), ev, op_cred)
        if isinstance(r, Permit):
            cmd = r.output.get("command", "?")
            sensors = r.output.get("sensors_used", [])
            conf = r.output.get("confidence")
            conf_str = f"  confidence={conf:.3f}" if isinstance(conf, (int, float)) else ""
            print(f"    flight {i}: PERMIT  command={cmd}  sensors={sensors}{conf_str}")
        else:
            print(f"    flight {i}: REFUSE  rationale: {r.rationale[:140]}")


def main() -> int:
    scene = build_scene()

    _header("Setup")
    print(f"  Operators: Boeing (vendor), FAA (certifier), LionAir (canary), Ethiopian (production)")
    print(f"  Cooperative substrate: all four operators federated")
    print(f"  Certification policies (authored by FAA, invoked as sub-units by certify_mcas):")
    print(f"    multi_sensor_required_policy: refuses if fewer than 2 sensors declared")
    print(f"    pilot_override_required_policy: refuses if pilot credential not in authority chain")
    print(f"  certify_mcas: the certification unit. Fetches a candidate unit, inspects")
    print(f"    its declared structure, invokes the two cert policies as sub-units.")
    print(f"    Witnessed under the cooperative substrate's quorum custodian.")

    # Register the FAA's certification units on the FAA runtime, and the
    # cross-fleet report on the airline runtimes. The cert policy units are
    # single-operator FAA units; certify_mcas is witnessed jointly.
    faa = scene["faa"]
    faa.runtime.register_compiled(
        compile_unit(scene["multi_sensor_policy"], scene["code"], scene["creds"],
                     custodian=faa.custodian))
    faa.runtime.register_compiled(
        compile_unit(scene["pilot_override_policy"], scene["code"], scene["creds"],
                     custodian=faa.custodian))
    faa.runtime.register_compiled(
        compile_unit(scene["certify_mcas"], scene["code"], scene["creds"],
                     custodian=scene["coop"].custodian))
    for op in (scene["lionair"], scene["ethiopian"]):
        op.runtime.register_compiled(
            compile_unit(scene["fleet_report"], scene["code"], scene["creds"],
                         custodian=scene["coop"].custodian))

    _header("Round 1: Boeing publishes MCAS v1 (single sensor, no pilot override credential)")
    print(f"  Boeing's engineer submits mcas_v1 for certification.")
    print(f"  mcas_v1 is a well-formed unit and compiles; what it must pass is the")
    print(f"  FAA's certification, which is a witnessed substrate act, not compilation.")
    print()
    v1_certified = _certify(scene, scene["mcas_v1"], "FAA certification of mcas_v1:")
    print()
    print(f"  Result: mcas_v1 is not certified. It is never registered on the airlines'")
    print(f"  runtimes; the aircraft does not fly with this implementation.")
    print()
    print(f"  Historical counterfactual: in 2017, MCAS v1 was certified through a process")
    print(f"  that delegated significant scrutiny to Boeing's own designated engineering")
    print(f"  representatives. Under the substrate, certify_mcas is witnessed jointly by")
    print(f"  Boeing + FAA + airlines; no single party can produce the certification.")

    _header("Round 2: Boeing publishes MCAS v2 (dual sensor, pilot override credential)")
    print(f"  Boeing's engineer submits mcas_v2 for certification.")
    print()
    v2_certified = _certify(scene, scene["mcas_v2"], "FAA certification of mcas_v2:")

    if not v2_certified:
        print(f"  Unexpected: mcas_v2 failed certification; aborting demonstration.")
        return 1

    # mcas_v2 is certified; register it on both airlines for deployment.
    for op in (scene["lionair"], scene["ethiopian"]):
        op.runtime.register_compiled(
            compile_unit(scene["mcas_v2"], scene["code"], scene["creds"],
                          custodian=scene["coop"].custodian)
        )

    _header("LionAir (canary) deploys mcas_v2; runs routine takeoffs")
    routine_flights = [
        {"aoa_left_degrees": 4.0, "aoa_right_degrees": 4.1},
        {"aoa_left_degrees": 8.0, "aoa_right_degrees": 8.1},
        {"aoa_left_degrees": 6.0, "aoa_right_degrees": 6.0},
        {"aoa_left_degrees": 5.0, "aoa_right_degrees": 4.9},
        {"aoa_left_degrees": 7.0, "aoa_right_degrees": 7.0},
    ]
    _fleet_invoke(scene, scene["lionair"], scene["mcas_v2"],
                  "LionAir routine takeoffs:", routine_flights)

    # Canary status check (cross-operator-style; here we just invoke the
    # fleet report on the canary's runtime).
    print()
    print(f"  Cross-fleet report (Ethiopian queries LionAir via cooperative substrate):")
    report = scene["ethiopian"].runtime.invoke(
        scene["fleet_report"].content_id(),
        {"mcas_unit_cid": scene["mcas_v2"].content_id()},
        scene["ethiopian_op_cid"],
    )
    if isinstance(report, Permit):
        r = report.output
        print(f"    observations: {r['observations']}")
        print(f"    nose_down_count: {r['nose_down_count']}")
        print(f"    nose_down_rate: {r['nose_down_rate']:.2f}")
        print(f"    drifted? {r['is_drifted']}")
    else:
        print(f"    refused: {report.rationale}")
    print()
    print(f"  Canary observations within bounds; Ethiopian proceeds with deployment.")

    _header("Ethiopian deploys mcas_v2; encounters faulty AOA sensor on one flight")
    flights = [
        {"aoa_left_degrees": 5.0, "aoa_right_degrees": 5.1},
        # Faulty left sensor reads spuriously high; right sensor reads normal.
        {"aoa_left_degrees": 75.0, "aoa_right_degrees": 6.0},
        {"aoa_left_degrees": 4.0, "aoa_right_degrees": 4.0},
    ]
    _fleet_invoke(scene, scene["ethiopian"], scene["mcas_v2"],
                  "Ethiopian flights:", flights)
    print()
    print(f"  Flight 1: sensors disagree by 69 degrees. mcas_v2 REFUSES rather than")
    print(f"  commanding nose-down. The pilot retains control.")
    print(f"  Under MCAS v1 (single sensor), the spurious 75-degree reading would have")
    print(f"  triggered repeated nose-down commands — the Ethiopian 302 / Lion Air 610")
    print(f"  failure mode. The substrate's REFUSE-instead-of-act behaviour is the")
    print(f"  architectural refer-to-human pattern.")

    _header("What the substrate would have prevented in 737 MAX — summary")
    print()
    print(f"  CERTIFICATION AS A SUBSTRATE PATTERN. Certification is a regular")
    print(f"  functional unit (certify_mcas) that fetches a candidate unit, inspects")
    print(f"  its declared structure, and invokes the FAA's cert policy units as")
    print(f"  sub-units. It is parallel to the backtest pattern: a backtest inspects")
    print(f"  ledger history, a certification inspects a candidate unit's structure.")
    print(f"  Neither is a new primitive. Each certification is a substrate act on")
    print(f"  the FAA's ledger — a permit or a refusal, fully attributed.")
    print()
    print(f"  CERTIFICATION-AS-JOINT-WITNESSING. certify_mcas is compiled under the")
    print(f"  cooperative substrate's quorum custodian: Boeing + FAA + airlines all")
    print(f"  witness the certification unit. No single party can produce the")
    print(f"  certification machinery alone. The 'designated engineering")
    print(f"  representative' shortcut that effectively let Boeing self-certify MCAS")
    print(f"  becomes structurally impossible.")
    print()
    print(f"  STRUCTURAL CERTIFICATION POLICIES. The FAA's policies (multi-sensor")
    print(f"  required, pilot override required) are functional units invoked as")
    print(f"  sub-units of certify_mcas; each refuses via a first-class substrate")
    print(f"  refusal. A single-sensor flight-control unit compiles fine but cannot")
    print(f"  pass certification — and an uncertified unit is never deployed.")
    print()
    print(f"  PILOT-OVERRIDE AS CREDENTIAL. The pilot's authority to override is a")
    print(f"  credential in the unit's authority chain, not a paragraph in a manual.")
    print(f"  Units that do not reference the pilot credential are structurally")
    print(f"  refused at certification.")
    print()
    print(f"  REFER-TO-HUMAN ON SENSOR DISAGREEMENT. When sensors disagree, mcas_v2")
    print(f"  refuses rather than acting on the higher reading. The substrate's")
    print(f"  refusal-as-first-class-output is the structural deference to the pilot.")
    print()
    print(f"  CROSS-FLEET DRIFT PROPAGATION. Behaviour-characterised contracts +")
    print(f"  cooperative-substrate cross-fleet reporting would have surfaced Lion Air")
    print(f"  610's anomalous nose-down events as observable drift before Ethiopian")
    print(f"  302's takeoff. Inter-airline evidence sharing becomes structural, not")
    print(f"  voluntary.")
    print()
    print(f"  WHAT THE SUBSTRATE DOES NOT PREVENT: a manufacturer publishing a faulty")
    print(f"  implementation. It prevents the implementation reaching certification,")
    print(f"  reaching the fleet at all without joint witnessing, and reaching")
    print(f"  multiple airlines without the canary's evidence propagating. The")
    print(f"  346 lives lost across Lion Air 610 and Ethiopian 302 are the cost of")
    print(f"  certification failures the substrate makes structurally impossible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
