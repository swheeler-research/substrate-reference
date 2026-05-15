"""
Modelling the SolarWinds supply-chain compromise against the substrate's
content-addressing and multi-custodian quorum commitments.

In December 2020 FireEye disclosed that SolarWinds' Orion network
monitoring software had been compromised at the build pipeline: Russian
state actors (SVR / APT29) inserted the SUNBURST backdoor into Orion
builds between March and June 2020. The trojanised builds were signed
by SolarWinds' legitimate code-signing certificates and distributed
through normal update channels. Approximately 18,000 SolarWinds customers
received the trojanised update, including US federal agencies (Treasury,
Commerce, DHS, State, Justice, Energy) and many Fortune 500 companies.
Detection came months later through unrelated network-behaviour anomalies.

The substrate-relevant failure points:

1. The build artefact was trusted on the basis of the vendor's signature
   alone. A compromised build had a valid signature; signature did not
   guarantee build integrity.
2. There was no independent witness of the build. Customers had no way
   to verify that the artefact they received was the one a legitimate
   build process had produced.
3. Behaviour drift was not monitored. The trojanised Orion had different
   network behaviour from legitimate Orion (calling out to attacker
   infrastructure); months passed before anyone noticed.
4. Cross-customer evidence sharing was absent. Each affected organisation
   discovered the compromise independently and slowly.

The substrate's commitments map to each:

1. CONTENT-ADDRESSING (the Horizon correction applied to build artefacts).
   A build's content_id is a hash of its bytes. A compromised build has
   a different content_id from a legitimate one. Customers deploying by
   content_id reject any artefact whose hash does not match the expected
   value. The vendor's signature on a different artefact does not change
   the content_id.

2. MULTI-CUSTODIAN QUORUM. Compiled forms (here, deployed builds) can
   require witness from multiple independent custodians under a
   cooperative-substrate quorum. A compromised vendor cannot produce a
   quorum-valid witness without also compromising the independent
   custodians. The substrate's QuorumCustodian + cooperative-substrate
   pattern makes this structural.

3. DRIFT DETECTION on behaviour-characterised units. The substrate's
   drift monitor fires when output distributions leave declared
   calibration. A behaviour change in Orion (new network destinations,
   new traffic patterns) would have triggered drift if the unit's
   network behaviour were declared as a calibration criterion.

4. CROSS-OPERATOR AUDIT. Any customer / regulator can invoke a
   cooperative-substrate audit on the vendor's build records and on
   each affected customer's deployment ledger. Evidence sharing is
   structural, not voluntary.

This demonstration runs a vendor (SolarWinds), a customer (FederalAgency),
and an independent auditor (BuildVerifier) federated under a cooperative
substrate. Shows: legitimate build deployed under multi-custodian quorum;
compromised build rejected because the auditor will not witness it; the
substrate's content-addressing structurally surfaces the substitution.

Run with: python -m examples.solarwinds.run
"""
