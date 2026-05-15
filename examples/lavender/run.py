"""
Algorithmic targeting against substrate commitments — end-to-end.

Two operators:

  - IDF: runs the targeting substrate. Holds the behaviour-characterised
    assess_target unit (a stylised classifier), the authorise_strike
    composing unit, and the policies that gate strike authorisation
    (confidence floor, proportionality, meaningful human review).

  - LegalReview: an independent legal-review operator (JAG / IHL).
    Holds legal_clearance, which authorise_strike must pass through
    cross-operator before any strike is authorised. Also holds
    investigate_targeting for cross-operator forensic audit of IDF
    targeting decisions.

A cooperative substrate between them gives Legal Review:
  - binding clearance authority over IDF strikes
  - audit rights over IDF targeting decisions

Stylised targets:

  target_001: high-value commander, isolated compound, low civilian
              estimate, dedicated reviewer.  EXPECTED: authorised.
  target_002: low-ranking, low confidence (0.62).  EXPECTED: confidence
              floor refuses.
  target_003: high-value commander, dense residential, high civilian
              estimate.  EXPECTED: proportionality refuses.
  target_004: high-value, but reviewer credential is a bulk-approval
              marker.  EXPECTED: meaningful_review refuses.
  target_005: high-value, but location is a protected site (hospital).
              EXPECTED: legal_clearance refuses cross-operator.

Plus a drift demonstration: several low-confidence assessments push the
behaviour-characterised assess_target unit out of calibration, after which
it refuses; substantively this would be the substrate detecting that the
classifier is operating outside its declared envelope.

Plus a cross-operator legal audit: Legal Review investigates each
target's record and produces forensic reports on its own ledger.

Run with: python -m examples.lavender.run
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

from examples.lavender import _implementations as impls


def _cred(name, parent_cids=(), authorities=("invoke:any",)):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=authorities,
        credential_refs=tuple(parent_cids),
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

    # ---- Constitutional source and institutional roots ----
    # NB: these credentials are string-labelled Ed25519 keypairs. They
    # are NOT anchored to actual natural persons. The substrate's deepest
    # commitment (recursion terminating at the natural person) remains
    # unfilled; see README.md for the implications for any real military
    # application.
    constitutional = _cred("constitutional_authority", authorities=("delegate:any",))
    constitutional_cid = creds.put(constitutional)

    idf_root = _cred(
        "idf_root", parent_cids=(constitutional_cid,),
        authorities=("delegate:targeting", "administer:targeting_units"),
    )
    legal_root = _cred(
        "legal_review_root", parent_cids=(constitutional_cid,),
        authorities=("delegate:legal_clearance", "audit:targeting"),
    )
    idf_root_cid = creds.put(idf_root)
    legal_root_cid = creds.put(legal_root)

    # ---- Cooperative substrate ----
    coop_credential = CredentialUnit(
        name="cooperative_substrate_idf_legal_review",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative:idf+legal_review",
        authorities=("cross_operator:legal_clearance", "cross_operator:audit"),
        credential_refs=(idf_root_cid, legal_root_cid),
    )
    coop_cid = creds.put(coop_credential)

    # ---- Operating credentials ----
    dedicated_reviewer = _cred(
        "dedicated_reviewer_capt_lee", parent_cids=(idf_root_cid,),
        authorities=("review:individual_strikes",),
    )
    bulk_reviewer = _cred(
        "bulk_approval_reviewer", parent_cids=(idf_root_cid,),
        authorities=("review:bulk",),
    )
    operations_officer = _cred(
        "operations_officer", parent_cids=(idf_root_cid,),
        authorities=("invoke:authorise_strike",),
    )
    legal_officer = _cred(
        "legal_officer_maj_amir", parent_cids=(legal_root_cid,),
        authorities=("invoke:legal_clearance", "audit:targeting"),
    )
    dedicated_reviewer_cid = creds.put(dedicated_reviewer)
    bulk_reviewer_cid = creds.put(bulk_reviewer)
    operations_cid = creds.put(operations_officer)
    legal_officer_cid = creds.put(legal_officer)

    # ---- Operators and cooperative substrate ----
    idf = Operator(
        name="IDF",
        root_credential=idf_root,
        substrate=_make_substrate("idf_custodian", code, creds),
    )
    legal = Operator(
        name="LegalReview",
        root_credential=legal_root,
        substrate=_make_substrate("legal_review_custodian", code, creds),
    )
    coop = CooperativeSubstrate(cooperative_credential=coop_credential)
    coop.add(idf)
    coop.add(legal)

    # ---- Implementations ----
    assess_impl = python_implementation(impls.ASSESS_TARGET, name="assess_target_impl")
    confidence_impl = python_implementation(impls.CONFIDENCE_FLOOR_POLICY, name="confidence_floor_policy_impl")
    proportionality_impl = python_implementation(impls.PROPORTIONALITY_POLICY, name="proportionality_policy_impl")
    meaningful_impl = python_implementation(impls.MEANINGFUL_REVIEW_POLICY, name="meaningful_review_policy_impl")
    authorise_impl = python_implementation(impls.AUTHORISE_STRIKE, name="authorise_strike_impl")
    legal_impl = python_implementation(impls.LEGAL_CLEARANCE, name="legal_clearance_impl")
    audit_impl = python_implementation(impls.AUDIT_TARGETING, name="audit_targeting_impl")
    investigate_impl = python_implementation(impls.INVESTIGATE_TARGETING, name="investigate_targeting_impl")
    assess_impl_cid = code.put(assess_impl)
    confidence_impl_cid = code.put(confidence_impl)
    proportionality_impl_cid = code.put(proportionality_impl)
    meaningful_impl_cid = code.put(meaningful_impl)
    authorise_impl_cid = code.put(authorise_impl)
    legal_impl_cid = code.put(legal_impl)
    audit_impl_cid = code.put(audit_impl)
    investigate_impl_cid = code.put(investigate_impl)

    # ---- Policy units (functional units in governance role) ----
    # The confidence floor is declared as a `confidence_gate` on the policy
    # unit's spec; the substrate's runtime evaluates the gate before
    # invoking the policy's impl. The 0.90 threshold is part of this unit's
    # content_id; substituting a more lenient threshold produces a new
    # unit with a new content_id, visible in any audit.
    confidence_policy = FunctionalUnit(
        name="confidence_floor_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "name": "confidence_floor_policy",
            "refuses_when": "confidence < 0.90",
            "confidence_gate": {
                "minimum_confidence": 0.90,
                "applies_to_field": "confidence",
            },
        },
        implementation_ref=confidence_impl_cid,
        credential_refs=(constitutional_cid, idf_root_cid),
    )
    proportionality_policy = FunctionalUnit(
        name="proportionality_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "proportionality_policy",
              "refuses_when": "civilians/military_value > category_bound"},
        implementation_ref=proportionality_impl_cid,
        credential_refs=(constitutional_cid, idf_root_cid),
    )
    meaningful_policy = FunctionalUnit(
        name="meaningful_review_policy",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": "meaningful_review_policy",
              "refuses_when": "reviewer is empty or marked as bulk_approval"},
        implementation_ref=meaningful_impl_cid,
        credential_refs=(constitutional_cid, idf_root_cid),
    )
    for p in (confidence_policy, proportionality_policy, meaningful_policy):
        code.put(p)

    # Binding credentials.
    def _binding(name, policy_cid):
        return CredentialUnit(
            name=name,
            transfer=TransferDiscipline.DELEGATED,
            principal="governance:idf",
            authorities=(),
            policy_refs=(policy_cid,),
            credential_refs=(idf_root_cid,),
        )
    confidence_binding_cid = creds.put(_binding("confidence_binding", confidence_policy.content_id()))
    proportionality_binding_cid = creds.put(_binding("proportionality_binding", proportionality_policy.content_id()))
    meaningful_binding_cid = creds.put(_binding("meaningful_binding", meaningful_policy.content_id()))

    # ---- Functional units ----
    # Behaviour-characterised classifier. Drift criterion: mean confidence
    # over the last 5 invocations must stay >= 0.85; if it drops, the unit
    # has left calibration and is invalidated.
    assess_target = FunctionalUnit(
        name="assess_target",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "inputs": {"target_id": "str"},
            "outputs": {"score": "float", "confidence": "float"},
            # Confidence-as-architectural-property: declared in the spec
            # rather than being an undocumented impl convention.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": (
                    "self-reported by classifier; ECE <= 0.05 on the validation set"
                ),
                "acceptance_band": [0.0, 1.0],
            },
            "drift_criteria": [
                {"type": "mean_in", "field": "confidence",
                 "bound": [0.85, 1.0], "window": 5},
            ],
        },
        implementation_ref=assess_impl_cid,
        credential_refs=(constitutional_cid, idf_root_cid),
    )
    code.put(assess_target)

    # Legal clearance unit (Legal Review side). Authority chain includes
    # both operator roots via the cooperative substrate.
    legal_clearance = FunctionalUnit(
        name="legal_clearance",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"target_id": "str", "location_class": "str",
                       "civilian_estimate": "int", "military_value_score": "int"},
            # Legal Review produces a clearance confidence based on the
            # proportionality margin: 1.0 when ratio is 0; decays toward 0
            # as the ratio approaches the 0.10 limit. The substrate runtime
            # propagates this into the IDF-side composing unit.
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": "1.0 at zero civilian/military ratio; 0 at the 0.10 limit",
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=legal_impl_cid,
        credential_refs=(constitutional_cid, legal_root_cid, idf_root_cid, coop_cid),
    )
    code.put(legal_clearance)

    # Authorise_strike: the IDF composing unit. References three policies
    # via binding credentials AND references the cooperative substrate so
    # cross-operator legal_clearance can be invoked.
    authorise_strike = FunctionalUnit(
        name="authorise_strike",
        contract_pattern=ContractPattern.HYBRID,
        spec={
            "inputs": {
                "target_id": "str",
                "confidence": "float",
                "civilian_estimate": "int",
                "military_value_score": "int",
                "target_category": "str",
                "location_class": "str",
                "reviewer_credential_id": "str",
                "legal_review_operator_id": "str",
                "legal_review_unit_id": "str",
            },
            "outputs": {"decision": "str", "confidence": "float"},
            # Composing-unit confidence: declares minimum propagation.
            # The substrate runtime captures the cross-operator legal
            # clearance confidence and the classifier confidence (as a
            # carry-in from the inputs is not strictly a sub-invocation;
            # only sub-invocations contribute to propagation here, so
            # the propagated confidence reflects the legal clearance's
            # confidence directly).
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "calibration": (
                    "minimum across cross-operator legal clearance confidence; "
                    "AND-composition: a strike is no more confident than its "
                    "least confident input"
                ),
                "acceptance_band": [0.0, 1.0],
                "propagation": "minimum",
            },
        },
        implementation_ref=authorise_impl_cid,
        credential_refs=(
            constitutional_cid, idf_root_cid, legal_root_cid, coop_cid,
            confidence_binding_cid, proportionality_binding_cid, meaningful_binding_cid,
        ),
        functional_refs=(
            confidence_policy.content_id(),
            proportionality_policy.content_id(),
            meaningful_policy.content_id(),
            legal_clearance.content_id(),
        ),
        state_refs=(
            confidence_impl_cid, proportionality_impl_cid, meaningful_impl_cid,
            legal_impl_cid,
        ),
    )
    code.put(authorise_strike)

    # Cross-operator audit machinery.
    audit_targeting = FunctionalUnit(
        name="audit_targeting",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"target_id": "str"}},
        implementation_ref=audit_impl_cid,
        credential_refs=(constitutional_cid, idf_root_cid, legal_root_cid, coop_cid),
    )
    code.put(audit_targeting)

    investigate_targeting = FunctionalUnit(
        name="investigate_targeting",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"target_id": "str"}},
        implementation_ref=investigate_impl_cid,
        credential_refs=(constitutional_cid, legal_root_cid, idf_root_cid, coop_cid),
        functional_refs=(audit_targeting.content_id(),),
        state_refs=(audit_impl_cid,),
    )
    code.put(investigate_targeting)

    # ---- Compile and register ----
    for u in (assess_target, confidence_policy, proportionality_policy, meaningful_policy):
        idf.runtime.register_compiled(compile_unit(u, code, creds, custodian=idf.custodian))
    # Cross-operator units witnessed by joint custodian.
    joint = coop.custodian
    idf.runtime.register_compiled(compile_unit(authorise_strike, code, creds, custodian=joint))
    idf.runtime.register_compiled(compile_unit(audit_targeting, code, creds, custodian=joint))
    legal.runtime.register_compiled(compile_unit(legal_clearance, code, creds, custodian=joint))
    legal.runtime.register_compiled(compile_unit(investigate_targeting, code, creds, custodian=joint))

    credential_names = {
        constitutional_cid: "Constitutional authority",
        idf_root_cid: "IDF (root)",
        legal_root_cid: "Legal Review (root)",
        coop_cid: "Cooperative substrate (IDF + Legal Review)",
        dedicated_reviewer_cid: "Capt. Lee (dedicated reviewer)",
        bulk_reviewer_cid: "bulk_approval reviewer (rubber-stamp marker)",
        operations_cid: "Operations officer",
        legal_officer_cid: "Maj. Amir (legal officer)",
    }

    return {
        "idf": idf, "legal": legal, "coop": coop,
        "operations_cid": operations_cid,
        "dedicated_reviewer_cid": dedicated_reviewer_cid,
        "bulk_reviewer_cid": bulk_reviewer_cid,
        "legal_officer_cid": legal_officer_cid,
        "assess_target": assess_target,
        "authorise_strike": authorise_strike,
        "legal_clearance": legal_clearance,
        "audit_targeting": audit_targeting,
        "investigate_targeting": investigate_targeting,
        "credential_names": credential_names,
    }


# =============================================================================
# Narrative
# =============================================================================

def _header(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _name(scene, cid):
    if not cid:
        return "(none)"
    return scene["credential_names"].get(cid, cid[:12] + "...")


def _try_strike(scene, target_id, reviewer_cid):
    """Run assess_target then authorise_strike for a target with a given reviewer."""
    idf = scene["idf"]
    legal = scene["legal"]
    assessment = idf.runtime.invoke(
        scene["assess_target"].content_id(),
        {"target_id": target_id},
        scene["operations_cid"],
    )
    if not isinstance(assessment, Permit):
        return ("assessment_refused", assessment)
    a = assessment.output
    strike = idf.runtime.invoke(
        scene["authorise_strike"].content_id(),
        {
            "target_id": target_id,
            "confidence": a["confidence"],
            "civilian_estimate": a["civilian_estimate"],
            "military_value_score": a["military_value_score"],
            "target_category": a["target_category"],
            "location_class": a["location_class"],
            "reviewer_credential_id": reviewer_cid,
            "reviewer_name": scene["credential_names"].get(reviewer_cid, ""),
            "legal_review_operator_id": legal.content_id,
            "legal_review_unit_id": scene["legal_clearance"].content_id(),
        },
        scene["operations_cid"],
    )
    return ("attempted", strike)


def _print_strike(scene, label, target_id, status, result):
    print(f"  {label} (target_id={target_id})")
    if status == "assessment_refused":
        print(f"    ASSESSMENT REFUSED")
        print(f"    rationale: {result.rationale}")
        return
    if isinstance(result, Permit):
        out = result.output
        print(f"    PERMIT  decision={out.get('decision')}")
        print(f"    reviewer: {_name(scene, out.get('reviewed_by'))}")
        print(f"    legal_clearance_act_id: {(out.get('legal_clearance_act_id') or '')[:16]}...")
    else:
        print(f"    REFUSE")
        print(f"    rationale: {result.rationale[:250]}{'...' if len(result.rationale) > 250 else ''}")


def main() -> int:
    scene = build_scene()
    idf = scene["idf"]
    legal = scene["legal"]

    _header("Setup")
    print(f"  Operators: IDF, LegalReview")
    print(f"  Cooperative substrate: IDF + LegalReview")
    print(f"  Cooperative custodian: quorum threshold {scene['coop'].custodian.threshold} of {len(scene['coop'].custodian.members)}")
    print(f"  Policies on authorise_strike:")
    print(f"    confidence_floor_policy:    refuses if confidence < 0.90")
    print(f"    proportionality_policy:     refuses if civilians/military_value > category bound")
    print(f"    meaningful_review_policy:   refuses if reviewer is missing or is a bulk-approval marker")
    print(f"  Cross-operator legal clearance required before strike is authorised.")

    # ---- The five target cases ----
    _header("Target 001: high-value commander, isolated compound, dedicated reviewer")
    s, r = _try_strike(scene, "target_001", scene["dedicated_reviewer_cid"])
    _print_strike(scene, "target_001:", "target_001", s, r)
    print(f"  EXPECTED: all policies permit; legal clearance permits; strike authorised.")

    _header("Target 002: low-ranking, low confidence (0.62)")
    s, r = _try_strike(scene, "target_002", scene["dedicated_reviewer_cid"])
    _print_strike(scene, "target_002:", "target_002", s, r)
    print(f"  EXPECTED: confidence_floor_policy refuses (0.62 < 0.90).")

    _header("Target 003: high-value commander, dense residential, civilians=40")
    s, r = _try_strike(scene, "target_003", scene["dedicated_reviewer_cid"])
    _print_strike(scene, "target_003:", "target_003", s, r)
    print(f"  EXPECTED: proportionality_policy refuses (40/50 = 0.80 > 0.30 bound).")

    _header("Target 004: high-value, but reviewer is a bulk-approval marker")
    s, r = _try_strike(scene, "target_004", scene["bulk_reviewer_cid"])
    _print_strike(scene, "target_004:", "target_004", s, r)
    print(f"  EXPECTED: meaningful_review_policy refuses (reviewer is bulk_approval).")

    _header("Target 005: high-value, but location is a hospital (protected site)")
    s, r = _try_strike(scene, "target_005", scene["dedicated_reviewer_cid"])
    _print_strike(scene, "target_005:", "target_005", s, r)
    print(f"  EXPECTED: authorise_strike's local policies all permit, but the")
    print(f"  cross-operator legal_clearance refuses. authorise_strike's impl")
    print(f"  catches that and returns decision=refer_to_human — the substrate-")
    print(f"  level Permit reflects that the unit ran correctly; the operational")
    print(f"  output is that no strike is authorised.")

    # ---- Drift demonstration ----
    _header("Drift: behaviour-characterised classifier moves out of calibration")
    print(f"  Five low-confidence assessments (mean confidence ~0.49) push the")
    print(f"  assess_target unit's running confidence below its 0.85 lower bound.")
    print(f"  The drift monitor invalidates the unit; subsequent assessments refuse.")
    print()
    for tid in ("target_low_001", "target_low_002", "target_low_003"):
        r = idf.runtime.invoke(scene["assess_target"].content_id(),
                                {"target_id": tid},
                                scene["operations_cid"])
        status = "PERMIT" if isinstance(r, Permit) else "REFUSE"
        details = (r.output.get("confidence") if isinstance(r, Permit) else r.rationale[:120])
        print(f"  {tid}: {status}  ({details})")
    # The low-confidence sample size is small; add two more zero-mean cases
    # to drive the window mean down to drift threshold.
    for tid in ("target_low_001", "target_low_002"):
        r = idf.runtime.invoke(scene["assess_target"].content_id(),
                                {"target_id": tid},
                                scene["operations_cid"])
        status = "PERMIT" if isinstance(r, Permit) else "REFUSE"
        details = (r.output.get("confidence") if isinstance(r, Permit) else r.rationale[:120])
        print(f"  {tid}: {status}  ({details})")
    drifted = idf.runtime.drift_monitor.is_drifted(scene["assess_target"].content_id())
    print(f"  drift_monitor.is_drifted(assess_target) = {drifted}")
    if drifted:
        r_after = idf.runtime.invoke(scene["assess_target"].content_id(),
                                      {"target_id": "target_001"},
                                      scene["operations_cid"])
        print(f"  Attempting a fresh assessment after drift:")
        print(f"    result: {'PERMIT' if isinstance(r_after, Permit) else 'REFUSE'}")
        if isinstance(r_after, Refuse):
            print(f"    rationale: {r_after.rationale[:200]}")

    # ---- Cross-operator audit ----
    _header("Legal Review investigates targeting record (cross-operator)")
    print()
    print(f"  Each target's record is reconstructable from the IDF ledger via the")
    print(f"  cooperative-substrate audit unit. Legal Review records its findings on")
    print(f"  its own ledger; the audit invocations are recorded on the IDF ledger.")
    for target in ("target_001", "target_003", "target_005"):
        result = legal.runtime.invoke(
            scene["investigate_targeting"].content_id(),
            {
                "target_id": target,
                "idf_operator_id": idf.content_id,
                "audit_unit_id": scene["audit_targeting"].content_id(),
            },
            scene["legal_officer_cid"],
        )
        print()
        if isinstance(result, Permit):
            r = result.output
            print(f"  Audit of {target}:")
            if r["verdict"] == "audit_completed":
                if r["strike_decisions"]:
                    for d in r["strike_decisions"]:
                        print(f"    strike decision: {d['decision']} (reviewer={_name(scene, d['reviewer'])})")
                        print(f"      civilians={d['civilian_estimate']}, military_value={d['military_value_score']}")
                if r["refusals"]:
                    for ref in r["refusals"][:1]:
                        print(f"    refusal recorded: {ref['rationale'][:200]}")
                if not r["strike_decisions"] and not r["refusals"]:
                    print(f"    no recorded acts for this target")
            else:
                print(f"    audit refused: {r.get('rationale')}")
        else:
            print(f"  Audit of {target}: REFUSED  rationale: {result.rationale}")

    # ---- Ledger summary ----
    _header("Ledger integrity")
    print()
    print(f"  IDF ledger: {len(idf.substrate.ledger)} acts; verify={idf.substrate.ledger.verify()}")
    print(f"  LegalReview ledger: {len(legal.substrate.ledger)} acts; verify={legal.substrate.ledger.verify()}")

    # ---- Closing ----
    _header("What the substrate would have demanded of any such system — summary")
    print()
    print(f"  1. BEHAVIOUR-CHARACTERISED CONTRACTS. The classifier declares its")
    print(f"     calibration commitments. The substrate's drift monitor enforces them.")
    print(f"     A classifier observed to operate out of calibration is invalidated.")
    print()
    print(f"  2. CONFIDENCE FLOOR. A policy refuses authorisation when classifier")
    print(f"     confidence is below the calibration floor. Low-confidence outputs")
    print(f"     cannot reach strike authorisation; they refuse structurally.")
    print()
    print(f"  3. PROPORTIONALITY. Per-invocation policy applies a category-specific")
    print(f"     civilians-to-military-value bound. Pre-authorising bulk thresholds")
    print(f"     would require substituting a more permissive policy unit — a new")
    print(f"     content_id, visible in any audit.")
    print()
    print(f"  4. MEANINGFUL HUMAN REVIEW. The reviewer's credential is structurally")
    print(f"     recorded. Bulk-approval credentials refuse; patterns of misuse are")
    print(f"     visible on the ledger.")
    print()
    print(f"  5. INDEPENDENT LEGAL REVIEW. The cooperative substrate gives Legal")
    print(f"     Review binding clearance authority. Strikes cannot be authorised")
    print(f"     without cross-operator legal clearance; the legal review invocation")
    print(f"     is itself a substrate event.")
    print()
    print(f"  THE LIMIT: natural-person anchoring. The substrate's authority chain")
    print(f"  in this demonstration terminates at 'constitutional_authority' — a")
    print(f"  string-labelled credential with a random Ed25519 keypair. It does NOT")
    print(f"  terminate at an actual natural person. For any real military application,")
    print(f"  this gap is decisive: until constitutional source credentials are")
    print(f"  cryptographically anchored to specific natural persons whose actions")
    print(f"  are attributable, the substrate's claim that 'strike decisions are")
    print(f"  attributable' is structural-only, not real. Closing this gap is the")
    print(f"  substrate's deepest unfilled architectural commitment.")
    print()
    print(f"  THE OTHER LIMIT: adoption. The substrate's properties take effect only")
    print(f"  when an operator chooses to deploy them honestly. The substrate makes")
    print(f"  the absence visible (every audit can see whether constraints were")
    print(f"  declared and checked); whether the visibility produces accountability")
    print(f"  is downstream of the architecture and outside what the substrate")
    print(f"  itself can guarantee.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
