"""OAST core unit tests. No live network; loopback fixture only."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from zest.research.oast.types import (
    OastCallbackNotFoundError,
    OastTokenExpiredError,
)
from tests.fixtures.oast import LoopbackOastPort


class OastTokenLifecycleTests(unittest.TestCase):
    def test_mint_token_carries_provenance(self) -> None:
        port = LoopbackOastPort()
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        token = port.mint_token(
            token_id="tok-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            target_reference="http://example.com",
            expires_at=expires,
        )
        self.assertEqual(token.token_id, "tok-1")
        self.assertEqual(token.research_run_id, "run-1")
        self.assertEqual(token.hypothesis_id, "hyp-1")
        self.assertEqual(token.target_reference, "http://example.com")
        self.assertEqual(token.expires_at, expires)

    def test_poll_empty_when_no_callback(self) -> None:
        port = LoopbackOastPort()
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        port.mint_token(
            token_id="tok-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            target_reference="http://example.com",
            expires_at=expires,
        )
        callbacks = port.poll("tok-1", now=datetime.now(timezone.utc))
        self.assertEqual(callbacks, ())

    def test_register_and_poll_callback(self) -> None:
        port = LoopbackOastPort()
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        port.mint_token(
            token_id="tok-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            target_reference="http://example.com",
            expires_at=expires,
        )
        callback = port.register_callback(
            "tok-1",
            source_address="192.0.2.1",
            request_summary={"path": "/cb", "method": "GET", "headers": {"User-Agent": "test"}},
        )
        self.assertEqual(callback.token_id, "tok-1")
        self.assertEqual(callback.source_address, "192.0.2.1")
        callbacks = port.poll("tok-1", now=datetime.now(timezone.utc))
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(callbacks[0].callback_id, callback.callback_id)

    def test_poll_missing_token_raises(self) -> None:
        port = LoopbackOastPort()
        with self.assertRaises(OastCallbackNotFoundError):
            port.poll("missing", now=datetime.now(timezone.utc))

    def test_expired_token_rejects_poll(self) -> None:
        port = LoopbackOastPort()
        expires = datetime.now(timezone.utc) - timedelta(minutes=1)
        port.mint_token(
            token_id="tok-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            target_reference="http://example.com",
            expires_at=expires,
        )
        with self.assertRaises(OastTokenExpiredError):
            port.poll("tok-1", now=datetime.now(timezone.utc))

    def test_register_callback_for_missing_token_raises(self) -> None:
        port = LoopbackOastPort()
        with self.assertRaises(OastCallbackNotFoundError):
            port.register_callback("missing")


if __name__ == "__main__":
    unittest.main()
