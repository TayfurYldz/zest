from __future__ import annotations

import json
import unittest

import pathsetup  # noqa: F401

from zest.integrations.models.cli_session import (
    classify_codex_account_capacity,
    probe_codex_account_capacity,
)


class _RecordingInput:
    def __init__(self):
        self.writes = []

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass

    def close(self):
        pass


class _QueuedOutput:
    def __init__(self, messages):
        self._lines = [
            json.dumps(item) + "\n"
            for item in messages
        ]

    def readline(self):
        if not self._lines:
            return ""

        return self._lines.pop(0)


class _FakeProcess:
    def __init__(self, messages):
        self.stdin = _RecordingInput()
        self.stdout = _QueuedOutput(
            messages
        )
        self._running = True

    def poll(self):
        return None if self._running else 0

    def terminate(self):
        self._running = False

    def kill(self):
        self._running = False

    def wait(self, timeout=None):
        del timeout
        self._running = False
        return 0


class CodexCapacityProbeTests(
    unittest.TestCase
):
    def test_classifier_available_account_bucket(
        self,
    ):
        snapshot = (
            classify_codex_account_capacity(
                {
                    "rateLimits": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 0,
                            "resetsAt": 2000,
                        },
                        "secondary": {
                            "usedPercent": 78,
                            "resetsAt": 3000,
                        },
                        "rateLimitReachedType": None,
                        "spendControlReached": False,
                    }
                }
            )
        )

        self.assertIs(
            snapshot.available,
            True,
        )
        self.assertIs(
            snapshot.blocked,
            False,
        )
        self.assertIsNone(
            snapshot.reset_at
        )

    def test_classifier_uses_reset_only_from_explicitly_exhausted_window(
        self,
    ):
        snapshot = (
            classify_codex_account_capacity(
                {
                    "rateLimits": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 100,
                            "resetsAt": 2222,
                        },
                        "secondary": {
                            "usedPercent": 90,
                            "resetsAt": 9999,
                        },
                        "rateLimitReachedType": (
                            "rate_limit_reached"
                        ),
                        "spendControlReached": False,
                    }
                }
            )
        )

        self.assertIs(
            snapshot.available,
            False,
        )
        self.assertIs(
            snapshot.blocked,
            True,
        )
        self.assertEqual(
            snapshot.reset_at,
            2222,
        )

    def test_probe_uses_only_account_metadata_wire_methods(
        self,
    ):
        process = _FakeProcess(
            [
                {
                    "id": 1,
                    "result": {
                        "codexHome": (
                            "/var/lib/zest/.codex"
                        ),
                    },
                },
                {
                    "id": 2,
                    "result": {
                        "rateLimits": {
                            "limitId": "codex",
                            "primary": {
                                "usedPercent": 0,
                                "resetsAt": 2000,
                            },
                            "secondary": {
                                "usedPercent": 78,
                                "resetsAt": 3000,
                            },
                            "rateLimitReachedType": None,
                            "spendControlReached": False,
                        }
                    },
                },
            ]
        )

        popen_calls = []

        def popen_factory(
            argv,
            **kwargs,
        ):
            popen_calls.append(
                (argv, kwargs)
            )
            return process

        def select_fn(
            readers,
            writers,
            errors,
            timeout,
        ):
            del writers, errors, timeout
            return readers, [], []

        snapshot = probe_codex_account_capacity(
            executable_name="codex",
            popen_factory=popen_factory,
            select_fn=select_fn,
        )

        self.assertIs(
            snapshot.available,
            True,
        )

        self.assertEqual(
            popen_calls[0][0],
            (
                "codex",
                "app-server",
                "--stdio",
            ),
        )

        self.assertIs(
            popen_calls[0][1]["shell"],
            False,
        )

        methods = [
            json.loads(line)["method"]
            for line in process.stdin.writes
        ]

        self.assertEqual(
            methods,
            [
                "initialize",
                "initialized",
                "account/rateLimits/read",
            ],
        )

        self.assertNotIn(
            "thread/start",
            methods,
        )
        self.assertNotIn(
            "turn/start",
            methods,
        )

    def test_probe_protocol_failure_is_unknown_not_healthy(
        self,
    ):
        process = _FakeProcess(
            [
                {
                    "id": 1,
                    "error": {
                        "message": (
                            "secret provider detail"
                        )
                    },
                },
            ]
        )

        def popen_factory(
            argv,
            **kwargs,
        ):
            del argv, kwargs
            return process

        def select_fn(
            readers,
            writers,
            errors,
            timeout,
        ):
            del writers, errors, timeout
            return readers, [], []

        snapshot = probe_codex_account_capacity(
            executable_name="codex",
            popen_factory=popen_factory,
            select_fn=select_fn,
        )

        self.assertIsNone(
            snapshot.available
        )
        self.assertIsNone(
            snapshot.blocked
        )
        self.assertEqual(
            snapshot.detail,
            "CAPACITY_PROBE_FAILED",
        )
        self.assertNotIn(
            "secret",
            snapshot.detail,
        )


if __name__ == "__main__":
    unittest.main()
