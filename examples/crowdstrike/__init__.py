"""
Modelling the CrowdStrike Falcon "Channel File 291" outage of July 2024
against the substrate's content-addressing, drift detection, and
staged-rollout commitments.

On 19 July 2024 CrowdStrike pushed a faulty channel file update (Channel
File 291) to Falcon endpoint protection running on Windows. The update
caused approximately 8.5 million Windows devices to crash with Blue Screen
of Death. Airlines, banks, hospitals, broadcasters, and emergency services
were taken offline; flights were grounded; surgeries cancelled. Direct
losses to Fortune 500 companies exceeded USD 5.4 billion; total economic
impact has been estimated above USD 10 billion. The root cause was a
logic error in a content update that bypassed the validation applied to
code updates because it was treated as data, not code.

The substrate-relevant failure points:

1. The code/data distinction was security-incoherent. The channel file
   could affect kernel-level behaviour but did not go through the same
   validation as a binary update.
2. Updates were pushed simultaneously to all customers. No staged
   rollout, no canary cohort, no drift detection before mass deployment.
3. Customers had no structural opt-out from immediate deployment.
4. Rollback was difficult: customers had to manually boot machines into
   safe mode and remove the file, multiplied across millions of devices.

The substrate's commitments map to each:

1. CONTENT-ADDRESSING applies uniformly to all behaviour-affecting
   artefacts. The substrate does not distinguish "code" from "data"
   updates at the architectural level: every update is a content-
   addressed artefact, and every deployment requires the same
   witnessing and policy checks.

2. STAGED DEPLOYMENT via cooperative substrate. A canary customer
   operator deploys first; its drift monitor watches for anomalies;
   production customers deploy only after the canary has cleared. The
   substrate's cross-operator pattern makes this structural — production
   refuses to deploy any update the canary has not cleared.

3. CUSTOMER-SIDE WITNESSING REQUIREMENT. The customer's deployment
   policy requires the canary to have run the update for a minimum
   window without drift before production accepts it. The vendor cannot
   force immediate deployment; the customer's cooperative-substrate
   policy gates it.

4. STRUCTURAL ROLLBACK. Previous compiled forms are content-addressed
   and retained. Rollback is invoking the prior content_id; it is not a
   special operation but a redeployment of the previously-good version.

This demonstration runs a vendor (CrowdStrike), a canary cohort
(CanaryCluster), and a production cohort (ProductionCluster) federated
under a cooperative substrate. Shows: a good update permitting through
both cohorts; a faulty update permitted by the canary at first, then
flagged by the canary's drift monitor, and refused by production as a
result. Rollback to a previous version proceeds normally.

Run with: python -m examples.crowdstrike.run
"""
