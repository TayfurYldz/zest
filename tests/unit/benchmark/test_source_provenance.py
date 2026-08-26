from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.benchmark.source_provenance import (
    source_provenance_from_snapshots,
)
from zest.integrations.models.discovery import gate_04b_status
from zest.safe_data import SecretMaterialError


class SourceProvenanceTests(unittest.TestCase):
    def test_clean_tree_is_authoritative(self) -> None:
        provenance = source_provenance_from_snapshots(commit_hash="abc123")
        self.assertFalse(provenance.git_dirty)
        self.assertTrue(provenance.authoritative)
        self.assertEqual(provenance.label, "AUTHORITATIVE")

    def test_tracked_modification_is_dirty(self) -> None:
        provenance = source_provenance_from_snapshots(
            commit_hash="abc123",
            tracked_diff="diff --git a/src/x.py b/src/x.py\n",
        )
        self.assertTrue(provenance.git_dirty)
        self.assertIsNotNone(provenance.tracked_diff_sha256)
        self.assertEqual(provenance.label, "DEVELOPMENT_NON_AUTHORITATIVE")

    def test_untracked_source_changes_fingerprint(self) -> None:
        clean = source_provenance_from_snapshots(commit_hash="abc123")
        dirty = source_provenance_from_snapshots(
            commit_hash="abc123",
            untracked_paths=["src/zest/new_module.py"],
        )
        self.assertNotEqual(clean.source_fingerprint, dirty.source_fingerprint)
        self.assertTrue(dirty.git_dirty)

    def test_dirty_benchmark_cannot_be_authoritative_gate_04b(self) -> None:
        result = gate_04b_status(
            available_model_configurations=("openai", "anthropic"),
            executed_live_configurations=("openai", "anthropic"),
            comparable=True,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
            source_authoritative=False,
        )
        self.assertNotEqual(result["status"], "PASS")
        self.assertIn("dirty", result["reason"])

    def test_secret_files_are_refused(self) -> None:
        with self.assertRaises(SecretMaterialError):
            source_provenance_from_snapshots(
                commit_hash="abc",
                untracked_paths=[".env"],
            )


if __name__ == "__main__":
    unittest.main()
