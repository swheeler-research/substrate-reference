"""
Five Eyes / mass surveillance against the substrate.

The 2013 Snowden disclosures documented mass-surveillance programmes
operated jointly by the Five Eyes signals-intelligence alliance (US NSA,
UK GCHQ, Canada CSE, Australia ASD, New Zealand GCSB). Documented
programmes included PRISM (US-side bulk collection from major US tech
companies), XKeyscore (search interface across collected data), Tempora
(UK-side bulk fibre-optic cable interception), and bilateral
arrangements that allowed each agency to query the others' holdings.

The substrate-relevant failure modes:

1. SCOPE INVISIBILITY. The programmes' actual scope was not visible to
   the constitutional authorities that nominally oversaw them. Foreign
   Intelligence Surveillance Court (FISC) orders authorised broad
   collection categories; downstream queries against the collected data
   were not auditable by the authorising bodies in any meaningful sense.

2. CROSS-JURISDICTIONAL CIRCUMVENTION. Each agency was prohibited from
   spying on its own citizens; the Five Eyes arrangement allowed each
   to query the others' holdings of data on the prohibited citizens.
   The legal constraints on each agency were structurally circumvented
   by the bilateral arrangements.

3. AUTHORITY CHAIN OPACITY. The credentials that authorised specific
   queries against collected data were not visible to oversight. A query
   could be made under any of several authorities; the chain from query
   to authorising person was structurally obscured.

4. NO STRUCTURAL POLICY ON COLLECTION SCOPE. Collection was governed
   by classified policies that were not subject to public or even most
   parliamentary review. Whether a specific query exceeded the
   authorising policy was not structurally checkable.

5. WHISTLEBLOWING AS THE ONLY DETECTION PATH. The programmes' scope
   became known because of Edward Snowden's disclosures, not because
   of any structural oversight. Auditing the surveillance machinery
   required removing classified documents from the agencies and giving
   them to journalists.

The substrate's commitments map to each:

1. SCOPE VISIBILITY through the ledger. Every collection / query act is
   a ledger event. The ledger is verifiable by any party with access.
   Cross-operator audit by oversight authorities (parliamentary
   committees, courts) is structural.

2. CROSS-JURISDICTIONAL CIRCUMVENTION refused by delegation enforcement.
   A query authorised under jurisdiction A's credentials, executed
   against jurisdiction B's data, requires the cooperative substrate
   between A and B to permit it explicitly. The bilateral arrangement
   becomes a credential whose contents are auditable.

3. AUTHORITY CHAIN VISIBILITY. The substrate's authority chain walk
   surfaces every credential a query was made under. There is no
   anonymous query.

4. STRUCTURAL POLICIES on collection. Collection scope is a policy
   functional unit; queries that violate it are refused. Adding a more
   permissive policy is a substrate event recorded on the ledger.

5. STRUCTURAL OVERSIGHT replaces whistleblowing. Oversight authorities
   are cross-operator audit operators; they have structurally-committed
   audit rights via cooperative substrate.

This is the substrate's most architecturally ambitious test. It is also
the one most starkly bounded by what code cannot do: the substrate
cannot prevent governments from doing what they choose to do; it cannot
prevent constitutional sources from being captured; it cannot prevent
oversight bodies from being denied access. What it CAN demonstrate is
the architectural mechanism that would make systemic surveillance
structurally legible to its constitutional sources — turning the
question from "do the authorising bodies know what is being done?" into
"do the authorising bodies look?".

The demonstration is stylised. It does not claim to model what any
specific real intelligence agency has done or does. It models the
architectural mechanism that the substrate would impose on any
algorithmic mass-collection / cross-jurisdictional query system.

Run with: python -m examples.five_eyes.run
"""
