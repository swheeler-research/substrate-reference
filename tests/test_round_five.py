"""Round five of the hostile review, in code.

1. The trust root counts distinct keys, and a quorum member's single
   signature is not a quorum witness.
2. The implementation facade holds no attribute referring to the runtime.
3. A compilation-integrity failure is an invalidation event: an
   administrative act on the ledger, after which the form is refused on
   that ground, and the invalidation survives a restart.
4. A confidence gate with a subject field refuses an act for another
   subject (exercised on the Lavender scene in test_lavender_review).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import dataclasses

from substrate.archives import CodeArchive, CredentialsArchive as CredentialArchive
from substrate.compile import compile_unit
from substrate.federation import (
    LocalCustodian, QuorumCustodian, verify_compiled_form_trusted,
)
from substrate.ledger import FederatedLedger as Ledger
from substrate.primitives import (
    ContractPattern, CredentialUnit, FunctionalUnit, TransferDiscipline,
)
from substrate.implementations import python_implementation
from substrate.runtime import Runtime, Refuse, Permit, UntrustedWitness


_PASS = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"ok": True}
"""


def _cred(name, refs=()):
    return CredentialUnit(
        name=name, transfer=TransferDiscipline.DELEGATED, principal=name,
        authorities=("invoke:any",), credential_refs=tuple(refs),
    )


def _unit(code, creds):
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("op", (root_cid,)))
    unit = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED, spec={},
        implementation_ref=code.put(python_implementation(_PASS)), credential_refs=(root_cid,),
    )
    unit_cid = code.put(unit)
    return unit, unit_cid, op_cid


def test_a_quorum_members_single_signature_is_not_a_quorum_witness():
    code, creds = CodeArchive(), CredentialArchive()
    unit, unit_cid, op_cid = _unit(code, creds)
    a, b, c = LocalCustodian("a"), LocalCustodian("b"), LocalCustodian("c")
    quorum = QuorumCustodian(members=(a, b, c), threshold=2, name="q")
    rt = Runtime(code, creds, Ledger())
    rt.trust_custodian(quorum)
    assert rt.witness_is_trusted(compile_unit(unit, code, creds, custodian=quorum))
    # Member a alone, as a single signature: refused. Membership is not quorum.
    assert not rt.witness_is_trusted(compile_unit(unit, code, creds, custodian=a))
    with pytest.raises(UntrustedWitness):
        rt.register_compiled(compile_unit(unit, code, creds, custodian=a))


def test_the_trust_root_counts_distinct_keys_not_names():
    code, creds = CodeArchive(), CredentialArchive()
    unit, unit_cid, op_cid = _unit(code, creds)
    a, b, c = LocalCustodian("a"), LocalCustodian("b"), LocalCustodian("c")
    quorum = QuorumCustodian(members=(a, b, c), threshold=2, name="q")
    form = compile_unit(unit, code, creds, custodian=quorum)
    payload = form.witness_payload
    contribs = payload["contributions"]
    # Keep one genuine contribution and present it twice under two names.
    name, contrib = next(iter(contribs.items()))
    doctored = dict(payload)
    doctored["contributions"] = {name: contrib, name + "_again": dict(contrib)}
    doctored["threshold"] = 2
    forged = dataclasses.replace(form, witness_payload=doctored)
    quorum_keys = {m.public_key_hex for m in quorum.members}
    assert not verify_compiled_form_trusted(forged, (), [(quorum_keys, 2)])


def test_the_verifier_threshold_governs_over_the_payloads():
    code, creds = CodeArchive(), CredentialArchive()
    unit, unit_cid, op_cid = _unit(code, creds)
    a, b, c = LocalCustodian("a"), LocalCustodian("b"), LocalCustodian("c")
    loose = QuorumCustodian(members=(a, b, c), threshold=1, name="q")
    form = compile_unit(unit, code, creds, custodian=loose)
    quorum_keys = {m.public_key_hex for m in loose.members}
    payload = form.witness_payload
    name, contrib = next(iter(payload["contributions"].items()))
    one = dataclasses.replace(form, witness_payload={**payload, "contributions": {name: contrib}})
    assert verify_compiled_form_trusted(one, (), [(quorum_keys, 1)])
    # A verifier that declared threshold 2 refuses a one-signature witness
    # whatever the payload says.
    assert not verify_compiled_form_trusted(one, (), [(quorum_keys, 2)])


_PROBE = """
def implementation(inputs, runtime, invoking_credential_id):
    leaks = []
    for name in dir(runtime):
        if name.startswith("__") and name not in ("__iter__", "__len__"):
            continue
        obj = getattr(runtime, name)
        if type(obj).__name__ == "Runtime":
            leaks.append(name)
    for name in ("ledger", "code", "drift_monitor"):
        view = getattr(runtime, name)
        for attr in dir(view):
            if attr.startswith("__"):
                continue
            if type(getattr(view, attr)).__name__ in ("Ledger", "CodeArchive", "DriftMonitor"):
                leaks.append(name + "." + attr)
    return {"leaks": leaks, "slots": list(getattr(type(runtime), "__slots__", ()))}
"""


def test_the_facade_holds_no_attribute_referring_to_the_runtime():
    code, creds = CodeArchive(), CredentialArchive()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("op", (root_cid,)))
    unit = FunctionalUnit(
        name="probe", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED, spec={},
        implementation_ref=code.put(python_implementation(_PROBE)), credential_refs=(root_cid,),
    )
    unit_cid = code.put(unit)
    own = LocalCustodian("ours")
    rt = Runtime(code, creds, Ledger()); rt.trust_custodian(own)
    rt.register_compiled(compile_unit(unit, code, creds, custodian=own))
    r = rt.invoke(unit_cid, {}, op_cid)
    assert isinstance(r, Permit), getattr(r, "rationale", None)
    assert r.output["leaks"] == []
    assert "_rt" not in r.output["slots"]


def test_an_integrity_failure_is_recorded_and_survives_restart():
    code, creds = CodeArchive(), CredentialArchive()
    unit, unit_cid, op_cid = _unit(code, creds)
    own, other = LocalCustodian("ours"), LocalCustodian("other")
    ledger = Ledger()
    rt = Runtime(code, creds, ledger)
    form = compile_unit(unit, code, creds, custodian=own)
    rt.register_compiled(form)            # bare mode: accepted
    rt.trust_custodian(other)             # the trust root is then declared
    r = rt.invoke(unit_cid, {}, op_cid)
    assert isinstance(r, Refuse) and "integrity" in r.rationale
    admin = [a for a in ledger if a.kind == "administrative"
             and a.inputs.get("action") == "integrity_failure"]
    assert len(admin) == 1
    assert admin[0].invoking_credential_id == ""          # mechanism-authored
    assert admin[0].inputs["compiled_form"] == form.content_id()
    assert admin[0].governance_tick is not None
    # Again: refused on the recorded invalidation, no second act.
    r2 = rt.invoke(unit_cid, {}, op_cid)
    assert isinstance(r2, Refuse) and "invalidated" in r2.rationale
    assert len([a for a in ledger if a.kind == "administrative"]) == 1
    # Restart on the same ledger, with the form now trusted: still invalidated.
    rt2 = Runtime(code, creds, ledger)
    rt2.trust_custodian(own)
    rt2.register_compiled(form)
    r3 = rt2.invoke(unit_cid, {}, op_cid)
    assert isinstance(r3, Refuse) and "invalidated" in r3.rationale


_PRODUCER = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"subject": inputs["subject"], "confidence": 0.99}
"""


def test_a_gate_with_a_subject_field_refuses_an_act_for_another_subject():
    code, creds = CodeArchive(), CredentialArchive()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("op", (root_cid,)))
    producer = FunctionalUnit(
        name="producer", contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"confidence": {"produces": True, "output_field": "confidence",
                             "calibration": "fixed", "acceptance_band": [0.0, 1.0]}},
        implementation_ref=code.put(python_implementation(_PRODUCER)), credential_refs=(root_cid,),
    )
    producer_cid = code.put(producer)
    consumer = FunctionalUnit(
        name="consumer", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence_gate": {"minimum_confidence": 0.9, "applies_to_field": "confidence",
                                  "source": "act", "act_field": "evidence",
                                  "producing_unit": producer_cid, "subject_field": "subject"}},
        implementation_ref=code.put(python_implementation(_PASS)), credential_refs=(root_cid,),
    )
    consumer_cid = code.put(consumer)
    own = LocalCustodian("ours")
    rt = Runtime(code, creds, Ledger()); rt.trust_custodian(own)
    rt.register_compiled(compile_unit(producer, code, creds, custodian=own))
    rt.register_compiled(compile_unit(consumer, code, creds, custodian=own))
    ev = rt.invoke(producer_cid, {"subject": "alpha"}, op_cid)
    assert isinstance(ev, Permit)
    ok = rt.invoke(consumer_cid, {"subject": "alpha", "evidence": ev.act_id}, op_cid)
    assert isinstance(ok, Permit), ok.rationale
    wrong = rt.invoke(consumer_cid, {"subject": "beta", "evidence": ev.act_id}, op_cid)
    assert isinstance(wrong, Refuse)
    assert "concerns subject 'alpha', not 'beta'" in wrong.rationale


def test_an_operator_in_two_cooperative_substrates_holds_two_quorums():
    """The LIBOR scene: a bank belongs to the panel arrangement and to the
    audit arrangement, with different memberships and thresholds. A form
    witnessed by either quorum verifies on the bank's runtime; a quorum
    short of either threshold does not."""
    code, creds = CodeArchive(), CredentialArchive()
    unit, unit_cid, op_cid = _unit(code, creds)
    a, b, c, d = (LocalCustodian(n) for n in "abcd")
    panel = QuorumCustodian(members=(a, b, c), threshold=3, name="panel")
    audit = QuorumCustodian(members=(a, b, c, d), threshold=4, name="audit")
    rt = Runtime(code, creds, Ledger())
    rt.trust_custodian(panel); rt.trust_custodian(audit)
    assert rt.witness_is_trusted(compile_unit(unit, code, creds, custodian=panel))
    assert rt.witness_is_trusted(compile_unit(unit, code, creds, custodian=audit))
    short = QuorumCustodian(members=(a, b), threshold=2, name="short")
    assert not rt.witness_is_trusted(compile_unit(unit, code, creds, custodian=short))


def test_a_refused_parent_does_not_consume_the_cited_act():
    """Round five, finding 21. All policies evaluate, so the gate's own
    permit used to consume the assessment even when another policy refused
    the strike; the retry with the right reviewer was then refused as
    already consumed. The consumption record is now the governed unit's
    permit."""
    import io, contextlib
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from examples.lavender import run as lv
    with contextlib.redirect_stdout(io.StringIO()):
        scene = lv.build_scene()
        status, first = lv._try_strike(scene, "target_001", scene["bulk_reviewer_cid"])
        assert isinstance(first, Refuse) and "review authority" in first.rationale
        assessment_act = scene["idf"].runtime.act_by_id(
            [a for a in scene["idf"].runtime.ledger if a.kind == "invocation"
             and isinstance(a.output_or_rationale, dict)
             and a.output_or_rationale.get("assessed_by") == "automated_classifier"][-1].content_id()
        ).content_id()
        status, second = lv._try_strike(scene, "target_001", scene["dedicated_reviewer_cid"],
                                        assessment_act=assessment_act)
    assert isinstance(second, Permit), getattr(second, "rationale", None)
    with contextlib.redirect_stdout(io.StringIO()):
        status, third = lv._try_strike(scene, "target_001", scene["dedicated_reviewer_cid"],
                                       assessment_act=assessment_act)
    assert isinstance(third, Refuse) and "already consumed" in third.rationale
