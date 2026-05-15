"""
Cross-operator Universal Credit advance payment.

The Phase 2-deepened demonstration: two operators (DWP and Home Office)
each running a substrate instance, federated, with a cooperative
substrate establishing mutual recognition. DWP's advance_payment_decision
unit invokes Home Office's right_to_reside_check via the cross-operator
runtime; both operators record their acts on their own ledgers; the
audit trail spans both.

The cooperative substrate is just a credential with parent references to
both operator roots, plus optional cross-operator policy refs. The
architecture composes it without new machinery. Each unit that
participates in cross-operator invocation references the cooperative
substrate in its credential_refs; that pulls both operators' constitutional
roots into the unit's authority chain, which makes credentials rooted in
either operator delegated under the unit.

What is demonstrated:

- Operator identity (each operator's root credential is its identity).
- Cooperative substrate as content-addressed credential (no new primitive).
- Cross-operator invocation through runtime.invoke_in().
- Delegation enforcement across operator boundaries (a UK Parliament-
  rooted DWP credential is delegated under a UK Parliament-rooted Home
  Office unit, BECAUSE the cooperative substrate brings both roots into
  the unit's authority chain).
- Each operator owns its own ledger; the joint audit trail is the union
  of the two ledgers, joinable on cross-operator act references.
- The Horizon correction, content-addressing, policies-as-functions,
  structural type-checking — every Phase 1.x commitment carries through
  unchanged.

Run with: python -m examples.cross_operator_uc.run
"""
