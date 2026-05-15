"""Tests for confidence-as-architectural-property and uncertainty propagation.

Three layers:

1. confidence module unit tests: validate_confidence_spec, validate_confidence_gate,
   propagate, evaluate_confidence_gate.
2. Compile-time integration: malformed confidence sections refuse compilation.
3. Runtime integration: propagation through composition; gate refusal; impl
   override of propagated value.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import CompilationRefused, compile_unit
from substrate.confidence import (
    ConfidenceSpecError,
    evaluate_confidence_gate,
    propagate,
    validate_confidence_gate,
    validate_confidence_spec,
)
from substrate.federation import LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime


def _cred(name, parent_cids=()):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=name,
        authorities=("invoke:test",),
        credential_refs=tuple(parent_cids),
    )


# =============================================================================
# validate_confidence_spec
# =============================================================================

def test_validate_confidence_spec_none_ok():
    validate_confidence_spec(None)


def test_validate_confidence_spec_non_dict_raises():
    with pytest.raises(ConfidenceSpecError):
        validate_confidence_spec("not a dict")


def test_validate_confidence_spec_requires_produces():
    with pytest.raises(ConfidenceSpecError, match="produces"):
        validate_confidence_spec({})


def test_validate_confidence_spec_produces_must_be_bool():
    with pytest.raises(ConfidenceSpecError, match="produces.*bool"):
        validate_confidence_spec({"produces": "yes"})


def test_validate_confidence_spec_minimal_ok():
    validate_confidence_spec({"produces": False})
    validate_confidence_spec({"produces": True})


def test_validate_confidence_spec_acceptance_band_must_be_pair():
    with pytest.raises(ConfidenceSpecError, match="acceptance_band"):
        validate_confidence_spec({"produces": True, "acceptance_band": [0.0]})


def test_validate_confidence_spec_acceptance_band_lo_must_le_hi():
    with pytest.raises(ConfidenceSpecError, match="lo.*hi"):
        validate_confidence_spec(
            {"produces": True, "acceptance_band": [1.0, 0.0]}
        )


def test_validate_confidence_spec_known_propagation():
    for fn in ("minimum", "product", "mean", "harmonic_mean"):
        validate_confidence_spec({"produces": True, "propagation": fn})


def test_validate_confidence_spec_unknown_propagation_raises():
    with pytest.raises(ConfidenceSpecError, match="propagation"):
        validate_confidence_spec({"produces": True, "propagation": "median"})


def test_validate_confidence_spec_custom_propagation_requires_ref():
    with pytest.raises(ConfidenceSpecError, match="ref"):
        validate_confidence_spec(
            {"produces": True, "propagation": {"function": "custom"}}
        )


def test_validate_confidence_spec_output_field_must_be_nonempty():
    with pytest.raises(ConfidenceSpecError, match="output_field"):
        validate_confidence_spec({"produces": True, "output_field": ""})


# =============================================================================
# validate_confidence_gate
# =============================================================================

def test_validate_confidence_gate_none_ok():
    validate_confidence_gate(None)


def test_validate_confidence_gate_requires_minimum_confidence():
    with pytest.raises(ConfidenceSpecError, match="minimum_confidence"):
        validate_confidence_gate({})


def test_validate_confidence_gate_minimum_must_be_numeric():
    with pytest.raises(ConfidenceSpecError, match="minimum_confidence"):
        validate_confidence_gate({"minimum_confidence": "high"})


def test_validate_confidence_gate_field_must_be_nonempty_string():
    with pytest.raises(ConfidenceSpecError, match="applies_to_field"):
        validate_confidence_gate(
            {"minimum_confidence": 0.5, "applies_to_field": ""}
        )


def test_validate_confidence_gate_minimal_ok():
    validate_confidence_gate({"minimum_confidence": 0.85})


# =============================================================================
# propagate
# =============================================================================

def test_propagate_empty_returns_none():
    assert propagate([], "minimum") is None
    assert propagate([None, None], "minimum") is None


def test_propagate_minimum():
    assert propagate([0.9, 0.5, 0.7], "minimum") == 0.5


def test_propagate_product():
    assert propagate([0.5, 0.5], "product") == 0.25


def test_propagate_mean():
    assert propagate([0.4, 0.6], "mean") == 0.5


def test_propagate_harmonic_mean():
    # 2 / (1/0.5 + 1/0.5) = 0.5
    assert propagate([0.5, 0.5], "harmonic_mean") == 0.5
    # Harmonic mean penalises low: harmonic(0.1, 0.9) = 2/(10 + 1.111) = ~0.18
    assert propagate([0.1, 0.9], "harmonic_mean") < propagate([0.1, 0.9], "mean")


def test_propagate_harmonic_mean_zero_returns_zero():
    assert propagate([0.0, 0.9], "harmonic_mean") == 0.0


def test_propagate_ignores_nones():
    assert propagate([None, 0.7, None, 0.9], "minimum") == 0.7


def test_propagate_unknown_function_raises():
    with pytest.raises(ValueError, match="unknown propagation"):
        propagate([0.5], "median")


# =============================================================================
# evaluate_confidence_gate
# =============================================================================

def test_gate_permits_when_value_above_threshold():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.5}, {"confidence": 0.9}
    )
    assert rationale is None


def test_gate_permits_at_threshold():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.85}, {"confidence": 0.85}
    )
    assert rationale is None


def test_gate_refuses_below_threshold():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.85}, {"confidence": 0.5}
    )
    assert rationale is not None
    assert "below" in rationale


def test_gate_refuses_missing_field():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.85}, {"other_field": 0.99}
    )
    assert rationale is not None
    assert "not present" in rationale


def test_gate_refuses_non_numeric():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.85}, {"confidence": "high"}
    )
    assert rationale is not None
    assert "numeric" in rationale


def test_gate_refuses_bool_as_non_numeric():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.85}, {"confidence": True}
    )
    assert rationale is not None


def test_gate_custom_field():
    rationale = evaluate_confidence_gate(
        {"minimum_confidence": 0.5, "applies_to_field": "calibrated_score"},
        {"calibrated_score": 0.7},
    )
    assert rationale is None


def test_gate_none_section_returns_none():
    assert evaluate_confidence_gate(None, {"confidence": 0.1}) is None


# =============================================================================
# Compile-time integration
# =============================================================================

def _make_scene():
    code = CodeArchive()
    creds = CredentialsArchive()
    root = _cred("root")
    root_cid = creds.put(root)
    return code, creds, root_cid


def test_compile_refuses_malformed_confidence_spec():
    code, creds, root_cid = _make_scene()
    impl_state = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {}\n",
        name="t_impl",
    )
    impl_cid = code.put(impl_state)
    bad = FunctionalUnit(
        name="bad_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence": {"produces": "yes"}},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    with pytest.raises(CompilationRefused, match="confidence"):
        compile_unit(bad, code, creds, custodian=LocalCustodian())


def test_compile_refuses_unknown_propagation():
    code, creds, root_cid = _make_scene()
    impl_state = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {}\n",
        name="t_impl",
    )
    impl_cid = code.put(impl_state)
    bad = FunctionalUnit(
        name="bad_propagation",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence": {"produces": True, "propagation": "median"}},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    with pytest.raises(CompilationRefused, match="propagation"):
        compile_unit(bad, code, creds, custodian=LocalCustodian())


def test_compile_admits_well_formed_confidence_spec():
    code, creds, root_cid = _make_scene()
    impl_state = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {'confidence': 0.9}\n",
        name="t_impl",
    )
    impl_cid = code.put(impl_state)
    ok = FunctionalUnit(
        name="ok_unit",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "confidence": {
                "produces": True,
                "output_field": "confidence",
                "acceptance_band": [0.0, 1.0],
                "propagation": "minimum",
                "calibration": "synthetic",
            }
        },
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    compile_unit(ok, code, creds, custodian=LocalCustodian())


def test_compile_refuses_malformed_gate():
    code, creds, root_cid = _make_scene()
    impl_state = python_implementation(
        "def implementation(inputs, runtime, invoking_credential_id):\n    return {}\n",
        name="t_impl",
    )
    impl_cid = code.put(impl_state)
    bad = FunctionalUnit(
        name="bad_gate",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence_gate": {"minimum_confidence": "high"}},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    with pytest.raises(CompilationRefused, match="confidence_gate"):
        compile_unit(bad, code, creds, custodian=LocalCustodian())


# =============================================================================
# Runtime integration: propagation
# =============================================================================

SUB_IMPL = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"value": inputs.get("value", 0), "confidence": inputs.get("confidence", 0.9)}
"""

PARENT_IMPL_INVOKES_SUBS = """
def implementation(inputs, runtime, invoking_credential_id):
    sub_id = inputs["sub_id"]
    results = []
    for c in inputs["sub_confidences"]:
        r = runtime.invoke(sub_id, {"value": 1, "confidence": c}, invoking_credential_id)
        results.append(r.output["confidence"])
    return {"sub_results": results}
"""


def _runtime_scene_with_parent_and_sub(parent_propagation):
    """Build a parent unit that invokes a sub unit; both produce confidence."""
    code = CodeArchive()
    creds = CredentialsArchive()
    root = _cred("root")
    root_cid = creds.put(root)
    invoker = _cred("invoker", parent_cids=(root_cid,))
    invoker_cid = creds.put(invoker)

    sub_impl = python_implementation(SUB_IMPL, name="sub_impl")
    sub_impl_cid = code.put(sub_impl)
    sub_unit = FunctionalUnit(
        name="sub_unit",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "confidence": {"produces": True, "acceptance_band": [0.0, 1.0]},
        },
        implementation_ref=sub_impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(sub_unit)

    parent_impl = python_implementation(PARENT_IMPL_INVOKES_SUBS, name="parent_impl")
    parent_impl_cid = code.put(parent_impl)
    parent_unit = FunctionalUnit(
        name="parent_unit",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "confidence": {
                "produces": True,
                "propagation": parent_propagation,
                "acceptance_band": [0.0, 1.0],
            },
        },
        implementation_ref=parent_impl_cid,
        credential_refs=(root_cid,),
        functional_refs=(sub_unit.content_id(),),
        state_refs=(sub_impl_cid,),
    )
    code.put(parent_unit)

    ledger = FederatedLedger()
    rt = Runtime(code, creds, ledger)
    custodian = LocalCustodian()
    rt.register_compiled(compile_unit(sub_unit, code, creds, custodian=custodian))
    rt.register_compiled(compile_unit(parent_unit, code, creds, custodian=custodian))
    return rt, parent_unit, sub_unit, invoker_cid


def test_runtime_propagates_minimum_to_parent_output():
    rt, parent, sub, invoker_cid = _runtime_scene_with_parent_and_sub("minimum")
    result = rt.invoke(
        parent.content_id(),
        {"sub_id": sub.content_id(), "sub_confidences": [0.9, 0.5, 0.7]},
        invoker_cid,
    )
    assert isinstance(result, Permit)
    # Parent did not set confidence explicitly; propagation injects the min.
    assert result.output["confidence"] == 0.5


def test_runtime_propagates_product_to_parent_output():
    rt, parent, sub, invoker_cid = _runtime_scene_with_parent_and_sub("product")
    result = rt.invoke(
        parent.content_id(),
        {"sub_id": sub.content_id(), "sub_confidences": [0.8, 0.5]},
        invoker_cid,
    )
    assert isinstance(result, Permit)
    assert abs(result.output["confidence"] - 0.4) < 1e-9


def test_runtime_propagates_mean_to_parent_output():
    rt, parent, sub, invoker_cid = _runtime_scene_with_parent_and_sub("mean")
    result = rt.invoke(
        parent.content_id(),
        {"sub_id": sub.content_id(), "sub_confidences": [0.4, 0.6]},
        invoker_cid,
    )
    assert isinstance(result, Permit)
    assert abs(result.output["confidence"] - 0.5) < 1e-9


PARENT_IMPL_OVERRIDES = """
def implementation(inputs, runtime, invoking_credential_id):
    sub_id = inputs["sub_id"]
    for c in inputs["sub_confidences"]:
        runtime.invoke(sub_id, {"value": 1, "confidence": c}, invoking_credential_id)
    return {"sub_done": True, "confidence": 0.99}
"""


def test_runtime_impl_override_wins_over_propagation():
    """If the impl explicitly sets the confidence field, propagation does not overwrite it."""
    code = CodeArchive()
    creds = CredentialsArchive()
    root = _cred("root")
    root_cid = creds.put(root)
    invoker = _cred("invoker", parent_cids=(root_cid,))
    invoker_cid = creds.put(invoker)

    sub_impl = python_implementation(SUB_IMPL, name="sub_impl_override")
    sub_impl_cid = code.put(sub_impl)
    sub_unit = FunctionalUnit(
        name="sub_unit_override",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={"confidence": {"produces": True}},
        implementation_ref=sub_impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(sub_unit)

    parent_impl = python_implementation(PARENT_IMPL_OVERRIDES, name="parent_override_impl")
    parent_impl_cid = code.put(parent_impl)
    parent_unit = FunctionalUnit(
        name="parent_override",
        contract_pattern=ContractPattern.BEHAVIOUR_CHARACTERISED,
        spec={
            "confidence": {"produces": True, "propagation": "minimum"},
        },
        implementation_ref=parent_impl_cid,
        credential_refs=(root_cid,),
        functional_refs=(sub_unit.content_id(),),
        state_refs=(sub_impl_cid,),
    )
    code.put(parent_unit)

    ledger = FederatedLedger()
    rt = Runtime(code, creds, ledger)
    custodian = LocalCustodian()
    rt.register_compiled(compile_unit(sub_unit, code, creds, custodian=custodian))
    rt.register_compiled(compile_unit(parent_unit, code, creds, custodian=custodian))

    result = rt.invoke(
        parent_unit.content_id(),
        {"sub_id": sub_unit.content_id(), "sub_confidences": [0.1, 0.2]},
        invoker_cid,
    )
    assert isinstance(result, Permit)
    # Impl set 0.99; propagation would have set 0.1 (minimum); impl wins.
    assert result.output["confidence"] == 0.99


# =============================================================================
# Runtime integration: confidence_gate
# =============================================================================

GATE_IMPL = """
def implementation(inputs, runtime, invoking_credential_id):
    return {"ok": True}
"""


def test_runtime_gate_permits_when_threshold_met():
    code = CodeArchive()
    creds = CredentialsArchive()
    root = _cred("root")
    root_cid = creds.put(root)
    invoker = _cred("invoker", parent_cids=(root_cid,))
    invoker_cid = creds.put(invoker)

    impl_state = python_implementation(GATE_IMPL, name="gate_impl")
    impl_cid = code.put(impl_state)
    unit = FunctionalUnit(
        name="gated_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={
            "confidence_gate": {"minimum_confidence": 0.5},
        },
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(unit)

    ledger = FederatedLedger()
    rt = Runtime(code, creds, ledger)
    rt.register_compiled(compile_unit(unit, code, creds, custodian=LocalCustodian()))

    permit_result = rt.invoke(unit.content_id(), {"confidence": 0.9}, invoker_cid)
    assert isinstance(permit_result, Permit)

    refuse_result = rt.invoke(unit.content_id(), {"confidence": 0.2}, invoker_cid)
    assert isinstance(refuse_result, Refuse)
    assert "confidence_gate" in refuse_result.rationale


def test_runtime_gate_refuses_missing_field():
    code = CodeArchive()
    creds = CredentialsArchive()
    root = _cred("root")
    root_cid = creds.put(root)
    invoker = _cred("invoker", parent_cids=(root_cid,))
    invoker_cid = creds.put(invoker)

    impl_state = python_implementation(GATE_IMPL, name="gate_impl_missing")
    impl_cid = code.put(impl_state)
    unit = FunctionalUnit(
        name="gated_unit_missing",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence_gate": {"minimum_confidence": 0.5}},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(unit)

    ledger = FederatedLedger()
    rt = Runtime(code, creds, ledger)
    rt.register_compiled(compile_unit(unit, code, creds, custodian=LocalCustodian()))

    result = rt.invoke(unit.content_id(), {"other": 1}, invoker_cid)
    assert isinstance(result, Refuse)
    assert "not present" in result.rationale


def test_gate_does_not_observe_for_propagation():
    """Confirm that a gate refusal does not pollute the parent's propagation frame."""
    # This is implicit because a refused invocation does not have a confidence
    # value to observe; the test is a regression marker.
    code = CodeArchive()
    creds = CredentialsArchive()
    root = _cred("root")
    root_cid = creds.put(root)
    invoker = _cred("invoker", parent_cids=(root_cid,))
    invoker_cid = creds.put(invoker)

    impl_state = python_implementation(GATE_IMPL, name="gate_isolated")
    impl_cid = code.put(impl_state)
    unit = FunctionalUnit(
        name="gate_isolated_unit",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"confidence_gate": {"minimum_confidence": 0.9}},
        implementation_ref=impl_cid,
        credential_refs=(root_cid,),
    )
    code.put(unit)

    ledger = FederatedLedger()
    rt = Runtime(code, creds, ledger)
    rt.register_compiled(compile_unit(unit, code, creds, custodian=LocalCustodian()))

    result = rt.invoke(unit.content_id(), {"confidence": 0.5}, invoker_cid)
    assert isinstance(result, Refuse)
    # After invocation, the stack should be empty (no leak).
    assert rt._invocation_stack == []
