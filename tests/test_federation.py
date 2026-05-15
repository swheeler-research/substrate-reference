"""Tests for the federation custodian stub and the CooperativeSubstrate registry."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.federation import (
    CooperativeSubstrate,
    LocalCustodian,
    QuorumCustodian,
    QuorumNotMet,
    WitnessRequest,
    WitnessResponse,
)
from substrate.operator import create_operator
from substrate.primitives import CredentialUnit, TransferDiscipline


def test_local_custodian_returns_a_signed_response():
    cust = LocalCustodian()
    response = cust.witness(WitnessRequest(payload_hash="a" * 64))
    assert isinstance(response, WitnessResponse)
    assert response.custodian == "local"
    # An Ed25519 signature is 64 bytes = 128 hex characters.
    assert len(response.signature) == 128


def test_witness_is_deterministic_for_same_input():
    """Ed25519 is deterministic: the same key signing the same payload yields the same signature."""
    cust = LocalCustodian(name="alpha")
    r1 = cust.witness(WitnessRequest(payload_hash="a" * 64))
    r2 = cust.witness(WitnessRequest(payload_hash="a" * 64))
    assert r1.signature == r2.signature


def test_witness_differs_across_custodians():
    """Two custodians have different keypairs, so different signatures."""
    a = LocalCustodian(name="alpha").witness(WitnessRequest(payload_hash="a" * 64))
    b = LocalCustodian(name="beta").witness(WitnessRequest(payload_hash="a" * 64))
    assert a.signature != b.signature


def test_witness_differs_across_payloads():
    cust = LocalCustodian()
    a = cust.witness(WitnessRequest(payload_hash="a" * 64))
    b = cust.witness(WitnessRequest(payload_hash="b" * 64))
    assert a.signature != b.signature


def test_local_custodian_has_public_key_hex():
    """The public key is a 32-byte (64-hex-char) Ed25519 verifying key."""
    cust = LocalCustodian()
    assert isinstance(cust.public_key_hex, str)
    assert len(cust.public_key_hex) == 64
    # Two custodians have different public keys.
    assert LocalCustodian().public_key_hex != cust.public_key_hex


def test_local_custodian_seeded_from_private_key_bytes():
    """Supplying the same 32-byte seed yields the same keypair and signatures."""
    seed = b"\x01" * 32
    cust_a = LocalCustodian("a", private_key_bytes=seed)
    cust_b = LocalCustodian("b", private_key_bytes=seed)
    # Same key material -> same public key, same signature on same payload.
    assert cust_a.public_key_hex == cust_b.public_key_hex
    payload = WitnessRequest(payload_hash="a" * 64)
    assert cust_a.witness(payload).signature == cust_b.witness(payload).signature


def test_local_custodian_rejects_non_32_byte_seed():
    with pytest.raises(ValueError, match="32 bytes"):
        LocalCustodian("x", private_key_bytes=b"short")


# =============================================================================
# Witness verification
# =============================================================================

def test_verify_witness_accepts_valid_signature():
    from substrate.federation import verify_witness
    cust = LocalCustodian()
    payload = "deadbeef" * 8  # 64 hex chars
    response = cust.witness(WitnessRequest(payload_hash=payload))
    assert verify_witness(cust.public_key_hex, payload, response.signature) is True


def test_verify_witness_rejects_tampered_signature():
    from substrate.federation import verify_witness
    cust = LocalCustodian()
    payload = "deadbeef" * 8
    response = cust.witness(WitnessRequest(payload_hash=payload))
    # Flip a byte in the signature.
    tampered = ("00" if response.signature[:2] != "00" else "ff") + response.signature[2:]
    assert verify_witness(cust.public_key_hex, payload, tampered) is False


def test_verify_witness_rejects_wrong_public_key():
    from substrate.federation import verify_witness
    cust = LocalCustodian()
    other = LocalCustodian()
    payload = "deadbeef" * 8
    response = cust.witness(WitnessRequest(payload_hash=payload))
    assert verify_witness(other.public_key_hex, payload, response.signature) is False


def test_verify_witness_rejects_different_payload():
    from substrate.federation import verify_witness
    cust = LocalCustodian()
    payload = "deadbeef" * 8
    other_payload = "cafebabe" * 8
    response = cust.witness(WitnessRequest(payload_hash=payload))
    assert verify_witness(cust.public_key_hex, other_payload, response.signature) is False


def test_verify_witness_handles_malformed_inputs():
    from substrate.federation import verify_witness
    # Not hex.
    assert verify_witness("zzz", "deadbeef" * 8, "00" * 64) is False
    # Wrong length signature.
    assert verify_witness("00" * 32, "deadbeef" * 8, "00") is False


# =============================================================================
# Quorum witness verification
# =============================================================================

def test_verify_quorum_witness_accepts_valid_multi_sig():
    from substrate.federation import verify_quorum_witness
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    q = QuorumCustodian(members=(a, b), threshold=2, name="ab")
    payload = "deadbeef" * 8
    response = q.witness(WitnessRequest(payload_hash=payload))
    # The structured multi-sig is attached as quorum_payload.
    assert verify_quorum_witness(payload, response.witness_payload) is True


def test_verify_quorum_witness_rejects_tampered_signature():
    from substrate.federation import verify_quorum_witness
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    q = QuorumCustodian(members=(a, b), threshold=2, name="ab")
    payload = "deadbeef" * 8
    response = q.witness(WitnessRequest(payload_hash=payload))
    # Tamper with one member's signature.
    tampered = dict(response.witness_payload)
    tampered["contributions"] = dict(tampered["contributions"])
    first_member = next(iter(tampered["contributions"]))
    tampered["contributions"][first_member] = dict(tampered["contributions"][first_member])
    tampered["contributions"][first_member]["signature"] = "00" * 64
    # With threshold=2 and one tampered (so 1 valid), verification fails.
    assert verify_quorum_witness(payload, tampered) is False


def test_verify_quorum_witness_accepts_below_threshold_tampering_if_threshold_lower():
    """If threshold is 1 and one member's sig is valid, multi-sig verifies."""
    from substrate.federation import verify_quorum_witness
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    q = QuorumCustodian(members=(a, b), threshold=1, name="ab")
    payload = "deadbeef" * 8
    response = q.witness(WitnessRequest(payload_hash=payload))
    # Tamper with one signature; threshold=1 means one good signature is still enough.
    tampered = dict(response.witness_payload)
    tampered["contributions"] = dict(tampered["contributions"])
    first_member = next(iter(tampered["contributions"]))
    tampered["contributions"][first_member] = dict(tampered["contributions"][first_member])
    tampered["contributions"][first_member]["signature"] = "00" * 64
    assert verify_quorum_witness(payload, tampered) is True


def test_verify_compiled_form_with_single_custodian():
    """A compiled form witnessed by a LocalCustodian is self-contained for verification."""
    from substrate.archives import CodeArchive, CredentialsArchive
    from substrate.compile import compile_unit
    from substrate.federation import verify_compiled_form
    from substrate.primitives import ContractPattern, FunctionalUnit

    code = CodeArchive()
    creds = CredentialsArchive()
    parl_cid = creds.put(_root("parliament"))
    unit = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={}, credential_refs=(parl_cid,),
    )
    code.put(unit)
    custodian = LocalCustodian("test")
    cf = compile_unit(unit, code, creds, custodian=custodian)
    assert verify_compiled_form(cf) is True


def test_verify_compiled_form_with_quorum_custodian():
    """A compiled form witnessed by a QuorumCustodian also verifies self-contained."""
    from substrate.archives import CodeArchive, CredentialsArchive
    from substrate.compile import compile_unit
    from substrate.federation import verify_compiled_form
    from substrate.primitives import ContractPattern, FunctionalUnit

    code = CodeArchive()
    creds = CredentialsArchive()
    parl_cid = creds.put(_root("parliament"))
    unit = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={}, credential_refs=(parl_cid,),
    )
    code.put(unit)
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    quorum = QuorumCustodian(members=(a, b), threshold=2, name="ab")
    cf = compile_unit(unit, code, creds, custodian=quorum)
    assert verify_compiled_form(cf) is True


def test_verify_compiled_form_rejects_tampered_compiled_form():
    """Mutating the compiled form's content invalidates its witness."""
    from dataclasses import replace
    from substrate.archives import CodeArchive, CredentialsArchive
    from substrate.compile import compile_unit
    from substrate.federation import verify_compiled_form
    from substrate.primitives import ContractPattern, FunctionalUnit

    code = CodeArchive()
    creds = CredentialsArchive()
    parl_cid = creds.put(_root("parliament"))
    unit = FunctionalUnit(
        name="u", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={}, credential_refs=(parl_cid,),
    )
    code.put(unit)
    cf = compile_unit(unit, code, creds, custodian=LocalCustodian("test"))
    assert verify_compiled_form(cf) is True

    # Tamper with the compiled form: swap in a different (bogus) source_unit cid.
    # The unwitnessed payload now hashes to something the signature was not
    # made over; verification fails.
    tampered = replace(cf, source_unit="0" * 64)
    assert verify_compiled_form(tampered) is False


def test_verify_quorum_witness_rejects_payload_substitution():
    """Verification fails if the recorded payload_hash does not match the claimed payload."""
    from substrate.federation import verify_quorum_witness
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    q = QuorumCustodian(members=(a, b), threshold=2, name="ab")
    payload = "deadbeef" * 8
    response = q.witness(WitnessRequest(payload_hash=payload))
    assert verify_quorum_witness("cafebabe" * 8, response.witness_payload) is False


# =============================================================================
# CooperativeSubstrate registry
# =============================================================================

def _root(name):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("delegate:any",),
    )


def test_cooperative_substrate_starts_empty():
    coop = CooperativeSubstrate()
    assert len(coop) == 0
    assert coop.members() == ()
    assert coop.content_id is None  # no cooperative credential attached


def test_cooperative_substrate_add_and_lookup_by_content_id():
    coop = CooperativeSubstrate()
    op_a = create_operator("DWP", _root("dwp"))
    op_b = create_operator("HomeOffice", _root("ho"))
    coop.add(op_a)
    coop.add(op_b)

    assert len(coop) == 2
    assert op_a.content_id in coop
    assert coop.operator(op_a.content_id) is op_a
    assert coop.operator(op_b.content_id) is op_b
    assert set(coop.members()) == {op_a.content_id, op_b.content_id}


def test_cooperative_substrate_unknown_operator_raises_key_error():
    coop = CooperativeSubstrate()
    with pytest.raises(KeyError):
        coop.operator("0" * 64)


def test_cooperative_substrate_membership_check():
    coop = CooperativeSubstrate()
    op = create_operator("DWP", _root("dwp"))
    assert op.content_id not in coop
    coop.add(op)
    assert op.content_id in coop


def test_cooperative_substrate_with_credential_has_identity():
    coop_credential = CredentialUnit(
        name="dwp_homeoffice_coop",
        transfer=TransferDiscipline.DELEGATED,
        principal="cooperative",
        authorities=("cross_operator:invoke",),
    )
    coop = CooperativeSubstrate(cooperative_credential=coop_credential)
    assert coop.content_id == coop_credential.content_id()


# =============================================================================
# Quorum custodian
# =============================================================================

def _payload(name="payload"):
    return WitnessRequest(payload_hash=name * (64 // len(name) + 1))


def test_quorum_requires_at_least_one_member():
    with pytest.raises(ValueError, match="at least one member"):
        QuorumCustodian(members=(), threshold=1)


def test_quorum_threshold_must_not_exceed_member_count():
    a = LocalCustodian("a")
    with pytest.raises(ValueError, match="exceeds member count"):
        QuorumCustodian(members=(a,), threshold=2)


def test_quorum_witness_combines_member_signatures():
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    q = QuorumCustodian(members=(a, b), threshold=2, name="ab")
    req = _payload("p")
    response = q.witness(req)
    # The joint signature is a content_hash (64 hex chars) over the
    # structured multi-sig payload; the underlying structured payload is
    # available as response.witness_payload for verification.
    assert isinstance(response.signature, str)
    assert len(response.signature) == 64
    assert response.custodian == "ab"
    assert response.witness_payload["threshold"] == 2
    assert set(response.witness_payload["contributions"]) == {"a", "b"}


def test_quorum_witness_is_deterministic_for_same_payload():
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    q = QuorumCustodian(members=(a, b), threshold=2)
    r1 = q.witness(_payload("p1"))
    r2 = q.witness(_payload("p1"))
    assert r1.signature == r2.signature


def test_quorum_witness_differs_when_members_differ():
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    c = LocalCustodian("c")
    q_ab = QuorumCustodian(members=(a, b), threshold=2, name="ab")
    q_ac = QuorumCustodian(members=(a, c), threshold=2, name="ac")
    assert q_ab.witness(_payload("p1")).signature != q_ac.witness(_payload("p1")).signature


def test_quorum_witness_differs_for_different_thresholds():
    """Two quorums over the same members but different thresholds produce
    different witnesses; the threshold is part of the witness identity."""
    a = LocalCustodian("a")
    b = LocalCustodian("b")
    c = LocalCustodian("c")
    q_2of3 = QuorumCustodian(members=(a, b, c), threshold=2, name="abc")
    q_3of3 = QuorumCustodian(members=(a, b, c), threshold=3, name="abc")
    assert q_2of3.witness(_payload("p")).signature != q_3of3.witness(_payload("p")).signature


class _FailingCustodian:
    name = "failing"
    def witness(self, request):
        raise RuntimeError("simulated custodian failure")


def test_quorum_raises_when_threshold_not_met():
    """If too few members sign, the quorum raises QuorumNotMet."""
    good = LocalCustodian("good")
    bad = _FailingCustodian()
    q = QuorumCustodian(members=(good, bad), threshold=2, name="needs_both")
    with pytest.raises(QuorumNotMet, match="threshold 2"):
        q.witness(_payload("p"))


def test_quorum_permits_when_threshold_met_despite_some_failures():
    good = LocalCustodian("good")
    bad = _FailingCustodian()
    q = QuorumCustodian(members=(good, bad), threshold=1, name="permissive")
    response = q.witness(_payload("p"))
    assert response.signature


def test_cooperative_substrate_custodian_is_a_quorum_over_members():
    coop = CooperativeSubstrate()
    op_a = create_operator("DWP", _root("dwp"))
    op_b = create_operator("HomeOffice", _root("ho"))
    coop.add(op_a)
    coop.add(op_b)

    cust = coop.custodian
    assert isinstance(cust, QuorumCustodian)
    assert cust.threshold == 2  # default: all members
    member_names = {m.name for m in cust.members}
    assert member_names == {"DWP", "HomeOffice"}


def test_cooperative_substrate_custodian_uses_configured_threshold():
    coop = CooperativeSubstrate(quorum_threshold=1)
    coop.add(create_operator("A", _root("a")))
    coop.add(create_operator("B", _root("b")))
    coop.add(create_operator("C", _root("c")))
    assert coop.custodian.threshold == 1
    assert len(coop.custodian.members) == 3


def test_empty_cooperative_substrate_has_no_custodian():
    coop = CooperativeSubstrate()
    with pytest.raises(ValueError, match="no members"):
        coop.custodian
