"""
Substrates and the operators that run them.

A substrate is the post-compilation running instantiation: archives,
ledger, runtime, custodian. One or more units, composed or uncomposed,
nested at any level. Substrates can themselves be composed and nested as
units (a cooperative substrate is the composition of substrates run by
different operators).

An operator is the institutional authority running a substrate. The
operator's identity is its root credential; the substrate is what the
operator runs. In the simple case, one operator runs one substrate; the
relationship is 1:1. Phase 2-deepened keeps this simple case.

The architectural recursion: a unit is a unit; a composed unit is a unit;
an operator-run substrate is a unit; a cooperative substrate composing
operator-run substrates is also a unit, just at composite scale. The
same primitives, compile-at-commit, runtime evaluation, and ledger
discipline operate at every level.
"""

from __future__ import annotations

from dataclasses import dataclass

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.federation import LocalCustodian
from substrate.ledger import Act, FederatedLedger
from substrate.primitives import CredentialUnit
from substrate.runtime import Runtime, _is_delegated


@dataclass
class Substrate:
    """The post-compilation running instantiation.

    Holds the archives (code and credentials), the ledger, the runtime,
    and the custodian that witnesses compiled forms produced here. A
    substrate is one or more units instantiated and ready to invoke;
    individual units inside the substrate have their own content_ids,
    the substrate as a whole is identified by what it composes (in
    practice, by the operator that runs it).

    A substrate can be a single unit, a composing unit with sub-units, or
    a nested composition. The substrate machinery does not care which —
    the same archives hold them all, the same runtime invokes them, the
    same ledger records the acts.
    """
    code: CodeArchive
    credentials: CredentialsArchive
    ledger: FederatedLedger
    custodian: LocalCustodian
    runtime: Runtime


@dataclass
class Operator:
    """The institutional authority running a substrate.

    An operator's identity is its root credential; the operator is
    addressable by the content_id of that credential. The substrate is
    what the operator runs.

    The operator carries no archives, no ledger, no runtime of its own.
    Those belong to the substrate. The operator is the authority; the
    substrate is the running thing. This is the architectural separation
    the user named: substrates can be composed and nested; operators are
    who runs them.

    Convenience properties (.code, .credentials, .ledger, .runtime,
    .custodian) forward to the operator's substrate so existing
    operator.code-style access keeps working.
    """
    name: str
    root_credential: CredentialUnit
    substrate: Substrate

    @property
    def content_id(self) -> str:
        return self.root_credential.content_id()

    @property
    def code(self) -> CodeArchive:
        return self.substrate.code

    @property
    def credentials(self) -> CredentialsArchive:
        return self.substrate.credentials

    @property
    def ledger(self) -> FederatedLedger:
        return self.substrate.ledger

    @property
    def runtime(self) -> Runtime:
        return self.substrate.runtime

    @property
    def custodian(self) -> LocalCustodian:
        return self.substrate.custodian

    # =========================================================================
    # Administrative actions: ledger-recorded operator events
    # =========================================================================
    #
    # Each administrative action goes through the same authorisation
    # discipline as a runtime invocation: the authorising credential must
    # be delegated under this operator's root authority. The act is
    # recorded on this operator's ledger with kind="administrative",
    # whether it executes or is refused (the audit trail captures both
    # successful operator actions and unauthorised attempts).
    #
    # The archive method is only called when authorisation succeeds. If
    # the underlying archive operation itself fails (e.g. trying to
    # revoke a credential that does not exist), the act is recorded as
    # refused with the archive's error as the rationale, and the
    # exception is then re-raised for the caller.

    def _authorise(self, authorising_credential_id: str, action: str, target_descriptor: dict):
        """Return None if authorised; otherwise record a refused admin act
        and return a tuple (refused_act, rationale) so the caller can
        raise the appropriate exception.
        """
        # The authorising credential must exist and be delegated under
        # this operator's root authority.
        try:
            self.substrate.credentials.get(authorising_credential_id)
        except KeyError:
            rationale = f"authorising credential {authorising_credential_id} not found in archive"
            refused = self._record_admin(
                authorising_credential_id, action, target_descriptor,
                verdict="refused", details={"reason": rationale},
            )
            return refused, rationale
        except Exception as exc:  # revoked, superseded, deprecated
            rationale = f"authorising credential is invalid: {exc}"
            refused = self._record_admin(
                authorising_credential_id, action, target_descriptor,
                verdict="refused", details={"reason": rationale},
            )
            return refused, rationale

        if not _is_delegated(
            authorising_credential_id,
            (self.root_credential.content_id(),),
            self.substrate.credentials,
        ):
            rationale = (
                f"authorising credential {authorising_credential_id} is not delegated "
                f"under operator {self.name}'s root authority ({self.content_id})"
            )
            refused = self._record_admin(
                authorising_credential_id, action, target_descriptor,
                verdict="refused", details={"reason": rationale},
            )
            return refused, rationale
        return None

    def _record_admin(
        self,
        authorising_credential_id: str,
        action: str,
        target_descriptor: dict,
        verdict: str,
        details,
    ) -> Act:
        """Record an administrative act on this operator's ledger."""
        inputs = {"action": action, **target_descriptor}
        act = Act(
            kind="administrative",
            previous_act_id=self.substrate.ledger.latest(),
            compiled_form_id="",
            invoking_credential_id=authorising_credential_id,
            inputs=inputs,
            verdict=verdict,
            output_or_rationale=details,
        )
        self.substrate.ledger.append(act)
        return act

    def revoke_credential(self, target_credential_id: str, authorising_credential_id: str) -> Act:
        """Revoke a credential, recording the act on the ledger."""
        target_descriptor = {"target_credential": target_credential_id}
        refusal = self._authorise(authorising_credential_id, "revoke_credential", target_descriptor)
        if refusal is not None:
            _, rationale = refusal
            raise PermissionError(rationale)
        self.substrate.credentials.revoke(target_credential_id)
        return self._record_admin(
            authorising_credential_id, "revoke_credential", target_descriptor,
            verdict="executed",
            details={"revoked": target_credential_id, "operator": self.name},
        )

    def deprecate_credential(self, target_credential_id: str, authorising_credential_id: str) -> Act:
        """Mark a credential deprecated, recording the act on the ledger."""
        target_descriptor = {"target_credential": target_credential_id}
        refusal = self._authorise(authorising_credential_id, "deprecate_credential", target_descriptor)
        if refusal is not None:
            _, rationale = refusal
            raise PermissionError(rationale)
        self.substrate.credentials.deprecate(target_credential_id)
        return self._record_admin(
            authorising_credential_id, "deprecate_credential", target_descriptor,
            verdict="executed",
            details={"deprecated": target_credential_id, "operator": self.name},
        )

    def supersede_credential(
        self,
        old_credential_id: str,
        new_credential_id: str,
        authorising_credential_id: str,
    ) -> Act:
        """Mark a credential superseded by a successor, recording the act."""
        target_descriptor = {"old_credential": old_credential_id, "new_credential": new_credential_id}
        refusal = self._authorise(authorising_credential_id, "supersede_credential", target_descriptor)
        if refusal is not None:
            _, rationale = refusal
            raise PermissionError(rationale)
        self.substrate.credentials.supersede(old_credential_id, new_credential_id)
        return self._record_admin(
            authorising_credential_id, "supersede_credential", target_descriptor,
            verdict="executed",
            details={
                "superseded": old_credential_id,
                "successor": new_credential_id,
                "operator": self.name,
            },
        )

    def deprecate_unit(self, target_unit_id: str, authorising_credential_id: str) -> Act:
        """Mark a unit deprecated, recording the act on the ledger."""
        target_descriptor = {"target_unit": target_unit_id}
        refusal = self._authorise(authorising_credential_id, "deprecate_unit", target_descriptor)
        if refusal is not None:
            _, rationale = refusal
            raise PermissionError(rationale)
        self.substrate.code.deprecate(target_unit_id)
        return self._record_admin(
            authorising_credential_id, "deprecate_unit", target_descriptor,
            verdict="executed",
            details={"deprecated": target_unit_id, "operator": self.name},
        )

    def reset_drift(self, target_unit_id: str, authorising_credential_id: str) -> Act:
        """Reset drift-monitor state for a unit, recording the act.

        Drift state is per-runtime; resetting it requires operator
        authorisation. Useful when the unit's implementation has been
        recalibrated or replaced, or when drift detection was a false
        alarm.
        """
        target_descriptor = {"target_unit": target_unit_id}
        refusal = self._authorise(authorising_credential_id, "reset_drift", target_descriptor)
        if refusal is not None:
            _, rationale = refusal
            raise PermissionError(rationale)
        self.substrate.runtime.drift_monitor.reset(target_unit_id)
        return self._record_admin(
            authorising_credential_id, "reset_drift", target_descriptor,
            verdict="executed",
            details={"reset": target_unit_id, "operator": self.name},
        )


def create_substrate(custodian_name: str = "local") -> Substrate:
    """Build a fresh substrate: empty archives, empty ledger, default custodian."""
    code = CodeArchive()
    credentials = CredentialsArchive()
    ledger = FederatedLedger()
    custodian = LocalCustodian(name=custodian_name)
    runtime = Runtime(code, credentials, ledger)
    return Substrate(
        code=code, credentials=credentials, ledger=ledger,
        custodian=custodian, runtime=runtime,
    )


def create_operator(name: str, root_credential: CredentialUnit) -> Operator:
    """Build an operator with a fresh substrate.

    The root credential is put into the substrate's credentials archive so
    the operator can reference its own identity. Use this for the common
    case; build the Substrate and Operator directly if you need finer
    control (e.g. shared archives, replaying an existing ledger).
    """
    substrate = create_substrate(custodian_name=name)
    substrate.credentials.put(root_credential)
    return Operator(name=name, root_credential=root_credential, substrate=substrate)
