from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.platform.url_normalize import (
    IDNA_ERROR,
    PARSE_ERROR,
    PATH_AMBIGUOUS,
    USERINFO_ERROR,
    normalize_url,
    resolve_redirect_location,
)


class UrlNormalizeTests(unittest.TestCase):
    def test_userinfo_is_not_stripped_to_authorize(self) -> None:
        candidate = normalize_url("https://user@example.com/app")
        self.assertEqual(candidate.normalization_error, USERINFO_ERROR)
        self.assertIsNone(candidate.normalized_host)

    def test_malformed_idna_denies(self) -> None:
        candidate = normalize_url("https://xn--/example")
        self.assertIsNotNone(candidate.normalization_error)

    def test_ipv6_canonical_equality(self) -> None:
        left = normalize_url("http://[2001:db8:0:0:0:0:0:1]/")
        right = normalize_url("http://[2001:db8::1]/")
        self.assertIsNone(left.normalization_error)
        self.assertEqual(left.normalized_host, right.normalized_host)

    def test_encoded_slash_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/a%2Fsecret")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)
        self.assertIsNone(candidate.scope_match_path)

    def test_encoded_dot_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/%2e%2e/etc")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)

    def test_dot_dot_segment_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/app/../secret")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)

    def test_dot_segment_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/app/./secret")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)

    def test_backslash_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/app\\secret")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)

    def test_double_slash_path_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/app//secret")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)

    def test_malformed_percent_is_ambiguous(self) -> None:
        candidate = normalize_url("https://example.com/app/%zz")
        self.assertEqual(candidate.normalization_error, PATH_AMBIGUOUS)

    def test_query_and_fragment_are_not_path_identity(self) -> None:
        candidate = normalize_url("https://example.com/app?next=https://evil.example#/admin")
        self.assertEqual(candidate.scope_match_path, "/app")
        self.assertIsNone(candidate.normalization_error)

    def test_resolve_redirect_location_against_actual_response_url(self) -> None:
        base = "http://127.0.0.1:8080/a/b"
        absolute = resolve_redirect_location(base, "https://example.com/x")
        self.assertEqual(absolute.normalized_host, "example.com")
        self.assertEqual(absolute.scope_match_path, "/x")
        rooted = resolve_redirect_location(base, "/next")
        self.assertEqual(rooted.scope_match_path, "/next")
        self.assertEqual(rooted.normalized_host, "127.0.0.1")
        parent = resolve_redirect_location(base, "../next")
        self.assertEqual(parent.scope_match_path, "/next")
        protocol = resolve_redirect_location(base, "//other-host/path")
        self.assertEqual(protocol.normalized_host, "other-host")
        self.assertEqual(protocol.normalized_scheme, "http")
        self.assertEqual(protocol.scope_match_path, "/path")
        query = resolve_redirect_location(base, "?page=2")
        self.assertEqual(query.scope_match_path, "/a/b")
        fragment = resolve_redirect_location(base, "#only")
        self.assertEqual(fragment.scope_match_path, "/a/b")

    def test_resolve_redirect_location_rejects_unsupported_schemes(self) -> None:
        base = "http://127.0.0.1:8080/a"
        for location in (
            "javascript:alert(1)",
            "data:text/html,hi",
            "file:///etc/passwd",
            "blob:http://127.0.0.1/id",
        ):
            candidate = resolve_redirect_location(base, location)
            self.assertEqual(candidate.normalization_error, PARSE_ERROR, location)

    def test_resolve_redirect_location_rejects_userinfo(self) -> None:
        candidate = resolve_redirect_location(
            "http://127.0.0.1:8080/a",
            "http://user:pass@example.com/next",
        )
        self.assertEqual(candidate.normalization_error, USERINFO_ERROR)


if __name__ == "__main__":
    unittest.main()
