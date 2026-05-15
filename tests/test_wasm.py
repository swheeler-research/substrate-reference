"""Tests for the WASM implementation executor.

WAT (WebAssembly Text format) is compiled to WASM bytes inline in the
test fixtures, so the tests are self-contained: no pre-built .wasm files
checked into the repository.

Environment note: some platforms (notably macOS hardened-runtime Python
binaries on Apple Silicon) prevent wasmtime from allocating JIT pages,
which causes any WASM execution to SIGKILL the process. The tests below
detect this at module load and skip the whole file rather than crashing
the test runner. On a working environment (Python 3.11+ from Homebrew or
pyenv, or a non-hardened Python binary), all tests run.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

wasmtime = pytest.importorskip("wasmtime")


def _wasm_execution_works() -> bool:
    """Probe in a subprocess: does invoking a WASM function actually return
    here, or does it SIGKILL? On hardened-runtime macOS, JIT page
    allocation fails and the process is killed; we cannot catch that.
    Running the probe in a subprocess lets us detect it without crashing
    the test runner.
    """
    probe = (
        "import wasmtime\n"
        "engine = wasmtime.Engine()\n"
        "b = wasmtime.wat2wasm(b'(module (func (export \"f\") (result i32) i32.const 1))')\n"
        "m = wasmtime.Module(engine, b)\n"
        "s = wasmtime.Store(engine)\n"
        "i = wasmtime.Instance(s, m, [])\n"
        "assert i.exports(s)['f'](s) == 1\n"
        "print('ok')\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and b"ok" in result.stdout


pytestmark = pytest.mark.skipif(
    not _wasm_execution_works(),
    reason="wasmtime JIT not functional in this environment (hardened runtime?); "
           "WASM executor architecturally present but not exercisable here",
)

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.compile import compile_unit
from substrate.implementations import python_implementation, wasm_implementation
from substrate.ledger import FederatedLedger
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime


# =============================================================================
# WAT fixtures
# =============================================================================

def _wat_to_wasm_bytes(wat: str) -> bytes:
    """Compile WAT to raw .wasm bytes via wasmtime's text-to-binary path."""
    return wasmtime.wat2wasm(wat)


# A minimal WASM module that ignores its inputs and returns the JSON
# string {"ran":true}. The literal lives in linear memory; alloc returns
# a fixed offset (we only allocate input space; output uses the data
# segment region).
_HARDCODED_OUTPUT_WAT = """
(module
  (memory (export "memory") 1)
  (data (i32.const 0) "{\\"ran\\":true}")
  (func (export "alloc") (param i32) (result i32)
    ;; Place inputs at offset 1024 (well past the data segment).
    i32.const 1024)
  (func (export "implementation") (param i32 i32) (result i64)
    ;; Output is at offset 0, length 12.
    i64.const 12
    i64.const 0
    i64.const 32
    i64.shl
    i64.or))
"""


_REFUSAL_WAT = """
(module
  (memory (export "memory") 1)
  (data (i32.const 0) "{\\"_substrate_refuse\\":\\"wasm-side refusal\\"}")
  (func (export "alloc") (param i32) (result i32)
    i32.const 1024)
  (func (export "implementation") (param i32 i32) (result i64)
    i64.const 41
    i64.const 0
    i64.const 32
    i64.shl
    i64.or))
"""


_TRAPPING_WAT = """
(module
  (memory (export "memory") 1)
  (func (export "alloc") (param i32) (result i32)
    i32.const 1024)
  (func (export "implementation") (param i32 i32) (result i64)
    unreachable))
"""


# =============================================================================
# Fixtures
# =============================================================================

def _constitutional_source():
    return CredentialUnit(
        name="parliament",
        transfer=TransferDiscipline.DELEGATED,
        principal="parliament",
        authorities=("delegate:any",),
    )


def _caseworker(parent_cid):
    return CredentialUnit(
        name="caseworker",
        transfer=TransferDiscipline.DELEGATED,
        principal="caseworker_1",
        authorities=("invoke:test",),
        credential_refs=(parent_cid,),
    )


def _wire_wasm_unit(wat: str):
    """Build a runtime with a single functional unit whose implementation
    is the given WAT compiled to WASM.

    Returns (runtime, source_cid, cw_cid, ledger, unit).
    """
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_constitutional_source())
    cw_cid = creds.put(_caseworker(parliament_cid))

    wasm_bytes = _wat_to_wasm_bytes(wat)
    impl_state = wasm_implementation(wasm_bytes)
    impl_cid = code.put(impl_state)

    unit = FunctionalUnit(
        name="wasm_doer",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"inputs": {}, "outputs": {}},
        implementation_ref=impl_cid,
        credential_refs=(parliament_cid, cw_cid),
    )
    code.put(unit)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(unit, code, creds))
    return runtime, unit.content_id(), cw_cid, led, unit


# =============================================================================
# Sanity: wasmtime is available and WAT compiles
# =============================================================================

def test_wat_compiles_to_wasm_bytes():
    wasm_bytes = _wat_to_wasm_bytes("(module)")
    assert isinstance(wasm_bytes, (bytes, bytearray))
    assert wasm_bytes[:4] == b"\x00asm"


# =============================================================================
# wasm_implementation helper
# =============================================================================

def test_wasm_implementation_helper_produces_state_unit():
    wasm_bytes = _wat_to_wasm_bytes("(module)")
    impl = wasm_implementation(wasm_bytes)
    assert impl.content["language"] == "wasm"
    assert impl.content["module_hex"] == bytes(wasm_bytes).hex()
    assert impl.content["entrypoint"] == "implementation"


def test_wasm_implementation_rejects_non_bytes():
    with pytest.raises(TypeError):
        wasm_implementation("not bytes")


def test_wasm_implementation_content_id_changes_with_bytes():
    """The Horizon correction holds for WASM too: different bytes produce
    different content_ids, which means the unit referencing them is a
    different unit."""
    a = wasm_implementation(_wat_to_wasm_bytes("(module)"))
    b = wasm_implementation(_wat_to_wasm_bytes("(module (func))"))
    assert a.content_id() != b.content_id()


# =============================================================================
# End-to-end: WASM units invoked through the runtime
# =============================================================================

def test_wasm_implementation_executes_and_returns_output():
    runtime, src_cid, cw_cid, led, _ = _wire_wasm_unit(_HARDCODED_OUTPUT_WAT)
    result = runtime.invoke(src_cid, {"any": "input"}, cw_cid)
    assert isinstance(result, Permit)
    assert result.output == {"ran": True}
    assert len(led) == 1
    assert list(led)[0].verdict == "permit"


def test_wasm_refusal_marker_produces_refuse():
    """A WASM implementation that returns _substrate_refuse becomes a Refuse."""
    runtime, src_cid, cw_cid, led, _ = _wire_wasm_unit(_REFUSAL_WAT)
    result = runtime.invoke(src_cid, {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "wasm-side refusal" in result.rationale


def test_wasm_trap_produces_refuse():
    """A WASM implementation that traps (`unreachable`) becomes a Refuse."""
    runtime, src_cid, cw_cid, led, _ = _wire_wasm_unit(_TRAPPING_WAT)
    result = runtime.invoke(src_cid, {}, cw_cid)
    assert isinstance(result, Refuse)
    assert "wasm trap" in result.rationale or "trap" in result.rationale.lower()


# =============================================================================
# Cross-language: a Python parent invokes a WASM sub-unit
# =============================================================================

def test_python_parent_invokes_wasm_sub_unit():
    """Runtime composition across languages: a Python implementation invokes
    a WASM sub-unit through the standard runtime pipeline. Each
    sub-invocation produces its own ledger entry. The sub-unit's
    implementation language is irrelevant to the parent."""
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()

    parliament_cid = creds.put(_constitutional_source())
    cw_cid = creds.put(_caseworker(parliament_cid))

    wasm_bytes = _wat_to_wasm_bytes(_HARDCODED_OUTPUT_WAT)
    wasm_impl_state = wasm_implementation(wasm_bytes)
    wasm_impl_cid = code.put(wasm_impl_state)
    wasm_unit = FunctionalUnit(
        name="wasm_leaf",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=wasm_impl_cid,
        credential_refs=(parliament_cid, cw_cid),
    )
    code.put(wasm_unit)

    parent_source = f"""
def implementation(inputs, runtime, invoking_credential_id):
    sub = runtime.invoke({wasm_unit.content_id()!r}, {{}}, invoking_credential_id)
    return {{'sub_output': sub.output, 'sub_act': sub.act_id}}
"""
    parent_impl_state = python_implementation(parent_source)
    parent_impl_cid = code.put(parent_impl_state)
    parent = FunctionalUnit(
        name="python_parent",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=parent_impl_cid,
        functional_refs=(wasm_unit.content_id(),),
        state_refs=(wasm_impl_cid,),
        credential_refs=(parliament_cid, cw_cid),
    )
    code.put(parent)

    runtime = Runtime(code, creds, led)
    runtime.register_compiled(compile_unit(wasm_unit, code, creds))
    runtime.register_compiled(compile_unit(parent, code, creds))

    result = runtime.invoke(parent.content_id(), {}, cw_cid)
    assert isinstance(result, Permit)
    assert result.output["sub_output"] == {"ran": True}
    assert len(led) == 2  # WASM sub-act, then Python parent act
    assert led.verify() is True
