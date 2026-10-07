"""The meaningful-review policy, asserted.

An independent review found the rewritten policy testing a principal that no
credential in the scene carried, so the genuine reviewer was refused, and the
demonstration script, carrying no assertions, exited 0. The policy is now
asserted here: the genuine reviewer permits, the bulk-approval reviewer refuses,
and a reviewer credential the operations officer mints for itself refuses, which
is the self-issue class the other demonstrations already test.
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


def _strike(scene, target, reviewer_cid):
    with contextlib.redirect_stdout(io.StringIO()):
        return lv._try_strike(scene, target, reviewer_cid)


def test_the_genuine_reviewer_permits_the_clean_target():
    scene = _scene()
    status, r = _strike(scene, "target_001", scene["dedicated_reviewer_cid"])
    assert status == "attempted"
    assert isinstance(r, Permit), getattr(r, "rationale", None)
    assert r.output.get("decision") == "strike_authorised"


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
