#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ARCHIVE_SHA256="e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9"
EXPECTED_BASE_HEAD="5b81b3d1ead3ed837ccade4fd17e104389fb1e26"
EXPECTED_RELEASE_NAME="zest-canonical-phase8-rc3-browser-page-fanout-hotfix"
EXPECTED_ALEMBIC_HEAD="a51_001_phase66_dic (head)"
EXPECTED_TRACKED_MODIFIED=66
EXPECTED_UNTRACKED=65
RECOVERY_BRANCH="baseline/deployed-phase8-rc3"
REPO_URL="https://github.com/TayfurYldz/zest.git"

usage() {
  cat <<'EOF'
Usage:
  import_deployed_phase8_baseline.sh ARCHIVE [WORKDIR] [--push]

Example:
  ./scripts/stabilization/import_deployed_phase8_baseline.sh \
    ~/zest-provenance-20260909/deployed-source.tar.gz \
    ~/zest-phase8-baseline-recovery \
    --push

The importer:
  * verifies the exact deployed artifact SHA-256,
  * rejects unsafe tar paths,
  * verifies every file against deployed-manifest.sha256 embedded alongside the source state,
  * asserts the recorded dirty-tree provenance and zero deletions,
  * starts from exact base 5b81b3d1...,
  * copies only the manifest-declared modified/untracked source files,
  * verifies the staged path set exactly,
  * commits one recovered deployed-source baseline,
  * optionally pushes baseline/deployed-phase8-rc3.

It does not modify the VDS, database, current symlink, services, scope, authority, or product behavior.
EOF
}

if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage
  exit 2
fi

ARCHIVE="$(readlink -f "$1")"
WORKDIR="${2:-$HOME/zest-phase8-baseline-recovery}"
PUSH=0
if [[ "${2:-}" == "--push" ]]; then
  WORKDIR="$HOME/zest-phase8-baseline-recovery"
  PUSH=1
elif [[ "${3:-}" == "--push" ]]; then
  PUSH=1
elif [[ $# -eq 3 ]]; then
  usage
  exit 2
fi

if [[ ! -f "$ARCHIVE" ]]; then
  echo "ERROR: archive not found: $ARCHIVE" >&2
  exit 1
fi

if [[ -e "$WORKDIR" ]]; then
  echo "ERROR: workdir already exists; refusing destructive reuse: $WORKDIR" >&2
  exit 1
fi

command -v git >/dev/null || { echo "ERROR: git is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 is required" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "ERROR: sha256sum is required" >&2; exit 1; }

actual_archive_sha="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
if [[ "$actual_archive_sha" != "$EXPECTED_ARCHIVE_SHA256" ]]; then
  echo "ERROR: deployed artifact hash mismatch" >&2
  echo "expected=$EXPECTED_ARCHIVE_SHA256" >&2
  echo "actual=$actual_archive_sha" >&2
  exit 1
fi

echo "artifact_sha256=$actual_archive_sha"

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
SNAPSHOT="$TMP/snapshot"
mkdir -p "$SNAPSHOT"

python3 - "$ARCHIVE" "$SNAPSHOT" <<'PY'
import os
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
out = Path(sys.argv[2])

with tarfile.open(archive, "r:gz") as tf:
    members = tf.getmembers()
    bad = []
    for member in members:
        name = member.name
        parts = Path(name).parts
        if os.path.isabs(name) or ".." in parts:
            bad.append(name)
        if member.issym() or member.islnk():
            target = member.linkname
            if os.path.isabs(target) or ".." in Path(target).parts:
                bad.append(f"{name} -> {target}")
    if bad:
        raise SystemExit("unsafe archive members: " + repr(bad[:10]))
    tf.extractall(out, filter="data")
print(f"archive_members={len(members)}")
PY

MANIFEST="$SNAPSHOT/deployed-manifest.sha256"
# The deployed source tar intentionally contains the release tree, not the sibling capture manifest.
# Use release/release-manifest.json for path admission, then validate the extracted tree by hashing it
# against the sibling manifest if it is available next to the archive.
CAPTURE_MANIFEST="$(dirname "$ARCHIVE")/deployed-manifest.sha256"
if [[ ! -f "$CAPTURE_MANIFEST" ]]; then
  echo "ERROR: expected sibling deployed-manifest.sha256 next to archive" >&2
  exit 1
fi

python3 - "$SNAPSHOT" "$CAPTURE_MANIFEST" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = Path(sys.argv[2])
missing = []
mismatch = []
count = 0
manifest_paths = set()
for raw in manifest.read_text(encoding="utf-8").splitlines():
    if not raw.strip():
        continue
    expected, raw_path = raw.split(None, 1)
    path = raw_path.strip().lstrip("*")
    if path.startswith("./"):
        path = path[2:]
    p = root / path
    count += 1
    manifest_paths.add(path)
    if not p.is_file():
        missing.append(path)
        continue
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != expected:
        mismatch.append((path, expected, actual))

actual_files = {
    str(p.relative_to(root))
    for p in root.rglob("*")
    if p.is_file()
}
extra = sorted(actual_files - manifest_paths)
not_extracted = sorted(manifest_paths - actual_files)
if missing or mismatch or extra or not_extracted:
    raise SystemExit(
        f"capture manifest verification failed: entries={count} "
        f"missing={len(missing)} mismatch={len(mismatch)} "
        f"extra={len(extra)} not_extracted={len(not_extracted)}"
    )
print(f"capture_manifest_entries={count}")
print("capture_manifest_verification=PASS")
PY

RELEASE_MANIFEST="$SNAPSHOT/release/release-manifest.json"
if [[ ! -f "$RELEASE_MANIFEST" ]]; then
  echo "ERROR: release/release-manifest.json missing from artifact" >&2
  exit 1
fi

python3 - \
  "$RELEASE_MANIFEST" \
  "$EXPECTED_BASE_HEAD" \
  "$EXPECTED_RELEASE_NAME" \
  "$EXPECTED_ALEMBIC_HEAD" \
  "$EXPECTED_TRACKED_MODIFIED" \
  "$EXPECTED_UNTRACKED" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_base = sys.argv[2]
expected_release = sys.argv[3]
expected_alembic = sys.argv[4]
expected_modified = int(sys.argv[5])
expected_untracked = int(sys.argv[6])
data = json.loads(path.read_text(encoding="utf-8"))
checks = {
    "base_head": data.get("base_head") == expected_base,
    "release_name": data.get("release_name") == expected_release,
    "alembic_head": data.get("alembic_head") == expected_alembic,
    "acceptance_ready": data.get("acceptance_ready") is True,
    "deleted_count": data.get("deleted_count") == 0 and data.get("deleted_files") == [],
    "tracked_modified_count": data.get("tracked_modified_count") == expected_modified,
    "untracked_count": data.get("untracked_count") == expected_untracked,
    "tracked_modified_len": len(data.get("tracked_modified_files", [])) == expected_modified,
    "untracked_len": len(data.get("untracked_files", [])) == expected_untracked,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("release manifest contract failed: " + ", ".join(failed))
paths = data["tracked_modified_files"] + data["untracked_files"]
if len(paths) != len(set(paths)):
    raise SystemExit("duplicate modified/untracked path in release manifest")
for p in paths:
    pp = Path(p)
    if pp.is_absolute() or ".." in pp.parts:
        raise SystemExit(f"unsafe release manifest path: {p}")
print(f"tracked_modified={expected_modified}")
print(f"untracked={expected_untracked}")
print("release_manifest_contract=PASS")
PY

mkdir -p "$(dirname "$WORKDIR")"
git clone "$REPO_URL" "$WORKDIR"
cd "$WORKDIR"
git checkout --detach "$EXPECTED_BASE_HEAD"

# A reserved remote branch may already exist at the exact base commit. Build a local branch from the base.
git switch -c "$RECOVERY_BRANCH"

python3 - "$SNAPSHOT" "$RELEASE_MANIFEST" "$WORKDIR" <<'PY'
import json
import shutil
import sys
from pathlib import Path

snapshot = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
repo = Path(sys.argv[3]).resolve()
data = json.loads(manifest_path.read_text(encoding="utf-8"))
paths = data["tracked_modified_files"] + data["untracked_files"]
for rel in paths:
    src = (snapshot / rel).resolve()
    dst = (repo / rel).resolve()
    if snapshot not in src.parents:
        raise SystemExit(f"source escaped snapshot: {rel}")
    if repo not in dst.parents:
        raise SystemExit(f"destination escaped repo: {rel}")
    if not src.is_file():
        raise SystemExit(f"manifest-declared source file missing: {rel}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
print(f"copied_paths={len(paths)}")
PY

git add -A

python3 - "$RELEASE_MANIFEST" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = sorted(data["tracked_modified_files"] + data["untracked_files"])
actual = subprocess.check_output(
    ["git", "diff", "--cached", "--name-only"],
    text=True,
).splitlines()
actual = sorted(line for line in actual if line.strip())
if actual != expected:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    raise SystemExit(
        f"staged path set mismatch: expected={len(expected)} actual={len(actual)} "
        f"missing={missing[:10]} extra={extra[:10]}"
    )
print(f"staged_paths={len(actual)}")
print("staged_path_set=PASS")
PY

# Sanity-compile every Python file as bytes so UTF-8 BOM compatibility shims are handled correctly.
python3 - <<'PY'
from pathlib import Path
errors = []
count = 0
for p in Path('.').rglob('*.py'):
    if '.git' in p.parts:
        continue
    count += 1
    try:
        compile(p.read_bytes(), str(p), 'exec')
    except Exception as exc:
        errors.append((str(p), repr(exc)))
if errors:
    raise SystemExit(f"python byte-compile failed: {errors[:10]}")
print(f"python_files_compiled={count}")
print("python_byte_compile=PASS")
PY

commit_message="baseline: recover deployed Phase8 RC3 source"
git commit -m "$commit_message" \
  -m "Recovered exactly from deployed artifact SHA-256 $EXPECTED_ARCHIVE_SHA256." \
  -m "Artifact provenance base: $EXPECTED_BASE_HEAD; release: $EXPECTED_RELEASE_NAME; Alembic: $EXPECTED_ALEMBIC_HEAD. No VDS/runtime behavior was modified by the recovery process."

RECOVERED_COMMIT="$(git rev-parse HEAD)"
echo "recovered_commit=$RECOVERED_COMMIT"
echo "recovery_branch=$RECOVERY_BRANCH"
echo "recovery_status=COMMITTED"

if [[ "$PUSH" -eq 1 ]]; then
  git push --set-upstream origin "$RECOVERY_BRANCH"
  echo "push_status=PASS"
else
  echo "push_status=SKIPPED"
  echo "next_command=cd '$WORKDIR' && git push --set-upstream origin '$RECOVERY_BRANCH'"
fi
