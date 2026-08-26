"""Non-Worker Integration/ModelPort allowlist. Not Worker execution policy.

Strix and Codex are not Worker capabilities. This list is identity + bounded
side-effect only so Core can authorize existing Integration calls without
placing them in the Worker capability-definition tree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntegrationAllowlistEntry:
    capability_id: str
    action: str
    minimum_side_effect_level: int
    maximum_side_effect_level: int


STRIX_DIAGNOSTIC_PING_ACTION = "ping"
INTEGRATION_CAPABILITY_VERSION = "integration"
INTEGRATION_DEFINITION_FINGERPRINT_PREFIX = "integration."

_STRIX_PING = IntegrationAllowlistEntry(
    capability_id="strix.diagnostic.ping",
    action=STRIX_DIAGNOSTIC_PING_ACTION,
    minimum_side_effect_level=0,
    maximum_side_effect_level=0,
)

INTEGRATION_CAPABILITY_ALLOWLIST: dict[tuple[str, str], IntegrationAllowlistEntry] = {
    (_STRIX_PING.capability_id, _STRIX_PING.action): _STRIX_PING,
}


def integration_capability_ids() -> frozenset[str]:
    return frozenset(capability_id for capability_id, _action in INTEGRATION_CAPABILITY_ALLOWLIST)


def lookup_integration_capability(
    capability_id: str, action: str
) -> IntegrationAllowlistEntry | None:
    return INTEGRATION_CAPABILITY_ALLOWLIST.get((capability_id, action))


def integration_definition_fingerprint(capability_id: str) -> str:
    return f"{INTEGRATION_DEFINITION_FINGERPRINT_PREFIX}{capability_id}"
