"""
Modelling algorithmic target selection in war against the substrate's
architectural commitments.

This demonstration is informed by reporting on the Lavender system
(Israeli military, Gaza, 2023-2024, per +972 Magazine and Local Call,
April 2024) but is not a reconstruction of any real system. We do not
have authoritative information about Lavender's internal architecture;
the public reporting describes operational practices and outcomes. The
substrate cannot verify or falsify claims about specific systems
operated by specific militaries.

What the substrate CAN demonstrate is what its architectural commitments
would demand of any algorithmic system used for such decisions. The
reported failure modes — high-volume algorithmic targeting with nominal
20-second human review, pre-authorised collateral damage thresholds,
behaviour-characterised systems with known error rates not acted on —
each map to a substrate commitment that would have made the failure
structurally visible or impossible.

Five failure modes from the reporting, mapped to substrate commitments:

1. Behaviour-characterised system with insufficient calibration commitments
   acted on at runtime.
   SUBSTRATE: behaviour-characterised contract pattern requires calibration
   commitments declared in the spec. Drift detection enforces them at
   runtime; out-of-calibration outputs invalidate the unit.

2. Human review reduced to nominal approval (~20 seconds per target).
   SUBSTRATE: refer-to-human policies must be evaluated through
   functional units that produce verifiable outputs. The reviewing
   credential's invocation is itself a ledger event; rubber-stamping
   would be visible in the timing and pattern of acts.

3. Pre-authorised collateral damage thresholds.
   SUBSTRATE: policy evaluation is per-invocation. A policy that permits
   bulk-pre-authorisation would be a single policy unit whose acceptance
   bounds are themselves auditable. Substituting a more permissive
   policy is a substrate event (administrative act on the ledger).

4. Drift in classifier accuracy not detected or acted on.
   SUBSTRATE: drift criteria declared in the spec; runtime monitor;
   out-of-calibration outputs invalidate the unit. Once drifted, the
   unit refuses until reset by an authorised operator action (itself a
   ledger event).

5. Independent legal review absent or structurally limited.
   SUBSTRATE: cooperative-substrate audit by a legal review operator
   (e.g. IHL/JAG, ICRC, court of jurisdiction) with bilaterally-
   committed audit rights. Audit invocations recorded on both ledgers.

What the substrate does NOT address — and this is the most important
qualification for any military application:

- Institutional natural-person anchoring. The substrate's architectural
  mechanism for terminating the authority chain at natural persons is
  demonstrated in `examples/constitutional_anchoring/`: a constitutional
  source credential is a credential whose parent_refs are natural-person
  credentials. The mechanism composes from existing primitives. In THIS
  demonstration, the constitutional source is a string-labelled credential
  with a random Ed25519 keypair (the natural-person layer is shown
  separately rather than duplicated here). Real institutional anchoring
  — biometric attestation, hardware security modules, legal recognition
  of cryptographic credentials, processes for key generation, loss,
  recovery, death, and succession — is outside what code can verify.
  For a military application this is decisive: the substrate's claim
  that strike decisions are attributable to specific authorising persons
  is operationally meaningful only when the architectural mechanism is
  combined with an institutional apparatus that anchors specific keypairs
  to specific humans.

- Adoption. Even if the substrate had all of these properties, it
  delivers them only when an operator chooses to deploy the constraints
  honestly. The substrate makes the absence visible (every audit can
  see whether constraints were declared and whether they were checked);
  whether the visibility produces accountability is downstream of the
  architecture.

The demonstration runs a stylised scenario with synthetic targets and
shows where the substrate's enforcement would have refused, and where
substrate-style audit would have made the system's behaviour legible
to an independent reviewer.

Run with: python -m examples.lavender.run
"""
