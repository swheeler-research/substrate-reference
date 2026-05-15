"""
Implementation source strings for the constitutional anchoring demonstration.
"""


# A trivial unit. The demonstration is not about what the unit does; it
# is about the unit's authority chain visibly terminating at natural
# persons.
PROCESS_APPLICATION = """
def implementation(inputs, runtime, invoking_credential_id):
    return {
        "application_id": inputs["application_id"],
        "decision": "processed",
        "decided_by": invoking_credential_id,
    }
"""
