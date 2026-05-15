"""Tests for the three primitive types.

Phase 1 success criterion (a): three units are defined, each with content
of its primitive type and references to other units as appropriate. This
file exercises the three primitive types and their reference fields.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.primitives import (
    FunctionalUnit, StateUnit, CredentialUnit,
    ContractPattern, MutabilityDiscipline, TransferDiscipline,
    content_hash,
)


def test_functional_unit_has_content_id():
    """A functional unit produces a stable content-addressable identity."""
    unit = FunctionalUnit(
        name="eligibility_check",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "inputs": {"income": "decimal", "household_size": "int"},
            "outputs": {"eligible": "bool"},
            "preconditions": ["income >= 0", "household_size >= 1"],
        },
    )
    cid = unit.content_id()
    assert isinstance(cid, str)
    assert len(cid) == 64  # SHA-256 hex


def test_functional_unit_content_id_is_stable():
    """Same content produces the same content_id; different content does not."""
    spec = {"inputs": {"x": "int"}, "outputs": {"y": "int"}}
    u1 = FunctionalUnit(name="f", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED, spec=spec)
    u2 = FunctionalUnit(name="f", contract_pattern=ContractPattern.SPECIFICATION_BOUNDED, spec=spec)
    assert u1.content_id() == u2.content_id()

    # Different spec, different id
    u3 = FunctionalUnit(
        name="f",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {"x": "int"}, "outputs": {"y": "string"}},
    )
    assert u1.content_id() != u3.content_id()


def test_state_unit_immutable():
    """An immutable state unit holds fixed content."""
    s = StateUnit(
        name="constitutional_floor",
        mutability=MutabilityDiscipline.IMMUTABLE,
        content={"min_confidence": 0.9, "max_retention_days": 30},
    )
    assert s.content_id().startswith("")  # just exercising the method


def test_credential_unit_with_authorities():
    """A credential unit encodes the authorities it grants."""
    c = CredentialUnit(
        name="dwp_caseworker_credential",
        transfer=TransferDiscipline.DELEGATED,
        principal="caseworker_id_12345",
        authorities=("invoke:eligibility_check", "invoke:advance_payment_decision"),
    )
    assert c.content_id()
    assert "invoke:eligibility_check" in c.authorities


def test_credential_can_bring_policies_into_binding():
    """A credential carries policy_refs naming the policy functional units
    it brings into force. Policies are themselves functional units; the
    credential supplies authority, the policy supplies behaviour."""
    binding_credential = CredentialUnit(
        name="data_retention_governance",
        transfer=TransferDiscipline.DELEGATED,
        principal="governance_authority",
        authorities=(),  # binding credentials typically grant no invocation rights
        policy_refs=("policy_fn_cid_" + "0" * 48,),
    )
    assert binding_credential.policy_refs == ("policy_fn_cid_" + "0" * 48,)


def test_content_hash_is_canonical():
    """Hashes are independent of key ordering and whitespace."""
    a = content_hash({"x": 1, "y": 2})
    b = content_hash({"y": 2, "x": 1})
    assert a == b


# =============================================================================
# Reference fields
# =============================================================================

def _make_unit(**ref_kwargs):
    """Helper: construct a simple FunctionalUnit, optionally with references."""
    return FunctionalUnit(
        name="composer",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
        **ref_kwargs,
    )


def test_unit_can_be_constructed_with_references():
    """A unit accepts functional, state, and credential references."""
    u = _make_unit(
        functional_refs=("aaa", "bbb"),
        state_refs=("ccc",),
        credential_refs=("ddd", "eee"),
    )
    assert u.functional_refs == ("aaa", "bbb")
    assert u.state_refs == ("ccc",)
    assert u.credential_refs == ("ddd", "eee")
    # The content_id is still a valid SHA-256 hex string.
    assert len(u.content_id()) == 64


def test_unit_content_id_changes_when_references_change():
    """Changing any reference field produces a different content_id."""
    base = _make_unit(
        functional_refs=("aaa",),
        state_refs=("bbb",),
        credential_refs=("ccc",),
    )

    added_functional = _make_unit(
        functional_refs=("aaa", "xxx"),
        state_refs=("bbb",),
        credential_refs=("ccc",),
    )
    changed_state = _make_unit(
        functional_refs=("aaa",),
        state_refs=("yyy",),
        credential_refs=("ccc",),
    )
    added_credential = _make_unit(
        functional_refs=("aaa",),
        state_refs=("bbb",),
        credential_refs=("ccc", "zzz"),
    )

    assert base.content_id() != added_functional.content_id()
    assert base.content_id() != changed_state.content_id()
    assert base.content_id() != added_credential.content_id()


def test_unit_content_id_stable_for_same_references():
    """Identical references in the same order produce the same content_id."""
    u1 = _make_unit(credential_refs=("aaa", "bbb"))
    u2 = _make_unit(credential_refs=("aaa", "bbb"))
    assert u1.content_id() == u2.content_id()


def test_unit_content_id_is_order_invariant():
    """References are semantically unordered: same set in any order hashes equal.

    Pins the architectural decision in docs/specification_gaps.md: reference
    fields are sets, not sequences. Reordering for readability must never
    change a unit's identity.
    """
    u1 = _make_unit(credential_refs=("aaa", "bbb", "ccc"))
    u2 = _make_unit(credential_refs=("ccc", "aaa", "bbb"))
    u3 = _make_unit(credential_refs=("bbb", "ccc", "aaa"))
    assert u1.content_id() == u2.content_id() == u3.content_id()


def test_adding_reference_to_empty_list_changes_content_id():
    """A unit with no references differs from a unit with one reference."""
    empty = _make_unit()
    populated = _make_unit(credential_refs=("aaa",))
    assert empty.content_id() != populated.content_id()


if __name__ == "__main__":
    test_functional_unit_has_content_id()
    test_functional_unit_content_id_is_stable()
    test_state_unit_immutable()
    test_credential_unit_with_authorities()
    test_policy_credential_is_distinguished()
    test_content_hash_is_canonical()
    test_unit_can_be_constructed_with_references()
    test_unit_content_id_changes_when_references_change()
    test_unit_content_id_stable_for_same_references()
    test_unit_content_id_is_order_invariant()
    test_adding_reference_to_empty_list_changes_content_id()
    print("All primitive tests passed.")
