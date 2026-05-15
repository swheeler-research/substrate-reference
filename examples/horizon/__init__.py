"""
Modelling the Horizon failure case in the substrate.

The Post Office Horizon IT scandal saw hundreds of postmasters
prosecuted for theft / false accounting on the strength of shortfalls
reported by the Horizon system. The failure mode, reduced to its
mechanism:

1. The accounting implementation had bugs that produced phantom shortfalls.
2. Fujitsu (the contractor) had remote access and modified branch accounts;
   modifications were not attributable to the actual modifier in any
   way the postmasters or the courts could access.
3. The Post Office prosecuted postmasters on these figures, relying on
   the legal presumption that computer evidence is reliable.
4. Bugs known to Fujitsu and the Post Office were not disclosed.
5. The audit trail was not reachable by the people accused.

The substrate's commitments map to each failure point:

- (1) Implementations are content-addressed: each invocation records WHICH
      implementation produced the figure.
- (2) Every act on the ledger is signed by an invoking credential.
      Attribution is structural.
- (3) The substrate provides cryptographically witnessed verifiable evidence
      independent of the operator's word.
- (4) Implementation deprecation is itself an administrative act on the
      ledger; the operator cannot quietly fix a bug.
- (5) Audit reachability is structurally addressed via cooperative-substrate
      arrangements: the Court of Appeals is a separate operator with audit
      rights over the Post Office via a credential both operators have
      committed to. Audit invocations are recorded on both ledgers and
      cannot be unilaterally rewritten.

The demonstration is a single end-to-end run:

- Steps 1-4: the Horizon failure mechanism reproduced (postmaster records
  sales; Fujitsu engineer makes "credit_reversal" modifications; buggy
  balance_check reports a phantom shortfall).
- Steps 5-7: the Court of Appeals investigates via cross-operator audit
  through the cooperative substrate. The forensic report is produced on
  the Court's ledger; the audit invocation is recorded on the Post
  Office's ledger. Neither side can deny the audit happened or its
  contents.
- Steps 8-10: bug discovered; balance_check_v1 deprecated by an
  administrative act on the Post Office's ledger; re-run with the
  fixed balance_check_v2 against the same data produces the correct
  figure. Anyone with the ledger and v2 can verify this independently.

Run with: python -m examples.horizon.run
"""
