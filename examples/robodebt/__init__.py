"""
Modelling the Robodebt scheme in the substrate.

The Australian Online Compliance Intervention (2015-2019), commonly
called Robodebt, was an automated debt recovery scheme run by
Centrelink (Department of Human Services / Services Australia). It
compared welfare recipients' reported fortnightly income against
Australian Taxation Office (ATO) annual income data, divided the annual
figure by 26 to produce a "fortnightly average," and treated any
discrepancy as an overpayment.

The mathematical failure: income averaging is valid only when income is
roughly evenly distributed across the year. For variable-income workers
(gig workers, seasonal workers, students who worked part of the year),
the averaging produces wildly incorrect "overpayments." Recipients were
pursued for debts they did not owe; the burden of proof had been
reversed; human review had been removed. The Federal Court ruled the
scheme unlawful in 2019. The Royal Commission (2022-2023) found systemic
failure. At least three suicides were directly linked. The settlement
reached $1.8 billion.

The substrate's relevant commitments, mapped to each failure point:

1. The algorithm's preconditions were unenforced — income averaging was
   applied regardless of whether the assumption held.
   SUBSTRATE: structural preconditions on the algorithm refuse to compute
   when the assumption is violated. The substrate makes refusal a
   first-class output, recorded on the ledger.

2. The burden of proof was reversed — recipients had to disprove the
   debt; the agency did not have to prove it.
   SUBSTRATE: the substrate cannot produce a debt without all required
   inputs satisfying the algorithm's preconditions. The agency must
   structurally meet the preconditions; until then, there is no debt to
   contest.

3. Human review was removed — the algorithm auto-issued debts at scale.
   SUBSTRATE: a "refer to human" policy can be attached structurally; the
   refuse-wins composition gives strictest-binding-wins.

4. The scheme was not legally authorised — implemented without proper
   foundation.
   SUBSTRATE: every unit's authority chain must trace to a constitutional
   source. An algorithm without that chain cannot be compiled, witnessed,
   or invoked.

5. Audit and appeal were structurally biased — recipients had limited
   access to the data underlying their debts.
   SUBSTRATE: cross-operator audit via a cooperative substrate gives the
   Commonwealth Ombudsman (or any properly-authorised audit operator)
   structural access to the data and the algorithm's reasoning.

This demonstration runs both the original Robodebt algorithm (v1, no
preconditions) and an improved version (v2, with the income-variability
precondition declared and enforced) against two claimants — Sarah with
stable monthly income, James with highly variable gig-work income —
showing concretely what the substrate's enforcement would have done
differently. The Ombudsman is modelled as a separate operator with
cross-operator audit rights over Services Australia via a cooperative
substrate.

Run with: python -m examples.robodebt.run
"""
