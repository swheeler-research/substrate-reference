"""
Federation: custodians that witness compiled forms.

A custodian holds an Ed25519 keypair and signs payload hashes. The
witness is a real cryptographic signature: anyone holding the
custodian's public key can verify a witness was produced by the custodian
that holds the corresponding private key; forging requires the private
key. This is the cryptographic foundation under the substrate's
attestability claims.

A QuorumCustodian composes multiple custodians and produces a joint
witness: a multi-signature whose verification requires a threshold of
member signatures to be valid against their respective public keys.
Multi-signature (each member signs independently) is simpler to audit
than true threshold signatures (BLS, Shamir) because each member's
contribution is independently visible; the substrate's transparency
commitments favour this. The interface admits future drop-in of true
threshold schemes if their compactness becomes valuable.

Verification helpers (`verify_witness`, `verify_quorum_witness`) let
external parties (auditors, regulators, other operators) check signature
validity without holding any private key. This is the architectural
shift from "witnesses are SHA-256 anyone can compute" to "witnesses are
cryptographically attributable to specific custodians."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from substrate.primitives import content_hash


@dataclass(frozen=True)
class WitnessRequest:
    """A compile-time request for a custodian's witness over a payload.

    The payload is identified by its content hash, not its content. The
    custodian witnesses by hash; the requester is responsible for ensuring
    the hash corresponds to the intended payload.
    """
    payload_hash: str


@dataclass(frozen=True)
class WitnessResponse:
    """A custodian's witness over a payload hash.

    The `signature` field is a compact string for inclusion in content-
    addressing — for a single custodian, the raw Ed25519 signature hex;
    for a quorum, a content_hash over the structured multi-signature.

    The `witness_payload` carries the full verification data: the
    custodian's public key(s), the original payload_hash, the
    signature(s), and the witness type. It is what a downstream verifier
    needs to check the witness's validity without holding any private
    key. compile_unit() carries this onto the CompiledForm so compiled
    forms are self-contained for verification.
    """
    custodian: str
    signature: str
    witness_payload: dict = None

    def __post_init__(self):
        if self.witness_payload is None:
            object.__setattr__(self, "witness_payload", {})


class LocalCustodian:
    """A single in-process custodian with a real Ed25519 keypair.

    On construction, generates a fresh Ed25519 keypair unless an existing
    private key is supplied. The witness for a payload is a real Ed25519
    signature over the payload hash bytes (hex-decoded), encoded as hex
    in the WitnessResponse. The signature is deterministic per Ed25519's
    specification: the same key signing the same payload always produces
    the same signature.

    `public_key_hex` exposes the verifying key as a 64-character hex
    string. This is what a counterparty needs to verify the custodian's
    witnesses. Two custodians with different private keys produce
    different public keys; a custodian's identity is its public key, not
    its name (the name is a human-readable label).

    For test fixtures and reproducible runs, supply `private_key_bytes`
    (32 bytes) at construction; the same bytes always yield the same
    keypair. Production use should generate keys fresh; the bytes-based
    constructor is for testing and for restoring a custodian's identity
    across process restarts.
    """

    def __init__(self, name: str = "local", private_key_bytes: bytes = None):
        self.name = name
        if private_key_bytes is not None:
            if not isinstance(private_key_bytes, (bytes, bytearray)) or len(private_key_bytes) != 32:
                raise ValueError("private_key_bytes must be exactly 32 bytes if supplied")
            self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(private_key_bytes))
        else:
            self._private_key = ed25519.Ed25519PrivateKey.generate()
        # Cache the public key bytes for cheap repeated access.
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        self._public_key_bytes = self._private_key.public_key().public_bytes(
            encoding=Encoding.Raw, format=PublicFormat.Raw,
        )

    @property
    def public_key_hex(self) -> str:
        return self._public_key_bytes.hex()

    def witness(self, request: WitnessRequest) -> WitnessResponse:
        try:
            payload_bytes = bytes.fromhex(request.payload_hash)
        except ValueError:
            payload_bytes = request.payload_hash.encode("utf-8")
        signature_bytes = self._private_key.sign(payload_bytes)
        signature_hex = signature_bytes.hex()
        witness_payload = {
            "type": "single_signature",
            "custodian": self.name,
            "public_key": self.public_key_hex,
            "payload_hash": request.payload_hash,
            "signature": signature_hex,
        }
        return WitnessResponse(
            custodian=self.name,
            signature=signature_hex,
            witness_payload=witness_payload,
        )


def verify_witness(public_key_hex: str, payload_hash: str, signature_hex: str) -> bool:
    """Verify an Ed25519 witness signature given the custodian's public key.

    Returns True if the signature is a valid Ed25519 signature over the
    payload hash bytes by the holder of the private key matching the
    given public key. False otherwise (invalid signature, malformed
    inputs, wrong key).
    """
    try:
        public_key_bytes = bytes.fromhex(public_key_hex)
        signature_bytes = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    try:
        payload_bytes = bytes.fromhex(payload_hash)
    except ValueError:
        payload_bytes = payload_hash.encode("utf-8")
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
    except Exception:
        return False
    try:
        public_key.verify(signature_bytes, payload_bytes)
    except InvalidSignature:
        return False
    return True


class QuorumNotMet(Exception):
    """A quorum custodian could not gather a threshold of member signatures.

    The compiled form is not produced; the substrate refuses to admit it.
    """


class QuorumCustodian:
    """A custodian composed of multiple member custodians; the witness is
    valid only when a threshold of members sign.

    The joint signature is the content_hash of the sorted-by-name member
    signatures plus the quorum metadata. Because every member's signature
    is itself a deterministic function of the payload and the member's
    name (in the LocalCustodian stub), the joint signature is also
    deterministic — given the same payload and the same member custodians,
    the joint signature is reproducible. This is structurally honest about
    the quorum pattern; real cryptographic threshold signatures (BLS,
    Shamir) are future work that would slot into this same interface.

    A QuorumCustodian IS a custodian (it has a `.witness()` method that
    returns a WitnessResponse); it can be passed to compile_unit() the
    same way a LocalCustodian can. This is compositional uniformity at
    the witness layer: a quorum custodian is to a local custodian what a
    cooperative substrate is to a unit-scale substrate.
    """

    def __init__(self, members: tuple, threshold: int, name: str = "quorum"):
        if not members:
            raise ValueError("QuorumCustodian requires at least one member")
        if threshold < 1:
            raise ValueError(f"QuorumCustodian threshold must be >= 1, got {threshold}")
        if threshold > len(members):
            raise ValueError(
                f"QuorumCustodian threshold {threshold} exceeds member count {len(members)}"
            )
        self.members = tuple(members)
        self.threshold = threshold
        self.name = name

    def witness(self, request: WitnessRequest) -> WitnessResponse:
        """Gather member signatures; produce the joint witness if threshold is met.

        The joint witness encodes each member's public key, name, and
        signature alongside the quorum metadata. The encoding is the
        sorted JSON of these contributions, content-hashed for the
        WitnessResponse.signature field. External verifiers reconstruct
        the same encoding and check each member signature against its
        own public key.

        Storing the structured multi-sig under the joint signature's
        content_hash keeps the WitnessResponse.signature a single string
        (matching the LocalCustodian shape) while letting verification
        recover the underlying signatures from the compiled form's
        custodian_payload (carried alongside the witness on compiled
        forms produced under a QuorumCustodian).
        """
        contributions: dict = {}
        failures: list = []
        for member in self.members:
            try:
                response = member.witness(request)
                public_key_hex = getattr(member, "public_key_hex", None)
            except Exception as exc:
                failures.append((getattr(member, "name", "?"), str(exc)))
                continue
            if public_key_hex is None:
                failures.append((response.custodian, "member exposes no public_key_hex"))
                continue
            contributions[response.custodian] = {
                "public_key": public_key_hex,
                "signature": response.signature,
            }

        if len(contributions) < self.threshold:
            raise QuorumNotMet(
                f"quorum {self.name!r}: only {len(contributions)} of {len(self.members)} "
                f"members signed (threshold {self.threshold}); failures: {failures}"
            )

        # The structured multi-sig is carried on WitnessResponse.witness_payload
        # so downstream callers (e.g. compile_unit) can carry it forward
        # onto the compiled form for self-contained verification. The
        # signature string is a deterministic content_hash over the
        # structured payload for compactness.
        ordered_contributions = {k: contributions[k] for k in sorted(contributions)}
        payload = {
            "type": "quorum_witness",
            "quorum_name": self.name,
            "threshold": self.threshold,
            "payload_hash": request.payload_hash,
            "contributions": ordered_contributions,
        }
        return WitnessResponse(
            custodian=self.name,
            signature=content_hash(payload),
            witness_payload=payload,
        )


def verify_compiled_form(compiled_form) -> bool:
    """Verify a compiled form's witness using its own witness_payload.

    Compiled forms are self-contained for verification: this helper
    recomputes the unwitnessed payload hash, then uses the carried
    witness_payload to check the witness signature(s). No external
    information (custodian public keys, quorum membership) is required.

    Returns True only if the signature(s) verify and the witness_payload's
    recorded payload_hash matches the recomputed hash.
    """
    expected_hash = content_hash(compiled_form.unwitnessed_payload())
    payload = compiled_form.witness_payload
    if not isinstance(payload, dict):
        return False
    payload_type = payload.get("type")
    if payload.get("payload_hash") != expected_hash:
        return False
    if payload_type == "single_signature":
        return verify_witness(
            public_key_hex=payload.get("public_key", ""),
            payload_hash=expected_hash,
            signature_hex=payload.get("signature", ""),
        )
    if payload_type == "quorum_witness":
        return verify_quorum_witness(expected_hash, payload)
    return False


def verify_quorum_witness(payload_hash: str, quorum_payload: dict) -> bool:
    """Verify a quorum multi-signature.

    Given the original payload hash and the structured quorum_payload
    (as produced by QuorumCustodian.witness), checks that:

    - The recorded payload_hash matches the supplied payload_hash.
    - At least `threshold` member signatures verify against their own
      public keys over the payload hash.

    Returns True only if both conditions hold.
    """
    if not isinstance(quorum_payload, dict):
        return False
    if quorum_payload.get("type") != "quorum_witness":
        return False
    if quorum_payload.get("payload_hash") != payload_hash:
        return False
    threshold = quorum_payload.get("threshold")
    contributions = quorum_payload.get("contributions")
    if not isinstance(threshold, int) or not isinstance(contributions, dict):
        return False
    valid = 0
    for _custodian_name, contrib in contributions.items():
        if not isinstance(contrib, dict):
            continue
        if verify_witness(
            public_key_hex=contrib.get("public_key", ""),
            payload_hash=payload_hash,
            signature_hex=contrib.get("signature", ""),
        ):
            valid += 1
    return valid >= threshold


class CooperativeSubstrate:
    """A composition of substrates run by different operators.

    A cooperative substrate is itself a substrate at composite scale.
    Its constituents are the substrates of its member operators; its
    authority is established by the cooperative credential (a credential
    with parent references to each member operator's root); its policies
    are the policies attached to that cooperative credential.

    The cooperative substrate is identified by the content_id of its
    cooperative credential. Member operators reference this credential
    in their unit credential_refs to opt into the cooperation; the
    presence of the cooperative credential in a unit's authority chain
    is what enables credentials from any member operator to be delegated
    under the unit.

    Phase 2-deepened scope: an in-process registry plus an optional
    cooperative_credential reference. Cross-operator invocation works
    because member operator runtimes share a Python process and the
    cooperative substrate routes calls directly to the target's runtime.
    Per-operator archives and credential exchange protocols are deferred.

    The API is shaped so that a later phase implementing cross-process /
    cross-network composition can slot in without disturbing callers:
    "get me the substrate at this operator's content_id" regardless of
    where it lives.
    """

    def __init__(self, cooperative_credential=None, quorum_threshold: int = None):
        self._members: dict = {}
        self.cooperative_credential = cooperative_credential
        # The quorum threshold for the cooperative substrate's joint
        # custodian. None means "require all members" (full unanimity);
        # any value 1..len(members) is a strict-quorum threshold.
        self._quorum_threshold = quorum_threshold

    @property
    def content_id(self):
        """The cooperative substrate's identity is its cooperative credential's content_id.

        Returns None if no cooperative credential is attached (the
        registry is functional without one but is then not a substrate
        proper, just a federation of operators).
        """
        if self.cooperative_credential is None:
            return None
        return self.cooperative_credential.content_id()

    def add(self, operator) -> str:
        """Add a member operator. Returns its content_id.

        Sets a back-reference on the operator's substrate's runtime so
        impls can invoke cross-operator units via
        `runtime.invoke_in(target_op_cid, ...)`.
        """
        cid = operator.content_id
        self._members[cid] = operator
        operator.substrate.runtime.cooperative_substrate = self
        return cid

    def operator(self, operator_content_id: str):
        """Look up a member operator by its content_id. Raises KeyError if unknown."""
        if operator_content_id not in self._members:
            raise KeyError(
                f"cooperative substrate does not include operator {operator_content_id}; "
                f"members are: {sorted(self._members)}"
            )
        return self._members[operator_content_id]

    def members(self) -> tuple:
        """Sorted tuple of member operator content_ids."""
        return tuple(sorted(self._members))

    def __contains__(self, operator_content_id: str) -> bool:
        return operator_content_id in self._members

    def __len__(self) -> int:
        return len(self._members)

    @property
    def custodian(self) -> QuorumCustodian:
        """A QuorumCustodian over the member operators' custodians.

        This is the custodian that witnesses compiled forms produced
        under the cooperative substrate (i.e. units that reference the
        cooperative credential in their authority chain). It is the
        substrate-as-unit pattern applied at the witness layer: a
        cooperative substrate's custodian is a composition of its
        members' custodians, just as a cooperative substrate is a
        composition of its members' substrates.

        Raises ValueError if the cooperative substrate has no members
        yet (a quorum over zero members is undefined).
        """
        member_custodians = tuple(op.custodian for op in self._members.values())
        if not member_custodians:
            raise ValueError(
                "cooperative substrate has no members; cannot construct a quorum custodian"
            )
        threshold = self._quorum_threshold if self._quorum_threshold is not None else len(member_custodians)
        name = "cooperative_quorum:" + ",".join(sorted(c.name for c in member_custodians))
        return QuorumCustodian(members=member_custodians, threshold=threshold, name=name)


# Legacy alias retained for the brief Phase 2-deepened transition. Will be
# removed when the cross-operator demonstration is renamed for the
# substrate-as-unit vocabulary refactor. New code should import
# CooperativeSubstrate directly.
Federation = CooperativeSubstrate
