from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from zest.tools.registry import load_capability_registry


_HTTP_ENVELOPE_CAPABILITIES = frozenset({
    "http.transaction",
    "http.authentication",
    "http.authorization.differential",
    "http.state_transition",
    "browser.page",
})


def _network_envelope_for(request: dict[str, Any]) -> dict[str, Any] | None:
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        return None
    origin = arguments.get("authorized_origin")
    path = arguments.get("path")
    if not isinstance(origin, str) or not origin.strip():
        return None
    if not isinstance(path, str):
        return None
    parsed = urlsplit(origin.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.hostname or ""
    if not host:
        return None
    port = parsed.port
    if port is None:
        port = 80 if parsed.scheme == "http" else 443
    document_path = path if path.startswith("/") else f"/{path}"
    if host != "127.0.0.1":
        return None
    return {
        "normalized_scheme": parsed.scheme,
        "normalized_host": host.lower(),
        "normalized_port": port,
        "document_path": document_path,
        "origin_wide": True,
        "allowed_path_prefixes": [],
        "denied_path_prefixes": [],
        "loopback_only": True,
        "source_scope_rule_ids": ["test"],
    }


def valid_worker_request(**overrides: Any) -> dict[str, Any]:
    capability = str(overrides.get("worker_capability") or "diagnostic.echo")
    action = str(overrides.get("action") or "echo")
    version = "1"
    fingerprint = "unbound-test-fingerprint"
    found = load_capability_registry().lookup(capability, action)
    if found is not None:
        definition, _action = found
        version = definition.version
        fingerprint = definition.definition_fingerprint
    request: dict[str, Any] = {
        "contract_version": "v1",
        "correlation": {
            "correlation_id": "corr-1",
            "research_run_id": "run-1",
            "experiment_id": "exp-1",
            "request_id": "req-1",
        },
        "worker_capability": "diagnostic.echo",
        "action": "echo",
        "target_reference": "target-1",
        "authorization_decision_reference": "authz-1",
        "execution_budget": {
            "budget_id": "budget-1",
            "max_requests": 1,
            "max_tool_calls": 1,
            "max_runtime_ms": 10_000,
            "max_concurrency": 1,
        },
        "side_effect_level": 0,
        "capability_version": version,
        "capability_definition_fingerprint": fingerprint,
        "secret_references": [],
        "arguments": {"message": "ping"},
    }
    request.update(overrides)
    if "capability_version" not in overrides or "capability_definition_fingerprint" not in overrides:
        capability = str(request["worker_capability"])
        action = str(request["action"])
        found = load_capability_registry().lookup(capability, action)
        if found is not None:
            definition, _action = found
            if "capability_version" not in overrides:
                request["capability_version"] = definition.version
            if "capability_definition_fingerprint" not in overrides:
                request["capability_definition_fingerprint"] = definition.definition_fingerprint
    if request.get("worker_capability") in _HTTP_ENVELOPE_CAPABILITIES and "network_envelope" not in request:
        envelope = _network_envelope_for(request)
        if envelope is not None:
            request["network_envelope"] = envelope
    return request
