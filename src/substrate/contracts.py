"""
Structural contracts and compile-time type checking.

A unit's spec is its contract. The spec may carry a `preconditions` list
describing what inputs the unit will accept; each entry is a structured
constraint over a named variable. The compiler reads these structured
constraints (it does not extract them from the implementation source) and
verifies that compositions of units have jointly satisfiable preconditions.
An incomposable composition is a type error: TypeMismatch.

Phase 1.6 supports per-variable constraints with these operators:

    lte    value <= constant
    lt     value <  constant
    gte    value >= constant
    gt     value >  constant
    eq     value == constant
    ne     value != constant
    in     value in {constants...}
    not_in value not in {constants...}

Cross-variable constraints (e.g. x + y <= 10) are deferred. The
constants are JSON-comparable (numbers, strings, lists of these). The
implementation trusts that the unit author's implementation actually
refuses inputs outside the declared precondition; the substrate verifies
the contract structure, not the impl's faithfulness to it.

See docs/specification_gaps.md "Non-reconcilability is a type mismatch".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_NUMERIC_OPS = {"lte", "lt", "gte", "gt"}
_ALL_OPS = _NUMERIC_OPS | {"eq", "ne", "in", "not_in"}


class TypeMismatch(Exception):
    """A composition's contracts cannot be jointly satisfied.

    The rationale names the variable, the offending units (by content_id),
    and the constraints whose intersection is empty.
    """


@dataclass(frozen=True)
class Constraint:
    """A single precondition contributed by a particular unit.

    source_unit: content_id of the unit whose spec contributed this constraint.
    var: the variable name the constraint is on.
    op: one of the supported operators.
    value: the constant on the right-hand side.
    """
    source_unit: str
    var: str
    op: str
    value: Any


def parse_constraint(source_unit: str, raw: dict) -> Constraint:
    """Parse a single precondition dict from a unit's spec into a Constraint.

    Raises ValueError if the raw dict is malformed (unknown op, missing
    fields). Malformed preconditions are a unit-author error; we surface
    them rather than silently ignore them.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"precondition is not a dict: {raw!r}")
    for k in ("var", "op", "value"):
        if k not in raw:
            raise ValueError(f"precondition missing {k!r}: {raw!r}")
    op = raw["op"]
    if op not in _ALL_OPS:
        raise ValueError(f"precondition has unknown op {op!r}; expected one of {sorted(_ALL_OPS)}")
    return Constraint(
        source_unit=source_unit,
        var=raw["var"],
        op=op,
        value=raw["value"],
    )


def check_satisfiable(constraints: list) -> None:
    """Verify the union of constraints is jointly satisfiable per variable.

    Constraints are grouped by variable; each variable's constraints are
    analysed in isolation (no cross-variable reasoning). If any variable's
    constraints have an empty satisfying set, raises TypeMismatch.
    """
    by_var: dict = {}
    for c in constraints:
        by_var.setdefault(c.var, []).append(c)
    for var, cs in by_var.items():
        _check_variable(var, cs)


def _check_variable(var: str, cs: list) -> None:
    """Analyse the constraints on a single variable for satisfiability."""
    # Separate by operator family.
    numeric = [c for c in cs if c.op in _NUMERIC_OPS]
    eqs = [c for c in cs if c.op == "eq"]
    nes = [c for c in cs if c.op == "ne"]
    ins = [c for c in cs if c.op == "in"]
    not_ins = [c for c in cs if c.op == "not_in"]

    # Numeric range: compute the intersection of bounds.
    if numeric:
        # lower_bound is the largest of all gte/gt constraints; upper_bound
        # is the smallest of all lte/lt constraints. Use (value, strict)
        # so we can compare strict vs non-strict at the boundary.
        lower = None  # (value, strict)
        upper = None
        for c in numeric:
            if c.op in ("gte", "gt"):
                strict = c.op == "gt"
                if lower is None or (c.value, strict) > (lower[0], lower[1]):
                    lower = (c.value, strict)
            else:
                strict = c.op == "lt"
                if upper is None or (c.value, not strict) < (upper[0], not upper[1]):
                    upper = (c.value, strict)
        if lower is not None and upper is not None:
            lo_val, lo_strict = lower
            up_val, up_strict = upper
            if lo_val > up_val:
                raise TypeMismatch(
                    f"variable {var!r}: lower bound {_fmt_lower(lo_val, lo_strict)} "
                    f"exceeds upper bound {_fmt_upper(up_val, up_strict)} "
                    f"(contributed by: {sorted({c.source_unit[:12] for c in numeric})})"
                )
            if lo_val == up_val and (lo_strict or up_strict):
                raise TypeMismatch(
                    f"variable {var!r}: bounds {_fmt_lower(lo_val, lo_strict)} and "
                    f"{_fmt_upper(up_val, up_strict)} meet at a strict boundary; no value satisfies both "
                    f"(contributed by: {sorted({c.source_unit[:12] for c in numeric})})"
                )

    # Equality: all eq constraints must agree on a single value.
    if eqs:
        distinct = {repr(c.value) for c in eqs}
        if len(distinct) > 1:
            raise TypeMismatch(
                f"variable {var!r}: multiple eq constraints with differing values "
                f"({sorted(distinct)}) "
                f"(contributed by: {sorted({c.source_unit[:12] for c in eqs})})"
            )
        # An eq value must not be excluded by any ne.
        eq_value = eqs[0].value
        for c in nes:
            if c.value == eq_value:
                raise TypeMismatch(
                    f"variable {var!r}: eq value {eq_value!r} is forbidden by a ne constraint "
                    f"from {c.source_unit[:12]}"
                )
        # An eq value must satisfy every in / not_in.
        for c in ins:
            if eq_value not in c.value:
                raise TypeMismatch(
                    f"variable {var!r}: eq value {eq_value!r} not in allowed set {sorted(c.value)} "
                    f"from {c.source_unit[:12]}"
                )
        for c in not_ins:
            if eq_value in c.value:
                raise TypeMismatch(
                    f"variable {var!r}: eq value {eq_value!r} in forbidden set {sorted(c.value)} "
                    f"from {c.source_unit[:12]}"
                )

    # Membership: the intersection of all `in` sets, minus the union of
    # `not_in` sets, must be non-empty. (Only checked when we have at
    # least one `in`; otherwise the universe is unbounded and `not_in` is
    # vacuously satisfiable.)
    if ins and not eqs:
        allowed = set(ins[0].value)
        for c in ins[1:]:
            allowed &= set(c.value)
        for c in not_ins:
            allowed -= set(c.value)
        if not allowed:
            raise TypeMismatch(
                f"variable {var!r}: intersection of allowed-sets minus forbidden-sets is empty "
                f"(contributed by: {sorted({c.source_unit[:12] for c in (ins + not_ins)})})"
            )


def _fmt_lower(value: Any, strict: bool) -> str:
    op = ">" if strict else ">="
    return f"{op} {value!r}"


def _fmt_upper(value: Any, strict: bool) -> str:
    op = "<" if strict else "<="
    return f"{op} {value!r}"
