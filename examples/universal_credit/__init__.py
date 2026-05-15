"""
Universal Credit advance payment: the worked example for Phase 1.

The composition is: an `advance_payment_decision` functional unit composes
three sub-units (`eligibility_check`, `fraud_risk_assessment`,
`identity_verification`), each bound by its own policy credentials. At
compile-at-commit the policies roll up under strictest-binding-wins; at
runtime an invocation either permits with an output or refuses with a
rationale, and either way records an Act on the federated ledger.

The example is deliberately small. It exists to verify that the central
architectural commitments of the substrate (three primitive types,
composition through references, compile-at-commit, content-addressed
compiled forms, runtime evaluation against the rolled-up policy, ledger
recording) produce working software end-to-end.
"""
