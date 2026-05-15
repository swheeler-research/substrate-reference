"""Tests for the append-only federated ledger."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from substrate.ledger import Act, FederatedLedger, LedgerError


def _act(prev_id="", verdict="permit", output="ok"):
    return Act(
        previous_act_id=prev_id,
        compiled_form_id="cf_" + "0" * 60,
        invoking_credential_id="cred_" + "0" * 58,
        inputs={"x": 1},
        verdict=verdict,
        output_or_rationale=output,
    )


def test_empty_ledger_latest_is_empty_string():
    led = FederatedLedger()
    assert led.latest() == ""


def test_first_act_must_reference_empty_string():
    led = FederatedLedger()
    led.append(_act(prev_id=""))
    assert len(led) == 1


def test_each_act_must_reference_prior_act():
    led = FederatedLedger()
    first = _act(prev_id="")
    led.append(first)
    second = _act(prev_id=first.content_id(), output="second")
    led.append(second)
    assert len(led) == 2


def test_appending_with_wrong_previous_raises():
    led = FederatedLedger()
    first = _act(prev_id="")
    led.append(first)
    # Wrong previous_act_id.
    with pytest.raises(LedgerError):
        led.append(_act(prev_id="0" * 64, output="bad"))


def test_verify_walks_the_chain():
    led = FederatedLedger()
    a1 = _act(prev_id="")
    led.append(a1)
    a2 = _act(prev_id=a1.content_id(), output="b")
    led.append(a2)
    a3 = _act(prev_id=a2.content_id(), output="c")
    led.append(a3)
    assert led.verify() is True


def test_act_content_id_changes_when_any_field_changes():
    a = _act(verdict="permit", output="ok")
    b = _act(verdict="refuse", output="ok")
    assert a.content_id() != b.content_id()


def test_iteration_yields_acts_in_order():
    led = FederatedLedger()
    a1 = _act(prev_id="")
    led.append(a1)
    a2 = _act(prev_id=a1.content_id(), output="b")
    led.append(a2)
    seen = list(led)
    assert seen == [a1, a2]
