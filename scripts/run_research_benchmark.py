"""Run the Research Brain benchmark. Live adapters stay in Integrations."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zest.benchmark.runner import identity_for_cli_session, identity_for_live, run_cli
from zest.interface.git_provenance import collect_source_provenance
from zest.integrations.models.cli_session import (
    CodexCliSessionAdapter,
    load_codex_model_configurations,
    probe_codex_cli,
)
from zest.integrations.models.discovery import ProbeMode, discover_configured_runtimes, gate_04b_status
from zest.integrations.models.factory import resolve_live_adapter
from zest.tools.capabilities import CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY


def resolve_live(adapter_id: str, model_id: str | None):
    configs = load_codex_model_configurations()
    config = next((item for item in configs if item.configuration_id == adapter_id), None)
    if config is not None:
        availability = probe_codex_cli(
            executable_name=config.executable,
            configuration=config,
            live_probe=True,
        )
        payload = json.dumps(availability.to_mapping(), ensure_ascii=True)
        if (
            availability.readiness is None
            or not availability.readiness.benchmark_compatible
            or availability.executable is None
        ):
            print(f"UNAVAILABLE {payload}", file=sys.stderr)
            return None
        port = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable=availability.executable,
            version=availability.version,
            model=config.model,
            configuration_id=config.configuration_id,
        )
        identity = identity_for_cli_session(
            adapter_identity="codex.cli.session",
            runtime_id=config.configuration_id,
            runtime_version=availability.version,
            provider_model_id=config.model,
            configuration_fingerprint=availability.configuration_fingerprint,
        )
        return port, identity

    handle = resolve_live_adapter(adapter_id, model_id=model_id)
    payload = json.dumps(handle.availability.to_mapping(), ensure_ascii=True)
    if not handle.availability.available or handle.port is None:
        print(f"UNAVAILABLE {payload}", file=sys.stderr)
        return None
    identity = identity_for_live(
        adapter_identity=handle.adapter_identity or adapter_id,
        provider_adapter_identity=handle.provider_adapter_identity or adapter_id,
        provider_model_id=handle.provider_model_id or (model_id or ""),
    )
    return handle.port, identity


def discover_runtimes(*, live_probe: bool = False):
    mode = ProbeMode.LIVE if live_probe else ProbeMode.PASSIVE
    return discover_configured_runtimes(probe_mode=mode)


def evaluate_live_status(**kwargs):
    provenance = collect_source_provenance(ROOT)
    kwargs.setdefault("source_authoritative", provenance.authoritative)
    return gate_04b_status(**kwargs)


if __name__ == "__main__":
    provenance = collect_source_provenance(ROOT)
    raise SystemExit(
        run_cli(
            git_commit=provenance.commit_hash,
            resolve_live=resolve_live,
            discover_runtimes=discover_runtimes,
            evaluate_live_status=evaluate_live_status,
        )
    )
