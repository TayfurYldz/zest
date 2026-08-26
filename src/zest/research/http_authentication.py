"""Typed HTTP form-login plans. Not authorization. Not a WorkerRequest."""

from __future__ import annotations

from zest.research.compiler import ExperimentIntent, compile_experiment_intent
from zest.research.identity_session import HttpFormLoginProfile, Identity
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import HTTP_AUTHENTICATION_CAPABILITY, HTTP_AUTHENTICATION_LOGIN_ACTION

HTTP_AUTHENTICATION_EVALUATION_STRATEGY = "http.authentication.v1"
HTTP_AUTHENTICATION_EXPECTED_OBSERVATION = "authentication response facts were observed"
HTTP_AUTHENTICATION_DISCONFIRMING_OBSERVATION = "no authentication response facts were observed"


def plan_http_login(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    identity: Identity,
    profile: HttpFormLoginProfile,
    username: str,
    authorized_origin: str,
    session_context_id: str,
) -> ExperimentPlan:
    """Compile a bounded HTTP form login. Does not authorize, resolve secrets, or dispatch."""

    if not isinstance(username, str) or not username.strip():
        raise ResearchInputError("username must be a non-empty string")
    if not isinstance(session_context_id, str) or not session_context_id.strip():
        raise ResearchInputError("session_context_id must be a non-empty string")
    if identity.authentication_profile_reference != profile.profile_id:
        raise ResearchInputError("identity is not bound to this authentication profile")
    if profile.method != "POST":
        raise ResearchInputError("HTTP form login supports POST only")
    return compile_experiment_intent(
        ExperimentIntent(
            hypothesis_id=hypothesis_id,
            capability_id=HTTP_AUTHENTICATION_CAPABILITY,
            action=HTTP_AUTHENTICATION_LOGIN_ACTION,
            target_reference=target_reference,
            arguments={
                "authorized_origin": authorized_origin,
                "path": profile.path,
                "username": username,
                "username_field": profile.username_field,
                "password_secret_name": profile.password_secret_name,
                "session_cookie_name": profile.session_cookie_name,
                "session_context_id": session_context_id,
                "identity_id": identity.identity_id,
                "success_status_codes": list(profile.success_status_codes),
            },
            requested_budget_id=budget_id,
            expected_observation=HTTP_AUTHENTICATION_EXPECTED_OBSERVATION,
            disconfirming_observation=HTTP_AUTHENTICATION_DISCONFIRMING_OBSERVATION,
            evaluation_strategy=HTTP_AUTHENTICATION_EVALUATION_STRATEGY,
        )
    )
