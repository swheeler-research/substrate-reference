"""The meaningful-review policy, asserted.

An independent review found the rewritten policy testing a principal that no
credential in the scene carried, so the genuine reviewer was refused, and the
demonstration script, carrying no assertions, exited 0. A later review found
the policy resolving a credential identifier typed into the strike's inputs,
which established that a reviewer existed and not that anyone had reviewed
anything. The review is now an act on the ledger, made by the reviewer under
their own credential and naming the assessment reviewed; the strike names
that act. Asserted here: the genuine reviewer's act permits; a review act
under the bulk-approval credential refuses; a review act by a credential the
operations officer minted for itself refuses; a review act for another target
refuses; a review act reused by a second strike refuses; and a strike naming
another target's assessment refuses at the confidence gate.
"""

import io
import sys
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.lavender import run as lv
from substrate.primitives import CredentialUnit, TransferDiscipline
from substrate.runtime import Permit, Refuse


def _scene():
    with contextlib.redirect_stdout(io.StringIO()):
        return lv.build_scene()


def _strike(scene, target, reviewer_cid, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return lv._try_strike(scene, target, reviewer_cid, **kw)


def _assess(scene, target):
    return scene["idf"].runtime.invoke(
        scene["assess_target"].content_id(), {"target_id": target}, scene["operations_cid"],
    )


def _review(scene, target, assessment_act, reviewer_cid):
    return scene["idf"].runtime.invoke(
        scene["review_target"].content_id(),
        {"target_id": target, "assessment_act": assessment_act}, reviewer_cid,
    )


def test_the_genuine_reviewer_permits_the_clean_target():
    scene = _scene()
    status, r = _strike(scene, "target_001", scene["dedicated_reviewer_cid"])
    assert status == "attempted"
    assert isinstance(r, Permit), getattr(r, "rationale", None)
    assert r.output.get("decision") == "strike_authorised"
    assert r.output.get("reviewed_by") == scene["dedicated_reviewer_cid"]
    # The strike's record names the review act, which is on the ledger
    # under the reviewer's credential.
    review = scene["idf"].runtime.act_by_id(r.output["review_act"])
    assert review is not None and review.invoking_credential_id == scene["dedicated_reviewer_cid"]


def test_the_bulk_approval_reviewer_is_refused_on_its_authority_not_its_name():
    scene = _scene()
    status, r = _strike(scene, "target_001", scene["bulk_reviewer_cid"])
    assert status == "attempted"
    assert isinstance(r, Refuse)
    assert "individual-strike review authority" in r.rationale


def test_a_reviewer_the_invoker_issued_for_itself_is_refused():
    """Same name, same authority string, derived from the operations officer
    rather than from the IDF root. Self-review."""
    scene = _scene()
    creds = scene["idf"].runtime.credentials
    forged = creds.put(CredentialUnit(
        name="dedicated_reviewer_capt_lee",
        transfer=TransferDiscipline.DELEGATED,
        principal="dedicated_reviewer_capt_lee",
        authorities=("review:individual_strikes",),
        credential_refs=(scene["operations_cid"],),
    ))
    status, r = _strike(scene, "target_001", forged)
    assert status == "attempted"
    assert isinstance(r, Refuse)
    assert "self-review" in r.rationale


def test_a_review_of_another_target_is_refused():
    scene = _scene()
    a4 = _assess(scene, "target_004"); assert isinstance(a4, Permit)
    rv4 = _review(scene, "target_004", a4.act_id, scene["dedicated_reviewer_cid"])
    assert isinstance(rv4, Permit)
    status, r = _strike(scene, "target_001", scene["dedicated_reviewer_cid"], review_act=rv4.act_id)
    assert status == "attempted"
    assert isinstance(r, Refuse)
    assert "concerns 'target_004', not 'target_001'" in r.rationale


def test_a_review_act_is_consumed_by_one_strike():
    scene = _scene()
    status, first = _strike(scene, "target_001", scene["dedicated_reviewer_cid"])
    assert isinstance(first, Permit)
    review_act = first.output["review_act"]
    # A fresh assessment, the old review: refused as consumed.
    status, second = _strike(scene, "target_001", scene["dedicated_reviewer_cid"], review_act=review_act)
    assert isinstance(second, Refuse)
    assert "already consumed" in second.rationale or "reviewed assessment" in second.rationale


def test_a_strike_naming_another_targets_assessment_is_refused():
    """target_001's assessment (confidence 0.97) cannot carry target_002
    (0.62) past the floor. The review unit refuses to review it; and a
    genuine review of target_002 does not help, because the gate binds the
    cited act to the subject and the other two policies read the act."""
    scene = _scene()
    a1 = _assess(scene, "target_001"); assert isinstance(a1, Permit)
    status, r = _strike(scene, "target_002", scene["dedicated_reviewer_cid"], assessment_act=a1.act_id)
    assert status == "review_refused"
    assert "concerns 'target_001', not 'target_002'" in r.rationale
    a2 = _assess(scene, "target_002"); assert isinstance(a2, Permit)
    rv2 = _review(scene, "target_002", a2.act_id, scene["dedicated_reviewer_cid"])
    assert isinstance(rv2, Permit)
    status, r = _strike(scene, "target_002", scene["dedicated_reviewer_cid"],
                        assessment_act=a1.act_id, review_act=rv2.act_id)
    assert status == "attempted" and isinstance(r, Refuse)
    assert "target_001" in r.rationale and "target_002" in r.rationale


def test_the_facts_come_from_the_assessment_act_not_the_inputs():
    """The strike's inputs carry no casualty figures; proportionality reads
    the cited act. Target 003's assessment refuses on its own figures."""
    scene = _scene()
    status, r = _strike(scene, "target_003", scene["dedicated_reviewer_cid"])
    assert isinstance(r, Refuse)
    assert "proportionality violated" in r.rationale
