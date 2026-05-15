"""Tests for the structural contract / type-mismatch machinery.

The compiler analyses each functional unit's declared `preconditions`
(opt-in) and refuses compositions whose constraints have no satisfying
assignment per variable. Non-reconcilable policies are a type mismatch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.contracts import (
    Constraint,
    TypeMismatch,
    check_satisfiable,
    parse_constraint,
)
from substrate.implementations import python_implementation
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)


# =============================================================================
# Unit tests of check_satisfiable
# =============================================================================

def _c(var, op, value, source="src"):
    return Constraint(source_unit=source, var=var, op=op, value=value)


def test_satisfiable_compatible_numeric_bounds():
    # x <= 30 and x <= 60: satisfiable (any x <= 30)
    check_satisfiable([_c("x", "lte", 30, "a"), _c("x", "lte", 60, "b")])


def test_unsat_numeric_lower_above_upper():
    with pytest.raises(TypeMismatch) as exc:
        check_satisfiable([_c("x", "gte", 90, "a"), _c("x", "lte", 30, "b")])
    assert "'x'" in str(exc.value)


def test_unsat_strict_boundary():
    # x > 30 and x <= 30: meet at boundary with one strict; no value satisfies.
    with pytest.raises(TypeMismatch):
        check_satisfiable([_c("x", "gt", 30, "a"), _c("x", "lte", 30, "b")])


def test_satisfiable_non_strict_boundary():
    # x >= 30 and x <= 30: x must equal 30.
    check_satisfiable([_c("x", "gte", 30, "a"), _c("x", "lte", 30, "b")])


def test_unsat_conflicting_eq_values():
    with pytest.raises(TypeMismatch):
        check_satisfiable([_c("p", "eq", "uk", "a"), _c("p", "eq", "eu", "b")])


def test_unsat_eq_forbidden_by_ne():
    with pytest.raises(TypeMismatch):
        check_satisfiable([_c("p", "eq", "uk", "a"), _c("p", "ne", "uk", "b")])


def test_unsat_empty_in_intersection():
    with pytest.raises(TypeMismatch):
        check_satisfiable([
            _c("purpose", "in", ["eligibility"], "a"),
            _c("purpose", "in", ["fraud"], "b"),
        ])


def test_satisfiable_in_intersection_non_empty():
    check_satisfiable([
        _c("purpose", "in", ["eligibility", "fraud"], "a"),
        _c("purpose", "in", ["eligibility"], "b"),
    ])


def test_unsat_in_minus_not_in_empty():
    with pytest.raises(TypeMismatch):
        check_satisfiable([
            _c("purpose", "in", ["a", "b"], "a"),
            _c("purpose", "not_in", ["a", "b"], "b"),
        ])


def test_per_variable_isolation():
    # x and y are independent; constraints on each are independently satisfiable.
    check_satisfiable([
        _c("x", "lte", 30, "a"),
        _c("y", "gte", 90, "b"),
    ])


# =============================================================================
# parse_constraint
# =============================================================================

def test_parse_constraint_basic():
    c = parse_constraint("source_cid", {"var": "x", "op": "lte", "value": 30})
    assert c.var == "x"
    assert c.op == "lte"
    assert c.value == 30
    assert c.source_unit == "source_cid"


def test_parse_constraint_rejects_unknown_op():
    with pytest.raises(ValueError):
        parse_constraint("s", {"var": "x", "op": "weirdo", "value": 1})


def test_parse_constraint_rejects_missing_field():
    with pytest.raises(ValueError):
        parse_constraint("s", {"var": "x", "op": "lte"})


# =============================================================================
# Integration: compile_unit catches type mismatches in policy compositions
# =============================================================================

def _policy_with_precondition(code, name, preconditions, source="def implementation(inputs, runtime, cred_id):\n    return {}\n"):
    impl_state = python_implementation(source)
    impl_cid = code.put(impl_state)
    fn = FunctionalUnit(
        name=name,
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"name": name, "preconditions": preconditions},
        implementation_ref=impl_cid,
    )
    code.put(fn)
    return fn


def _binding(name, policy_fn_cid):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal="governance",
        authorities=(),
        policy_refs=(policy_fn_cid,),
    )


def test_compile_refuses_incomposable_policies():
    """Two policies with overlapping inputs but incompatible preconditions
    refuse compilation as a TypeMismatch, with the rationale naming the
    contributing units and the variable."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(CredentialUnit(
        name="parliament", transfer=TransferDiscipline.DELEGATED,
        principal="parliament", authorities=("delegate:any",),
    ))

    strict = _policy_with_precondition(
        code, "retention_max_30",
        [{"var": "max_retention_days", "op": "lte", "value": 30}],
    )
    contradictory = _policy_with_precondition(
        code, "retention_min_90",
        [{"var": "max_retention_days", "op": "gte", "value": 90}],
    )
    b1 = creds.put(_binding("b1", strict.content_id()))
    b2 = creds.put(_binding("b2", contradictory.content_id()))

    unit = FunctionalUnit(
        name="doer",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref="",  # spec-only is fine; we only compile
        credential_refs=(parliament_cid, b1, b2),
        functional_refs=(strict.content_id(), contradictory.content_id()),
        state_refs=(strict.implementation_ref, contradictory.implementation_ref),
    )
    code.put(unit)

    with pytest.raises(TypeMismatch) as exc:
        compile_unit(unit, code, creds)
    msg = str(exc.value)
    assert "max_retention_days" in msg


def test_compile_succeeds_when_policy_preconditions_are_compatible():
    """The Universal Credit pattern: three retention policies (lte 30/60/90)
    are mutually satisfiable (any value <=30 satisfies all three)."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(CredentialUnit(
        name="parliament", transfer=TransferDiscipline.DELEGATED,
        principal="parliament", authorities=("delegate:any",),
    ))

    policies = [
        _policy_with_precondition(
            code, f"retention_max_{n}",
            [{"var": "max_retention_days", "op": "lte", "value": n}],
        )
        for n in (30, 60, 90)
    ]
    bindings = [creds.put(_binding(f"b_{i}", p.content_id())) for i, p in enumerate(policies)]

    unit = FunctionalUnit(
        name="doer",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref="",
        credential_refs=(parliament_cid, *bindings),
        functional_refs=tuple(p.content_id() for p in policies),
        state_refs=tuple(p.implementation_ref for p in policies),
    )
    code.put(unit)

    # Should compile without raising.
    cf = compile_unit(unit, code, creds)
    assert len(cf.policies) == 3


def test_compile_ignores_units_without_declared_preconditions():
    """Policies without structured preconditions contribute nothing to the
    type check; their refusals happen at runtime as before. Mixing declared
    and undeclared policies is fine."""
    code = CodeArchive()
    creds = CredentialsArchive()

    parliament_cid = creds.put(CredentialUnit(
        name="parliament", transfer=TransferDiscipline.DELEGATED,
        principal="parliament", authorities=("delegate:any",),
    ))

    declared = _policy_with_precondition(
        code, "declared", [{"var": "x", "op": "lte", "value": 10}],
    )
    undeclared = _policy_with_precondition(code, "undeclared", [])
    bindings = [
        creds.put(_binding("b1", declared.content_id())),
        creds.put(_binding("b2", undeclared.content_id())),
    ]

    unit = FunctionalUnit(
        name="doer",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref="",
        credential_refs=(parliament_cid, *bindings),
        functional_refs=(declared.content_id(), undeclared.content_id()),
        state_refs=(declared.implementation_ref, undeclared.implementation_ref),
    )
    code.put(unit)

    compile_unit(unit, code, creds)  # no TypeMismatch
