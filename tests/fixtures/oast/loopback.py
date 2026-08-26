"""Loopback OAST implementation for tests. No live network."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zest.application.identity import new_opaque_id
from zest.research.oast.types import (
    OastCallback,
    OastCallbackNotFoundError,
    OastPort,
    OastToken,
    OastTokenExpiredError,
)


class LoopbackOastPort(OastPort):
    """In-memory OAST port for deterministic testing. Never contacts live infrastructure."""

    def __init__(self) -> None:
        self._tokens: dict[str, OastToken] = {}
        self._callbacks: dict[str, list[OastCallback]] = {}

    def mint_token(
        self,
        *,
        token_id: str,
        research_run_id: str,
        hypothesis_id: str,
        target_reference: str,
        expires_at: datetime,
    ) -> OastToken:
        token = OastToken(
            token_id=token_id,
            research_run_id=research_run_id,
            hypothesis_id=hypothesis_id,
            target_reference=target_reference,
            expires_at=expires_at,
        )
        self._tokens[token.token_id] = token
        self._callbacks[token.token_id] = []
        return token

    def poll(self, token_id: str, *, now: datetime) -> tuple[OastCallback, ...]:
        token = self._tokens.get(token_id)
        if token is None:
            raise OastCallbackNotFoundError(f"token not found: {token_id}")
        if now > token.expires_at:
            raise OastTokenExpiredError(f"token expired: {token_id}")
        return tuple(self._callbacks.get(token_id, []))

    def register_callback(
        self,
        token_id: str,
        *,
        source_address: str = "127.0.0.1",
        request_summary: dict[str, Any] | None = None,
        received_at: datetime | None = None,
    ) -> OastCallback:
        if token_id not in self._tokens:
            raise OastCallbackNotFoundError(f"token not found: {token_id}")
        callback = OastCallback(
            callback_id=new_opaque_id(),
            token_id=token_id,
            source_address=source_address,
            request_summary=request_summary or {"path": "/", "method": "GET"},
            received_at=received_at or datetime.now(timezone.utc),
        )
        self._callbacks[token_id].append(callback)
        return callback
