"""Collect git working-tree snapshots for operator/benchmark provenance.

Composition-root helper. Benchmark does not spawn git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zest.benchmark.source_provenance import SourceProvenance, source_provenance_from_snapshots


def collect_source_provenance(root: Path | None = None) -> SourceProvenance:
    source_root = root or Path.cwd()
    commit = _git_output(source_root, ["rev-parse", "HEAD"]) or "unknown"
    tracked_diff = _git_output(source_root, ["diff", "HEAD", "--"]) or ""
    untracked = _git_output(source_root, ["ls-files", "--others", "--exclude-standard", "-z"]) or ""
    untracked_files = [item for item in untracked.split("\0") if item]
    return source_provenance_from_snapshots(
        commit_hash=commit,
        tracked_diff=tracked_diff,
        untracked_paths=untracked_files,
    )


def _git_output(root: Path, args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8")
