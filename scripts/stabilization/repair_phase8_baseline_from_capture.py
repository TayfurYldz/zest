#!/usr/bin/env python3
"""Repair the recovered Phase8 baseline from the exact deployed-source capture.

This tool operates only on a caller-supplied local Git checkout. It requires the
known recovered baseline commit, a clean worktree, and the exact captured
artifact SHA-256. It repairs only the 22 differences proven by S1.5, stages the
result, and refuses success unless the staged Git object tree exactly matches the
captured deployed snapshot (excluding release metadata/cache artifacts).

It does not commit, push, deploy, touch /opt/zest/current, or mutate runtime state.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

EXPECTED_HEAD = "ae89df540893a972866ce0e7f91765bf7a0244f6"
EXPECTED_ARCHIVE_SHA256 = "e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9"
EXPECTED_BRANCH = "baseline/deployed-phase8-rc3"

CONTENT_MISMATCH = {
    "src/zest/resources/contracts/v1/capabilities/browser.page.json",
    "src/zest/tools/browser_page_policy.py",
    "src/zest/worker_runtime/python/browser_page.py",
    "src/zest/worker_runtime/python/packaged_registry.py",
    "src/zest/worker_runtime/python/resources/capabilities/browser.page.json",
    "tests/unit/application/test_browser_page.py",
    "tests/unit/tools/test_browser_page_capability.py",
    "workers/python/zest_worker/browser_page.py",
    "workers/python/zest_worker/packaged_registry.py",
    "workers/python/zest_worker/resources/capabilities/browser.page.json",
}

MODE_MISMATCH = {
    "scripts/check_contracts.py",
    "scripts/clean_install_smoke.py",
    "scripts/export_source.py",
    "scripts/run_research_benchmark.py",
    "scripts/start_wsl_test_postgres.py",
    "scripts/start_wsl_test_postgres.sh",
    "scripts/vds_checkpoint16_fixture.py",
    "scripts/verify_zest_release.py",
    "scripts/zest_db.py",
    "scripts/zest_status.py",
}

GIT_ONLY = {
    ".cursor/rules/zest.mdc",
    "var/artifacts/.gitkeep",
}


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ignored(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        not path
        or path == "."
        or path.startswith("release/")
        or path.startswith(".git/")
        or path.startswith(".venv/")
        or "__pycache__" in p.parts
        or ".pytest_cache" in p.parts
        or p.suffix in {".pyc", ".pyo"}
    )


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def snapshot_index(archive: Path) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[tarfile.TarInfo, bytes]]]:
    index: dict[str, tuple[str, str]] = {}
    payloads: dict[str, tuple[tarfile.TarInfo, bytes]] = {}
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            path = member.name
            while path.startswith("./"):
                path = path[2:]
            if ignored(path):
                continue
            if member.isfile():
                fh = tf.extractfile(member)
                if fh is None:
                    raise SystemExit(f"cannot read archive member: {path}")
                data = fh.read()
                mode = "100755" if member.mode & 0o111 else "100644"
                index[path] = (git_blob_sha(data), mode)
                if path in CONTENT_MISMATCH:
                    payloads[path] = (member, data)
            elif member.issym():
                data = member.linkname.encode()
                index[path] = (git_blob_sha(data), "120000")
    return index, payloads


def git_index(repo: Path) -> dict[str, tuple[str, str]]:
    raw = subprocess.check_output(["git", "-C", str(repo), "ls-files", "-s", "-z"])
    result: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, path_raw = record.split(b"\t", 1)
        mode, sha, stage_no = metadata.decode().split()
        path = path_raw.decode()
        if ignored(path):
            continue
        if stage_no != "0":
            raise SystemExit(f"unexpected staged index entry: {path} stage={stage_no}")
        result[path] = (sha, mode)
    return result


def audit(snapshot: dict[str, tuple[str, str]], tree: dict[str, tuple[str, str]]):
    buckets = {
        "MATCH": set(),
        "CONTENT_MISMATCH": set(),
        "MODE_MISMATCH": set(),
        "SNAPSHOT_ONLY": set(),
        "GIT_ONLY": set(),
    }
    for path in sorted(set(snapshot) | set(tree)):
        snap = snapshot.get(path)
        git = tree.get(path)
        if snap is None:
            status = "GIT_ONLY"
        elif git is None:
            status = "SNAPSHOT_ONLY"
        elif snap[0] != git[0]:
            status = "CONTENT_MISMATCH"
        elif snap[1] != git[1]:
            status = "MODE_MISMATCH"
        else:
            status = "MATCH"
        buckets[status].add(path)
    return buckets


def print_audit(label: str, buckets) -> None:
    print(label)
    for name in ("MATCH", "CONTENT_MISMATCH", "MODE_MISMATCH", "SNAPSHOT_ONLY", "GIT_ONLY"):
        print(f"{name}={len(buckets[name])}")
    total = sum(len(buckets[name]) for name in ("CONTENT_MISMATCH", "MODE_MISMATCH", "SNAPSHOT_ONLY", "GIT_ONLY"))
    print(f"TOTAL_NON_MATCH={total}")


def require_pre_audit(buckets) -> None:
    expected = {
        "CONTENT_MISMATCH": CONTENT_MISMATCH,
        "MODE_MISMATCH": MODE_MISMATCH,
        "SNAPSHOT_ONLY": set(),
        "GIT_ONLY": GIT_ONLY,
    }
    for name, paths in expected.items():
        if buckets[name] != paths:
            print(f"ERROR: pre-repair {name} differs from the proven S1.5 audit")
            print("expected:")
            for item in sorted(paths):
                print(f"  {item}")
            print("actual:")
            for item in sorted(buckets[name]):
                print(f"  {item}")
            raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--archive", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    archive = Path(args.archive).expanduser().resolve()

    if not (repo / ".git").exists():
        raise SystemExit(f"not a Git checkout: {repo}")
    if not archive.is_file():
        raise SystemExit(f"capture not found: {archive}")

    head = run(repo, "rev-parse", "HEAD")
    branch = run(repo, "branch", "--show-current")
    dirty = run(repo, "status", "--porcelain")
    archive_sha = sha256_file(archive)

    print(f"repo={repo}")
    print(f"head={head}")
    print(f"branch={branch}")
    print(f"archive={archive}")
    print(f"archive_sha256={archive_sha}")

    if head != EXPECTED_HEAD:
        raise SystemExit(f"unexpected HEAD; expected {EXPECTED_HEAD}")
    if branch != EXPECTED_BRANCH:
        raise SystemExit(f"unexpected branch; expected {EXPECTED_BRANCH}")
    if dirty:
        raise SystemExit("worktree must be clean before exact baseline repair")
    if archive_sha != EXPECTED_ARCHIVE_SHA256:
        raise SystemExit("capture SHA-256 mismatch")

    snapshot, payloads = snapshot_index(archive)
    if set(payloads) != CONTENT_MISMATCH:
        raise SystemExit("capture does not contain the complete proven content-mismatch set")

    before = audit(snapshot, git_index(repo))
    print_audit("PRE_REPAIR_AUDIT", before)
    require_pre_audit(before)
    print("PRE_REPAIR_CONTRACT=PASS")

    for path in sorted(CONTENT_MISMATCH):
        member, data = payloads[path]
        dst = repo / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        current = dst.stat().st_mode
        if member.mode & 0o111:
            dst.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else:
            dst.chmod(current & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))

    for path in sorted(MODE_MISMATCH):
        dst = repo / path
        if not dst.is_file():
            raise SystemExit(f"mode-repair path missing: {path}")
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    for path in sorted(GIT_ONLY):
        dst = repo / path
        if not dst.exists() and not dst.is_symlink():
            raise SystemExit(f"Git-only path unexpectedly missing before repair: {path}")
        dst.unlink()

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)

    after = audit(snapshot, git_index(repo))
    print_audit("POST_REPAIR_STAGED_AUDIT", after)
    non_match = sum(
        len(after[name])
        for name in ("CONTENT_MISMATCH", "MODE_MISMATCH", "SNAPSHOT_ONLY", "GIT_ONLY")
    )
    if non_match:
        raise SystemExit("post-repair staged tree is not an exact deployed snapshot")

    staged = [line for line in run(repo, "diff", "--cached", "--name-only").splitlines() if line]
    if len(staged) != 22:
        raise SystemExit(f"expected 22 staged paths, got {len(staged)}")

    print(f"staged_paths={len(staged)}")
    print("EXACT_SNAPSHOT_STAGED=PASS")
    print("No commit or push was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
