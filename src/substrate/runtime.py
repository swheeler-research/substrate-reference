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
   If any policy returns Refuse, the parent refuses. Strictest-binding-wins
   emerges from this refuse-wins semantic: any policy that contributes a
   stricter binding contributes a refusal under conditions where the
   looser binding would have permitted (PP 3.3).
   Evaluation does not stop at the first refusal. Every in-scope policy
   is evaluated and every refusal is recorded on the refused act, because
   the compiled form's policy tuple is sorted by content identity and
   that order is arbitrary with respect to authority, specificity or
   scale; stopping at its first refusing entry would make the recorded
   attribution a function of a hash. The architecture's roll-up "does not
   privilege any particular authority's policies" (PP 3.3) and its
   forensic reconstructibility claim (PP 4.10) is a claim about the
   ledger alone, so the ledger must hold all of them.
6. If every policy permits, execute the source unit's implementation.
   The implementation is itself a content-addressed state unit (the
   Horizon correction): the runtime fetches it from the archive by the
   source unit's implementation_ref, exec()s it once per process (cached
   by content_id), and calls the entrypoint with
   `(inputs, runtime, invoking_credential_id)`. A unit with no
   implementation_ref refuses (specification-only units cannot run).
6. Record the verdict (permit with output, or refuse with rationale) as
   an Act on the federated ledger. Every invocation produces a ledger
   entry, regardless of outcome. The entry also records the act's direct
   sub-invocations by content identity, so that lineage queries
   reconstruct the call tree from the ledger alone (PP 3.6.1); the
   references are taken from the invocation frame the runtime already
   maintains for calibration propagation, and are recorded on refusals
   as well as permits, since an act that refused after invoking
   sub-units has a lineage worth keeping.

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
from substrate.clock import GovernanceClock
from substrate.drift import DriftMonitor
from substrate.federation import verify_compiled_form
from substrate.ledger import Act, FederatedLedger, PolicyRefusal
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


# =============================================================================
# The invocation context
# =============================================================================

@dataclass(frozen=True)
class ResolvedCredential:
    """A credential resolved through the runtime, with its status established.

    A policy that needs to know something about authority must not take the
    invoker's word for it. This is what the runtime hands back instead: the
    credential as the archive holds it, its authority chain as resolution
    walks it, and its status as the invalidation surface currently reports it.

    `valid` is False if the credential is absent from the archive, revoked,
    superseded or deprecated. A policy that reads any other field without
    checking `valid` has reintroduced the defect this type exists to close.
    """

    credential_id: str
    credential: Any
    authority_chain: tuple
    valid: bool
    status: str

    def bears(self, principal: str) -> bool:
        """True if this credential is valid and names exactly this principal.

        The principal is part of the credential's content and therefore of
        its content identity, so a credential whose principal differs is a
        different credential. That is what makes this test meaningful: it
        cannot be satisfied by relabelling.
        """
        return self.valid and getattr(self.credential, "principal", None) == principal

    def descends_from(self, credential_id: str) -> bool:
        """True if this credential is valid and derives authority from that one."""
        return self.valid and credential_id in self.authority_chain


@dataclass(frozen=True)
class InvocationContext:
    """The four components the architecture says the rolled-up policy
    evaluates against: the principal invoking the act, the inputs being
    supplied, the state being operated on, and the temporal context.

    Before this type existed, one of the four was reachable by a policy and
    it was the one the caller controls. Every substantive policy verdict in
    the demonstrations was therefore a function of a value the invoker had
    supplied, which is sound where the input is the thing being governed and
    is a defect wherever the claim is about identity, authority or
    authorisation, because those are precisely what an invoker must not be
    able to assert about itself.

    `principal` is the resolved invoking credential, not its identity string.
    `state` maps the content identity of each state unit the compiled form
    references to the unit the archive holds.

    `entry_tick` is the governance clock as it read when evaluation of this act
    began, and `wall_time` the operator's reading at the same moment. It is
    deliberately not the tick the act will finally record: an act commits after
    its policies, each policy evaluation is itself an act, and each advances the
    clock, so the committing tick is not knowable at entry. What a policy needs
    is the instant the act it governs began, which is what this is. An earlier
    version of this type predicted the committing tick and was wrong by the
    number of policies in scope.
    """

    principal: ResolvedCredential
    inputs: dict
    state: dict
    entry_tick: int
    wall_time: str
    compiled_form_id: str
    target_unit_id: str


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
        clock=None,
    ):
        self.code = code_archive
        self.credentials = credentials_archive
        self.ledger = ledger
        # The governance clock. Every act records the tick it occurred at and
        # the operator's wall-clock reading, so the ordering of acts is
        # substantiated by the chain and their timing is substantiated by the
        # clock. A demonstration that needs a reproducible ledger passes a
        # FixedClock, because an act's content identity covers its recorded
        # time. See `substrate.clock`.
        self.clock = clock if clock is not None else GovernanceClock()
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
        # Invocation stack for confidence propagation and lineage. Each
        # frame is a dict {"sub_confidences": [...], "sub_acts": [...]}.
        # Pushed before an impl runs so the impl's runtime.invoke() calls
        # deposit their results' confidence and their act identities into
        # the parent's frame. Frames are NOT pushed for policy-evaluation
        # invocations; policy results don't propagate as confidence, and a
        # policy act is a sub-invocation of the runtime's own evaluation
        # rather than of the parent's implementation (a policy refusal
        # reaches the parent's entry through `policy_refusals` instead).
        # See `substrate.confidence` and PP 3.6.1.
        self._invocation_stack: list = []
        # The invocation context of the act currently being evaluated, as a
        # stack so that a sub-invocation's policies see the sub-invocation's
        # context and not its parent's. Read by implementations and policies
        # through `invocation_context()`.
        self._context_stack: list = []

    def register_compiled(self, compiled_form: CompiledForm) -> str:
        """Put a compiled form in the code archive and index it for invoke().

        Returns the compiled form's content_id. After this call, invoke()
        with the source unit's content_id will find this compiled form.
        """
        cid = self.code.put(compiled_form)
        self._compiled_by_source[compiled_form.source_unit] = cid
        return cid

    def resolve_credential(self, credential_id: str) -> ResolvedCredential:
        """Resolve a credential through the archives and establish its status.

        This is the method a policy calls when it needs to know something
        about authority. It is the whole of the answer to the finding that
        no policy in the reference implementation resolved anything through
        the runtime: resolution is possible, nothing required it, and so
        every policy that spoke about authority spoke about a string the
        invoker had supplied.

        A credential that is absent, revoked, superseded or deprecated comes
        back with `valid` False and a `status` naming which. The authority
        chain is resolved by the same upward walk compilation performs, and
        is empty when the credential itself does not resolve.
        """
        status = "valid"
        credential = None
        try:
            credential = self.credentials.get(credential_id)
        except CredentialRevoked:
            status = "revoked"
        except CredentialSuperseded as exc:
            status = f"superseded by {exc.successor_cid}"
        except CredentialDeprecated:
            status = "deprecated"
        except KeyError:
            status = "not found"
        if credential is None:
            return ResolvedCredential(
                credential_id=credential_id, credential=None,
                authority_chain=(), valid=False, status=status,
            )
        return ResolvedCredential(
            credential_id=credential_id, credential=credential,
            authority_chain=self.credential_authority_chain(credential_id),
            valid=True, status=status,
        )

    def credential_authority_chain(self, credential_id: str) -> tuple:
        """The credential's ancestry, walked upward to its roots.

        Includes the credential itself. Resolution here uses the compile-time
        accessor, so revoked and superseded ancestors still appear: the chain
        is a structural fact about the credential's derivation and does not
        change when an ancestor's status does. Whether each credential in the
        chain is presently valid is a separate question, which
        `resolve_credential` answers for the credential it is given and the
        runtime's own stage three answers for the compiled form's chain.
        """
        seen: list = []
        frontier = [credential_id]
        while frontier:
            cid = frontier.pop()
            if cid in seen:
                continue
            seen.append(cid)
            try:
                unit = self.credentials.get_for_compile(cid)
            except KeyError:
                continue
            for parent in getattr(unit, "credential_refs", ()):
                if parent not in seen:
                    frontier.append(parent)
        return tuple(seen)

    def compiled_authority_chain(self, source_unit_id: str) -> Optional[tuple]:
        """The authority chain of a unit's compiled form, or None if uncompiled.

        A policy whose verdict is a claim about what authority a unit recognises
        needs the chain the compilation actually resolved, not an assertion
        about it. The certification policy in the Boeing demonstration is the
        case: it previously read a boolean saying whether a pilot-override
        credential was in the candidate's chain, which the party seeking
        certification supplied.
        """
        compiled_cid = self._compiled_by_source.get(source_unit_id)
        if compiled_cid is not None:
            try:
                return tuple(self.code.get_for_audit(compiled_cid).authority_chain)
            except Exception:
                pass
        # A certifier inspects a unit compiled under the operator that authored
        # it, so this runtime may hold no compiled form for it. Fall back to the
        # unit's own declared credential references, which are archive content
        # and part of the unit's content identity. Weaker than the resolved
        # chain, because it is one level rather than transitive, and still not
        # something the party under inspection can assert at invocation. The
        # architecture's answer to the gap is witnessing by independent
        # recompilation, which the register records as unsubstantiated.
        try:
            unit = self.code.get_for_audit(source_unit_id)
        except Exception:
            return None
        return tuple(getattr(unit, "credential_refs", ()) or ())

    def invocation_context(self) -> Optional[InvocationContext]:
        """The context of the act currently being evaluated, or None.

        Available to a unit's implementation and to every policy evaluated
        for that act. None outside an invocation.
        """
        return self._context_stack[-1] if self._context_stack else None

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

        The cross-operator act's identity is also deposited in the
        caller's frame, so the caller's own entry references it as a
        sub-invocation. The referenced act lives on the target
        operator's ledger rather than this one; that is what PP 3.6.1
        describes, the sub-invocation appearing on both operators'
        ledgers and joinable across them by reference.
        """
        if self.cooperative_substrate is None:
            raise RuntimeError(
                "runtime's substrate is not part of a cooperative substrate; "
                "cross-operator invocation requires the operator to be added to one first"
            )
        target = self.cooperative_substrate.operator(target_operator_id)
        result = target.runtime.invoke(source_unit_id, inputs, invoking_credential_id)
        # Cross-operator lineage. The callee ran on another Runtime with its
        # own invocation stack, so the reference is deposited here rather
        # than by the callee. Recorded for refusals as well as permits. A
        # target that resolves to this same runtime has already deposited
        # the reference itself; depositing again would record the one
        # sub-invocation twice.
        if target.runtime is not self and self._invocation_stack:
            self._invocation_stack[-1]["sub_acts"].append(result.act_id)
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

        This method is the lineage boundary. The frame belonging to the act
        whose implementation is invoking us is captured before the pipeline
        runs and before any frame of our own is pushed; whatever verdict the
        pipeline reaches, its act identity is deposited there. That is what
        lets the parent's own entry record its direct sub-invocations
        (PP 3.6.1). A structural error raises instead of returning a
        verdict, and so deposits nothing: no act was committed to reference.
        """
        caller_frame = (
            self._invocation_stack[-1]
            if _observe_for_propagation and self._invocation_stack
            else None
        )
        result = self._invoke(
            source_unit_id, inputs, invoking_credential_id, _observe_for_propagation,
        )
        if caller_frame is not None:
            caller_frame["sub_acts"].append(result.act_id)
        return result

    def _invoke(
        self,
        source_unit_id: str,
        inputs: dict,
        invoking_credential_id: str,
        _observe_for_propagation: bool = True,
    ):
        """The invocation pipeline itself. Call invoke(), not this.

        Separated from invoke() so that every return path, permit or
        refusal, passes through one place where the act's identity is
        deposited in the calling act's frame.
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
        #
        #     An ABSENT witness fails this check too. PP 3.2 admits a
        #     compiled form to the code archive only when the required
        #     quorum of witness signatures is present, so a form carrying
        #     no witness payload has not been admitted under the
        #     architecture's own terms and must not execute. Treating a
        #     missing payload as a pass would make the whole check
        #     bypassable by omission.
        if not verify_compiled_form(compiled_form):
            return self._refuse(
                compiled_form,
                inputs,
                invoking_credential_id,
                "compilation integrity check failed; witness does not verify",
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

        # 4b. Build the invocation context, so that the policies about to be
        #     evaluated can reach all four of the components the architecture
        #     names rather than only the inputs. The principal is resolved
        #     here, once, rather than left as a string for each policy to
        #     take on trust. The clock is read without advancing it: the act
        #     this context belongs to has not been committed yet, and the
        #     tick it will carry is the next one.
        #
        #     Building the context never changes a verdict. Every resolution
        #     it performs is total: a credential that does not resolve comes
        #     back invalid rather than raising, and state resolution uses the
        #     audit accessor, so a deprecated or superseded unit in the
        #     reference set is still visible to a policy that wants to see it
        #     and is not an error on the way to building the context. The
        #     stages that do refuse have already run.
        #     A policy evaluation does not get a context of its own. The
        #     architecture says the rolled-up policy evaluates against *the
        #     act's* invocation context, singular, and a policy governs the act
        #     that brought it into scope. Giving each policy its own context
        #     made three of the four components describe the policy instead:
        #     `target_unit_id` named the policy, `state` resolved the policy's
        #     own references, and the tick advanced once per policy act. So the
        #     context is pushed only for an invocation that is not a policy
        #     evaluation, and a policy reads the context of the act it governs.
        if _observe_for_propagation:
            self._context_stack.append(InvocationContext(
                principal=self.resolve_credential(invoking_credential_id),
                inputs=inputs,
                state=self._resolve_state_refs(compiled_form),
                entry_tick=self.clock.reading(),
                wall_time=self.clock.wall(),
                compiled_form_id=compiled_form.content_id(),
                target_unit_id=compiled_form.source_unit,
            ))
        try:
            return self._evaluate_and_execute(
                compiled_form, inputs, invoking_credential_id,
                _observe_for_propagation,
            )
        finally:
            if _observe_for_propagation:
                self._context_stack.pop()

    def _resolve_state_refs(self, compiled_form) -> dict:
        """The state units the compiled form references, by content identity.

        This is the architecture's "state being operated on" component of the
        invocation context. A state unit absent from the archive is omitted
        rather than raising: the compiled form's reference resolution already
        established that every reference resolved at compilation, so an
        absence here is an archive fault and not a policy's business.
        """
        resolved: dict = {}
        try:
            unit = self.code.get_for_audit(compiled_form.source_unit)
        except Exception:
            return resolved
        for cid in getattr(unit, "state_refs", ()) or ():
            try:
                resolved[cid] = self.code.get_for_audit(cid)
            except Exception:
                continue
        return resolved

    def _evaluate_and_execute(
        self, compiled_form, inputs, invoking_credential_id,
        _observe_for_propagation,
    ):
        """Policy evaluation and execution, inside the invocation context.

        Split out of `_invoke` so the context is pushed and popped in one
        place and every return path through policy evaluation and execution
        leaves the stack balanced.
        """
        # 5. Evaluate every policy. Each policy is a functional unit; we
        #    invoke it through this same runtime path. Each policy
        #    invocation produces its own Act on the ledger.
        #
        #    Evaluation does not stop at the first refusal. Several
        #    policies may each refuse the same act, and compiled_form.policies
        #    is sorted by content identity, so the first refusal reached is
        #    selected by SHA ordering: arbitrary with respect to the
        #    authority, specificity or scale of the policies it orders. The
        #    loop therefore evaluates all of them and records every refusal
        #    on the refused act, so the attribution an auditor reads off the
        #    ledger is complete rather than incidental (PP 3.3, PP 4.10).
        #    The rationale string keeps its existing shape and names one
        #    refusal; `policy_refusals` on the act carries the whole set.
        #
        #    The cost falls on the refusal path only. A permitted act
        #    already evaluated every policy; a refused act now evaluates
        #    the policies after the first refuser too, so a unit with n
        #    in-scope policies commits up to n policy acts rather than
        #    stopping at the refusing one.
        policy_refusals: list = []
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
                policy_refusals.append(PolicyRefusal(
                    policy_id=policy_cid,
                    rationale=policy_result.rationale,
                    act_id=policy_result.act_id,
                ))
        if policy_refusals:
            first = policy_refusals[0]
            return self._refuse(
                compiled_form, inputs, invoking_credential_id,
                f"policy {first.policy_id} refused: {first.rationale} "
                f"(policy act: {first.act_id})",
                policy_refusals=tuple(policy_refusals),
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

        # 5c. Push a frame for this impl's sub-invocations. It collects
        #     their confidences, for propagation, and their act identities,
        #     for lineage. Skipped for policy-context invocations (policies
        #     don't propagate as confidence, and their acts are not the
        #     parent implementation's sub-invocations).
        if _observe_for_propagation:
            self._invocation_stack.append({"sub_confidences": [], "sub_acts": []})
        try:
            try:
                output = impl_callable(inputs, self, invoking_credential_id)
            except Exception as exc:
                # The impl may have invoked sub-units before raising. Those
                # acts are on the ledger and belong to this act's lineage,
                # so the refusal records them (PP 3.6.1).
                return self._refuse(
                    compiled_form,
                    inputs,
                    invoking_credential_id,
                    f"implementation raised: {type(exc).__name__}: {exc}",
                    sub_invocations=self._current_sub_invocations(_observe_for_propagation),
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

            # 6. Commit a permit, recording the acts this impl caused.
            #    Read before the frame is popped in the finally clause.
            result = self._permit(
                compiled_form, inputs, invoking_credential_id, output,
                sub_invocations=self._current_sub_invocations(_observe_for_propagation),
            )
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

    def _current_sub_invocations(self, _observe_for_propagation: bool) -> tuple:
        """The act identities collected in this act's own frame, in order.

        Returns an empty tuple when no frame of our own was pushed, which
        is the policy-evaluation case: reading the top of the stack then
        would read the calling implementation's frame and attribute its
        sub-invocations to the policy act.
        """
        if not _observe_for_propagation or not self._invocation_stack:
            return ()
        return tuple(self._invocation_stack[-1]["sub_acts"])

    def _permit(self, compiled_form, inputs, credential_id, output, sub_invocations=()):
        act = Act(
            governance_tick=self.clock.tick(),
            recorded_time=self.clock.wall(),
            previous_act_id=self.ledger.latest(),
            compiled_form_id=compiled_form.content_id(),
            invoking_credential_id=credential_id,
            inputs=inputs,
            verdict="permit",
            output_or_rationale=output,
            sub_invocations=tuple(sub_invocations),
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

    def _refuse(
        self,
        compiled_form,
        inputs,
        credential_id,
        rationale,
        sub_invocations=(),
        policy_refusals=(),
    ):
        act = Act(
            governance_tick=self.clock.tick(),
            recorded_time=self.clock.wall(),
            previous_act_id=self.ledger.latest(),
            compiled_form_id=compiled_form.content_id(),
            invoking_credential_id=credential_id,
            inputs=inputs,
            verdict="refuse",
            output_or_rationale=rationale,
            sub_invocations=tuple(sub_invocations),
            policy_refusals=tuple(policy_refusals),
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


