"""
Constitutional anchoring: terminating the recursion at natural persons.

The substrate's deepest architectural commitment is that authority chains
terminate at the natural persons whose constitutional source credentials
ground every authority. Throughout the other demonstrations, the
constitutional source has been a string-labelled credential with a
randomly-generated Ed25519 keypair — "parliament_uk" or "crown" or
"constitutional_authority". The chain terminates at a label, not at a
person.

This demonstration shows the architectural mechanism that would
terminate the recursion at natural persons, using the substrate's
existing machinery applied at the constitutional source level: a
constitutional source credential is just a credential whose parents are
natural-person credentials. Each natural person has their own real
Ed25519 keypair. Walking the authority chain upward from any unit
reaches specific named natural persons.

The architectural insight: natural-person anchoring is the cooperative-
substrate pattern applied at the root. No new substrate mechanism is
needed; the existing primitives compose to give it.

What this demonstrates:

1. The substrate's authority chain visibly terminating at named natural
   persons rather than at a string label.
2. Election succession: the constitutional source is superseded when its
   membership changes. Compiled forms depending on the old constitutional
   source refuse until recompilation under the new one.
3. Revocation of a natural-person credential mid-term: existing compiled
   forms that referenced the revoked person's credential in their
   authority chain refuse, propagating through the runtime's invalidation
   check.
4. The full audit trail of constitutional events: every supersession,
   revocation, and recompilation lands on the operator's ledger as an
   administrative act.

What this does NOT demonstrate (and cannot, in code):

- The institutional/legal/cryptographic apparatus that anchors a specific
  Ed25519 keypair to a specific natural human. In this demonstration,
  "MP Alice Brown" is a credential name; the human Alice Brown is
  notional. Real natural-person anchoring requires biometric attestation,
  hardware security modules, legal recognition of cryptographic
  credentials, and processes for key generation, loss, recovery, death,
  and succession that are outside what the substrate itself can verify.

- The political adoption that would make this real. The substrate
  provides the structural machinery; real constitutional sources
  (parliaments, courts, treaty bodies) would need to commit to operating
  through it.

What this DOES close: the architectural loop. The substrate's claim that
"decisions are attributable to natural persons" was previously
structural-only with no demonstration of the natural-person layer. This
example provides that demonstration. The substrate has the machinery;
the deployment of the machinery against real persons is outside the
substrate's domain.

Run with: python -m examples.constitutional_anchoring.run
"""
