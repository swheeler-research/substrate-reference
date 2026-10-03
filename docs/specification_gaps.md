# Specification Gaps

Places where the v1.4 paper does not specify something the implementation requires, and places where implementation work has caused architectural commitments to be revised. Each entry names the gap or revision, the decision made in code, and the rationale. The paper will be updated to reflect these decisions on the next revision pass.

## Administrative acts on the ledger (May 2026)

**Decision:** Every operator-level invalidation action (revoke a credential, deprecate a credential, deprecate a unit, supersede a credential, reset drift) is now recorded on the operator's ledger as an `Act` of kind `"administrative"`. Acts gain a `kind` field defaulting to `"invocation"`; administrative acts have `kind="administrative"`, `compiled_form_id=""`, `invoking_credential_id=<authorising credential>`, `inputs={"action": ..., target descriptor...}`, `verdict="executed"` or `"refused"`, and `output_or_rationale` carrying action details.

Operator methods (`Operator.revoke_credential`, `.deprecate_credential`, `.supersede_credential`, `.deprecate_unit`, `.reset_drift`) wrap the underlying archive / monitor calls with:

1. An authorisation check: the authorising credential must exist, be valid (not revoked / superseded / deprecated), and be delegated under the operator's root credential.
2. Recording the act on the operator's ledger — whether executed or refused.

Unauthorised attempts are themselves recorded as refused administrative acts and a `PermissionError` is raised. This means the audit trail captures *both* successful operator actions and unauthorised attempts; a regulator walking the ledger sees who tried to do what under what credential, with what outcome.

**Rationale:** Until now, operator administrative actions (revoke, deprecate, supersede, reset drift) were direct method calls on archives with no audit trace. An operator could deprecate a unit and there would be no record of who deprecated it, when, or under what authority. This contradicted the substrate's central commitment to attestability: every consequential act on the substrate should be a recorded act, attributable to a credential, walkable through the ledger.

Putting administrative actions on the ledger closes this gap. The same hash-chain machinery that makes invocation acts tamper-evident makes administrative acts tamper-evident. The same content-addressing that makes invocation acts attributable makes administrative acts attributable. The substrate now has a single uniform record of everything that has happened: invocations and administrations alike, in causal order, with the credential responsible for each.

**Implication for "who can administer what":** The authorisation check uses the same delegation machinery as runtime invocation — `_is_delegated()` walks the authorising credential's ancestry against the operator's root credential. An operator's root credential is automatically authorised (it trivially delegates from itself); credentials derived from the root are authorised (they walk up to the root); credentials rooted elsewhere are refused. This is the simplest meaningful authorisation: anything within the operator's own authority chain can administer; anything outside cannot. Finer-grained capability models (specific administrative authorities per action type, cooperative-substrate cross-operator administration) are future extensions; the structural shape is now established.

**What this completes:** The substrate's audit trail. From v1.4's commitment "every act is recorded": invocations were always recorded; administrative actions now are too. The ledger is a complete record of substrate activity. Combined with the six invalidation triggers and the cryptographic witness verification, the substrate now produces compiled forms and ledger entries that an auditor (or a regulator, or another operator under a cooperative substrate) can verify end-to-end without trusting the substrate's operator: the ledger's hash chain prevents tampering; each act's content_id is cryptographically determined; cross-references between acts (sub-act references, target content_ids) make composition trails reconstructible.

## Drift detection: the sixth invalidation trigger (May 2026)

**Decision:** Behaviour-characterised units declare a `drift_criteria` list in their spec; the runtime maintains a `DriftMonitor` that tracks per-unit recent outputs against the declared criteria; when a criterion is violated, the unit is marked drifted and subsequent invocations refuse with a rationale naming the criterion and the evidence (observed mean, observed rate, etc.). This completes the six v1.4 invalidation triggers.

**Why drift state lives on the runtime, not the archive.** Revocation, supersession, and deprecation are explicit operator actions: they change archive state at a specific moment. Drift is observation-driven: it depends on the running output statistics over a window. It is a property of *live behaviour*, not of *declared content*. Putting drift state on the runtime (per process) reflects that honestly. Archive state remains a function of operator decisions; runtime state reflects the substrate's observation of how the world actually behaves. The architecture's separation — archive content vs runtime observation — is mirrored here.

**Phase 3 minimum: two criterion types.**

- `mean_in`: the mean of a numeric output field over the last `window` observations must lie within `[lo, hi]`. Out-of-range marks drift.
- `rate_in`: the fraction of observations whose `field` equals `value` over the last `window` observations must lie within `[lo, hi]`.

Both are evaluated lazily: when fewer than `window` observations exist, the check is satisfied (under-sampled). Unknown criterion types are silently accepted (units can declare experimental criteria without breaking compilation; only known types are enforced). Additional criterion types (Kolmogorov-Smirnov, expected calibration error, distributional checks) are future extensions following the same shape.

**Sticky drift.** Once a unit is marked drifted, the flag does not auto-clear, even if subsequent observations satisfy the criterion. The architectural commitment is: a unit that has demonstrably left its calibration envelope at any point cannot be trusted without explicit operator intervention. `DriftMonitor.reset(unit_cid)` is the operator action that clears drift state — used when a replacement implementation is registered or when drift detection was a false alarm. The act of reset is itself something a substrate could (later) record on the ledger as an operator action requiring authority.

**Detection-after-permit semantics.** The invocation that *causes* drift (its output pushes the running mean across the threshold) still permits — its output is part of the observation history that established drift, not a consequence of it. Subsequent invocations refuse. This is the structurally correct order: drift is observed from outputs, so a refusal cannot precede the output. Tests pin this semantic.

**Restart resets drift.** Drift state is process-local; restarting the substrate's process loses the observation history. This is intentional: drift detection is a live-monitoring concern. Persistence of drift state is a future engineering decision. An operator restarting the substrate is implicitly saying "give it fresh data"; if drift recurs, it recurs.

**What this completes:** The six v1.4 invalidation triggers — credential revocation, policy supersession, constitutional source credential update, drift detection, versioning and deprecation, compilation integrity failure — are all implemented. Each routes through the runtime's uniform invocation flow with a clear refusal rationale. Constitutional currency ("no act under stale governance") is now structurally enforced at every invocation by checks against five archive-state reasons and one runtime-state reason. The substrate's commitment from v1.4 §2 is delivered.

## Invalidation triggers: supersession, deprecation, integrity check (May 2026)

**Decision:** Five of the six invalidation triggers from v1.4 §2 are now implemented as explicit machinery. The sixth (drift detection for behaviour-characterised units) is deferred to a separate pass with its own design.

| Trigger | Mechanism |
|---|---|
| 1. Credential revocation | `CredentialsArchive.revoke()` + `CredentialRevoked` raised by `get()`. (Existing.) |
| 2. Policy supersession | `CredentialsArchive.supersede(old_cid, new_cid)` + `CredentialSuperseded` raised by `get()`. Policies are credentials with policy_refs; superseding a policy credential propagates. |
| 3. Constitutional source credential update | Same mechanism as 2: the constitutional source is a credential; superseding it triggers the same `CredentialSuperseded` propagation. No special case in code. |
| 4. Drift detection | Deferred. Behaviour-characterised units declare drift criteria in their spec; runtime monitoring not yet implemented. |
| 5. Versioning and deprecation | `CredentialsArchive.deprecate()` + `CredentialDeprecated` for credentials; `CodeArchive.deprecate()` + `UnitDeprecated` for units. The runtime checks both. |
| 6. Compilation integrity failure | The runtime calls `verify_compiled_form()` on every invocation; a tampered or non-verifying compiled form refuses. |

The runtime's invocation flow now performs (in order): compiled form lookup; **compilation integrity check (new)**; invoking credential check (revocation / supersession / deprecation / missing); authority chain check (same four); delegation check; policy evaluation; source unit deprecation check; execution; ledger commit. Each invalidation reason produces a distinct, audit-legible refusal rationale.

**Rationale:** The substrate's commitment "no act under stale governance" is the architectural source of all six triggers. Each trigger names a way governance can become stale (credential withdrawn, policy replaced, authority root updated, unit's contract drifted out of calibration, unit deprecated by its author, witness no longer cryptographically valid). The substrate's uniform invalidation surface is the requirement that all six refuse through the same mechanism — the runtime's invocation flow — rather than through trigger-specific machinery.

This pass delivers exactly that: every refusal goes through the same `_refuse()` path, produces an Act on the ledger with a clear rationale, and propagates automatically via archive lookups. The credentials archive's `get()` is the choke point for revocation, supersession, and deprecation of credentials. The code archive's `get()` is the choke point for unit deprecation. `verify_compiled_form()` is the choke point for compilation integrity. Adding new invalidation reasons in future (drift, or others) follows the same shape: detect at lookup; raise a tagged exception; runtime catches and refuses with rationale.

**Architectural separation: invalidation is archive state, not unit content.**

A revoked / superseded / deprecated credential's content_id does not change. Historical ledger entries that referenced it remain resolvable through audit accessors (`get_for_audit`, `get_for_compile`). Invalidation propagates through *runtime archive behaviour*, not through content mutation. This preserves content-addressing as a pure function of unit content, which the substrate's compilation pipeline depends on.

Compile-time graph walks (`compile.py::_lookup`, `composition.py::compose_refs`) use the audit accessors so compilation produces a compiled form whose content_id is a function of unit content alone — invariant under operational changes (revocation, supersession, deprecation). The compiled form's witness is signed against this stable hash. Invalidation thereafter is the runtime's responsibility.

**What this completes:** Five of the six v1.4 invalidation triggers now have concrete machinery. Constitutional currency — no act under stale governance — holds as a structural property of the runtime's invocation flow. The remaining trigger (drift) is well-understood architecturally (behaviour-characterised contract pattern declares calibration; runtime monitors; out-of-calibration outputs invalidate the unit) but requires statistical machinery (running calibration error estimates, threshold checks against drift criteria) that is large enough to warrant its own pass.

## Compiled forms are self-contained for verification (May 2026)

**Decision:** Compiled forms now carry a `witness_payload: dict` field alongside the `witness: str` signature. The witness_payload contains everything needed to verify the witness independently: the custodian's public key (or quorum members' public keys), the original payload_hash that was signed, the signature(s), and the witness type tag. `WitnessResponse` similarly carries witness_payload alongside the compact signature string; `compile_unit()` propagates witness_payload from response to compiled form.

A new `verify_compiled_form(compiled_form)` helper performs the verification end-to-end: recomputes the unwitnessed payload hash, checks witness_payload's recorded payload_hash matches, dispatches by type to `verify_witness` (single signature) or `verify_quorum_witness` (multi-sig), returns True only if all checks pass.

**Rationale:** The previous cryptographic addition produced real Ed25519 signatures but required out-of-band knowledge (the custodian's public key) to verify them. Putting verification data on the compiled form makes verification self-contained: a third party with only the compiled form can verify it. This is the cryptographic analogue of the substrate's "audit trail by inspection, not interpretation" commitment — the compiled form is now a verifiable artefact, not a token requiring external lookup tables.

Tampering with the compiled form (mutating any of its fields after witnessing) invalidates the witness, because the recomputed unwitnessed_payload hash no longer matches what was signed. Tampering with the witness_payload directly (e.g. substituting a different signature) also fails: the signature would not verify against the public key, or the public key would not match the signature's expected signer.

**What this completes:** The cryptographic loop. A compiled form admitted to the substrate carries its own proof of admission. The chain of custody from "the unit's content + the rolled-up policy graph + the authority chain" through "the witnessing custodian's signature" to "any verifier with the compiled form alone" is closed. This is what the substrate's attestability commitment looks like at the smallest interesting scale; the architecture is ready for production hardening (key distribution, persistence, rotation) on top of a structurally complete substrate.

## Real cryptography: Ed25519 signatures and multi-sig verification (May 2026)

**Decision:** Witnesses are now real Ed25519 signatures. Each `LocalCustodian` holds an Ed25519 private key; signing a payload produces a 64-byte deterministic signature (Ed25519 is deterministic per RFC 8032), encoded as hex in `WitnessResponse.signature`. The custodian's public key is exposed via `LocalCustodian.public_key_hex`. The `verify_witness(public_key_hex, payload_hash, signature_hex)` helper performs asymmetric verification; anyone with the public key can verify; only the holder of the private key can produce.

For multi-custodian quorum, `QuorumCustodian.witness()` produces a structured multi-sig: each member contributes a real signature plus its public key, encoded under the member's custodian name. The joint witness's `signature` field is a content_hash over the structured payload (compact, fits in the existing string-typed signature field); the structured payload is attached to the WitnessResponse as `quorum_payload` and is what verification actually checks. `verify_quorum_witness(payload_hash, quorum_payload)` verifies each member signature independently and confirms at least `threshold` members signed validly.

**Rationale:** The substrate's central commitments — attestability, attribution, "no act under stale governance" — were structurally honest under the SHA-256 stub but were not cryptographically defensible. Anyone could compute a stub witness given the custodian's name; nothing was attributable; forging was trivial. Real Ed25519 signatures make these commitments cryptographically real: a witness is attributable to a specific keypair; forging requires the private key; verification is asymmetric and anyone can perform it.

This is the smallest crypto upgrade that delivers the substrate's claims. Ed25519 is small, fast, deterministic, widely supported, and standard (NIST FIPS 186-5). Threshold signatures (BLS) would let the joint witness be a single compact signature indistinguishable from a non-threshold one, but they require pairing-based cryptography and a much larger library footprint; for the substrate's audit-transparency goals, multi-signature (where each member's contribution is independently visible) is arguably better — auditors can see exactly who signed.

**What is now cryptographically guaranteed:**

- Witness attribution: a valid witness is a proof that the holder of a specific private key signed the specific payload hash. The custodian holding that private key is the only entity that could have produced it.
- Tamper resistance: changing the signature, the payload hash, or the public key in a multi-sig contribution breaks verification.
- Quorum integrity, as far as it goes: a multi-sig verifies only when at least `threshold` contributions carry valid signatures over the same payload hash, and substituting one contribution's signature for another's fails verification, because each public key is checked against its own claimed signature. **It does not establish that the signatories are the quorum's members.** See the October 2026 entry below; the earlier claim that an extra contribution from a non-member fails verification was wrong.
- Forge resistance of a signature, under reasonable cryptographic assumptions: Ed25519 has no known practical attack, so a signature by a given key cannot be forged. **This is not the same property as a witness being unforgeable**, and the earlier wording conflated the two: an acceptable witness can be produced without attacking any key, by supplying keys of one's own. See the October 2026 entry below.

**What is NOT yet handled (the remaining cryptographic gaps):**

- **Key distribution and rotation.** Custodian public keys are exposed via `public_key_hex` but there is no protocol for distributing them to verifying parties, no protocol for rotating compromised keys, no certificate chain anchoring keys to natural-person identities. Phase 4+ work.
- **Key persistence.** A `LocalCustodian` generates a fresh keypair each construction unless seeded with `private_key_bytes`. Production custodians need persistent identity (a key stored securely, rotated on a schedule). The `private_key_bytes` constructor is the seam for plugging in a real key-management system.
- **Custodian identity in compiled forms.** The `WitnessResponse.custodian` field carries the custodian's name (a string) but the witness's public key is not currently stored on the compiled form. Verifying a compiled form's witness requires the verifier to know the public key out-of-band. A natural next step: include the public key on the compiled form's witness, so verification is self-contained.
- **Constitutional source anchoring.** The substrate's recursion still terminates at "parliament" — a string. Tying constitutional source credentials to actual natural-person keypairs (via biometric attestation, multi-party computation, whatever the real anchoring becomes) is the deepest cryptographic gap. The substrate is now ready to receive it: any natural-person credential is just a credential with an Ed25519 keypair, used to author further credentials.

**Test environment note:** The `cryptography` Python package is now a dependency (alongside `pytest` and `wasmtime`). On systems where `cryptography` cannot be installed or imported, the substrate cannot run; this is documented in `requirements.txt` and recorded here as a real (and unavoidable) dependency footprint for a substrate that takes attestability seriously.

## Multi-custodian quorum: cooperative substrates have joint witnesses (May 2026)

**Decision:** A `QuorumCustodian` composes multiple member custodians and produces a joint witness valid only when a configurable threshold of members sign. Below threshold, `QuorumNotMet` is raised and the compiled form is not produced — the substrate refuses to admit it. The joint signature is the content_hash of the sorted member signatures plus the quorum metadata; reproducible from the same inputs; verifiable by any party with the member custodians.

The cooperative substrate exposes its own custodian via `coop.custodian`: a `QuorumCustodian` over its member operators' custodians. Cross-operator units (units that reference the cooperative credential in their authority chain) are compiled under this joint custodian, so their compiled forms carry joint witnesses rather than single-operator witnesses. The substrate's compile pipeline does not change — it accepts a custodian parameter as it always has; `QuorumCustodian` slots in alongside `LocalCustodian` under the same interface.

**Rationale:** The cooperative substrate is itself a substrate at composite scale (per the substrate-as-unit vocabulary refactor). A substrate has a custodian that witnesses compiled forms produced under its authority. The cooperative substrate's custodian must therefore exist and must reflect the joint authority of its member operators — otherwise compiled forms produced "under the cooperative substrate" would actually be witnessed by one member alone, which is the exact failure mode (single-custodian compromise) that multi-custodian quorum exists to prevent.

The architectural shape: a `QuorumCustodian` is to a `LocalCustodian` what a cooperative substrate is to a unit-scale substrate. Composition at the witness layer mirrors composition at the substrate layer. Both follow the same pattern: the composite has an identity (the cooperative credential / the quorum's metadata), members (constituent substrates / constituent custodians), and a threshold rule for admitting work (delegation / quorum threshold).

**Phase 2-deepened scope:**

- Member custodians are still `LocalCustodian`s with deterministic SHA-256 witnesses. The quorum mechanism is structurally correct; the underlying signatures are stubs. Real cryptographic threshold signatures (BLS, Shamir secret sharing) are future work that slots into the same `QuorumCustodian` interface.
- The threshold is configurable. Default is unanimous (`threshold = len(members)`). Cross-operator setups requiring N-of-M quorums supply `quorum_threshold=N` to `CooperativeSubstrate`.
- The cooperative substrate's `custodian` property constructs the `QuorumCustodian` on-the-fly from the current member set. Members can change between calls; the cooperative substrate's identity (its credential's content_id) is unchanged.
- Failure handling: if a member custodian raises during witnessing, it is recorded as a failure and the quorum is evaluated against successful signatures. If too few members signed, `QuorumNotMet` raises. This handles real-world cases (member offline, signature timeout) at the structural level.

**What this verifies:** Compiled forms admitted under a cooperative substrate require joint witnessing. A single operator cannot unilaterally produce a compiled form that the cooperative substrate is supposed to govern; the joint signature cannot be forged without all (or at-threshold) members' custodians signing.

**What is still stub:** The underlying signatures. A member custodian's "signature" is still a SHA-256 hash anyone can compute given the member's name. Real cryptographic attribution (each custodian has a private signing key; the joint signature is verifiable as a real cryptographic threshold signature) is the next layer; the structural shape is now complete to receive it.

## Vocabulary refactor: substrate is the post-compilation running thing (May 2026)

**Decision:** Names in the codebase are aligned with the architectural vocabulary precisely:

- **Substrate** (`src/substrate/operator.py::Substrate`) — the post-compilation running instantiation: archives, ledger, runtime, custodian. One or more units, composed or uncomposed, at any level of nesting. Substrates can themselves be composed and nested.
- **Operator** (`src/substrate/operator.py::Operator`) — the institutional authority running a substrate. The operator's identity is its root credential; the substrate is what the operator runs. The operator carries no archives or ledger directly; those belong to the substrate. Convenience properties (`.code`, `.runtime`, etc.) forward to the operator's substrate so call sites are not disrupted.
- **CooperativeSubstrate** (`src/substrate/federation.py::CooperativeSubstrate`, renamed from `Federation`) — a composition of substrates run by different operators. Itself a substrate at composite scale. Identified by its cooperative credential's content_id. Holds member operators and routes cross-operator invocations.
- **Runtime.cooperative_substrate** (renamed from `runtime.federation`) — back-reference set when the operator joins a cooperative substrate. Cross-operator invocations route through this.

`Federation` remains as a legacy alias for `CooperativeSubstrate` during the brief transition, to be removed when the cross-operator demonstration is fully migrated. New code should import `CooperativeSubstrate`.

**Rationale:**

The user pinned the vocabulary in conversation: "A substrate may consist of one or more units, composed or uncomposed at any level of nesting — it becomes a substrate once instantiated post compilation, and is run by an operator. Substrates can be composed and nested, effectively as individual units themselves. A cooperative substrate is a composition of substrates run by different operators."

This is the architectural endpoint of compositional uniformity: substrates are units; substrates compose into higher-order substrates; the cooperative substrate is one specific kind of composite substrate (the kind where the constituents are run by different operators). The previous code had `Operator` doing two jobs (carrying the institutional identity AND being the bundle of archives/ledger/runtime); the new split separates these. `Federation` was a misleading name for the registry — what it really represents IS the cooperative substrate, so it now carries that name.

This is a vocabulary refactor, not an architectural change. The substrate's behaviour is unchanged. The point is that the code can now be read with the architecture's own vocabulary, which makes subsequent reasoning about composite substrates, nested substrates, and the recursion-terminates-at-natural-persons commitment legible at the code level.

**What this confirms:** The recursive compositional shape is in the code, not just in the documentation. A `Substrate` can be a single unit (a state unit with a tiny implementation, for example) or a deeply nested composition of units, policies, and sub-substrates. Two substrates run by different operators compose into a `CooperativeSubstrate`, which is itself a substrate. The architecture has no architectural endpoint until the recursion bottoms out at the natural person.

## Operators, federation, cooperative substrate, delegation (May 2026)

**Decision:** Cross-operator composition is the Phase 2-deepened addition. Four pieces of structure introduced together, each minimal:

1. **Operator** (`src/substrate/operator.py`): a structural composition over Runtime + archives + ledger + custodian + a root credential that is the operator's identity. An operator's content_id IS its root credential's content_id. No new primitive type; this is structural wiring.

2. **Federation** (`src/substrate/federation.py`): a registry of operators that recognise each other for cross-operator composition. Not itself an authority; carries no credentials; witnesses nothing. It is plumbing — a way for an operator's runtime to look up another operator's runtime by content_id. `Federation.add(operator)` sets a back-reference on the operator's runtime so impls can call `runtime.invoke_in(target_operator_id, source_unit_id, inputs, credential_id)`.

3. **Cooperative substrate**: a normal credential with parent references to two (or more) operator roots, plus optional cross-operator policy refs. What makes it cooperative is structural: it appears in the credential graph of units from multiple operators, bringing both operators' roots into those units' authority chains. **No new primitive.** The architecture composes the cooperative substrate without new machinery, which was the point — compositional uniformity demands the cross-operator pattern be expressible in terms of the existing primitives.

4. **Delegation check** (new runtime behaviour in `Runtime.invoke`): the invoking credential's ancestry (walked upward through its credential_refs) must intersect the compiled form's authority chain. An empty authority chain is treated as permissionless (no delegation enforced); declaring authority opts the unit into delegation enforcement. This is the substrate-level mechanism that makes cross-operator authority work: when a unit references a cooperative substrate, that unit's authority chain includes both operators' roots, and credentials from either operator are structurally delegated.

**Rationale:**

The substrate's deepest architectural claim — that the same primitives operate at unit, institutional, national, and composite (cross-operator) scales — needed a test that genuinely exercised it. We had refined the unit-scale picture through Phases 1, 1.5, 1.6, and 1.7. Cross-operator composition is the smallest test that can falsify compositional uniformity. Building it surfaced what was implicit and what required new machinery; the result is that the cooperative substrate adds no architecture (it is a credential), the federation adds no architecture (it is a registry), the operator adds no architecture (it is a composition over existing parts), and only one substrate-level mechanism was missing: delegation enforcement.

The delegation check is the substrate-level companion to "no act under stale governance": **no act under non-delegated authority.** A credential invokes a unit only if it is structurally derivable from a credential the unit recognises as its authoring authority. The check is content-addressed (walks credential_refs through the credentials archive); revocation status does not influence it (revocation is checked separately, before delegation). The combination is: invoker exists, invoker is not revoked, no authority-chain credential is revoked, invoker is delegated under the chain, every policy permits.

**Phase 2-deepened simplifications recorded here:**

- **Shared archives between operators.** Each operator has its own ledger and custodian and runtime, but in the cross-operator demonstration both operators use the same CodeArchive and CredentialsArchive instances. A real distributed federation would have per-operator archives and a content-exchange protocol; that machinery is deferred. Sharing archives in-process is the smallest simplification that lets the substrate's content-addressing carry across operators trivially (anything one operator has, the other has too, because the archive object is the same Python object).

- **Multi-custodian quorum is NOT implemented in this pass.** Each operator's compiled forms are witnessed by that operator's single LocalCustodian. A real cross-operator compiled form (one that references the cooperative substrate) should be jointly witnessed by both operators' custodians under some quorum rule. Deferred to a subsequent pass.

- **Real cryptography is still stub.** Witnesses are SHA-256 of payloads; anyone can compute them; nothing is attributable. The cross-operator demonstration is structurally honest about composition; it is not cryptographically attributable.

- **Cross-operator credential exchange is implicit.** Because archives are shared, operator A's caseworker credential is in operator B's archive automatically. A real federation would require an explicit credential-exchange step where the cooperative substrate's commitments specify which credentials each operator must accept from the other.

**What the cross-operator demonstration shows:**

`examples/cross_operator_uc/` builds DWP and Home Office as separate operators federated together. DWP's advance_payment_decision invokes Home Office's right_to_reside_check via `runtime.invoke_in()`. Three cases:

- UK applicant → HO confirms RTR → DWP approves.
- EU applicant → HO refuses RTR → DWP refers to human, citing the HO act_id in its output so the audit trail is traceable.
- Caseworker revoked → DWP refuses before cross-operator call.

Both operators have their own ledgers (3 acts on DWP, 2 on HO). The joint audit trail is the union, joinable on cross-operator act references carried in act outputs. Operators retain sovereignty over their records.

**What this verifies and what it doesn't:**

Verifies: the architecture's primitives (three primitive types, references, compile-at-commit, content-addressed compiled forms, runtime evaluation, refusal as first-class output, policies-as-functions, structural contracts, the Horizon correction) compose cleanly across operator boundaries. The cooperative substrate is expressible as a credential. Delegation is structural. The four-phase plan turned out to need a fifth piece (delegation as runtime mechanism) that fell out naturally when cross-operator was attempted.

Does not verify: cross-operator under network partition, under hostile compromise, under real cryptographic attribution, with millions of acts, with multi-custodian quorum, with full credential-exchange protocols. Those are engineering, not architecture; the substrate's claim is the architectural pattern works, which this demonstration confirms.

## WASM as the sandboxed-execution choice (May 2026)

**Decision:** WASM is the substrate's sandboxed-execution mechanism for implementations. The state unit's content schema gains a `language: "wasm"` variant carrying `module_hex` (hex-encoded WASM bytes) and `entrypoint`. The runtime's `_resolve_implementation` dispatches by language; the WASM executor compiles the module once (cached by content_id) and creates a fresh `wasmtime.Store` and `Instance` per invocation, so implementations cannot share state across invocations or with their caller.

The Python executor remains supported. It is convenient for development, for trusted internal units (where sandboxing is not the priority), and for the existing UC example. New implementations intended for production should target WASM.

**ABI (Phase 1.7):**

- Exports `memory`, `alloc(size: i32) -> i32`, and the entrypoint as `(inputs_ptr: i32, inputs_len: i32) -> i64`.
- Inputs are JSON-encoded UTF-8 bytes; the runtime writes them into linear memory at the offset returned by `alloc`.
- The entrypoint returns an i64 packing `(output_ptr, output_len)` in the high and low halves.
- The runtime reads output bytes, decodes JSON.
- Refusal: the implementation returns an output dict containing `_substrate_refuse: "<rationale>"`. The runtime converts this to a `Refuse` with the rationale. A WASM trap also surfaces as a refusal (with the trap message in the rationale).

**Phase 1.8 (deferred):** Sub-unit invocation from WASM (a host-imported `runtime.invoke`). Adding this is straightforward but introduces marshalling concerns (passing credential ids and output dicts across the host boundary) that are best dealt with once a real WASM-authored unit motivates them.

**Why WASM specifically:**

- Implementations remain content-addressed; the Horizon correction is preserved unchanged. WASM closes the orthogonal sandboxing gap.
- The implementation host interface is small enough that we can reason about what implementations can and cannot do. The default is deny-everything: no filesystem, no network, no clock. Capabilities are added via host imports, deliberately.
- A real ecosystem of authoring toolchains (Rust, AssemblyScript, Go, others) targets WASM. The substrate does not need its own implementation language.
- WASM bytes are content-addressable cleanly. The same source compiled twice produces (within minor build-determinism caveats) the same bytes; if not, that is fine, because the substrate addresses the bytes themselves.

**Environment caveat:** WASM execution requires a Python environment that can allocate JIT pages. macOS hardened-runtime Python binaries (the system `python3` shipped via Xcode Command Line Tools on Apple Silicon) cannot, and any WASM call SIGKILLs the process. The test suite detects this via subprocess probe and skips the WASM tests on such environments. Recommended Python for development: Homebrew or pyenv 3.11+. The substrate code is otherwise environment-independent.

## Non-reconcilability is a type mismatch (May 2026)

**Decision:** A unit's spec is its contract. Composing two units is composing their contracts. An incomposable composition is a type error the compiler catches at compile-at-commit. Specifically, each functional unit's spec may carry a `preconditions` list of structured constraints (`{"var": str, "op": str, "value": Any}`). At compile time, the compiler collects every precondition from every functional unit in scope (the source unit and all policy units it transitively reaches), groups them by variable, and checks joint satisfiability per variable. An empty intersection raises `TypeMismatch` with a rationale naming the contributing units and the unsatisfiable constraint.

This recovers — and generalises — the compile-time conflict detection that the dict-based `NonReconcilablePolicy` machinery provided in the original design. The shift is that "non-reconcilability" is no longer a special property of policy combination; it is a structural property of contracts in composition. The same check applies to any composition where the unit contracts express their preconditions structurally.

**Rationale:** Once policies became functional units (see the entry below), the compile-time conflict-detection capability was lost because policies were opaque Python source. But that loss reflected an architectural mistake, not a fundamental constraint: composability of governance is a type-system question. If the unit declares its precondition structurally in its spec, the compiler can analyse it without executing it. The contract IS the spec; the spec is what the compiler reads; composition is contract composition.

This also restores symmetry with the substrate's deepest commitment: at every step, what is composable should be a property the substrate can verify by inspecting the content-addressed artefacts, not by running them. Pre-runtime inspection is the architectural value; making composability into a typing problem brings it under that umbrella.

**Phase 1.6 scope:**

- Operators supported: `lte`, `lt`, `gte`, `gt`, `eq`, `ne`, `in`, `not_in`. Per-variable only.
- Preconditions are *opt-in*. A policy without structured preconditions is not type-checked at compile time; its refusals happen at runtime as before. The two regimes coexist; declaring structurally unlocks compile-time conflict detection.
- The substrate trusts that the implementation honours the declared precondition (specification-bounded discipline). The compiler does not extract preconditions from source code; it reads them from the spec. A spec that lies about its impl is a unit-author failure the substrate does not detect.

**Phase 2 extensions (named here so they are not invented later):**

- Input/output type checking across functional composition. A downstream unit's input type must subsume the upstream unit's output type. The compiler checks; an incompatible pair refuses compilation as TypeMismatch.
- Behaviour-characterised bounds and drift criteria as structural contract elements. The compiler verifies compositions are jointly satisfiable within declared bounds; the runtime monitors executions for drift outside calibration.
- Cross-variable preconditions (constraints over multiple variables).
- Witnessed conformance: test artefacts (themselves content-addressed) demonstrating that an implementation honours its declared spec. Phase 1.6 trusts; Phase 2+ verifies.

## Policies are functional units, not credential payloads (May 2026)

**Decision:** A policy is a functional unit. The previous design (an `is_policy` flag on `CredentialUnit` plus a `policy_clauses` dict on the same unit) is retracted. `CredentialUnit` no longer carries `is_policy` or `policy_clauses`; it carries `policy_refs: tuple` — the content_ids of functional units the credential brings into binding.

The compile-at-commit pipeline collects the *transitive set of policy functional units* the source unit is governed by, rather than performing a strictest-binding-wins roll-up over dict-shaped clauses. The compiled form's `rolled_up_policy: dict` is replaced by `policies: tuple` (sorted content_ids of policy functional units).

At runtime, before executing the source unit's implementation, the runtime invokes each policy through the standard `runtime.invoke()` path. Each policy invocation produces its own Act on the ledger. If any policy returns `Refuse`, the parent refuses with the policy's rationale.

**Rationale:** A policy takes an invocation context as input and produces a verdict (permit, refuse, or pass-through). That is the definition of a functional unit. Treating policies as a separate type was a vestigial special case: it carried its own payload schema (the dict), its own evaluation discipline (the key-prefix convention), and its own conflict-detection machinery (strictest-binding-wins). All of that is collapsed into the existing three-primitive structure when policies are functional units.

This also unifies what the substrate exposes to behaviour-characterised contracts. A probabilistic policy — one that interprets whether an input satisfies a fuzzy constraint — has confidence/uncertainty as first-class properties because it is a functional unit with a `BEHAVIOUR_CHARACTERISED` contract pattern. Drift detection (when a behaviour-characterised unit's outputs leave calibration bounds) applies to probabilistic policies the same way it applies to any other behaviour-characterised unit. There is no special machinery.

**What is preserved:**

- Strictest-binding-wins emerges from refuse-wins. If three retention policies (30/60/90 day caps) are all in force, the 30-day policy is the first to refuse anything over 30 days, so the effective cap is 30. The composition semantic is unchanged; the mechanism is simpler.
- Wilful inclusion still applies. The source unit must list every transitively reachable policy unit in its `functional_refs`. `compose_refs` does this automatically.
- Authority and behaviour remain separated. The credential brings the policy into force; the policy is the function. Different authorities can compose policies authored elsewhere by binding them.

**What is lost:**

- Static conflict detection in the form that previously refused non-reconcilable dict-shaped policies. Conflicts now surface at runtime as policy-refusal cascades. A future phase can restore some static detection by letting policies expose a structural signature in their spec (e.g. `spec["structural_constraint"] = {"key": "max_retention_days", "op": "max", "value": 30}`), which the compiler can analyse without executing the policy. For now, runtime refusal is the only mechanism.

**Phase 1.5 decision: gate-only.** Policies permit or refuse; they do not transform the invocation context. Pass-through is `Permit` with no transformation. Transforming-policies (filter semantics, where one policy's output feeds the next) are deferred: they introduce ordering, which conflicts with the unordered-refs decision and adds substantial complexity. Phase 2+ can reintroduce them with explicit ordering machinery if needed.

**What this supersedes:** The earlier `specification_gaps.md` entry "Policy clause schema and strictest-binding-wins by key prefix" is superseded. The key-prefix convention is gone. The strictest-binding-wins guarantee is preserved through refuse-wins semantics rather than dict combination.

## The Horizon correction: implementations are content-addressed artefacts (May 2026)

**Decision:** A functional unit's implementation (the actual executable code that runs when the unit is invoked) is itself a content-addressed artefact held in the code archive as a state unit. The functional unit references its implementation by content_id through a dedicated `implementation_ref` field. The runtime fetches the implementation by hash and executes it. Implementations are no longer mutable runtime registrations sitting outside the content-addressing system.

**Rationale:** The earlier Phase 1 design kept implementations as Python callables registered against a unit's content_id through `Runtime.register_implementation()`. This was convenient and shipped quickly. It was also exactly the Horizon failure mode embedded in the substrate's foundation: the policies, the authority chain, the compiled form, the ledger entries — everything visible to governance — could look correct while the *actual behaviour* was a buggy or substituted Python function that nobody had hashed, witnessed, or audited.

The substrate's central commitment ("no act under stale governance") is undermined when *what is acting* is not part of what is governed. Implementations must be subject to the same content-addressing, witnessing, and invalidation machinery as policies and authority. Otherwise the substrate inspects everything except the thing that actually matters.

**What changes:**

- `FunctionalUnit` gains an `implementation_ref: str` field (the content_id of the implementation state unit). It participates in `content_id()`. A functional unit with no implementation_ref cannot be executed (the runtime refuses).
- Implementations are constructed via `python_implementation(source: str)` — a helper that wraps Python source text in an immutable state unit with content `{"language": "python", "source": "...", "entrypoint": "..."}`. The runtime executes by fetching the state unit, exec'ing the source in a per-implementation namespace (cached by content_id so the exec happens once per implementation), and calling the entrypoint.
- Wilful inclusion treats `implementation_ref` as a direct top-level reference; the implementation state unit and any state units it transitively depends on must satisfy the same closure check as functional/state/credential refs.
- `Runtime.register_implementation()` is removed. The implementation is in the archive; nothing extra needs registering.

**What this enables that the previous shape did not:**

- A unit's behaviour cannot drift after compilation: the compiled form references the unit, the unit references the implementation by hash, the runtime executes that exact implementation. Substituting a different Python function changes the implementation's content_id, which changes the unit's content_id, which invalidates the compiled form.
- The implementation is itself auditable: a natural person can read the implementation state unit's source text before approving the unit, in the same way they can read the rolled-up policy.
- Multi-language implementations are a small extension: the state unit's content has a `language` field; the runtime dispatches to a language-specific executor. Phase 1 supports only `python`; later phases add `wasm` or a constrained DSL.

**What this does not yet solve:**

- Python is not a constrained execution environment. A malicious or compromised implementation can still do anything Python can. The Phase 1 namespace passes `__builtins__` through; nothing is sandboxed. The content-addressing closes the substitution gap; sandboxing is a separate concern that belongs to a later phase (probably alongside WASM support).
- Implementations are still opaque to people who do not read code. A DSL with structural readability is the next step beyond this; for Phase 1, "the source text is auditable" is honest only for Python-literate readers.
- This does not address the policy-language anaemia, the missing cryptography, or the missing multi-custodian quorum. Those are separate architectural gaps; the implementation-content-addressing fix is necessary but not sufficient.

## Architectural revisions surfaced during implementation planning

### Retraction of the four-constituent boundary expression (May 2026)

**Decision:** The "four-constituent boundary expression" (functional / execution / identity / policies) is retracted from the architecture. Units have content (of one of three primitive types) plus references to other units. Composition is reference composition, not constituent composition.

**Rationale:** When the implementation plan tried to encode the four-constituent model as a class, the model collapsed under inspection. Three of the four "constituents" (execution, identity, policies) were sets of references to other units; only one (functional) was the unit's own content. The vocabulary was residue from earlier versions where the four constituents had primitive status (v3 through v9); the v10 simplification made the three roles primitive and demoted the constituents to a "composition pattern" but retained the vocabulary for continuity rather than because the architecture needed it.

**What replaces it:**

- Each unit type has its own content schema (code for functional, data for state, authority structure for credential).
- Each unit carries a set of references to other units. References are typed by the target's primitive type (functional, state, or credential references).
- Roles previously assigned to constituents are now properties of references: a unit's identity is whatever credential it references as its identity credential; a unit's policies are whatever credentials it references with policy role; a unit's execution dependencies are whatever functional, state, and credential units it references for invocation.

**What this preserves:**

- Compile-at-commit: the compiler still walks the unit's references, traverses the authority chain, and rolls up policies under strictest-binding-wins. The algorithm is structurally unchanged; it operates on the reference graph rather than on a four-constituent structure.
- Wilful-inclusion rule: every reference is explicit at the unit's top level; no implicit transitive inclusion.
- Roll-up under strictest-binding-wins: the compiler walks all credential references with policy role across the unit and its transitively referenced units; the strictest constraint wins.
- Uniform invalidation surface: when any referenced unit's compiled form changes, the unit's own compiled form invalidates. Invalidation propagates over the reference graph.
- Content-addressed compiled forms: a unit's compiled form is determined by its content plus the compiled forms of its references.
- Compositional uniformity at every scale: the same content-plus-references pattern operates at unit, institutional, national, and composite scales. The four-constituent vocabulary actually obscured this; without it, the recursion is cleaner.
- The recursion terminating at the natural person: the authority chain, followed through credential references, terminates at a constitutional source credential held by a natural person. No change.

### Execution-optimisation metadata moves from unit to compiled form (May 2026)

**Decision:** Execution-optimisation metadata (fused-form hashes, cached environment hashes, dispatch tables, any other pre-computed dispatch information) lives in the compiled form, not on the unit. The compiled form's schema includes `fused_form: ContentId | None`, `cached_environment: ContentId | None`, and `dispatch_table: ContentId | None`.

**Rationale:** The original four-constituent model's "execution constituent" was intended to carry pre-computed execution metadata (for example, the hash of a cached environment the unit would need at runtime). The intent was sound but the placement was wrong. Execution metadata is *derived from compilation*, not authored by the unit's author. The author doesn't know the hash of the fused form before compilation; the compiler computes it. Putting execution metadata on the unit was a category mismatch: the field was on the unit but the values in it were compilation outputs.

The compiled form is the right place for these artefacts because:

- It is what the runtime actually evaluates at every invocation.
- It is produced by the compiler, which is where the metadata is derived.
- It is content-addressed, so the optimisation artefacts can be looked up by hash at invocation with no graph walk.
- Invalidation already propagates through the compiled form's content; when an optimisation artefact becomes stale (because a dependency changed), the compiled form invalidates and recompilation produces a new artefact with a new hash.

**Phase 1 treatment:** The compiled form's optimisation fields exist in the schema but are stubbed as `None`. The compiler in Phase 1 does not produce fused forms or cached environments. Later phases populate these fields as real optimisations are implemented, without requiring a schema change.

**Open question for later phases:** Whether the author should be able to provide compiler *hints* (for example, "fuse with this dependency" or "use this runtime profile") as a separate field on the unit. Phase 1 defers this; the default is compiler-decided fusing. If evidence emerges that compiler-decided fusing is insufficient, a `fusion_hints` field can be added to the unit later.

## Phase 1 gaps

### Reference ordering on units: unordered sets, canonicalised by sort (May 2026)

**Decision:** A unit's reference fields (`functional_refs`, `state_refs`, `credential_refs`) are semantically unordered sets. The field type is `tuple` for immutability, but the tuple's order is presentation-only: `content_id()` sorts the references before folding them into the hashed payload. Two units that list the same references in different orders produce the same `content_id` and are the same unit.

**Rationale:** The paper does not say whether reference order matters. Both readings were plausible. Unordered won on the following grounds:

- **Maximises compiler optimisation freedom.** Fusion order, dispatch table construction, and execution scheduling are choices the compiler makes from a cost model. If reference order on the unit were semantic, the author would have pre-selected an ordering the compiler must either honour (losing optimisation freedom) or ignore (making the field misleading). Unordered references let the compiler choose; the chosen order is encoded in the resulting compiled-form artefacts, where it belongs.
- **Eliminates spurious content_id churn.** A programmer reordering references for readability would otherwise produce a different `content_id`, a different compiled form, and unnecessary recompilation cascades.
- **Maximises compiled-form deduplication.** Two units depending on the same set produce the same compiled form. This matters for the compiler's cache and, in Phase 3, for multi-custodian agreement on compiled-form hashes (custodians must converge on the same content_id from the same logical inputs).
- **Matches the spirit of content-addressing.** What a unit depends on is invariant under presentation order.
- **Keeps the author-hints pathway clean.** If evidence later emerges that the author needs to influence fusion or dispatch decisions, hints belong in a separate explicit field (the open question already flagged for execution-optimisation metadata), where they can be inspected and overridden, not smuggled into the dependency list.

**What this forecloses:** The author cannot encode an ordering preference inside the reference list itself. Acceptable; that pathway was not useful anyway.

**Implementation:** `content_id()` calls `sorted()` on each reference tuple before serialisation. Hashing is therefore order-invariant; the field's tuple structure is retained only because tuples are hashable and immutable, which the frozen dataclass requires.

### Credential revocation as a lookup-time fault (May 2026)

**Decision:** A credential's `content_id` is stable across its lifecycle (revocation does not change it). Revocation is a state of the credentials archive, not a state of the credential. The runtime accesses credentials only through `CredentialsArchive.get(content_id)`, which raises `CredentialRevoked` if the credential is revoked. There is no separate "check if active" step on the runtime path.

**Rationale:** Two questions are entangled here. The first is *where revocation is recorded* (on the credential itself, or as separate archive state). The second is *how revocation propagates* to compiled forms that reference the credential.

On the first question, recording revocation on the credential (which would change its `content_id`) breaks the audit trail. Historical ledger entries reference the credential by content_id at the time the act occurred. If revocation changed the hash, "the credential that was active at time T" would no longer be resolvable from the ledger entry alone. Keeping content_id stable preserves audit integrity: revocation is metadata the archive maintains about the credential, not part of what the credential is.

On the second question, propagation should be automatic across every referencing site. The structural way to achieve that under content-addressing is to make `get()` itself the choke point. Every code path that uses a credential goes through `get()`; if `get()` raises on revoked credentials, propagation happens at every site by construction, with no separate per-site status check to remember. This matches the spirit of "break to every reference is the propagation mechanism" without requiring the credential to mutate.

**Implementation:**

- `CredentialsArchive` has a private revoked set.
- `revoke(content_id)` adds the content_id to the revoked set; idempotent.
- `get(content_id)` raises `KeyError` if the credential was never put; raises `CredentialRevoked` (with the credential attached to the exception, so audit code can still inspect it) if the credential is in the revoked set; otherwise returns the credential.
- `status(content_id)` returns `"active"` or `"revoked"`. This is a diagnostic / enumeration path for the future invalidation surface, not the runtime path.

**Phase 3 generalisation:** The revoked set in Phase 1 stands in for what Phase 3 will implement as content-addressed revocation certificates on the ledger. `get()` in Phase 3 will check whether any revocation certificate targets the credential; the API does not need to change. Revocation becomes a first-class act subject to authority and policy machinery, rather than a side-channel mutation of archive state.

### Policy clause schema and strictest-binding-wins by key prefix (May 2026)

**Decision:** Phase 1 policy clauses are flat dicts of `{constraint_name: value}`. The combine semantics for a constraint key are determined by the key's prefix:

- `max_*` → strictest is the smallest value (e.g. `max_retention_days`: 30 vs 60 → 30).
- `min_*` → strictest is the largest value (e.g. `min_confidence`: 0.95 vs 0.90 → 0.95).
- `allowed_*` → strictest is the intersection of allowed sets (e.g. `allowed_purposes`: ["eligibility", "fraud"] ∩ ["eligibility"] → ["eligibility"]). An empty intersection is non-reconcilable.
- `forbidden_*` → strictest is the union of forbidden sets.
- Otherwise → equality required; differing values are non-reconcilable.

**Rationale:** The paper specifies the *principle* (strictest-binding-wins) but not the *mechanism*. A registry mapping constraint names to combine semantics is one option but introduces an extra synchronisation point: every new constraint type needs a registry entry, and the registry itself becomes a constitutional-source-like artefact that needs versioning, witnessing, and invalidation handling. Encoding the convention in the key prefix avoids the registry entirely. The author of a policy declares the combine semantics by choosing the constraint name; the substrate has nothing to look up.

**What this constrains:** Policy authors must name constraints with the appropriate prefix. A constraint that should compose by intersection must be named `allowed_*`, not `permitted_*`. This is a small cost paid by policy authors in exchange for a substrate with no constraint registry to maintain.

**Phase 2 reassessment:** If the Universal Credit example surfaces constraints whose combine semantics do not fit any of the four prefixes (or if the prefix convention causes naming awkwardness in real-policy text), revisit. A pluggable combiner registered per-constraint is the natural fallback.

### Wilful inclusion as top-level listing of all transitively reachable units (May 2026)

**Decision:** A unit's `compile_unit` succeeds only if every content_id reachable through any chain of references from the source unit is also present in the source unit's own `functional_refs`, `state_refs`, or `credential_refs`. Transitive inheritance through another unit's references is not enough; the source unit must list every dependency at its top level.

**Rationale:** The phrasing in `docs/handover.md` is "every unit referenced transitively must be reachable through explicit references at the source unit's top level. The compiler refuses compilation for any reference reached only by implicit traversal." Read literally and operationally, this means: take the set of all transitively reachable units; compare with the set of directly referenced units; refuse if the first is not a subset of the second.

This is the strict reading. The alternative (any chain of explicit references is fine) makes wilful inclusion vacuous given that the compiler only ever follows declared edges. The strict reading actually constrains the unit's author: composing a unit forces a deliberate declaration of every constituent, with no inheritance shortcut.

**Implication for composition style:** A composing unit's reference list will be long — it must enumerate its direct dependencies plus everything reachable through them. This is by design. It makes invalidation propagation transparent (a unit's compiled form invalidates only if a unit it explicitly references invalidates, not because something it didn't list happened to change), and it makes the unit's full dependency surface readable from the unit itself.

**Phase 2 reassessment:** If the Universal Credit example shows the reference list growing unmanageably long, consider a syntactic helper (e.g. a `compose_refs(other_unit)` function the author calls during construction that imports the other unit's references explicitly). The semantic discipline does not change.

### Compile-time policy roll-up is sufficient; runtime sub-unit invocation is the author's concern (May 2026)

**Decision:** There is no opt-in/opt-out flag for runtime re-evaluation of sub-unit policies. Compile-at-commit rolls up policies once; the runtime evaluates the rolled-up form. The uniform invalidation surface ensures that when a sub-unit's policy changes the parent's compiled form invalidates, refuses execution, and re-rolls on recompilation. No per-invocation graph walk is required for governance to remain current.

Runtime composition (a composing unit's implementation calling a sub-unit at runtime to obtain its output) is an orthogonal concern. It is accommodated by passing the runtime into the implementation; the implementation calls `runtime.invoke()` on the sub-unit when it needs the output, and each sub-invocation produces its own ledger entry. The author's *implementation* is the opt-in. No flag, no declarative override, no extra surface area on the unit.

**Rationale:** The "expensive work once at commit, cheap work per act" pattern is the architecture's central performance commitment. A flag that re-walked the graph at runtime would dilute that commitment without adding governance correctness, since compile-time roll-up plus uniform invalidation already provides currency.

### Policy key naming is the policy author's discipline (May 2026)

**Decision:** The substrate matches policy constraint keys to invocation context keys by exact-string equality. Whether the constraint name reads naturally against the input field name is a policy-authoring question, not a substrate concern. Authors who want a constraint named `allowed_purposes` to be checked against an input named `purpose` must rename one side.

**Rationale:** Adding a substrate-level mapping layer (constraint name → input field name) would create a third coordination surface between policy authors and unit authors. The current discipline (match the names) costs the policy author a small naming choice and avoids the coordination layer.

### Calibrated reliability metadata as architectural property (May 2026)

**Decision:** Every functional unit MAY declare a `confidence` section in its spec. The section declares: whether the unit produces a scalar confidence value at runtime; in which output field; what calibration claim the confidence is meant to bear; the acceptance band the unit guarantees; and (for units that invoke sub-units) which aggregation function combines sub-unit confidences into the unit's own confidence. Policy units MAY declare a `confidence_gate` section that refuses any invocation whose input confidence is below a declared threshold. The runtime aggregates sub-unit confidences through composition automatically: when a parent unit's impl invokes sub-units that produce confidence, the runtime collects those confidences and, on the parent's return, applies the declared aggregation function and writes the aggregated value into the parent's output (unless the impl already set it explicitly).

This is **scalar confidence handling**, not distributional uncertainty quantification. The substrate carries reliability metadata as a number; aggregation combines numbers under min / product / mean / harmonic_mean. Distributional UQ (outputs as PDF parameters or sample sets, Monte Carlo or analytical propagators, copulas, coverage-based acceptance) is deliberately out of scope and belongs in domain libraries built on substrate primitives. See `docs/architectural_boundary.md` for the principle.

The confidence section is **optional**. Specification-bounded units that do not deal with uncertain quantities omit it entirely. Behaviour-characterised units that produce reliability metadata declare it. There is no substrate-wide mandate that every unit declare confidence handling; the mandate is that, if a unit produces a confidence value, the spec must say so structurally rather than as an undocumented convention in the impl.

**Rationale:** Two related but distinct concerns made substrate-native:

1. **Confidence-as-architectural-property** — metadata about how a unit treats its own outputs (the contract pattern, the calibration commitment, the acceptance band, the aggregation function, the gate thresholds). This is declared in the unit's spec, part of its content_id, validated at compile-at-commit. It is the substrate's structural commitment to making reliability metadata visible.

2. **Scalar confidence aggregation at runtime** — the operational mechanism: per-output scalar confidence values produced at runtime, aggregated through composition under the declared aggregation function. This is what flows through the substrate at invocation time. It is NOT distributional propagation; that is domain library territory.

Together with the backtest pattern (see below), this closes the loop: a calibration claim is declared structurally; a backtest unit checks the claim against realised behaviour by walking the ledger; if the claim is violated, the backtest refuses on the ledger; an authorised operator deprecates the target unit via an administrative act; deprecation propagates through the uniform invalidation surface.

The existing substrate accommodates the spec section with no schema change to the three primitive types — the spec is freeform, so a structured `confidence` section is a discipline imposed at compile-time. The runtime gains one mechanism: a per-invocation stack that observes sub-invocation confidences and applies the parent's declared aggregation function on return.

**Aggregation function set:** Four canonical forms cover the common cases.

- `minimum`: weakest-link semantics. The parent's confidence is `min(sub_confidences)`. Used when all sub-results must hold and the parent is no more confident than its least confident input. AND-composition.
- `product`: independent-event semantics. The parent's confidence is the product of sub-confidences. Used when the sub-results are statistically independent.
- `mean`: equal-weight averaging. Used for ensemble or vote-like compositions where each sub-unit contributes equally.
- `harmonic_mean`: penalises low values more strongly than `mean` but less strongly than `minimum`. Useful for ensembles where one weak input should reduce but not dominate the result.

These are scalar aggregators of reliability metadata. They are NOT statistical propagation over distributions. Domain-specific propagators (Monte Carlo, linear-Gaussian, convolution, copula-aware) are domain library code; they compose on top of substrate primitives but are not substrate primitives themselves.

A custom slot (`propagation: {"function": "custom", "ref": "<functional_unit_cid>"}`) is reserved for unit authors who want their own scalar aggregator.

**Gate semantics:** A policy with `confidence_gate: {minimum_confidence: X, applies_to_field: "confidence"}` refuses at invocation time if the invocation's inputs include a field at the named path with value below X. The runtime evaluates the gate before invoking the policy's impl. The threshold X is in the policy's content_id; substituting a more lenient threshold produces a new content_id, visible in any audit. This is the architectural difference between "a constant in a policy implementation" and "a compiled policy threshold": the latter is content-addressed and structurally observable, even though the underlying mechanism (constant comparison) is the same.

**Implementation override:** If a parent unit declares aggregation but the impl explicitly writes a confidence value into the named output field, the impl's value wins. The runtime only injects the aggregated value when the impl did not. This preserves the option for an author to express a more sophisticated reliability model than the canonical aggregation functions accommodate, without abandoning the structural declaration.

**Compile-time check:** The compiler validates that any present `confidence` section is well-formed (required fields, aggregation function in the canonical set or a resolvable custom ref). The compiler does NOT require that every unit invoking sub-units declare aggregation — leaving the slot off means "the impl handles confidence explicitly, or the unit does not produce confidence."

**Phase 2 reassessment:** If real adoption shows the four canonical aggregation functions are insufficient, the custom aggregation path becomes load-bearing and the spec's `ref` field starts seeing use. If the gate mechanism handles most thresholding cleanly, deprecate writing threshold constants into policy impls and require gate declaration.

### Backtest as a substrate-recognised pattern (May 2026)

**Decision:** A backtest is a regular functional unit whose impl walks the ledger, pairs predictions made by a target unit with outcomes recorded by an outcome unit, computes a declared calibration metric, and refuses if the metric falls outside declared bounds. No new substrate primitive is required; the pattern is supported by canonical metric functions in `src/substrate/backtest.py` that unit authors use in their backtest impls. The backtest's refusal is itself a ledger event; an authorised operator reads the refusal and deprecates the target unit through the standard administrative API. Deprecation propagates via the uniform invalidation surface.

**Rationale:** A calibration claim declared in a unit's `confidence.calibration` field is governance-load-bearing only if it can be checked against realised behaviour. Without a check mechanism, the claim is a string; with a check mechanism, the claim is a content-addressed promise that the substrate can verify against the ledger. The backtest pattern closes this loop without introducing new substrate machinery: it uses existing units, ledger walks, refusal semantics, and the administrative invalidation API.

Operator-driven deprecation (rather than automatic invalidation on backtest refusal) is the deliberate choice. Every invalidation is currently an explicit ledger event; making backtest refusals automatically cascade would create silent invalidations that the principle "every consequential act is on the ledger" does not fully accommodate. The current pattern keeps the auditor's mental model clean: backtest refuses, operator sees the refusal, operator deprecates, deprecation cascades.

**Metric functions provided** (in `src/substrate/backtest.py`): `coverage_rate`, `brier_score`, `bucket_calibration_error`, `exceedance_rate`. These are reference implementations of standard calibration metrics. Unit authors inline them in their backtest impls. The substrate does NOT provide every possible calibration metric; the four canonical ones cover the common governance cases. Domain libraries can provide more sophisticated metrics on top.

**Phase 2 reassessment:** If backtests get scheduled rather than operator-invoked (e.g. via a cron pattern triggered by ledger events), the substrate may want a recognised "scheduled backtest" pattern. For now, backtests are invoked explicitly like any other unit.

### Certification as a substrate pattern, distinct from compilation and from runtime policy (May 2026)

**Decision:** Certification of a unit's structure is a substrate pattern, not a compile-time check and not a runtime invocation policy. A certification unit is a regular functional unit whose impl takes a candidate unit's content_id as a runtime input, fetches the candidate from the code archive, inspects its declared structure (spec fields, authority chain), and invokes certification policy units as sub-units with the extracted structure as their inputs. The certification unit is compiled under the relevant cooperative substrate's quorum custodian, so the certification machinery is jointly witnessed. Each certification is a first-class substrate act: a permit (certified) or a refusal (denied), recorded on the certifying operator's ledger.

This is the pattern `examples/boeing_737_max/` now uses (`certify_mcas`). It is exactly parallel to the backtest pattern: a backtest unit inspects ledger history; a certification unit inspects a candidate unit's structure. Neither is a new primitive.

**Rationale:** The Boeing example originally evaluated FAA certification through a plain Python helper (`_simulate_certification`) in `run.py` — no ledger act, no witnessing, no compiled form. The reason the substrate-native path was not taken initially was a category error: certification was assumed to be a runtime policy bound to the MCAS units via `policy_refs`. It is not, for two independent reasons.

1. **Certification is not a runtime invocation policy.** Runtime policies are evaluated by the runtime against an *invocation's inputs*. The FAA's certification policies check `sensors_declared` (a spec field of the candidate unit) and whether the pilot credential is in the candidate's authority chain (a structural property of the candidate). Neither is present in a flight invocation's inputs (`{"aoa_left_degrees": ...}`). Binding `multi_sensor_required_policy` to an MCAS unit as a `policy_ref` would cause the policy to evaluate against every flight's AOA readings, find no `sensors_declared` field, and refuse *every flight* of *every* MCAS unit, certified or not. Certification gates a unit's structure; runtime policies gate an invocation's inputs. They are categorically different.

2. **Certification is not a compile-time refusal.** A unit failing to compile means it is structurally malformed (unresolved references, non-reconcilable preconditions, wilful-inclusion failure). `mcas_v1` is not malformed — it is a perfectly valid single-sensor functional unit, and it compiles cleanly. What it fails is *certification*: a policy judgement about whether a structurally-valid unit is fit for a regulated purpose. Conflating the two would mean the compiler refuses well-formed units on policy grounds, which overloads compilation with judgements that belong to a named authority.

The certification pattern resolves both. The candidate is a runtime input to the certification unit (the same way a backtest's target unit is a runtime input), so the certification unit's impl can fetch the candidate and read its true structure. The certification unit invokes the certification policy units as sub-units, passing the extracted structure as their inputs — so the policies fire through the standard runtime pipeline, each producing a ledger act, each refusing as a first-class substrate refusal. The certification unit is witnessed under the cooperative substrate's quorum custodian, so "no single party can certify alone" is structural.

**Consequence for the paper:** the v1.0 paper's §5.8 describes Boeing certification as "FAA certification policies refuse compilation"; that framing is incorrect on the architecture. Certification does not refuse compilation; `mcas_v1` compiles. Certification is a separate witnessed act that refuses. The paper should be corrected to describe certification as a substrate pattern (parallel to the backtest pattern) rather than as compile-time policy refusal.

**Phase 2 reassessment:** If certification recurs across domains (it is plausibly relevant wherever a regulator admits a structurally-valid artefact for a regulated purpose), the substrate may want a documented certification-unit helper analogous to `src/substrate/backtest.py`. For now the pattern is demonstrated once, in the Boeing example, and documented here.

## Static roll-up of the policy stack is not performed (October 2026)

**Decision:** Recorded as a gap, not closed. The compiler collects the in-scope policy units as a sorted tuple of content identities (`CompiledForm.policies`); the runtime invokes each of them in turn at every act. No rolled-up policy expression is composed, and the compiled form carries none.

**Why this belongs on the register:** §3.2's stage 3 and §3.3 specify that the pipeline composes the in-scope policies into a single rolled-up policy expression, with the strictest binding governing each governance dimension, and §3.6 specifies that the runtime evaluates that compiled expression rather than re-traversing the policy graph. The companion paper's §4.2 names six commitments the prototype does not substantiate. This is a seventh and it is not currently listed.

**What does hold:** strictest-binding-wins. §3.3 states that it emerges from refuse-wins composition over policy units treated as ordinary functional units, and that is exactly what the runtime does. The guarantee is not in question; the artefact is. Compile-time detection of non-reconcilable compositions also holds for declared structured preconditions, through `_collect_constraints` and `check_satisfiable`, which compose the per-variable meet across the source unit and every policy unit in scope and refuse on an empty intersection. See the entry "Non-reconcilability is a type mismatch" above.

**What is therefore absent:** the rolled-up expression itself, and with it any representation of a governance dimension. Two commitments depend on that representation. Wilful-inclusion verification against the dimensions the operator's authored content declares applicable (§3.4) cannot be performed, because there is no dimension to check coverage of; the prototype enforces the reference-listing half of wilful inclusion (§3.4.1) only. And a policy pair that is incomparable on a shared dimension without being jointly unsatisfiable (§3.5, first locus) is not detected at compilation; such a pair is admitted.

**Reassessed and deliberately not restored (October 2026).** Restoring a declarative constraint layer alongside the policy units was considered and rejected on an approach review. It would duplicate the schema `preconditions` already provides, with a strictly smaller operator set, and evaluating a declared signature in place of the policy it stands for would be non-conforming under §6.1, which admits no implementation that evaluates against alternative policy structures, approximates the rolled-up policy's evaluation, or suppresses refusals the rolled-up policy produces. It would also remove drift observation, deprecation refusal and the confidence gate from any policy whose invocation was skipped, since all three run only inside an invocation. A production conforming implementation would compose the rolled-up expression at compile time from the policies' own content rather than from an author's assertion about them.

**Consequence for the paper:** §3.2's stage 3 and §6.1's required stages describe a stage this prototype does not perform. The companion paper's pipeline already omits it, so the two papers do not agree with each other on the six stages. Both should be reconciled against what refuse-wins actually provides.

## Witnessing signs a payload hash rather than verifying by independent recompilation (October 2026)

**Decision:** Recorded as a gap, not closed. The custodian is handed a `WitnessRequest` carrying a payload hash and signs that hash. It does not fetch the unit, and it does not run the compilation pipeline.

**Why this belongs on the register:** §3.2's emission and witnessing stage specifies that the federated custodians witness the compiled form by independent recompilation, each fetching the unit and its references, running the pipeline independently, and signing the compiled form's identity only if its own recompilation produces the same identity. The prototype's custodian interface cannot do this, because a payload hash is all it receives; the mechanism is unreachable through that interface rather than merely unimplemented.

**What does hold:** the signature is a real Ed25519 signature and `verify_witness` is genuine verification, so a witness does attest that the custodian holding that key saw that hash. `QuorumCustodian` composes several such signatures over the same hash.

**What is therefore absent:** any attestation that an independent run of the pipeline produced that hash. A quorum attests the same thing several times rather than agreeing independently, so the diversity assumption the architecture rests its strongest defensive claims on is not exercised. The consequence is visible at the compilation-integrity invalidation trigger (§3.7): the prototype detects a compiled form altered after witnessing, which is what `verify_compiled_form` checks, but not a compiler that produced the wrong compiled form in the first place.

**A prerequisite the architecture has not specified.** Witnessing by independent recompilation requires the pipeline to be deterministic, and §3.2's stage 1 fails resolution on credentials that are revoked or expired, which is mutable and clock-dependent. Two custodians recompiling either side of a revocation legitimately disagree. A production conforming implementation needs a determinism contract first: a canonical serialisation, a pinned satisfiability checker, and a revocation as-of time pinned into the declared inputs. Without it, a quorum that gates admission turns non-determinism into a liveness failure rather than a monitoring signal.


## Witness verification is self-referential; there is no authorised-custodian set (October 2026)

**Decision:** Recorded as a gap, not closed. `verify_compiled_form` and `verify_quorum_witness` take only the payload being verified. The payload names its own public keys, its own custodian names and its own threshold, so verification establishes that each contribution's signature matches the key presented alongside it, and nothing more.

**What this means concretely, demonstrated.** A quorum payload whose three contributions are signed by three freshly generated keypairs, belonging to nobody, verifies as a quorum of three. Adding one such contribution to a genuine two-member payload at a threshold of three carries it from refused to verified. No key compromise is involved; the signatures are real, and they are signatures by whoever chose to make them.

**Why the interface cannot currently do better.** The runtime calls `verify_compiled_form(compiled_form)` with one argument, because it has no membership set to supply. Members of a cooperative substrate are represented by the cooperative credential's parent references, which the verification path never consults. Closing this needs an expected-custodian set threaded from the archive or the cooperative substrate into verification, which is the same witness-bearing archive interface the absent-witness entry defers.

**Two claims in the real-cryptography entry above were corrected in the same pass**, because they asserted the opposite: that an extra contribution from a non-member fails verification, and that the substrate's witnesses are as forge-resistant as Ed25519. The first is false. The second conflates the unforgeability of a signature by a given key, which holds, with the unforgeability of an accepted witness, which does not.

**Related and now fixed, so recorded for completeness rather than as an open gap.** Two adjacent defects of the same shape, found in the same review and closed in `federation.py`:

- A threshold below one accepted zero signatures. The threshold arrives inside the payload, so `threshold: 0` or `threshold: -1` with no contributions satisfied `valid >= threshold`. `QuorumCustodian` rejects such a threshold at construction, but verification sees payloads its constructor did not produce. Verification now requires a threshold that is an integer of at least one.
- A payload field that was not a string raised `TypeError` out of `bytes.fromhex`, which escaped the runtime as an ungoverned exception rather than producing a refusal. Malformed input is now a failed verification.

**The general lesson, which is the reason these are grouped.** Each of these, including the absent-witness case, was the same defect: **an invariant enforced where a value is produced, and assumed where it is consumed.** A verification function sees data chosen by whoever produced it, so every constraint the producer's constructor enforces has to be re-established on the verifying side.

## A declared drift criterion can be well-formed and still unenforceable (October 2026)

**Decision:** Recorded as a residual, not closed. Compilation now refuses a criterion of a known type whose
declaration cannot be evaluated: no field, a malformed bound, an unevaluable window, or a `rate_in` with no
value to match. What it cannot refuse is a criterion that is **well-formed and wrong**: a correct `field` key
whose value names an output the unit never emits. Such a criterion passes validation, enters the unit's content
identity, presents as monitoring that field, and can never fire.

**Why this is not closed by the same move.** The producer side has no way to know which fields a unit's
implementation will emit; that is a property of the code, and the architecture is explicit that it does not
verify an implementation against its declarations. The consumption side could close it: `_evaluate_criterion`
returns `None`, meaning satisfied, when no observation in a full window carries the named field, and the
correct pattern already exists two modules away in `evaluate_confidence_gate`, which refuses when a required
input field is absent.

**Why it has deliberately not been changed.** Making drift fire when a declared field never appears would
change what the invalidation trigger means. Drift signals that observed behaviour has left a declared band.
"The declaration names a field that does not exist" is a different fault, and conflating the two would report a
unit as drifted when nothing about its behaviour has drifted. It would also break a unit whose field appears
intermittently by design. The right answer is probably a distinct signal rather than a reuse of this one, and
choosing is an architectural decision rather than a repair.

**The honest statement for a reader of PP 3.7:** the prototype enforces a drift criterion whose declaration it
can evaluate against observations that carry the named field. A criterion naming a field the unit does not emit
is inert, and the substrate does not report that it is inert.

## Backtest exceedance was computed over offered pairs rather than evaluated pairs (October 2026, fixed)

**Recorded because the defect manufactured evidence rather than withholding it**, which makes it the most
consequential of the family found in this review and worth a reader knowing it existed.

`exceedance_rate` dropped a pair from the numerator when either field was absent and kept it in the
denominator. A backtest over ten pairs whose outcome field name was mistyped therefore returned `0.0` rather
than no result, and the London Whale backtest unit, which distinguishes `None` as insufficient data from a rate
above the declared bound, reported `calibration_holds` with a plausible-looking pair count. A wholly violated
value-at-risk calibration claim read as holding, on no evaluable evidence.

The rate is now over the pairs actually evaluated, and no evaluable pair returns `None`. An existing test
asserted the old behaviour and has been corrected: it constructed two pairs, neither evaluable, and asserted
`0.0`.

**The general point, which is the fourth time this review has reached it:** a function that reports a
governance property must distinguish "the property holds" from "I could not establish the property". Returning
a falsy value for the second is how the second becomes the first.
