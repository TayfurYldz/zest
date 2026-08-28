from __future__ import annotations

import unittest
from types import SimpleNamespace

import pathsetup  # noqa: F401

from zest.application.discovery.control_events import _kind_for
from zest.research.discovery.types import ControlEventKind


class ControlEventKindTests(unittest.TestCase):
    def test_same_origin_absolute_redirect_is_redirect_boundary(self) -> None:
        result = SimpleNamespace(
            diagnostics={"response_url": "https://example.test/start"}
        )

        kind = _kind_for(
            "REDIRECT",
            "https://example.test/next",
            result,
        )

        self.assertEqual(kind, ControlEventKind.REDIRECT_BOUNDARY)

    def test_cross_origin_redirect_is_new_origin_boundary(self) -> None:
        result = SimpleNamespace(
            diagnostics={"response_url": "https://example.test/start"}
        )

        kind = _kind_for(
            "REDIRECT",
            "https://other.test/next",
            result,
        )

        self.assertEqual(kind, ControlEventKind.NEW_ORIGIN_BOUNDARY)

    def test_same_url_redirect_is_not_new_origin_boundary(self) -> None:
        result = SimpleNamespace(
            diagnostics={
                "response_url": "https://sandbox.braintreegateway.com/"
            }
        )

        kind = _kind_for(
            "REDIRECT",
            "https://sandbox.braintreegateway.com/",
            result,
        )

        self.assertEqual(kind, ControlEventKind.REDIRECT_BOUNDARY)


if __name__ == "__main__":
    unittest.main()
