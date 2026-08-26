"""Local artifact store hardening. Bytes stay off the SoR. Not Evidence.

Evidence-linked artifacts must not be silently deleted.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ArtifactStoreError(ValueError):
    """Artifact persistence failure. Not a research conclusion."""


DEFAULT_MAX_ARTIFACT_BYTES = 1_048_576
EVIDENCE_LINK_SUFFIX = ".evidence-linked"


@dataclass(frozen=True)
class ArtifactRef:
    relative_path: str
    sha256: str
    size_bytes: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "contains_bytes": False,
        }


class LocalArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        if max_bytes < 0:
            raise ArtifactStoreError("max_bytes must be non-negative; 0 is no allowance")
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._root.mkdir(parents=True, exist_ok=True)

    def persist(self, relative_path: str, content: bytes) -> ArtifactRef:
        target = self._bounded_path(relative_path)
        if self._max_bytes == 0:
            raise ArtifactStoreError("artifact store allowance is 0")
        if not isinstance(content, (bytes, bytearray)):
            raise ArtifactStoreError("content must be bytes")
        if len(content) > self._max_bytes:
            raise ArtifactStoreError("artifact exceeds size limit")
        digest = hashlib.sha256(content).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".artifact-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
        return ArtifactRef(relative_path=relative_path, sha256=digest, size_bytes=len(content))

    def verify(self, relative_path: str, expected_sha256: str) -> bytes:
        target = self._bounded_path(relative_path)
        if not target.is_file():
            raise ArtifactStoreError("artifact not found")
        content = target.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha256:
            raise ArtifactStoreError("artifact hash mismatch")
        return content

    def mark_evidence_linked(self, relative_path: str) -> None:
        target = self._bounded_path(relative_path)
        marker = target.with_name(target.name + EVIDENCE_LINK_SUFFIX)
        marker.write_text("evidence-linked", encoding="utf-8")

    def delete(self, relative_path: str) -> None:
        target = self._bounded_path(relative_path)
        marker = target.with_name(target.name + EVIDENCE_LINK_SUFFIX)
        if marker.exists():
            raise ArtifactStoreError("refusing to delete evidence-linked artifact")
        if target.exists():
            target.unlink()

    def _bounded_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ArtifactStoreError("relative_path is required")
        cleaned = relative_path.replace("\\", "/").lstrip("/")
        if ".." in Path(cleaned).parts or cleaned.startswith(".."):
            raise ArtifactStoreError("path traversal is not allowed")
        candidate = (self._root / cleaned).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactStoreError("path traversal is not allowed") from exc
        return candidate
