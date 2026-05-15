"""
The runtime.

At every act:

1. Look up the compiled form for the source unit being invoked. The
   runtime maintains an index from source unit content_id to its current
   compiled form's content_id; an unrecognised source unit raises.
2. Check the invoking credential's status. A revoked or missing credential
   refuses.
3. Check every credential in the compiled form's authority chain. A
   revoked credential anywhere in the chain refuses; this is how
   credential revocation propagates to active compiled forms.
4. Check delegation. The invoking credential's ancestry (walked upward
   via its credential_refs) must intersect the compiled form's authority
   chain — otherwise the invoking credential is not authorised to invoke
   under this unit's authority. An empty authority chain is permitted to
   mean "permissionless" (no delegation check); declaring authority opts
   the unit into delegation enforcement.
5. Evaluate every policy in the compiled form. Policies are themselves
   functional units; the runtime invokes each through this same invoke()
   pipeline (each policy invocation produces its own Act on the ledger).
   If any policy returns Refuse, the parent refuses with that policy's
   rationale. Strictest-binding-wins emerges from this refuse-wins
   semantic: when multiple policies cover the same dimension, the first
   to refuse anything outside its bound is the binding constraint.
6. If every policy permits, execute the source unit's implementation.
   The implementation is itself a content-addressed state unit (the
   Horizon correction): the runtime fetches it from the archive by the
   source unit's implementation_ref, exec()s it once per process (cached
   by content_id), and calls the entrypoint with
   `(inputs, runtime, invoking_credential_id)`. A unit with no
   implementation_ref refuses (specification-only units cannot run).
6. Record the verdict (permit with output, or refuse with rationale) as
   an Act on the federated ledger. Every invocation produces a ledger
   entry, regardless of outcome.

The runtime never walks the reference graph; it evaluates the already-
compiled form. Graph traversal happens once at compile-at-commit.
Runtime composition (a unit's implementation invoking another unit, or
the runtime invoking a policy unit) is a separate concern and goes
through this same invoke() path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from substrate.archives import (
    CodeArchive,
    CredentialDeprecated,
    CredentialRevoked,
    CredentialSuperseded,
    CredentialsArchive,
    UnitDeprecated,
)
from substrate.compile import CompiledForm
from substrate.confidence import (
    evaluate_confidence_gate,
    propagate,
)
from substrate.drift import DriftMonitor
from substrate.federation import verify_compiled_form
from substrate.ledger import Act, FederatedLedger
from substrate.primitives import StateUnit


@dataclass(frozen=True)
class Permit:
    """A successful invocation result."""
    output: Any
    act_id: str


@dataclass(frozen=True)
class Refuse:
    """A refused invocation result. The rationale names what failed."""
    rationale: str
    act_id: str


class Runtime:
    """Coordinates lookup, policy evaluation, execution, and ledger commit.

    The runtime is constructed with the three archives it depends on. Unit
    implementations (the actual Python callables that execute when a
    functional unit is invoked) are registered separately, keyed by the
    functional unit's content_id.
    """

    def __init__(
        self,
        code_archive: CodeArchive,
        credentials_archive: CredentialsArchive,
        ledger: FederatedLedger,
    ):
        self.code = code_archive
        self.credentials = credentials_archive
        self.ledger = ledger
        # Index: source unit content_id -> compiled form content_id.
        # Populated by register_compiled(); read by invoke().
        self._compiled_by_source: dict = {}
        # Cache: implementation state unit content_id -> (callable, language).
        # Populated lazily on first invocation; the cache key is the
        # immutable content_id so cache entries are valid for the lifetime
        # of the process.
        self._impl_cache: dict = {}
        # Optional back-reference to a CooperativeSubstrate, set when the
        # operator running this substrate joins a cooperative substrate.
        # Impls can use `runtime.invoke_in(target_op_cid, ...)` to invoke
        # units running under other member operators. None outside a
        # cooperative substrate; cross-operator calls raise then.
        self.cooperative_substrate = None
        # Drift monitor: tracks per-unit running output observations for
        # behaviour-characterised units that declare drift_criteria in
        # their spec. Drift is runtime state (per process), not archive
        # state, because it depends on observed outputs over time.
        self.drift_monitor = DriftMonitor()
        # Invocation stack for confidence propagation. Each frame is a
        # dict {"sub_confidences": [...]}. Pushed before an impl runs
        # so the impl's runtime.invoke() calls deposit their results'
        # confidence into the parent's frame. Frames are NOT pushed for
        # policy-evaluation invocations; policy results don't propagate
        # as confidence. See `substrate.confidence`.
        self._invocation_stack: list = []

    def register_compiled(self, compiled_form: CompiledForm) -> str:
        """Put a compiled form in the code archive and index it for invoke().

        Returns the compiled form's content_id. After this call, invoke()
        with the source unit's content_id will find this compiled form.
        """
        cid = self.code.put(compiled_form)
        self._compiled_by_source[compiled_form.source_unit] = cid
        return cid

    def invoke_in(
        self,
        target_operator_id: str,
        source_unit_id: str,
        inputs: dict,
        invoking_credential_id: str,
    ):
        """Invoke a unit on another operator's substrate via the cooperative substrate.

        This is the cross-operator counterpart to invoke(). The target
        operator's substrate performs all its own checks (credential
        status, authority chain, delegation, policy evaluation) and
        records the act on its own ledger. The caller's ledger does not
        gain an entry directly; if the calling impl wants its own act to
        reference the cross-operator act, it records the returned act_id
        in its own output.

        If the cross-operator result contains a confidence value (at the
        conventional `confidence` field), it is observed into the
        caller's current invocation frame so that the caller's declared
        propagation function combines it with local sub-confidences.
        """
        if self.cooperative_substrate is None:
            raise RuntimeError(
                "runtime's substrate is not part of a cooperative substrate; "
                "cross-operator invocation requires the operator to be added to one first"
            )
        target = self.cooperative_substrate.operator(target_operator_id)
        result = target.runtime.invoke(source_unit_id, inputs, invoking_credential_id)
        # Cross-operator confidence observation. The caller's frame (if any)
        # collects the sub-confidence so its propagation function applies.
        if isinstance(result, Permit) and self._invocation_stack:
            if isinstance(result.output, dict):
                conf = result.output.get("confidence")
                if isinstance(conf, (int, float)) and not isinstance(conf, bool):
                    self._invocation_stack[-1]["sub_confidences"].append(conf)
        return result

    def compiled_for(self, source_unit_id: str) -> CompiledForm:
        """Look up the registered compiled form for a source unit.

        Raises KeyError if the source unit has no registered compiled form.
        Uses the audit accessor so retrieving a compiled form for
        inspection does not raise on deprecation.
        """
        compiled_id = self._compiled_by_source.get(source_unit_id)
        if compiled_id is None:
            raise KeyError(source_unit_id)
        return self.code.get_for_audit(compiled_id)

    def invoke(
        self,
        source_unit_id: str,
        inputs: dict,
        invoking_credential_id: str,
        _observe_for_propagation: bool = True,
    ):
        """Invoke a unit by its source content_id.

        Returns Permit or Refuse; always commits a ledger entry (except for
        structural errors such as a source unit that was never compiled,
        which raise rather than refusing).

        Internal parameter `_observe_for_propagation` is set False by the
        runtime when invoking a policy unit during policy evaluation. It
        suppresses frame management and parent-frame observation for that
        invocation. Impls never set this; the default for impl-context
        invocations is True. See `substrate.confidence` for the propagation
        mechanism.
        """
        # 1. Look up the compiled form for this source unit.
        compiled_id = self._compiled_by_source.get(source_unit_id)
        if compiled_id is None:
            raise KeyError(
                f"source unit {source_unit_id} has no registered compiled form; "
                f"call register_compiled() with its CompiledForm first"
            )
        # Fetch the compiled form structurally; deprecation of the source
        # unit (not of the compiled form artefact) is checked at execution
        # time in step 5.
        artefact = self.code.get_for_audit(compiled_id)
        if not isinstance(artefact, CompiledForm):
            raise TypeError(
                f"{compiled_id} is not a CompiledForm "
                f"(got {type(artefact).__name__})"
            )
        compiled_form: CompiledForm = artefact

        # 1b. Compilation integrity check. The compiled form's witness must
        #     verify against its own content; otherwise we are about to
        #     execute under a witness that has been tampered with or
        #     produced by a key no longer in use. Refuse.
        if compiled_form.witness_payload and not verify_compiled_form(compiled_form):
            return self._refuse(
                compiled_form,
                inputs,
                invoking_credential_id,
                f"compilation integrity check failed; witness does not verify",
            )

        # 2. Check the invoking credential. All four invalidation reasons
        #    (revoked, superseded, deprecated, missing) refuse with a
        #    rationale naming the reason, so the audit trail records why.
        try:
            self.credentials.get(invoking_credential_id)
        except CredentialRevoked:
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"invoking credential {invoking_credential_id} is revoked",
            )
        except CredentialSuperseded as exc:
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"invoking credential {invoking_credential_id} superseded by "
                f"{exc.successor_cid}",
            )
        except CredentialDeprecated:
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"invoking credential {invoking_credential_id} is deprecated",
            )
        except KeyError:
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"invoking credential {invoking_credential_id} not found",
            )

        # 3. Check every credential in the authority chain.
        for cid in compiled_form.authority_chain:
            try:
                self.credentials.get(cid)
            except CredentialRevoked:
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"credential {cid} in authority chain is revoked",
                )
            except CredentialSuperseded as exc:
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"credential {cid} in authority chain superseded by "
                    f"{exc.successor_cid}",
                )
            except CredentialDeprecated:
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"credential {cid} in authority chain is deprecated",
                )
            except KeyError:
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"credential {cid} in authority chain not found",
                )

        # 4. Check delegation: the invoking credential's ancestry must
        #    intersect the compiled form's authority chain. An empty
        #    authority chain is permissionless (no delegation enforced).
        if compiled_form.authority_chain:
            if not _is_delegated(
                invoking_credential_id,
                compiled_form.authority_chain,
                self.credentials,
            ):
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"invoking credential {invoking_credential_id} is not delegated under "
                    f"this unit's authority (chain has {len(compiled_form.authority_chain)} credentials; "
                    f"none reached from invoker)",
                )

        # 5. Evaluate every policy. Each policy is a functional unit; we
        #    invoke it through this same runtime path. Each policy
        #    invocation produces its own Act on the ledger. If any policy
        #    returns Refuse, the parent refuses with the policy's
        #    rationale and the policy's act_id, so the audit trail can
        #    follow back to the refusing policy.
        for policy_cid in compiled_form.policies:
            if policy_cid == compiled_form.source_unit:
                # Defensive: a unit that has itself as a policy would
                # recurse without termination. Refuse rather than loop.
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"policy graph contains source unit {policy_cid} as its own policy",
                )
            policy_result = self.invoke(
                policy_cid, inputs, invoking_credential_id,
                _observe_for_propagation=False,
            )
            if isinstance(policy_result, Refuse):
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id,
                    f"policy {policy_cid} refused: {policy_result.rationale} "
                    f"(policy act: {policy_result.act_id})",
                )

        # 5. Execute the unit. The implementation is referenced by content_id
        #    from the source unit; we fetch it from the code archive and
        #    execute the artefact. A missing or empty implementation_ref
        #    refuses (the unit was admitted as specification-only and is
        #    not executable). A deprecated source unit refuses (versioning
        #    and deprecation invalidation trigger). A drifted source unit
        #    refuses (drift detection invalidation trigger, for
        #    behaviour-characterised units that declared drift criteria).
        try:
            source_unit = self.code.get(compiled_form.source_unit)
        except UnitDeprecated:
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"source unit {compiled_form.source_unit} is deprecated",
            )
        if self.drift_monitor.is_drifted(compiled_form.source_unit):
            criterion, evidence = self.drift_monitor.drift_reason(compiled_form.source_unit)
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"source unit {compiled_form.source_unit} drifted; "
                f"criterion {criterion!r} violated; evidence: {evidence}",
            )
        implementation_ref = getattr(source_unit, "implementation_ref", "")
        if not implementation_ref:
            return self._refuse(
                compiled_form,
                inputs,
                invoking_credential_id,
                f"unit {compiled_form.source_unit} has no implementation_ref; "
                f"specification-only units cannot be invoked",
            )
        try:
            impl_callable = self._resolve_implementation(implementation_ref)
        except _ImplementationError as exc:
            return self._refuse(
                compiled_form,
                inputs,
                invoking_credential_id,
                f"implementation {implementation_ref} could not be loaded: {exc}",
            )

        spec = source_unit.spec if isinstance(source_unit.spec, dict) else {}

        # 5b. Confidence gate. If the source unit declares a confidence_gate
        #     in its spec, evaluate against inputs before running the impl.
        #     A gate refusal is structurally distinct from a policy refusal
        #     even though both refuse at admission time: the gate threshold
        #     is part of the unit's content_id, so substituting a more
        #     lenient gate produces a new content_id and is visible in
        #     audit.
        gate = spec.get("confidence_gate")
        if gate is not None:
            gate_rationale = evaluate_confidence_gate(gate, inputs)
            if gate_rationale is not None:
                return self._refuse(
                    compiled_form, inputs, invoking_credential_id, gate_rationale,
                )

        # 5c. Push a confidence-propagation frame for this impl's
        #     sub-invocations. Skipped for policy-context invocations
        #     (policies don't propagate as confidence).
        if _observe_for_propagation:
            self._invocation_stack.append({"sub_confidences": []})
        try:
            try:
                output = impl_callable(inputs, self, invoking_credential_id)
            except Exception as exc:
                return self._refuse(
                    compiled_form,
                    inputs,
                    invoking_credential_id,
                    f"implementation raised: {type(exc).__name__}: {exc}",
                )

            # 5d. Apply scalar confidence aggregation if the unit declared
            #     it and the impl did not explicitly set the confidence
            #     field. Sub-invocation confidences observed during the
            #     impl run are combined via the declared aggregation
            #     function (min / product / mean / harmonic_mean) and
            #     written into the parent's output. This is scalar
            #     reliability aggregation; distributional uncertainty
            #     propagation is out of scope (see docs/architectural_boundary.md).
            confidence_spec = spec.get("confidence")
            if (
                _observe_for_propagation
                and isinstance(confidence_spec, dict)
                and confidence_spec.get("produces")
                and confidence_spec.get("propagation")
                and isinstance(output, dict)
            ):
                field = confidence_spec.get("output_field", "confidence")
                if output.get(field) is None:
                    propagation = confidence_spec["propagation"]
                    # Canonical-string propagation only at runtime; custom is
                    # validated at compile-at-commit but not yet executed here.
                    if isinstance(propagation, str):
                        sub_confs = self._invocation_stack[-1]["sub_confidences"]
                        propagated = propagate(sub_confs, propagation)
                        if propagated is not None:
                            output[field] = propagated

            # 5e. Update the drift monitor with this output. If the unit
            #     declares drift_criteria, the monitor checks whether the
            #     running statistics now violate any criterion. If they do,
            #     the unit is marked drifted from this point on; this
            #     invocation still permits (the output that triggered drift
            #     is itself part of the observed history), but subsequent
            #     invocations will refuse.
            drift_criteria = spec.get("drift_criteria", [])
            if drift_criteria:
                self.drift_monitor.observe(compiled_form.source_unit, output, drift_criteria)

            # 6. Commit a permit.
            result = self._permit(compiled_form, inputs, invoking_credential_id, output)
        finally:
            if _observe_for_propagation:
                self._invocation_stack.pop()

        # 6b. Observe this invocation's confidence into the now-top frame
        #     (which is our parent's frame, if any). This is how a parent
        #     unit's propagation function sees the confidences of the
        #     sub-units its impl invoked.
        if (
            _observe_for_propagation
            and isinstance(result, Permit)
            and self._invocation_stack
            and isinstance(result.output, dict)
        ):
            confidence_spec = spec.get("confidence")
            if isinstance(confidence_spec, dict) and confidence_spec.get("produces"):
                field = confidence_spec.get("output_field", "confidence")
                conf = result.output.get(field)
                if isinstance(conf, (int, float)) and not isinstance(conf, bool):
                    self._invocation_stack[-1]["sub_confidences"].append(conf)
        return result

    def _permit(self, compiled_form, inputs, credential_id, output):
        act = Act(
            previous_act_id=self.ledger.latest(),
            compiled_form_id=compiled_form.content_id(),
            invoking_credential_id=credential_id,
            inputs=inputs,
            verdict="permit",
            output_or_rationale=output,
        )
        self.ledger.append(act)
        return Permit(output=output, act_id=act.content_id())

    def _resolve_implementation(self, implementation_ref: str):
        """Fetch and prepare an implementation, caching the prepared callable.

        The cache key is the implementation's content_id, which is immutable
        by construction; entries never need invalidation within a process.
        Across processes, the cache is rebuilt from the archive on first
        use of each implementation.
        """
        cached = self._impl_cache.get(implementation_ref)
        if cached is not None:
            return cached
        try:
            artefact = self.code.get(implementation_ref)
        except KeyError:
            raise _ImplementationError(
                f"implementation state unit {implementation_ref} not in code archive"
            )
        if not isinstance(artefact, StateUnit):
            raise _ImplementationError(
                f"{implementation_ref} is not a StateUnit (got {type(artefact).__name__})"
            )
        content = artefact.content
        if not isinstance(content, dict) or "language" not in content:
            raise _ImplementationError(
                f"implementation state unit content is malformed; expected dict with 'language' key"
            )
        language = content["language"]
        if language == "python":
            callable_ = _prepare_python(content)
        elif language == "wasm":
            callable_ = _prepare_wasm(content)
        else:
            raise _ImplementationError(f"unsupported implementation language: {language!r}")
        self._impl_cache[implementation_ref] = callable_
        return callable_

    def _refuse(self, compiled_form, inputs, credential_id, rationale):
        act = Act(
            previous_act_id=self.ledger.latest(),
            compiled_form_id=compiled_form.content_id(),
            invoking_credential_id=credential_id,
            inputs=inputs,
            verdict="refuse",
            output_or_rationale=rationale,
        )
        self.ledger.append(act)
        return Refuse(rationale=rationale, act_id=act.content_id())


# =============================================================================
# Delegation
# =============================================================================

def _is_delegated(invoking_credential_id: str, authority_chain: tuple, credentials_archive) -> bool:
    """Return True if the invoking credential or any of its ancestors is in
    the authority chain.

    Walks the invoking credential's credential_refs upward through the
    credentials archive; if any credential encountered (including the
    invoking credential itself) matches a credential in authority_chain,
    the invocation is delegated under that unit's authority.

    Uses the compile-time accessor on the credentials archive so revocation
    status does not influence the delegation check. Revocation is a
    runtime concern (handled separately, before this check); delegation
    is a structural property of the credential graph.
    """
    chain_set = set(authority_chain)
    queue = [invoking_credential_id]
    visited: set = set()
    while queue:
        cid = queue.pop()
        if cid in visited:
            continue
        visited.add(cid)
        if cid in chain_set:
            return True
        try:
            cred = credentials_archive.get_for_compile(cid)
        except KeyError:
            # An ancestor cannot be walked further; continue with the rest
            # of the queue.
            continue
        for parent in cred.credential_refs:
            if parent not in visited:
                queue.append(parent)
    return False


# =============================================================================
# Implementation execution
# =============================================================================

class _ImplementationError(Exception):
    """Internal: raised when an implementation cannot be loaded; the runtime
    converts it to a refusal naming the implementation_ref."""


def _prepare_python(content: dict):
    """Compile a Python implementation state unit's source into a callable.

    The source is exec'd in a fresh module-level namespace; the entrypoint
    name is then looked up in that namespace and returned. The namespace
    is not sandboxed: a malicious implementation can do anything Python
    can. Use a wasm_implementation if sandboxing is required; the Python
    executor remains supported for development and for trusted internal
    units.
    """
    source = content.get("source")
    entrypoint = content.get("entrypoint", "implementation")
    if not isinstance(source, str):
        raise _ImplementationError("python implementation source must be a string")
    namespace: dict = {}
    try:
        exec(compile(source, f"<implementation:{entrypoint}>", "exec"), namespace)
    except Exception as exc:
        raise _ImplementationError(f"python implementation failed to load: {exc}")
    if entrypoint not in namespace:
        raise _ImplementationError(
            f"python implementation entrypoint {entrypoint!r} not defined in source"
        )
    impl = namespace[entrypoint]
    if not callable(impl):
        raise _ImplementationError(f"entrypoint {entrypoint!r} is not callable")
    return impl


def _prepare_wasm(content: dict):
    """Compile a WASM implementation state unit's bytes into a callable.

    The WASM module is compiled once at preparation time (cached by the
    caller's content_id index). Each invocation creates a fresh
    wasmtime.Store and Instance, so implementations cannot share state
    across invocations or with their callers; this is the sandboxing
    guarantee. The ABI is documented in src/substrate/implementations.py.
    """
    try:
        import wasmtime
    except ImportError:
        raise _ImplementationError(
            "WASM implementation requires the wasmtime package; install with "
            "`pip install wasmtime` (or `pip install -r requirements.txt`)"
        )

    module_hex = content.get("module_hex")
    entrypoint = content.get("entrypoint", "implementation")
    if not isinstance(module_hex, str):
        raise _ImplementationError("wasm implementation 'module_hex' must be a hex string")
    try:
        wasm_bytes = bytes.fromhex(module_hex)
    except ValueError as exc:
        raise _ImplementationError(f"wasm implementation module_hex is not valid hex: {exc}")

    engine = wasmtime.Engine()
    try:
        module = wasmtime.Module(engine, wasm_bytes)
    except Exception as exc:
        raise _ImplementationError(f"wasm implementation failed to compile: {exc}")

    import json as _json

    def callable_(inputs, runtime, invoking_credential_id):
        # Fresh store and instance per invocation: no shared state between
        # invocations of the same implementation, no shared state with the
        # parent runtime. This is the sandboxing default.
        store = wasmtime.Store(engine)
        try:
            instance = wasmtime.Instance(store, module, [])
        except Exception as exc:
            raise Exception(f"wasm instantiation failed: {exc}")

        exports = instance.exports(store)
        try:
            memory = exports["memory"]
            alloc = exports["alloc"]
            impl = exports[entrypoint]
        except KeyError as exc:
            raise Exception(
                f"wasm module is missing required export {exc.args[0]!r} "
                f"(expected: memory, alloc, {entrypoint})"
            )

        inputs_bytes = _json.dumps(inputs).encode("utf-8")
        try:
            inputs_ptr = alloc(store, len(inputs_bytes))
        except Exception as exc:
            raise Exception(f"wasm alloc failed: {exc}")
        memory.write(store, inputs_bytes, inputs_ptr)

        try:
            packed = impl(store, inputs_ptr, len(inputs_bytes))
        except Exception as exc:
            # A wasm trap surfaces as an exception; propagate it as a
            # runtime refusal with the trap message in the rationale.
            raise Exception(f"wasm trap: {exc}")

        out_ptr = (packed >> 32) & 0xFFFFFFFF
        out_len = packed & 0xFFFFFFFF
        out_bytes = memory.read(store, out_ptr, out_ptr + out_len)
        try:
            output = _json.loads(bytes(out_bytes).decode("utf-8"))
        except Exception as exc:
            raise Exception(f"wasm implementation produced non-JSON output: {exc}")

        # Refusal marker: the implementation signals refuse via the
        # _substrate_refuse key in its output dict. The runtime's outer
        # exception handler turns this into a Refuse with the rationale.
        if isinstance(output, dict) and "_substrate_refuse" in output:
            raise Exception(str(output["_substrate_refuse"]))

        return output

    return callable_


