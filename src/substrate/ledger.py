"""
The federated ledger.

Every act on the substrate (permits and refusals alike) is recorded as an
Act on an append-only ledger. Each Act references the previous Act's
content_id, forming a hash chain: tampering with any past Act invalidates
the chain from that point onward, and verify() can detect it.

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
    """
    previous_act_id: str
    compiled_form_id: str
    invoking_credential_id: str
    inputs: dict
    verdict: str
    output_or_rationale: Any
    kind: str = "invocation"

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
