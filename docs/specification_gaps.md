# Specification gaps

Design rationale for decisions the code makes that the papers state at a
higher level, and a record of commitments the prototype does not
substantiate. Entries are derived from the architectural papers; the
section citing each entry names it by title.

Paper references are to the principal paper, *The Sovereign Substrate: A
constitutional architecture for governed computation*
([10.5281/zenodo.19960841](https://doi.org/10.5281/zenodo.19960841)),
abbreviated PP, and to the companion paper, *The Sovereign Substrate:
Reference Architecture and Implementation*
([10.5281/zenodo.20237900](https://doi.org/10.5281/zenodo.20237900)),
abbreviated RA.

Contents:

- Wilful inclusion
- Non-reconcilability is a type mismatch
- The Horizon correction
- What the prototype does not substantiate, beyond RA 4.2

## Wilful inclusion

Cited by `compile.py` (stage 1 of the compilation pipeline) and
`composition.py`.

Silence on a governance dimension is inadmissible (PP 3.4). The rule
converts the default behaviour of current architectures, in which silence
permits, into the substrate's default, in which silence refuses. Absence
of declaration becomes an architecturally visible state rather than an
implicit permission discovered later by forensic analysis.

The rule extends to the reference structure (PP 3.4.1). Every
transitively reachable unit must appear in the source unit's own
references, not only in the references of the units it directly
references. The strict reading is what the compiler enforces, and the
paper gives two reasons for it.

First, invalidation propagation is transparent. A unit's compiled form is
invalidated only when a unit it explicitly references invalidates, so the
reference set is the unit's structural dependency surface. An operator
asking what a unit depends on reads the unit, and does not walk the
transitive closure to find out.

Second, it defeats hidden composition. If a unit depended on another
through an intermediate unit that did not itself list the dependency, the
dependency would be invisible on the intermediate unit's surface, and on
the surface of anything composing with the intermediate unit. Listing at
the top level requires every dependency to be visible at every scope at
which it is reachable.

The cost is bookkeeping, and PP 3.4.1 is explicit that this is a
discipline rather than an inconvenience: a conforming implementation may
provide a composition helper to maintain the lists. `composition.py` is
that helper.

In the code, `_transitive_refs` walks the graph, `_direct_refs` reads the
source unit's top level, and any difference raises
`WilfulInclusionFailure` naming the units reached but not listed. A
functional unit's `implementation_ref` counts as a direct reference: it is
a reference the author explicitly listed, held separately only so the
runtime can find it without scanning.

The dimension-coverage half of PP 3.4, which checks the rolled-up policy
expression against the dimensions the operator declares applicable, is not
performed; see the entry on static roll-up below.

## Non-reconcilability is a type mismatch

Cited by `compile.py` (stage 3b, the structural type check) and
`contracts.py`.

If two commitments cannot be reconciled at a unit, the unit refuses to
compile or the act refuses to execute (PP 3.5). Refusal is preferred to
silent reconciliation, because reconciliation requires authority the
architecture does not have: the substrate is not authorised to decide
which of two non-reconcilable commitments governs, and improvising a
synthesis would decide it without the authority to do so. The
architecture's contribution is to surface the conflict for political
resolution at the scale that has authority to resolve it.

PP 3.5 locates non-reconcilable composition in three places: between
policies in the rolled-up expression at compilation, between preconditions
in the referenced units at compilation, and at runtime between the act's
invocation context and the compiled form's commitments.

The second is the one the type check implements, and the reason it is a
type mismatch rather than a refusal. A unit's specification may declare
preconditions on its inputs: ranges for numeric variables, admitted values
for categorical ones, windows for temporal ones. The compiler intersects
all preconditions on each input variable across every unit in scope. An
empty intersection means no input exists that satisfies the composition,
so the composition was never admissible for any act. That is a property of
the contracts, not of any particular invocation, and PP 3.5 names it a
type mismatch at compile time accordingly. A runtime refusal would record
that one act was refused; a type mismatch records that the composition
cannot be admitted at all. The refusal names the variable, the conflicting
preconditions, and the units that contributed them.

This is the substrate's response to Robodebt. The Online Compliance
Intervention scheme's algorithm presumed roughly steady income across the
year as a precondition for valid comparison; gig-economy and casual
workers' income violated the precondition; the algorithm produced phantom
debts. With the precondition declared on the algorithm's input and the
variability characteristic declared on the income data's output, the
composition fails at compile time, and the algorithm cannot be admitted
against that data unless the precondition is relaxed or the input is
restricted to data the precondition admits.

In the code, `contracts.parse_constraint` reads each declared
precondition, `compile._collect_constraints` gathers them across the
source unit and every policy unit in scope, and
`contracts.check_satisfiable` raises `TypeMismatch` on an empty
intersection. Preconditions are opt-in: a unit that declares none
contributes nothing to the check and relies on runtime refusal instead.

## The Horizon correction

Cited by `primitives.py` (the `implementation_ref` field).

Every functional unit's executable artefact is a content-addressed state
unit, and the functional unit's content includes the artefact's
content-addressed identity (PP 3.2.1). Changing the artefact produces a
new state unit with a new identity, which means a new functional unit. The
substrate cannot mistake one implementation for another; substitution is
structurally observable.

This is the substrate's response to Horizon. In the failure modes the case
documents, the Post Office controlled the Horizon implementation;
modifications to the implementation by Fujitsu were not disclosed to
defence counsel; the system's outputs were treated as authoritative
evidence of postmasters' actions when in fact the implementation had been
quietly modified between the acts the records purported to attribute to
the postmasters and the testimony presented in court. Under the substrate,
modifying the executable artefact produces a new content identity, the new
functional unit is a structurally different unit, acts produced by the old
unit and acts produced by the new unit are attributed differently on the
ledger, and the modification is structurally visible.

The architecture is neutral on the artefact's form. It may be source in
any language the operator admits, bytecode in any sandboxed runtime the
operator admits, or a compiled binary under the operator's chosen
attestation arrangement; conforming implementations choose admissibility
according to their trust model. The architectural commitment is that
whichever form is admitted is content-addressed.

This is why the implementation is held by reference rather than inline.
`FunctionalUnit.implementation_ref` carries the content identity of a
state unit in the code archive, and that identity is inside the functional
unit's own `content_id()`. Two consequences follow. The implementation is
addressed, witnessed and invalidated by the same machinery as any other
unit, with no second mechanism for code. And the functional unit's
identity is a function of the code that will run, so a unit whose code has
been modified is a different unit, not the same unit behaving differently.
An empty `implementation_ref` means the unit is specification-only and
cannot be invoked; the runtime refuses rather than executing something
unnamed.

The prototype admits Python source and WASM bytecode (`implementations.py`,
and the two preparers in `runtime.py`). The Python executor is not
sandboxed and is supported for development and for trusted internal units;
WASM is the sandboxed option. Both are content-addressed, which is the
commitment the architecture makes.

## What the prototype does not substantiate, beyond RA 4.2

RA 4.2 lists six commitments the prototype does not yet substantiate at
the engineering depth a production conforming implementation would
require. These two belong on that list and are not currently on it. They
are gaps in the prototype, not gaps in the architecture.

**Static roll-up of the policy stack.** The architecture specifies that
the compilation pipeline composes the in-scope policy units into a single
rolled-up policy expression, with the strictest binding governing each
governance dimension (PP 3.2, PP 3.3), and that the runtime evaluates that
compiled expression rather than re-traversing the policy graph (PP 3.6).
The prototype's compiler collects the in-scope policy units as a sorted
tuple of content identities, and the runtime invokes each of them in turn
at every act. Strictest-binding-wins does hold: PP 3.3 states that it
emerges from refuse-wins composition over policy units treated as ordinary
functional units, and that is what the runtime does. What is absent is the
rolled-up expression itself, and with it any representation of a
governance dimension. Two commitments depend on that representation and
are therefore not substantiated. Wilful-inclusion verification against the
dimensions the operator declares applicable (PP 3.4) cannot be performed,
because the prototype has no notion of a dimension to check coverage of;
the prototype enforces the reference-listing half of wilful inclusion
(PP 3.4.1) only. And non-reconcilable policy pairs on the same dimension
(PP 3.5) are not detected at compilation, because comparing two policies
for strictness on a shared dimension requires the dimension; an
incomparable pair is admitted and, if it surfaces at all, surfaces as a
runtime refusal. A production conforming implementation would compose the
rolled-up expression at compile time.

**Witnessing signs a payload hash rather than verifying by independent
recompilation.** The architecture specifies that the federated custodians
witness the compiled form by independent recompilation: each custodian
fetches the unit and its references, runs the pipeline independently, and
signs the compiled form's identity only if its own recompilation produces
the same identity (PP 3.2, emission and witnessing). In the prototype the
custodian is handed a `WitnessRequest` carrying a payload hash and signs
that hash. It does not fetch the unit, and it does not run the pipeline.
The signature is a real Ed25519 signature and `verify_witness` is genuine
verification, so the witness does attest that the custodian holding that
key saw that hash. What it does not attest is that an independent run of
the compilation pipeline produced it. `QuorumCustodian` composes several
such signatures over the same hash, so a quorum attests the same thing
several times rather than agreeing independently. The consequence is
visible at the compilation-integrity invalidation trigger (PP 3.7): the
prototype detects a compiled form altered after witnessing, which is what
`verify_compiled_form` checks, but not a compiler that produced the wrong
compiled form in the first place. A production conforming implementation
would have each custodian recompile and witness its own result.
