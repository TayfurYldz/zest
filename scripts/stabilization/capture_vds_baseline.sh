#!/usr/bin/env bash
set -u

ROOT="${ZEST_ROOT:-/opt/zest/current}"
CANDIDATE_COMMIT="5b81b3d1ead3ed837ccade4fd17e104389fb1e26"

expected_blob() {
  case "$1" in
    src/zest/application/autonomous_research_controller.py) echo "629a2372c866220af716584732bef200063a78d6" ;;
    src/zest/application/zestd.py) echo "3eb25ffff1c263ff3aba24ed56cbef16579a33f2" ;;
    src/zest/application/local_run_supervisor.py) echo "3529b2bc7140f0f4e3586c4692ce56ea6c0d736c" ;;
    src/zest/interface/operator_api.py) echo "bde8ba42fb4cda593d362b41a9a3be4874c1abb0" ;;
    src/zest/integrations/models/cli_session.py) echo "9270a32e324e7a0eb5b4849d83183ea39920d7aa" ;;
    *) echo "UNKNOWN" ;;
  esac
}

section() {
  printf '\n============================================================\n%s\n============================================================\n' "$1"
}

section "S1 DEPLOYMENT PROVENANCE"
echo "candidate_commit=$CANDIDATE_COMMIT"
echo "current_link=$ROOT"
resolved="$(readlink -f "$ROOT" 2>/dev/null || true)"
echo "resolved_release=${resolved:-MISSING}"
if [[ -n "$resolved" ]]; then
  echo "release_id=$(basename "$resolved")"
fi

section "SERVICES"
for unit in zestd.service zest-dashboard.service; do
  echo "[$unit]"
  systemctl is-active "$unit" 2>/dev/null || true
  systemctl is-enabled "$unit" 2>/dev/null || true
  systemctl show "$unit" -p FragmentPath -p ActiveState -p SubState -p MainPID -p ExecStart -p EnvironmentFiles 2>/dev/null || true
  echo
 done

section "PACKAGE RUNTIME"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  "$ROOT/.venv/bin/python" - <<'PY'
import sys
from importlib.metadata import PackageNotFoundError, version
try:
    import zest
    print("zest_module=" + str(zest.__file__))
except Exception as exc:
    print("zest_import_error=" + exc.__class__.__name__)
print("python=" + sys.version.replace("\n", " "))
for package in ("zest", "playwright", "sqlalchemy", "psycopg", "alembic"):
    try:
        print(f"{package}={version(package)}")
    except PackageNotFoundError:
        print(f"{package}=NOT_INSTALLED")
PY
else
  echo "venv_python=MISSING"
fi

section "SOURCE FINGERPRINT"
match=1
files=(
  src/zest/application/autonomous_research_controller.py
  src/zest/application/zestd.py
  src/zest/application/local_run_supervisor.py
  src/zest/interface/operator_api.py
  src/zest/integrations/models/cli_session.py
)
for file in "${files[@]}"; do
  expected="$(expected_blob "$file")"
  if [[ -f "$ROOT/$file" ]] && command -v git >/dev/null 2>&1; then
    actual="$(git hash-object "$ROOT/$file" 2>/dev/null || true)"
  else
    actual="MISSING"
  fi
  status="MATCH"
  if [[ "$actual" != "$expected" ]]; then
    status="MISMATCH"
    match=0
  fi
  echo "$file expected=$expected actual=${actual:-HASH_FAILED} status=$status"
done
if [[ "$match" -eq 1 ]]; then
  echo "candidate_source_match=YES"
else
  echo "candidate_source_match=NO"
fi

section "BROWSER RUNTIME"
cache="/var/lib/zest/.cache/ms-playwright"
if [[ -d "$cache" ]]; then
  find "$cache" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort
else
  echo "playwright_cache=MISSING"
fi

section "ZESTD HEALTH"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  curl -fsS http://127.0.0.1:8766/health 2>/dev/null | "$ROOT/.venv/bin/python" -m json.tool || echo "zestd_health=UNAVAILABLE"
else
  curl -fsS http://127.0.0.1:8766/health 2>/dev/null || echo "zestd_health=UNAVAILABLE"
fi

section "POSTGRESQL"
psql --version 2>/dev/null || echo "psql_client=UNAVAILABLE"
if command -v sudo >/dev/null 2>&1; then
  sudo -u postgres psql -Atqc 'SHOW server_version;' 2>/dev/null | sed 's/^/postgres_server=/' || true
fi

section "CONFIGURATION PRESENCE (NAMES ONLY)"
env_file="/etc/zest/zest.env"
if [[ -f "$env_file" ]]; then
  # Print only variable names; never values.
  sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$env_file" \
    | sort -u \
    | grep -E '^(DATABASE_URL|ZEST_|CODEX_|INTERACTSH_|PLAYWRIGHT_)' \
    || true
else
  echo "zest_env=MISSING"
fi

section "BASELINE RESULT"
if [[ "$match" -eq 1 ]]; then
  echo "SOURCE_PROVENANCE=CANDIDATE_MATCH"
else
  echo "SOURCE_PROVENANCE=UNKNOWN_OR_MISMATCH"
fi
echo "S1_CAPTURE_COMPLETE=1"
