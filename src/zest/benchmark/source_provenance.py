"""Working-tree source provenance for benchmark reports. Not Evidence.

This module does not spawn git. Callers supply snapshots collected outside Benchmark.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from zest.benchmark.errors import BenchmarkError
from zest.safe_data import SecretMaterialError

SOURCE_SUFFIXES = (".py", ".md", ".toml", ".json", ".yml", ".yaml")
EXCLUDED_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "var", "dist", "build", ".cursor"}
)
REFUSED_NAMES = frozenset({".env", "auth.json", "credentials.json"})


@dataclass(frozen=True)
class SourceProvenance:
    commit_hash: str
    git_dirty: bool
    tracked_diff_sha256: str | None
    untracked_source_manifest_sha256: str | None
    source_fingerprint: str
    authoritative: bool
    label: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "commit_hash": self.commit_hash,
            "git_dirty": self.git_dirty,
            "tracked_diff_sha256": self.tracked_diff_sha256,
            "untracked_source_manifest_sha256": self.untracked_source_manifest_sha256,
            "source_fingerprint": self.source_fingerprint,
            "authoritative": self.authoritative,
            "label": self.label,
            "contains_file_contents": False,
            "contains_secrets": False,
        }


def source_provenance_from_snapshots(
    *,
    commit_hash: str,
    tracked_diff: str = "",
    untracked_paths: Iterable[str] = (),
) -> SourceProvenance:
    commit = commit_hash.strip() or "unknown"
    relevant_untracked = []
    for item in untracked_paths:
        normalized = str(item).replace("\\", "/")
        name = Path(normalized).name.lower()
        if name in REFUSED_NAMES or name.endswith(".pem"):
            raise SecretMaterialError("refusing credential/session file in source provenance")
        if Path(normalized).suffix.lower() in SOURCE_SUFFIXES and not any(
            part in EXCLUDED_DIR_NAMES for part in Path(normalized).parts
        ):
            relevant_untracked.append(normalized)
    tracked_diff_sha = (
        hashlib.sha256(tracked_diff.encode("utf-8")).hexdigest() if tracked_diff.strip() else None
    )
    untracked_manifest = "\n".join(sorted(relevant_untracked))
    untracked_sha = (
        hashlib.sha256(untracked_manifest.encode("utf-8")).hexdigest() if relevant_untracked else None
    )
    dirty = bool(tracked_diff.strip()) or bool(relevant_untracked)
    fingerprint_material = f"{commit}\n{tracked_diff_sha or ''}\n{untracked_sha or ''}"
    source_fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()
    return SourceProvenance(
        commit_hash=commit,
        git_dirty=dirty,
        tracked_diff_sha256=tracked_diff_sha,
        untracked_source_manifest_sha256=untracked_sha,
        source_fingerprint=source_fingerprint,
        authoritative=not dirty,
        label="AUTHORITATIVE" if not dirty else "DEVELOPMENT_NON_AUTHORITATIVE",
    )


def require_authoritative_gate_04b(provenance: SourceProvenance) -> None:
    if provenance.git_dirty or not provenance.authoritative:
        raise BenchmarkError(
            "dirty or untracked source cannot be labelled authoritative GATE 04B"
        )
