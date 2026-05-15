"""
Modelling the Boeing 737 MAX MCAS failure against the substrate's
content-addressing, certification-as-cooperative-substrate, and
behaviour-characterised drift commitments.

In October 2018 Lion Air flight 610 (189 dead) and in March 2019
Ethiopian Airlines flight 302 (157 dead) crashed shortly after takeoff.
Both crashes were attributed to the Maneuvering Characteristics
Augmentation System (MCAS), a flight-control software introduced for
the 737 MAX to compensate for the aircraft's altered aerodynamic
behaviour with new larger engines.

The mechanism, as established by NTSB, the JATR review, and the US
Congressional investigation:

1. MCAS pushed the nose down based on a SINGLE angle-of-attack (AOA)
   sensor input. No redundancy; no cross-check against the second AOA
   sensor on the aircraft.

2. Pilots were not informed that MCAS existed in initial documentation.
   The system was designed to be transparent to pilots; when it
   activated against a faulty sensor reading, pilots had no way to
   recognise what was happening or how to disable it.

3. Certification under the FAA's "amended type certificate" process
   delegated significant authority to Boeing's own designated engineering
   representatives. The MCAS modification was treated as not requiring
   pilot retraining, which kept the 737 MAX's certification cost low
   and its commercial position viable against the Airbus A320neo.

4. Software updates to MCAS bypassed the same scrutiny as the original
   certification. The "data" / "configuration" distinction was security-
   incoherent in the same way Channel File 291 would later be for
   CrowdStrike: behaviour-affecting changes did not go through the same
   validation as binary changes.

5. Drift in MCAS's actual behaviour vs its declared envelope was not
   monitored cross-fleet. After Lion Air 610, the second crash followed
   five months later because the cross-fleet evidence had not propagated.

The substrate's commitments map to each:

1. CONTENT-ADDRESSING applies to safety-critical software including
   sensor integration. Single-sensor dependency is structurally visible
   in the unit's reference graph; certification policies can require
   multi-sensor redundancy.

2. REFER-TO-HUMAN as structural. Pilots' authority to override
   automation is not documentation; it is a credential whose presence
   in the unit's authority chain is required. A unit that does not
   list a pilot-override credential cannot be certified.

3. CERTIFICATION AS COOPERATIVE SUBSTRATE. FAA + Boeing + airline as
   joint custodians under a cooperative substrate. Certification is
   joint witnessing; no single party can certify alone.

4. CONTENT-ADDRESSING UNIFIES CODE AND DATA. The substrate does not
   distinguish "binary" from "configuration" updates; both are content-
   addressed artefacts subject to the same policy gates and joint
   witnessing.

5. CROSS-FLEET DRIFT DETECTION. Behaviour-characterised contracts
   declare expected behaviour; cross-operator audit propagates drift
   evidence between airline operators. The substrate's cooperative-
   substrate machinery makes inter-airline evidence sharing structural,
   not voluntary.

This demonstration runs Boeing (vendor / type certificate holder), FAA
(certifier / cross-operator audit), and two airlines (canary +
production cohorts). Two MCAS implementations: the original (single-
sensor, no pilot transparency, declared in unit's authority chain
without pilot-override credential — refused at compile time) and a
revised version that includes both sensors and the pilot-override
credential. Demonstrates: certification-as-joint-witnessing; staged
deployment with cross-fleet drift; structural rollback; and the
substrate's refusal to certify a unit whose authority chain does not
include the operationally-required pilot credential.

Run with: python -m examples.boeing_737_max.run
"""
