"""Deterministic source export. Does not include .git, .venv, secrets, or runtime artifacts."""

from __future__ import annotations

import hashlib
import gzip
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zest.safe_data import SecretMaterialError

DEFAULT_SOURCE_ROOT_MARKERS = ("pyproject.toml", "src/zest")
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".claude",
        ".venv",
        "venv",
        "agent-memory-local",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cursor",
        ".idea",
        ".vscode",
        "node_modules",
        "htmlcov",
        "var",
        "dist",
        "build",
        ".tox",
    }
)
UNTRACKED_OPERATIONAL_PREFIXES = (
    "deploy/systemd/",
    "deploy/logrotate/",
)
EXCLUDED_FILE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".coverage",
    ".sqlite",
    ".db",
    ".dump",
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".coverage",
        "coverage.xml",
        ".env",
        ".env.local",
        "credentials.json",
        "auth.json",
        "id_rsa",
        "id_ed25519",
    }
)
REFUSED_NAME_MARKERS = (
    "auth.json",
    "credentials.json",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "session.json",
)
UNTRACKED_SOURCE_SUFFIXES = (
    ".py",
    ".sh",
    ".md",
    ".toml",
    ".json",
    ".yml",
    ".yaml",
    ".txt",
)


def find_source_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "zest").is_dir():
            return candidate
    raise FileNotFoundError("unable to locate Zest source root")


def _is_excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
        return True
    name = path.name.lower()
    if name in EXCLUDED_FILE_NAMES or name.startswith(".coverage"):
        return True
    if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return True
    return False


def _refused(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in REFUSED_NAME_MARKERS)


def _is_untracked_source_candidate(path: Path, root: Path) -> bool:
    relative = path.relative_to(root).as_posix()
    if any(relative.startswith(prefix) for prefix in UNTRACKED_OPERATIONAL_PREFIXES):
        return True
    return path.suffix.lower() in UNTRACKED_SOURCE_SUFFIXES


def iter_export_paths(
    root: Path,
    *,
    include_untracked_source: bool = False,
    tracked_files: Iterable[str] | None = None,
) -> list[Path]:
    selected: list[Path] = []
    if tracked_files is not None:
        for item in tracked_files:
            path = (root / item).resolve()
            if not path.is_file():
                continue
            if _is_excluded(path, root):
                continue
            if _refused(path):
                raise SecretMaterialError(f"refusing credential/session file: {item}")
            selected.append(path)
        if include_untracked_source:
            tracked_set = {str(Path(item)).replace("\\", "/") for item in tracked_files}
            for path in root.rglob("*"):
                if not path.is_file() or _is_excluded(path, root):
                    continue
                rel = path.relative_to(root).as_posix()
                if rel in tracked_set:
                    continue
                if not _is_untracked_source_candidate(path, root):
                    continue
                if _refused(path):
                    raise SecretMaterialError(f"refusing credential/session file: {rel}")
                selected.append(path)
    else:
        for path in root.rglob("*"):
            if not path.is_file() or _is_excluded(path, root):
                continue
            if _refused(path):
                raise SecretMaterialError(
                    f"refusing credential/session file: {path.relative_to(root).as_posix()}"
                )
            selected.append(path)
    unique = sorted({item.resolve() for item in selected}, key=lambda item: item.relative_to(root).as_posix())
    return unique


def _git_tracked_files(root: Path) -> list[str] | None:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def export_source_archive(
    output: str | Path,
    *,
    root: Path | None = None,
    include_untracked_source: bool = False,
) -> tuple[Path, Path]:
    source_root = root or find_source_root()
    tracked = _git_tracked_files(source_root)
    files = iter_export_paths(
        source_root,
        include_untracked_source=include_untracked_source,
        tracked_files=tracked,
    )
    archive_path = Path(output).resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_lines = []
    mtime = datetime(1980, 1, 1, tzinfo=timezone.utc).timestamp()
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gzip_file:
            with tarfile.open(
                fileobj=gzip_file,
                mode="w",
                format=tarfile.PAX_FORMAT,
                dereference=False,
            ) as archive:
                for path in files:
                    relative = path.relative_to(source_root).as_posix()
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    manifest_lines.append(f"{digest}  {relative}")
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.mtime = int(mtime)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
    manifest_body = "\n".join(manifest_lines) + ("\n" if manifest_lines else "")
    manifest_path = archive_path.with_suffix(archive_path.suffix + ".manifest")
    if archive_path.name.endswith(".tar.gz"):
        manifest_path = Path(str(archive_path) + ".manifest")
    manifest_path.write_text(manifest_body, encoding="utf-8")
    return archive_path, manifest_path
