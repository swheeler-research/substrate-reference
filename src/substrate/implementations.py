"""
Implementations as content-addressed state units.

A functional unit's implementation is the actual executable code that runs
when the unit is invoked. It lives in the code archive as a state unit;
the functional unit references it by content_id through its
`implementation_ref` field. This closes the Horizon gap: a unit's
behaviour cannot drift after compilation because the runtime executes the
exact implementation referenced by the compiled form, not whatever Python
function happens to be registered at invocation time.

Phase 1 supports only Python implementations. The state unit's content
schema is:

    {
        "language": "python",
        "source": "<python source text>",
        "entrypoint": "<function name to call>",
    }

The runtime knows how to execute language="python" implementations; it
exec()s the source in a per-implementation namespace (cached by
content_id so the exec happens once per unique implementation across the
process lifetime), looks up the entrypoint by name, and calls it with
(inputs, runtime, invoking_credential_id).

Phase 1.7 adds WASM. The state unit's content schema for WASM is:

    {
        "language": "wasm",
        "module_hex": "<hex-encoded WASM bytes>",
        "entrypoint": "<exported function name>",
    }

WASM gives the substrate sandboxed execution: a malicious or compromised
implementation cannot read files, make network calls, or mutate the
runtime, because the WASM execution environment denies it those
capabilities by construction. The Horizon correction (implementations
content-addressed) is preserved unchanged; WASM closes the orthogonal
"Python exec is not sandboxed" hole.

The WASM ABI for substrate implementations:

- Exports `memory` (the linear memory).
- Exports `alloc(size: i32) -> i32` (allocate `size` bytes; returns offset).
- Exports the entrypoint as `(inputs_ptr: i32, inputs_len: i32) -> i64`.
- The runtime serialises inputs to JSON UTF-8 bytes, calls `alloc()` to
  get an offset, writes the bytes into linear memory, and calls the
  entrypoint.
- The entrypoint returns an i64 packing (output_ptr, output_len) as two
  i32s in the high and low halves.
- The runtime reads `output_len` bytes from `output_ptr`, decodes as JSON.
- To refuse, the implementation returns an output dict containing
  `_substrate_refuse: "<rationale>"`. The runtime converts this to a
  Refuse with the rationale.

Sub-unit invocation from WASM (host-imported `runtime.invoke`) is Phase
1.8; Phase 1.7 demonstrates the substitution-resistant sandboxed
execution path without that complication.
"""

from __future__ import annotations

from substrate.primitives import MutabilityDiscipline, StateUnit


def python_implementation(
    source: str,
    *,
    entrypoint: str = "implementation",
    name: str = "implementation",
) -> StateUnit:
    """Wrap Python source text in an immutable state unit.

    The returned state unit is what a functional unit's
    `implementation_ref` should be set to (after `put`ting the state unit
    in the code archive). The state unit's content_id depends on the
    source text, the entrypoint name, and the language tag; semantically
    identical source with different whitespace produces a different
    content_id, which is the intended behaviour: any change to the
    implementation is a new artefact.
    """
    return StateUnit(
        name=name,
        mutability=MutabilityDiscipline.IMMUTABLE,
        content={
            "language": "python",
            "source": source,
            "entrypoint": entrypoint,
        },
    )


def wasm_implementation(
    wasm_bytes: bytes,
    *,
    entrypoint: str = "implementation",
    name: str = "wasm_implementation",
) -> StateUnit:
    """Wrap WASM bytecode in an immutable state unit.

    The bytes are stored hex-encoded in the state unit's content so the
    canonical-JSON content hash is well-defined (raw bytes are not
    JSON-serialisable). Content-addressing is preserved: the same bytes
    produce the same content_id; any change to the bytes (including
    trivial recompilation differences) produces a new content_id and a
    new artefact. The runtime fetches and executes the exact bytes the
    compiled form references.
    """
    if not isinstance(wasm_bytes, (bytes, bytearray)):
        raise TypeError(f"wasm_implementation expects bytes; got {type(wasm_bytes).__name__}")
    return StateUnit(
        name=name,
        mutability=MutabilityDiscipline.IMMUTABLE,
        content={
            "language": "wasm",
            "module_hex": bytes(wasm_bytes).hex(),
            "entrypoint": entrypoint,
        },
    )
