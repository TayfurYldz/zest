from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.worker_runtime.python.browser_envelope import OUTSIDE_ENVELOPE, UNSUPPORTED_SCHEME
from zest.worker_runtime.python.browser_route_policy import (
    FRAME_CHILD,
    FRAME_MAIN,
    ROUTE_AUTHORITY_TRANSITION,
    ROUTE_BLOCK_PASSIVE_BOUNDARY,
    ROUTE_BLOCK_UNSUPPORTED,
    DeniedBrowserRequest,
    blocked_boundary_record,
    classify_denied_browser_request,
    resolve_callable_flag,
    resolve_resource_type,
)


def _denied(**overrides) -> DeniedBrowserRequest:
    values = dict(
        url="http://127.0.0.1:8/x",
        resource_type="image",
        is_navigation_request=False,
        frame_kind=FRAME_MAIN,
        browser_action="navigate",
        deny_reason=OUTSIDE_ENVELOPE,
        representable=True,
        source_page="http://127.0.0.1:9/",
    )
    values.update(overrides)
    return DeniedBrowserRequest(**values)


class BrowserRoutePolicyTests(unittest.TestCase):
    def test_passive_image_is_boundary_not_reauth(self) -> None:
        classified = classify_denied_browser_request(_denied(resource_type="image"))
        self.assertEqual(classified.decision, ROUTE_BLOCK_PASSIVE_BOUNDARY)
        self.assertFalse(classified.reauth_required)
        self.assertTrue(classified.main_observation_preserved)
        record = blocked_boundary_record(_denied(resource_type="image"), classified)
        self.assertFalse(record["egress_occurred"])
        self.assertFalse(record["envelope_allowed"])
        self.assertEqual(record["frame_kind"], "main_page_subresource")
        self.assertEqual(record["route_decision"], "BLOCK_PASSIVE_BOUNDARY")

    def test_stylesheet_font_and_script_are_passive(self) -> None:
        for resource in ("stylesheet", "font", "script"):
            classified = classify_denied_browser_request(_denied(resource_type=resource))
            self.assertEqual(classified.decision, ROUTE_BLOCK_PASSIVE_BOUNDARY)
            self.assertFalse(classified.reauth_required)

    def test_main_frame_navigation_requires_reauth(self) -> None:
        classified = classify_denied_browser_request(
            _denied(
                resource_type="document",
                is_navigation_request=True,
                frame_kind=FRAME_MAIN,
                url="http://127.0.0.1:8/arrive",
            )
        )
        self.assertEqual(classified.decision, ROUTE_AUTHORITY_TRANSITION)
        self.assertTrue(classified.reauth_required)
        self.assertEqual(classified.channel, "REDIRECT")
        self.assertFalse(classified.main_observation_preserved)

    def test_passive_iframe_document_is_boundary_during_observe(self) -> None:
        classified = classify_denied_browser_request(
            _denied(
                resource_type="document",
                is_navigation_request=True,
                frame_kind=FRAME_CHILD,
                browser_action="navigate",
            )
        )
        self.assertEqual(classified.decision, ROUTE_BLOCK_PASSIVE_BOUNDARY)
        self.assertFalse(classified.reauth_required)
        self.assertTrue(classified.main_observation_preserved)

    def test_interact_iframe_navigation_requires_reauth(self) -> None:
        classified = classify_denied_browser_request(
            _denied(
                resource_type="document",
                is_navigation_request=True,
                frame_kind=FRAME_CHILD,
                browser_action="interact",
            )
        )
        self.assertEqual(classified.decision, ROUTE_AUTHORITY_TRANSITION)
        self.assertTrue(classified.reauth_required)
        self.assertEqual(classified.channel, "IFRAME")

    def test_oos_xhr_is_blocked_boundary_not_authority_grant(self) -> None:
        classified = classify_denied_browser_request(
            _denied(resource_type="xhr", is_navigation_request=False)
        )
        self.assertEqual(classified.decision, ROUTE_BLOCK_PASSIVE_BOUNDARY)
        self.assertFalse(classified.reauth_required)
        self.assertEqual(classified.boundary_kind, "ACTIVE_DATAFLOW")

    def test_unsupported_scheme_stays_blocked(self) -> None:
        classified = classify_denied_browser_request(
            _denied(
                url="file:///etc/passwd",
                resource_type="document",
                deny_reason=UNSUPPORTED_SCHEME,
                representable=False,
            )
        )
        self.assertEqual(classified.decision, ROUTE_BLOCK_UNSUPPORTED)
        self.assertFalse(classified.reauth_required)

    def test_playwright_navigation_method_is_not_treated_as_true(self) -> None:
        self.assertFalse(resolve_callable_flag(lambda: False))
        self.assertTrue(resolve_callable_flag(lambda: True))
        self.assertFalse(resolve_callable_flag(False))
        self.assertTrue(resolve_callable_flag(True))
        self.assertEqual(resolve_resource_type(lambda: "image"), "image")
        classified = classify_denied_browser_request(
            _denied(
                resource_type="image",
                is_navigation_request=resolve_callable_flag(lambda: False),
            )
        )
        self.assertEqual(classified.decision, ROUTE_BLOCK_PASSIVE_BOUNDARY)
        self.assertFalse(classified.reauth_required)

    def test_main_frame_document_without_navigation_flag_still_reauths(self) -> None:
        classified = classify_denied_browser_request(
            _denied(
                resource_type="document",
                is_navigation_request=False,
                frame_kind=FRAME_MAIN,
            )
        )
        self.assertEqual(classified.decision, ROUTE_AUTHORITY_TRANSITION)
        self.assertTrue(classified.reauth_required)


if __name__ == "__main__":
    unittest.main()
