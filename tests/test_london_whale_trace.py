"""The London Whale demonstration, asserted.

Appendix A of the principal paper traces this demonstration and says its tests
verify six substantive claims. Until this file existed there were no such
tests: no test referenced any demonstration, and the demonstration script
carries no assertions, so it could not fail. A review found that the appendix
diverged from the demonstration's real output in several particulars, and that
a demonstration which cannot fail is not a check.

Each test here corresponds to a sentence in Appendix A. Where the appendix was
corrected to match the demonstration, the test pins the corrected statement, so
that the trace and the code cannot drift apart again without this file saying
so.
"""

import io
import sys
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from examples.london_whale import run as lw
from substrate.primitives import FunctionalUnit, StateUnit
from substrate.runtime import Permit, Refuse


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scene():
    return _quiet(lw.build_scene)


def _authorise(scene, **kw):
    defaults = dict(
        var_unit_id=scene["var_v1_cid"], risk_factor=0.04, notional=500.0,
        actual_vol=20.0, escalation_credential_id="",
        invoking_cid=scene["desk_trader_cid"], label="t",
    )
    defaults.update(kw)
    return _quiet(lw._authorise, scene, **defaults)


def _last_act(scene):
    return list(scene["jpm"].runtime.ledger)[-1]


# =============================================================================
# The six claims the paper says these tests verify
# =============================================================================

def test_claim_1_the_two_var_models_have_distinct_compiled_forms():
    scene = _scene()
    assert scene["var_v1_cid"] != scene["var_v2_cid"]
    rt = scene["jpm"].runtime
    assert rt.compiled_for(scene["var_v1_cid"]).content_id() != \
        rt.compiled_for(scene["var_v2_cid"]).content_id()


def test_claim_2_drift_fires_on_the_fourth_observation_of_the_violation_pattern():
    scene = _scene()
    rt = scene["jpm"].runtime
    for vol in (16.0, 14.0, 15.0):
        _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=vol)
        assert not rt.drift_monitor.is_drifted(scene["var_v2_cid"])
    _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=17.0)
    assert rt.drift_monitor.is_drifted(scene["var_v2_cid"])


def test_claim_3_the_over_limit_position_is_refused_without_escalation():
    scene = _scene()
    out = _authorise(scene, notional=1500.0)
    assert out is None
    act = _last_act(scene)
    assert act.verdict == "refuse"
    assert "position_limit_policy refuses" in act.output_or_rationale


def test_claim_4_the_same_position_is_permitted_under_escalation_and_recorded():
    scene = _scene()
    out = _authorise(
        scene, notional=1500.0,
        escalation_credential_id=scene["senior_risk_escalation_cid"],
        invoking_cid=scene["senior_risk_officer_cid"],
    )
    assert out is not None and out["position_authorised"] is True
    act = _last_act(scene)
    assert act.verdict == "permit"
    assert act.output_or_rationale["escalation_credential_id"] == scene["senior_risk_escalation_cid"]


def test_claim_5_the_backtest_refuses_on_the_realised_exceedance_rate():
    scene = _scene()
    jpm = scene["jpm"]
    losses = [4.0, 5.0, 6.0, 18.0, 5.0, 4.0, 21.0, 6.0, 5.0, 17.0]
    for i, loss in enumerate(losses):
        pid = "pos_%03d" % i
        _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015,
                   actual_vol=7.5, position_id=pid)
        jpm.runtime.invoke(scene["report_realised_pnl"].content_id(),
                           {"position_id": pid, "realised_loss_million_usd": loss},
                           scene["risk_officer_cid"])
    r = jpm.runtime.invoke(scene["backtest_var_calibration"].content_id(), {
        "authorise_unit_id": scene["authorise_position"].content_id(),
        "outcome_unit_id": scene["report_realised_pnl"].content_id(),
        "max_exceedance_rate": 0.05,
    }, scene["risk_officer_cid"])
    assert isinstance(r, Refuse)
    assert "exceedance_rate=0.300" in r.rationale


def test_claim_6_occ_audit_reconstructs_the_positions_through_the_cooperative_substrate():
    scene = _scene()
    _authorise(scene)
    _authorise(scene, notional=1500.0)
    r = scene["occ"].runtime.invoke(scene["investigate_bank"].content_id(), {
        "bank_operator_id": scene["jpm"].content_id,
        "audit_unit_id": scene["audit_positions"].content_id(),
        "audit_inputs": {"authorise_unit_id": scene["authorise_position"].content_id()},
    }, scene["occ_inspector_cid"])
    assert isinstance(r, Permit)
    assert r.output["verdict"] == "audit_completed"
    assert len(r.output["permitted_positions"]) == 1
    assert len(r.output["refused_positions"]) == 1
    # The audit act is on the OCC's own ledger; the sub-invocation on JPMorgan's.
    assert len(list(scene["occ"].runtime.ledger)) == 1


# =============================================================================
# What the appendix now says, pinned so the trace and the code cannot drift
# =============================================================================

def test_the_scene_has_nine_functional_units_and_one_shared_constitutional_source():
    scene = _scene()
    code = scene["jpm"].runtime.code
    functional = [u for u in code._store.values() if isinstance(u, FunctionalUnit)]
    assert len(functional) == 9
    rt = scene["jpm"].runtime
    assert rt.constitutional_sources_of(scene["desk_trader_cid"]) == \
        rt.constitutional_sources_of(scene["occ_inspector_cid"])


def test_one_authorise_position_references_both_var_models_and_selects_by_input():
    """There is no recompilation on model substitution. The appendix and the
    companion previously said a second authorise_position was committed."""
    scene = _scene()
    unit = scene["authorise_position"]
    assert scene["var_v1_cid"] in unit.functional_refs
    assert scene["var_v2_cid"] in unit.functional_refs
    a = _authorise(scene, var_unit_id=scene["var_v1_cid"])
    b = _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=7.5)
    acts = list(scene["jpm"].runtime.ledger)
    # Both acts ran the same compiled form.
    parents = [x for x in acts if x.compiled_form_id == scene["jpm"].runtime.compiled_for(unit.content_id()).content_id()]
    assert len(parents) == 2


def test_the_two_operators_share_the_code_and_credentials_archives():
    scene = _scene()
    assert scene["jpm"].runtime.code is scene["occ"].runtime.code
    assert scene["jpm"].runtime.credentials is scene["occ"].runtime.credentials
    assert scene["jpm"].runtime.ledger is not scene["occ"].runtime.ledger


def test_round_2_is_refused_by_the_confidence_gate_not_by_drift():
    scene = _scene()
    out = _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=20.0)
    assert out is not None and out["position_authorised"] is False
    assert "confidence_gate refuses" in out["rationale"]
    assert not scene["jpm"].runtime.drift_monitor.is_drifted(scene["var_v2_cid"])


def test_after_drift_the_composing_act_permits_carrying_the_sub_refusal():
    """The invalidation reaches the VaR model's own invocation, which refuses.
    It does not reach authorise_position's compiled form, whose act commits as
    a permit with position_authorised False. The register discloses this."""
    scene = _scene()
    for vol in (16.0, 14.0, 15.0, 17.0):
        _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=vol)
    assert scene["jpm"].runtime.drift_monitor.is_drifted(scene["var_v2_cid"])
    out = _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=7.5)
    act = _last_act(scene)
    assert act.verdict == "permit"
    assert out["position_authorised"] is False
    assert "drifted" in out["rationale"]


def test_after_deprecation_the_same_shape_holds():
    scene = _scene()
    scene["jpm"].deprecate_unit(scene["var_v2_cid"], scene["risk_officer_cid"])
    out = _authorise(scene, var_unit_id=scene["var_v2_cid"], risk_factor=0.015, actual_vol=7.5)
    act = _last_act(scene)
    assert act.verdict == "permit"
    assert out["position_authorised"] is False
    assert "deprecated" in out["rationale"]


def test_the_audit_returns_positions_only_and_creates_no_state_unit():
    """AUDIT_POSITIONS skips administrative acts and filters to
    authorise_position acts, so the drift event, the reset, the backtest
    refusal and the deprecation are not in what the audit returns; and no
    forensic report state unit is committed anywhere."""
    scene = _scene()
    jpm = scene["jpm"]
    # One permitted and one refused position, so the audit has rows of both
    # verdicts to return; without them the test below would pass vacuously.
    assert _authorise(scene, notional=500.0) is not None
    assert _authorise(scene, notional=1500.0) is None
    jpm.reset_drift(scene["var_v2_cid"], scene["risk_officer_cid"])
    jpm.deprecate_unit(scene["var_v2_cid"], scene["risk_officer_cid"])
    code = scene["jpm"].runtime.code
    before = len(code._store)
    r = scene["occ"].runtime.invoke(scene["investigate_bank"].content_id(), {
        "bank_operator_id": jpm.content_id,
        "audit_unit_id": scene["audit_positions"].content_id(),
        "audit_inputs": {"authorise_unit_id": scene["authorise_position"].content_id()},
    }, scene["occ_inspector_cid"])
    assert isinstance(r, Permit)
    assert set(r.output) == {"verdict", "audit_act_id", "ledger_length",
                             "permitted_positions", "refused_positions"}
    # No administrative act appears in either list: every row is an
    # authorise_position act, so every row carries a notional.
    rows = r.output["permitted_positions"] + r.output["refused_positions"]
    assert len(r.output["permitted_positions"]) == 1
    assert len(r.output["refused_positions"]) == 1
    assert all("position_notional_million_usd" in row for row in rows)
    admin_acts = [a for a in scene["jpm"].runtime.ledger if a.kind == "administrative"]
    assert len(admin_acts) >= 2
    assert not any(row.get("act_id") == a.content_id() for a in admin_acts for row in rows)
    assert len(code._store) == before
    # The only state unit whose name mentions a report is the P&L recorder's
    # implementation, which exists before the audit runs. No forensic report.
    assert not any(isinstance(u, StateUnit) and "forensic" in getattr(u, "name", "").lower()
                   for u in code._store.values())


def test_administrative_acts_carry_no_clock_readings():
    """Disclosed defect, pinned so its closure is noticed."""
    scene = _scene()
    scene["jpm"].reset_drift(scene["var_v2_cid"], scene["risk_officer_cid"])
    act = _last_act(scene)
    assert act.kind == "administrative"
    assert act.governance_tick == 0 and act.recorded_time == "", (
        "administrative acts now carry clock readings; update the register "
        "and Appendix A")


# =============================================================================
# The desk limit is credential content, not an input
# =============================================================================

def test_a_declared_desk_limit_in_the_inputs_does_not_raise_the_limit():
    """Found in review: the limit was read from the inputs, so a trader could
    declare 5,000 million for a 1,500 million position and be permitted with
    no escalation. The limit now lives in the invoking credential's
    constraints, which are part of its content identity."""
    scene = _scene()
    r = scene["jpm"].runtime.invoke(scene["authorise_position"].content_id(), {
        "var_unit_id": scene["var_v1_cid"],
        "clearance_unit_id": scene["trade_clearance"].content_id(),
        "position_notional_million_usd": 1500.0,
        "declared_desk_limit_million_usd": 5000.0,
        "risk_factor": 0.04,
        "actual_realised_volatility_million_usd": 60.0,
        "escalation_credential_id": "",
        "position_id": "probe",
    }, scene["desk_trader_cid"])
    assert isinstance(r, Refuse)
    assert "desk limit of 1000.0" in r.rationale


def test_a_credential_carrying_no_desk_limit_authorises_nothing():
    scene = _scene()
    bare = scene["jpm"].runtime.credentials.put(lw._cred(
        "limitless_trader", parent_cids=(scene["jpm_root_cid"],)))
    out = _authorise(scene, notional=1.0, invoking_cid=bare)
    assert out is None
    assert "carries no desk limit" in _last_act(scene).output_or_rationale
