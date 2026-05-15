"""
Calibrated reliability metadata as a substrate primitive.

This module supports scalar confidence handling: a unit's spec MAY
declare a `confidence` section describing how it treats its own
outputs, and the runtime propagates scalar confidences through
composition by a declared aggregation function. This is NOT
distributional uncertainty quantification — distributions,
Monte Carlo propagators, copulas, and similar machinery are out of
scope for the substrate and belong in domain libraries built on top.
See `docs/architectural_boundary.md` for the principle and
`docs/specification_gaps.md` for the design rationale.

Two distinct concerns are made substrate-native here:

1. **Confidence-as-architectural-property.** Metadata about how a unit
   treats its own outputs, declared in the spec under a `confidence`
   section. Fields: `produces` (bool); `output_field` (the dict key
   where the confidence value appears); `calibration` (a short claim
   about what the confidence means — the load-bearing field for
   governance, since a backtest can check it against realised
   behaviour); `acceptance_band` (the [lo, hi] range the unit
   guarantees); `propagation` (which scalar aggregation function
   combines sub-unit confidences into this unit's confidence, for
   composing units). The section is part of the unit's content_id and
   validated at compile-at-commit.

2. **Scalar confidence aggregation through composition.** When a
   parent unit's impl invokes sub-units that produce confidence, the
   runtime collects those values and, on the parent's return, applies
   the declared aggregation function and writes the result into the
   parent's output (unless the impl already set the field explicitly).
   The runtime maintains an invocation stack of frames; each frame
   tracks the confidences of sub-invocations made from the current
   impl. The four canonical aggregation functions (minimum, product,
   mean, harmonic_mean) cover AND-composition, independent-event
   composition, and ensemble averaging. They are NOT statistical
   propagation over distributions; they are scalar aggregation of
   reliability metadata.

A policy unit MAY additionally declare a `confidence_gate` section
that refuses any invocation whose input confidence is below a declared
threshold. The threshold is in the policy's content_id; substituting
a more lenient threshold produces a new content_id and is structurally
visible in audit. This is the architectural difference between "a
threshold constant in a policy impl" and "a compiled policy threshold":
the latter is part of the unit's identity, even though the underlying
comparison is the same.

Implementation override: if a parent unit declares aggregation but the
impl explicitly writes a confidence value into the named output field,
the impl's value wins. The runtime only injects the aggregated value
when the impl did not. This preserves the option for authors to
express a more sophisticated reliability model than the canonical
aggregation functions accommodate.

For verifying calibration claims against realised behaviour, see
`substrate.backtest`. A backtest unit walks the ledger, joins
predictions with outcomes, computes a declared metric, and refuses if
the claim is violated. The refusal is itself a ledger event; an
authorised operator can then deprecate the target unit via an
administrative act, which propagates through the uniform invalidation
surface.
"""

from typing import Any, Optional


PROPAGATION_FUNCTIONS = frozenset({"minimum", "product", "mean", "harmonic_mean"})


class ConfidenceSpecError(Exception):
    """Raised when a confidence or confidence_gate spec section is malformed."""


def validate_confidence_spec(section: Any) -> None:
    """Validate a `confidence` section from a unit's spec.

    A None section is valid (omission means the unit does not declare
    confidence handling). A non-dict section, a missing 'produces' field,
    a non-bool 'produces', a malformed acceptance_band, or an unknown
    propagation function raises ConfidenceSpecError.
    """
    if section is None:
        return
    if not isinstance(section, dict):
        raise ConfidenceSpecError(
            f"confidence spec must be a dict; got {type(section).__name__}"
        )
    if "produces" not in section:
        raise ConfidenceSpecError("confidence spec requires 'produces' field")
    if not isinstance(section["produces"], bool):
        raise ConfidenceSpecError(
            f"confidence.produces must be a bool; got {type(section['produces']).__name__}"
        )
    band = section.get("acceptance_band")
    if band is not None:
        if not (isinstance(band, list) and len(band) == 2):
            raise ConfidenceSpecError(
                "confidence.acceptance_band must be a [lo, hi] list of two numbers"
            )
        lo, hi = band
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
            raise ConfidenceSpecError("confidence.acceptance_band values must be numeric")
        if lo > hi:
            raise ConfidenceSpecError(
                f"confidence.acceptance_band lo {lo} must be <= hi {hi}"
            )
    propagation = section.get("propagation")
    if propagation is not None:
        if isinstance(propagation, str):
            if propagation not in PROPAGATION_FUNCTIONS:
                raise ConfidenceSpecError(
                    f"confidence.propagation must be one of "
                    f"{sorted(PROPAGATION_FUNCTIONS)} or a custom dict; got {propagation!r}"
                )
        elif isinstance(propagation, dict):
            if propagation.get("function") != "custom":
                raise ConfidenceSpecError(
                    "confidence.propagation dict must have function='custom'"
                )
            if not propagation.get("ref"):
                raise ConfidenceSpecError(
                    "custom propagation requires 'ref' to a functional unit"
                )
        else:
            raise ConfidenceSpecError(
                "confidence.propagation must be a string from the canonical set or a "
                "dict with function='custom' and ref=<unit content_id>"
            )
    field = section.get("output_field", "confidence")
    if not isinstance(field, str) or not field:
        raise ConfidenceSpecError("confidence.output_field must be a non-empty string")


def validate_confidence_gate(section: Any) -> None:
    """Validate a `confidence_gate` section from a unit's spec.

    A None section is valid. A non-dict, missing minimum_confidence, or
    non-numeric threshold raises.
    """
    if section is None:
        return
    if not isinstance(section, dict):
        raise ConfidenceSpecError(
            f"confidence_gate must be a dict; got {type(section).__name__}"
        )
    minimum = section.get("minimum_confidence")
    if minimum is None or not isinstance(minimum, (int, float)):
        raise ConfidenceSpecError(
            "confidence_gate.minimum_confidence is required and must be numeric"
        )
    field = section.get("applies_to_field", "confidence")
    if not isinstance(field, str) or not field:
        raise ConfidenceSpecError(
            "confidence_gate.applies_to_field must be a non-empty string"
        )


def propagate(sub_confidences, function: str) -> Optional[float]:
    """Combine sub-unit confidences according to the named propagation function.

    Returns None if the input list is empty (or contains only Nones). For
    confidences in [0, 1] the result is in [0, 1]; values outside that
    range are not normalised.

    Functions:
      - minimum: weakest-link / AND-composition
      - product: independent-event composition
      - mean: equal-weight arithmetic mean
      - harmonic_mean: penalises low values more than mean, less than minimum
    """
    confs = [c for c in sub_confidences if c is not None]
    if not confs:
        return None
    if function == "minimum":
        return min(confs)
    if function == "product":
        result = 1.0
        for c in confs:
            result *= c
        return result
    if function == "mean":
        return sum(confs) / len(confs)
    if function == "harmonic_mean":
        if any(c <= 0 for c in confs):
            return 0.0
        return len(confs) / sum(1.0 / c for c in confs)
    raise ValueError(f"unknown propagation function: {function!r}")


def evaluate_confidence_gate(section: dict, inputs: Any) -> Optional[str]:
    """Evaluate a confidence_gate against invocation inputs.

    Returns None if the gate permits. Returns a rationale string if the
    gate refuses (missing field, non-numeric value, or value below
    threshold).
    """
    if section is None:
        return None
    minimum = section["minimum_confidence"]
    field = section.get("applies_to_field", "confidence")
    if not isinstance(inputs, dict):
        return (
            f"confidence_gate refuses: invocation inputs are not a dict; "
            f"required field {field!r} is unreachable"
        )
    actual = inputs.get(field)
    if actual is None:
        return (
            f"confidence_gate refuses: required input field {field!r} not present "
            f"(minimum_confidence = {minimum})"
        )
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return (
            f"confidence_gate refuses: input field {field!r} = {actual!r} is not numeric"
        )
    if actual < minimum:
        return (
            f"confidence_gate refuses: input {field}={actual:.4f} below "
            f"minimum_confidence {minimum:.4f}"
        )
    return None
