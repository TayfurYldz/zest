"""http.state_transition Transition A normalizer. Observed workflow facts only."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import (
    HTTP_STATE_TRANSITION_ACTION,
    HTTP_STATE_TRANSITION_CAPABILITY,
)

HTTP_STATE_TRANSITION_NORMALIZER_VERSION = "http.state_transition.v1"
HTTP_STATE_TRANSITION_OBSERVATION_KIND = "HTTP_STATE_TRANSITION_AUTHORIZATION"
STATUSES_WITHOUT_TARGET_OBSERVATION = frozenset(
    {
        "EXECUTION_FAILED",
        "BLOCKED",
        "CANCELLED",
        "TIMED_OUT",
        "BUDGET_EXHAUSTED",
        "REAUTHORIZATION_REQUIRED",
    }
)
_REQUEST_KEYS = (
    "pre_state_request",
    "transition_request",
    "post_state_request",
    "control_request",
)


class HttpStateTransitionNormalizer:
    capability = HTTP_STATE_TRANSITION_CAPABILITY
    action = HTTP_STATE_TRANSITION_ACTION
    version = HTTP_STATE_TRANSITION_NORMALIZER_VERSION

    def normalize(
        self,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> tuple[ObservationDraft, ...]:
        status = result.get("status")
        if status in STATUSES_WITHOUT_TARGET_OBSERVATION:
            return ()
        if status != "SUCCEEDED":
            raise MalformedNormalizedPayloadError(
                "unsupported WorkerResult.status for http.state_transition: "
                f"{status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError(
                "SUCCEEDED http.state_transition requires raw_result"
            )
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        for key in _REQUEST_KEYS:
            item = raw_result.get(key)
            if not isinstance(item, Mapping):
                raise MalformedNormalizedPayloadError(
                    f"SUCCEEDED http.state_transition raw_result.{key} must be an object"
                )
            if not isinstance(item.get("status"), int):
                raise MalformedNormalizedPayloadError(
                    f"SUCCEEDED http.state_transition raw_result.{key}.status must be an int"
                )
        pre = raw_result["pre_state_request"]
        transition = raw_result["transition_request"]
        post = raw_result["post_state_request"]
        control = raw_result["control_request"]
        pre_state = _optional_text(pre.get("state"))
        post_state = _optional_text(post.get("state"))
        actor = _optional_text(arguments.get("actor"))
        payload = {
            "authorized_origin": raw_result.get("authorized_origin")
            or arguments.get("authorized_origin"),
            "area": raw_result.get("area") or arguments.get("area"),
            "actor": actor,
            "actor_role": _optional_text(pre.get("actor_role") or post.get("actor_role")),
            "resource_id": raw_result.get("resource_id") or arguments.get("resource_id"),
            "requested_transition": raw_result.get("requested_transition")
            or arguments.get("transition"),
            "pre_state": pre_state,
            "response_status": transition.get("status"),
            "post_state": post_state,
            "state_changed": bool(pre_state and post_state and pre_state != post_state),
            "approved_by": _optional_text(post.get("approved_by")),
            "owner": _optional_text(pre.get("owner") or post.get("owner")),
            "delegated_reviewers": list(pre.get("delegated_reviewers") or []),
            "approve_requires_role": _optional_text(pre.get("approve_requires_role")),
            "approve_from_states": list(pre.get("approve_from_states") or []),
            "control_status": control.get("status"),
            "pre_status": pre.get("status"),
            "post_status": post.get("status"),
            "transition_ok": transition.get("ok") is True,
        }
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=HTTP_STATE_TRANSITION_OBSERVATION_KIND,
                payload=payload,
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
