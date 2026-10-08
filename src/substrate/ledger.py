"""
The federated ledger.

Every act on the substrate (permits and refusals alike) is recorded as an
Act on an append-only ledger. Each Act references the previous Act's
content_id, forming a hash chain: tampering with any past Act invalidates
the chain from that point onward, and verify() can detect it.

An Act also carries the lineage and attribution references the
architecture requires of a ledger entry. `sub_invocations` holds the
content identities of the acts the entry's own execution caused, so that
a lineage query reconstructs the call tree from the ledger alone
(PP 3.6.1). `policy_refusals` holds every policy refusal that
contributed to a refused verdict, so that attribution of a refusal does
not depend on which refusing policy the runtime happened to reach first
(PP 3.3, PP 4.10). Both are covered by the content hash, so the chain
makes them as tamper-evident as the verdict itself.

Phase 1 has a single in-process FederatedLedger. The "federated" in the
name refers to its future role: Phase 3 will replicate the ledger across
multiple custodians, and any divergence between custodians' ledgers will
be detectable through the same content-addressed chain machinery used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from substrate.primitives import _make_jsonable, content_hash


@dataclass(frozen=True)
class PolicyRefusal:
    """One policy's refusal of an act, recorded on the refused act.

    The runtime evaluates every in-scope policy and records a
    PolicyRefusal for each one that refused, rather than stopping at the
    first. Recording all of them is what makes the attribution
    independent of the order the policies are iterated in: the compiled
    form's policy tuple is sorted by content identity, which is
    arbitrary with respect to the authority, specificity or scale of the
    policies it holds, so privileging its first refusing entry would
    privilege a hash.

    Fields:
        policy_id: content_id of the refusing policy functional unit.
        rationale: the rationale the policy's own act carried.
        act_id: content_id of the policy's own Act on this ledger, so an
            auditor can read the refusal in full rather than in summary.
    """
    policy_id: str
    rationale: str
    act_id: str


@dataclass(frozen=True)
class Act:
    """A single act recorded on the ledger.

    Acts have a `kind` distinguishing what kind of substrate event they
    record. Two kinds exist (Phase 3):

    - "invocation" (default): a runtime invocation of a unit. The
      `compiled_form_id`, `invoking_credential_id`, `inputs`, `verdict`
      ("permit" or "refuse"), and `output_or_rationale` fields capture
      the invocation.

    - "administrative": an operator action affecting archive state
      (revocation, deprecation, supersession, drift reset). The
      `compiled_form_id` is empty; `invoking_credential_id` is the
      authorising credential; `inputs` describes the action being taken
      (action name + target content_id(s)); `verdict` is "executed" or
      "refused"; `output_or_rationale` carries action details or the
      refusal reason.

    Both kinds share the same hash-chain and content-addressing
    machinery, so the ledger is a single ordered sequence of acts that
    captures everything that happened on the substrate, whether
    invocation or operator action.

    Fields:
        previous_act_id: content_id of the prior Act ("" for the first).
        compiled_form_id: target compiled form (invocation) or "" (administrative).
        invoking_credential_id: the credential that authorised this act.
        inputs: invocation inputs, or administrative action descriptor.
        verdict: "permit"/"refuse" for invocations; "executed"/"refused"
            for administrative acts.
        output_or_rationale: invocation output, refusal rationale, or
            administrative action details.
        kind: "invocation" (default) or "administrative".
        sub_invocations: content_ids of the acts committed during this
            act's own execution: the direct sub-invocations its
            implementation made, in the order it made them. Direct
            only; the tree is recovered by following the edges at each
            level, so recording the transitive closure would duplicate
            what the chain already holds. Empty for an act whose
            implementation invoked nothing, for administrative acts, and
            for acts that refused before reaching their implementation.
        policy_refusals: a PolicyRefusal for every in-scope policy that
            refused this act. Empty on a permit. On a refusal caused by
            policy evaluation it holds the complete set;
            `output_or_rationale` continues to carry the summary
            rationale of one of them, unchanged in shape.
        governance_tick: the value of the substrate's governance clock at
            this act, a monotone counter advancing once per act. This is
            the clock the invalidation surface's bounded-latency property
            is measured in: a form affected at tick t is non-invocable by
            tick t + Delta. 0 for an act committed by a runtime with no
            clock, which is how acts constructed directly in tests read.
        confidence_origin: where the act's calibration value came from:
            "composed" (the declared propagation function over sub-unit
            values), "asserted" (the implementation set it), or "" (the
            unit produces no calibration value). An auditor can tell an
            asserted value from a composed one from the act alone.
        recorded_time: the operator's wall-clock reading at this act, as an
            ISO-8601 instant in UTC. What an inquiry asks for, and what a
            latency measurement in seconds needs. Not monotone across
            processes and not comparable across operators without a declared
            skew bound. "" for an act committed by a runtime with no clock.

    Both clock fields are inputs to the act's content identity. An act is an
    event rather than a compiled artefact, so covering its time breaks
    nothing: the invariance the compiled form needs, that an independent
    recompilation at any later time converges on the same identity, is a
    property of compilation and not of the ledger.
    """
    previous_act_id: str
    compiled_form_id: str
    invoking_credential_id: str
    inputs: dict
    verdict: str
    output_or_rationale: Any
    kind: str = "invocation"
    sub_invocations: tuple = ()
    policy_refusals: tuple = ()
    governance_tick: int = 0
    recorded_time: str = ""
    confidence_origin: str = ""

    def content_id(self) -> str:
        return content_hash({
            "type": "act",
            "kind": self.kind,
            "previous_act_id": self.previous_act_id,
            "compiled_form_id": self.compiled_form_id,
            "invoking_credential_id": self.invoking_credential_id,
            "inputs": _make_jsonable(self.inputs),
            "verdict": self.verdict,
            "output_or_rationale": _make_jsonable(self.output_or_rationale),
            "sub_invocations": list(self.sub_invocations),
            "policy_refusals": _make_jsonable(self.policy_refusals),
            "governance_tick": self.governance_tick,
            "recorded_time": self.recorded_time,
            "confidence_origin": self.confidence_origin,
        })


class LedgerError(Exception):
    """A ledger invariant has been violated (e.g. broken hash chain)."""


class FederatedLedger:
    """An append-only chain of Acts.

    Each appended Act must reference the prior Act's content_id (or the
    empty string for the very first Act). verify() walks the chain
    confirming the references match.
    """

    def __init__(self):
        self._acts: list = []

    def latest(self) -> str:
        """The content_id of the most recent Act, or "" if the ledger is empty."""
        if not self._acts:
            return ""
        return self._acts[-1].content_id()

    def append(self, act: Act) -> str:
        """Append an Act. The Act's previous_act_id must match the current latest()."""
        expected = self.latest()
        if act.previous_act_id != expected:
            raise LedgerError(
                f"Act's previous_act_id {act.previous_act_id!r} does not match "
                f"current latest {expected!r}"
            )
        self._acts.append(act)
        return act.content_id()

    def verify(self) -> bool:
        """Walk the chain. Each Act's previous_act_id must equal the prior Act's content_id."""
        prev = ""
        for act in self._acts:
            if act.previous_act_id != prev:
                return False
            prev = act.content_id()
        return True

    def __len__(self) -> int:
        return len(self._acts)

    def __iter__(self) -> Iterator[Act]:
        return iter(self._acts)
