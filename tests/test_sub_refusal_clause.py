"""The sub-refusal clause: a parent that declares sub_refusal: "refuse" cannot
route round a sub-unit's refusal.

The principal paper's composition section says a parent's implementation may
handle, propagate or convert a sub-invocation's refusal, and that the refusal
stays on the ledger either way. That is attribution, not propagation. The
clause is the propagation: a unit that declares it refuses whenever any
sub-invocation its implementation made refused, whatever the implementation
returned. These tests pin both halves and the compile-time check on the
clause's value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from substrate.archives import CodeArchive, CredentialsArchive
from substrate.clock import FixedClock
from substrate.compile import CompilationRefused, compile_unit
from substrate.federation import LocalCustodian
from substrate.implementations import python_implementation
from substrate.ledger import FederatedLedger
from substrate.primitives import (
    ContractPattern,
    CredentialUnit,
    FunctionalUnit,
    TransferDiscipline,
)
from substrate.runtime import Permit, Refuse, Runtime


def _cred(name, parent_cids=(), principal=None, authorities=("invoke:any",)):
    return CredentialUnit(
        name=name,
        transfer=TransferDiscipline.DELEGATED,
        principal=principal if principal is not None else name,
        authorities=authorities,
        credential_refs=tuple(parent_cids),
    )


# The sub-unit refuses when asked to; a policy would do the same, and the
# clause does not care which it was.
_CHILD = """
def implementation(inputs, runtime, invoking_credential_id):
    if inputs.get("forbidden"):
        raise ValueError("forbidden operation")
    return {"ok": True}
"""

# A parent that swallows the child's refusal and reports success anyway.
_PARENT = """
def implementation(inputs, runtime, invoking_credential_id):
    r = runtime.invoke(inputs["child"], {"forbidden": inputs.get("forbidden", False)}, invoking_credential_id)
    return {"child_refused": r.__class__.__name__ == "Refuse", "fallback": True}
"""


def _wire(clause):
    code = CodeArchive()
    creds = CredentialsArchive()
    led = FederatedLedger()
    root_cid = creds.put(_cred("root"))
    op_cid = creds.put(_cred("operator", parent_cids=(root_cid,)))
    child_impl_cid = code.put(python_implementation(_CHILD))
    child = FunctionalUnit(
        name="child",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={},
        implementation_ref=child_impl_cid,
        credential_refs=(root_cid,),
    )
    child_cid = code.put(child)
    spec = {} if clause is None else {"sub_refusal": clause}
    parent = FunctionalUnit(
        name="parent",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec=spec,
        implementation_ref=code.put(python_implementation(_PARENT)),
        credential_refs=(root_cid,),
        functional_refs=(child_cid,),
        # Wilful inclusion: the child's implementation is reachable through
        # the child, so the parent lists it at its own top level.
        state_refs=(child_impl_cid,),
    )
    parent_cid = code.put(parent)
    runtime = Runtime(code, creds, led, clock=FixedClock())
    custodian = LocalCustodian("test")
    runtime.register_compiled(compile_unit(child, code, creds, custodian=custodian))
    runtime.register_compiled(compile_unit(parent, code, creds, custodian=custodian))
    return runtime, parent_cid, child_cid, op_cid


def test_without_the_clause_a_parent_may_convert_a_sub_refusal_into_a_permit():
    runtime, parent_cid, child_cid, op_cid = _wire(None)
    r = runtime.invoke(parent_cid, {"child": child_cid, "forbidden": True}, op_cid)
    assert isinstance(r, Permit)
    assert r.output == {"child_refused": True, "fallback": True}
    # The refusal is on the ledger in the parent's lineage regardless.
    acts = list(runtime.ledger)
    parent_act = acts[-1]
    assert parent_act.verdict == "permit"
    child_act = acts[-2]
    assert child_act.verdict == "refuse"
    assert child_act.content_id() in tuple(parent_act.sub_invocations)


def test_with_the_clause_the_parent_refuses_whatever_its_implementation_returned():
    runtime, parent_cid, child_cid, op_cid = _wire("refuse")
    r = runtime.invoke(parent_cid, {"child": child_cid, "forbidden": True}, op_cid)
    assert isinstance(r, Refuse)
    assert r.rationale.startswith("sub_refusal clause: 1 sub-invocation(s) refused")
    acts = list(runtime.ledger)
    assert acts[-1].verdict == "refuse"
    assert acts[-2].verdict == "refuse"
    assert acts[-2].content_id() in tuple(acts[-1].sub_invocations)


def test_with_the_clause_a_permitting_sub_invocation_leaves_the_parent_alone():
    runtime, parent_cid, child_cid, op_cid = _wire("refuse")
    r = runtime.invoke(parent_cid, {"child": child_cid, "forbidden": False}, op_cid)
    assert isinstance(r, Permit)
    assert r.output["child_refused"] is False


def test_the_clause_is_part_of_the_compiled_form_identity():
    """Declaring the clause changes the unit's content, so its compiled form
    is a different artefact: the binding is in the governance object, not in
    runtime configuration."""
    _, with_cid, _, _ = _wire("refuse")
    _, without_cid, _, _ = _wire("handle")
    assert with_cid != without_cid


def test_a_misspelt_clause_refuses_compilation():
    code = CodeArchive()
    creds = CredentialsArchive()
    root_cid = creds.put(_cred("root"))
    unit = FunctionalUnit(
        name="bad",
        contract_pattern=ContractPattern.SPECIFICATION_BOUNDED,
        spec={"sub_refusal": "refuze"},
        implementation_ref=code.put(python_implementation(_CHILD)),
        credential_refs=(root_cid,),
    )
    code.put(unit)
    with pytest.raises(CompilationRefused):
        compile_unit(unit, code, creds, custodian=LocalCustodian("test"))
