"""Idempotent ControlEvent recovery from durable WorkerResult. Not Observation."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping
from urllib.parse import urlsplit

from zest.application.identity import new_opaque_id
from zest.data.records import ControlEventRecord, WorkerResultRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.discovery.types import ANONYMOUS_IDENTITY_ID, ControlEventKind


def ingest_control_event_from_worker_result(
    uow: UnitOfWork,
    result: WorkerResultRecord,
    *,
    created_at: datetime,
    identity_id: str = ANONYMOUS_IDENTITY_ID,
    target_reference: str = "target-1",
    session_context_id: str | None = None,
) -> ControlEventRecord | None:
    existing = uow.control_events.get_by_worker_result(result.worker_result_id)
    if existing is not None:
        return existing
    if result.status != "REAUTHORIZATION_REQUIRED":
        return None
    diagnostics = result.diagnostics if isinstance(result.diagnostics, Mapping) else {}
    channel = str(diagnostics.get("channel") or "REDIRECT")
    location = str(diagnostics.get("location") or diagnostics.get("raw_location") or "")
    kind = _kind_for(channel, location, result)
    parsed = urlsplit(location) if location else None
    record = ControlEventRecord(
        control_event_id=new_opaque_id(),
        research_run_id=result.research_run_id,
        event_kind=kind.value,
        worker_result_id=result.worker_result_id,
        identity_id=identity_id,
        target_reference=target_reference,
        created_at=created_at,
        session_context_id=session_context_id,
        channel=channel,
        location_origin=_origin(parsed) if parsed else None,
        location_path=(parsed.path or "/") if parsed else None,
        request_id=result.request_id,
    )
    uow.control_events.insert(record)
    return record


def _kind_for(channel: str, location: str, result: WorkerResultRecord) -> ControlEventKind:
    if channel == "POPUP":
        return ControlEventKind.POPUP_BOUNDARY
    if channel == "IFRAME":
        return ControlEventKind.IFRAME_BOUNDARY

    parsed = urlsplit(location) if location else None

    if channel in {"REDIRECT", "SPA"}:
        diagnostics = (
            result.diagnostics
            if isinstance(result.diagnostics, Mapping)
            else {}
        )
        response_url = str(diagnostics.get("response_url") or "")
        response_parsed = urlsplit(response_url) if response_url else None

        location_origin = _origin(parsed) if parsed else None
        response_origin = _origin(response_parsed) if response_parsed else None

        if (
            location_origin is not None
            and response_origin is not None
            and location_origin != response_origin
        ):
            return ControlEventKind.NEW_ORIGIN_BOUNDARY

        return ControlEventKind.REDIRECT_BOUNDARY

    if parsed and parsed.hostname:
        return ControlEventKind.NEW_ORIGIN_BOUNDARY

    return ControlEventKind.REAUTHORIZATION_REQUIRED


def _origin(parsed) -> str | None:
    if not parsed.scheme or not parsed.hostname:
        return None
    port = parsed.port
    if port is None:
        return f"{parsed.scheme}://{parsed.hostname}"
    return f"{parsed.scheme}://{parsed.hostname}:{port}"
