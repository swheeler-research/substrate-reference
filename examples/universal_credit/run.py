"""
End-to-end Universal Credit advance payment run.

Demonstrates the substrate's central architectural commitments under the
revisions made through Phase 1 and Phase 1.5:

- Three primitive types; composition through typed references.
- Compile-at-commit producing immutable content-addressed compiled forms.
- Implementations are content-addressed state units (the Horizon
  correction): the runtime executes the exact artefact referenced by the
  compiled form.
- Policies are functional units (the policies-as-functions unification):
  each policy is invoked by the runtime before the source unit's
  implementation runs. Refuse-wins gives strictest-binding-wins for free.
- Refusal-as-first-class-output: every refused invocation produces a
  ledger entry with a rationale naming the policy that refused, and
  carrying the policy invocation's act_id so the audit trail can be
  followed back.
- Hash-chained federated ledger verifiable end-to-end.

Run with: python -m examples.universal_credit.run
"""

import sys
from pathlib import Path

# Allow running directly from the repository without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.composition import compose_refs
from substrate.ledger import FederatedLedger
from substrate.runtime import Permit, Refuse, Runtime

from examples.universal_credit import credentials as creds_mod
from examples.universal_credit import policies as policies_mod
from examples.universal_credit import units as units_mod
from examples.universal_credit import composition as composition_mod


def build_and_compile():
    code = CodeArchive()
    creds = CredentialsArchive()
    ledger = FederatedLedger()

    # Authority chain.
    parliament_cid = creds.put(creds_mod.parliament())
    dwp_cid = creds.put(creds_mod.dwp(parliament_cid))
    caseworker_cid = creds.put(creds_mod.caseworker(dwp_cid))

    # Policies. Each policy is a (functional unit, implementation state
    # unit, binding credential) triple. Put the impl and the unit in the
    # code archive; put the binding credential in the credentials archive.
    def install_policy(policy_pair):
        policy_fn, impl, binding = policy_pair
        code.put(impl)
        code.put(policy_fn)
        binding_cid = creds.put(binding)
        return policy_fn, binding_cid

    r30_fn, r30_binding = install_policy(policies_mod.retention_policy_pair(30))
    r60_fn, r60_binding = install_policy(policies_mod.retention_policy_pair(60))
    r90_fn, r90_binding = install_policy(policies_mod.retention_policy_pair(90))
    c90_fn, c90_binding = install_policy(policies_mod.confidence_policy_pair(0.90))
    c95_fn, c95_binding = install_policy(policies_mod.confidence_policy_pair(0.95))
    p_strict_fn, p_strict_binding = install_policy(
        policies_mod.purpose_policy_pair(["eligibility"])
    )
    p_fraud_fn, p_fraud_binding = install_policy(
        policies_mod.purpose_policy_pair(["eligibility", "fraud"])
    )
    p_any_fn, p_any_binding = install_policy(
        policies_mod.purpose_policy_pair(["eligibility", "fraud", "identity"])
    )

    all_policy_fns = [r30_fn, r60_fn, r90_fn, c90_fn, c95_fn, p_strict_fn, p_fraud_fn, p_any_fn]

    # Sub-unit implementations.
    eligibility_impl_cid = code.put(composition_mod.trivial_eligibility_impl())
    fraud_impl_cid = code.put(composition_mod.trivial_fraud_impl())
    identity_impl_cid = code.put(composition_mod.trivial_identity_impl())

    # Sub-units. Each cites the authoring authority chain, the binding
    # credentials for its policies, and (for wilful inclusion) every
    # transitively-reachable unit. compose_refs computes that closure for
    # the policies the sub-unit binds, so the sub-unit's author does not
    # have to enumerate each policy's implementation state unit by hand.
    def sub_unit_refs(*policy_fns):
        return compose_refs(*policy_fns, code_archive=code, credentials_archive=creds)

    eligibility_refs = sub_unit_refs(r30_fn, p_strict_fn)
    eligibility = units_mod.eligibility_check(
        implementation_ref=eligibility_impl_cid,
        credential_refs=(parliament_cid, dwp_cid, r30_binding, p_strict_binding),
        functional_refs=eligibility_refs.functional,
        state_refs=eligibility_refs.state,
    )
    fraud_refs = sub_unit_refs(r60_fn, c90_fn, p_fraud_fn)
    fraud = units_mod.fraud_risk_assessment(
        implementation_ref=fraud_impl_cid,
        credential_refs=(parliament_cid, dwp_cid, r60_binding, c90_binding, p_fraud_binding),
        functional_refs=fraud_refs.functional,
        state_refs=fraud_refs.state,
    )
    identity_refs = sub_unit_refs(r90_fn, c95_fn, p_any_fn)
    identity = units_mod.identity_verification(
        implementation_ref=identity_impl_cid,
        credential_refs=(parliament_cid, dwp_cid, r90_binding, c95_binding, p_any_binding),
        functional_refs=identity_refs.functional,
        state_refs=identity_refs.state,
    )
    code.put(eligibility)
    code.put(fraud)
    code.put(identity)

    # Composing unit implementation.
    composing_impl = composition_mod.make_advance_payment_implementation(
        eligibility_source_cid=eligibility.content_id(),
        fraud_source_cid=fraud.content_id(),
        identity_source_cid=identity.content_id(),
    )
    composing_impl_cid = code.put(composing_impl)

    # compose_refs walks every reference type (including credential
    # policy_refs and functional implementation_refs) so the composing
    # unit's tuples cover the full transitive closure.
    refs = compose_refs(
        eligibility, fraud, identity,
        code_archive=code,
        credentials_archive=creds,
    )
    composing = composition_mod.advance_payment_decision(
        implementation_ref=composing_impl_cid,
        constituent_unit_cids=refs.functional,
        state_cids=refs.state,
        credential_cids=refs.credential,
    )
    code.put(composing)

    # Compile and register every unit the runtime may invoke. A single
    # operator's substrate has one custodian; every unit it admits is
    # witnessed by that custodian. We import and use a fresh
    # LocalCustodian here for the demonstration; in a real deployment
    # the operator's substrate has a persistent custodian whose private
    # key is part of its institutional identity.
    from substrate.federation import LocalCustodian
    custodian = LocalCustodian(name="dwp_custodian")
    runtime = Runtime(code, creds, ledger)
    for unit in (composing, eligibility, fraud, identity, *all_policy_fns):
        runtime.register_compiled(compile_unit(unit, code, creds, custodian=custodian))

    return runtime, composing.content_id(), caseworker_cid


def _print_compiled_form(runtime, source_cid):
    compiled = runtime.compiled_for(source_cid)
    source_unit = runtime.code.get(compiled.source_unit)
    print("=" * 70)
    print("Compiled form for advance_payment_decision")
    print("=" * 70)
    print(f"  source unit:       {compiled.source_unit[:16]}...")
    print(f"  compiled form id:  {compiled.content_id()[:16]}...")
    print(f"  authority chain:   {len(compiled.authority_chain)} credentials")
    print(f"  policies in scope: {len(compiled.policies)} functional units")
    for cid in compiled.policies:
        policy_unit = runtime.code.get(cid)
        print(f"    {cid[:16]}...  ({policy_unit.name})")
    print(f"  fused_form:        {compiled.fused_form}")
    print(f"  cached_env:        {compiled.cached_environment}")
    print(f"  dispatch_table:    {compiled.dispatch_table}")
    print(f"  witness:           {compiled.witness[:16]}...")
    print()
    print(f"  source unit's implementation_ref: {source_unit.implementation_ref[:16]}...")
    impl_state = runtime.code.get(source_unit.implementation_ref)
    src_preview = impl_state.content['source'].strip().splitlines()[0][:70]
    print(f"  implementation language: {impl_state.content['language']}")
    print(f"  implementation source (first line): {src_preview}")
    print()


def _print_result(label, result):
    print(f"--- {label} ---")
    if isinstance(result, Permit):
        print(f"  PERMIT")
        print(f"  output:   {result.output}")
        print(f"  act id:   {result.act_id[:16]}...")
    elif isinstance(result, Refuse):
        print(f"  REFUSE")
        print(f"  rationale: {result.rationale}")
        print(f"  act id:    {result.act_id[:16]}...")
    print()


def _print_ledger(ledger, runtime):
    print("=" * 70)
    print(f"Ledger ({len(ledger)} acts)")
    print("=" * 70)
    for i, act in enumerate(ledger):
        prev = act.previous_act_id[:16] if act.previous_act_id else "(genesis)"
        # Look up the compiled form to find the unit's name.
        try:
            cf = runtime.code.get(act.compiled_form_id)
            unit = runtime.code.get(cf.source_unit)
            unit_name = unit.name
        except Exception:
            unit_name = "(unknown)"
        print(f"  [{i}] verdict={act.verdict}  unit={unit_name}")
        print(f"      act id:        {act.content_id()[:16]}...")
        print(f"      prev act id:   {prev}")
        if act.verdict == "permit":
            out = act.output_or_rationale
            if isinstance(out, dict) and "decision" in out:
                print(f"      decision:      {out.get('decision')} ({out.get('rationale', '')})")
            else:
                preview = repr(out)
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                print(f"      output:        {preview}")
        else:
            print(f"      rationale:     {act.output_or_rationale}")
    print()
    print(f"  ledger.verify() -> {ledger.verify()}")
    print()


def main() -> int:
    runtime, composing_cid, caseworker_cid = build_and_compile()
    _print_compiled_form(runtime, composing_cid)

    good_inputs = {
        "applicant_id": "person_abc",
        "household_income_pence": 80_000,
        "household_size": 2,
        "requested_amount_pence": 50_00,
        "min_confidence": 0.97,
        "max_retention_days": 30,
        "allowed_purposes": "eligibility",
    }

    result1 = runtime.invoke(composing_cid, good_inputs, caseworker_cid)
    _print_result("Case 1: valid inputs (every policy permits)", result1)

    over_retention = dict(good_inputs)
    over_retention["max_retention_days"] = 60
    result2 = runtime.invoke(composing_cid, over_retention, caseworker_cid)
    _print_result("Case 2: retention 60 exceeds strictest cap (30); 30-day policy refuses", result2)

    wrong_purpose = dict(good_inputs)
    wrong_purpose["allowed_purposes"] = "fraud"
    result3 = runtime.invoke(composing_cid, wrong_purpose, caseworker_cid)
    _print_result("Case 3: purpose 'fraud' outside strictest allowed set ['eligibility']", result3)

    low_conf = dict(good_inputs)
    low_conf["min_confidence"] = 0.92
    result4 = runtime.invoke(composing_cid, low_conf, caseworker_cid)
    _print_result("Case 4: confidence 0.92 below strictest floor (0.95)", result4)

    runtime.credentials.revoke(caseworker_cid)
    result5 = runtime.invoke(composing_cid, good_inputs, caseworker_cid)
    _print_result("Case 5: invoking credential revoked (refused before any policy invoked)", result5)

    _print_ledger(runtime.ledger, runtime)

    # Case 6: demonstrate compile-time type-mismatch detection. Try to
    # compose a policy that requires max_retention_days >= 90 alongside
    # the existing 30-day cap. The compiler refuses the composition before
    # any runtime invocation; no ledger entry, no executable artefact, just
    # a structured refusal naming the variable and the contributing units.
    print("=" * 70)
    print("Case 6: incomposable policies refused at compile-at-commit")
    print("=" * 70)
    _demonstrate_compile_time_type_mismatch()
    return 0


def _demonstrate_compile_time_type_mismatch():
    from substrate.compile import compile_unit, TypeMismatch

    code = CodeArchive()
    creds = CredentialsArchive()
    parliament_cid = creds.put(creds_mod.parliament())

    strict = policies_mod.retention_policy_pair(30)
    contradictory_pair = policies_mod.retention_policy_pair(90)
    # Manually override one policy's spec to require >= 90 (the helpers
    # only build <= caps). Inline construction here since this is
    # demonstration scaffolding, not the canonical pattern.
    from substrate.implementations import python_implementation
    from substrate.primitives import ContractPattern, FunctionalUnit
    contradictory_impl = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n"
        "    if inputs.get('max_retention_days', 0) < 90:\n"
        "        raise Exception('requested retention below floor of 90')\n"
        "    return {}\n"
    )
    contradictory = FunctionalUnit(
        name="retention_min_90_days",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "name": "retention_min_90_days",
            "preconditions": [{"var": "max_retention_days", "op": "gte", "value": 90}],
        },
        implementation_ref=code.put(contradictory_impl),
    )
    code.put(contradictory)

    # Install the strict cap normally.
    code.put(strict[1])
    code.put(strict[0])
    strict_binding_cid = creds.put(strict[2])
    contradictory_binding = policies_mod._binding(
        "retention_min_90_binding", contradictory.content_id()
    )
    contradictory_binding_cid = creds.put(contradictory_binding)

    unit = FunctionalUnit(
        name="incomposable",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref="",
        credential_refs=(parliament_cid, strict_binding_cid, contradictory_binding_cid),
        functional_refs=(strict[0].content_id(), contradictory.content_id()),
        state_refs=(strict[0].implementation_ref, contradictory.implementation_ref),
    )
    code.put(unit)

    try:
        compile_unit(unit, code, creds)
        print("  UNEXPECTED: compile_unit succeeded; the type-mismatch check missed the conflict")
    except TypeMismatch as exc:
        print(f"  COMPILE REFUSED (TypeMismatch)")
        print(f"  rationale: {exc}")
    print()


if __name__ == "__main__":
    sys.exit(main())
