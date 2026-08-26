"""Control-plane identity generation. Workers and models do not choose these ids."""

from __future__ import annotations

import uuid


def new_opaque_id() -> str:
    """Globally unique opaque string for this Zest instance.

    Implementation uses UUID4. The canonical contract still sees an opaque
    string. This is not a PostgreSQL UUID column type and not a cross-language
    architecture requirement.
    """
    return str(uuid.uuid4())


def attempt_id_for(request_id: str) -> str:
    return f"ea:{request_id}"


def execution_decision_audit_id(request_id: str) -> str:
    return f"ae:exec:{request_id}"
