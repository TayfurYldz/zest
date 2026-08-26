#!/usr/bin/env bash
# Native Ubuntu source-release installer for Zest.
# Fail-fast, non-interactive, release-pinned. Does not curl remote code.
# Does not print secrets. Does not open PostgreSQL or zestd publicly.

set -euo pipefail

OPT_ROOT="/opt/zest"
RELEASES_DIR="${OPT_ROOT}/releases"
CURRENT_LINK="${OPT_ROOT}/current"
ETC_DIR="/etc/zest"
ENV_FILE="${ETC_DIR}/zest.env"
STATE_DIR="/var/lib/zest"
LOG_DIR="/var/log/zest"
SERVICE_USER="zest"
SERVICE_GROUP="zest"
MIN_PY_MAJOR=3
MIN_PY_MINOR=11

SOURCE=""
RELEASE_ID=""
PYTHON=""
DO_MIGRATE=0
INSTALL_UNITS=0
ENABLE=0

usage() {
  cat <<'EOF'
Usage: install_zest_release.sh --source DIR --release-id ID --python PYTHON [options]

Required:
  --source DIR       Immutable source tree to copy (local path only)
  --release-id ID    Release directory name under /opt/zest/releases/
  --python PYTHON    Python >= 3.11 interpreter

Optional:
  --migrate          Run Alembic upgrade head after install
  --install-units    Copy deploy/systemd units to /etc/systemd/system/
  --enable           systemctl enable --now zestd (implies --install-units)

Does not:
  fetch remote code, write secrets to git, use SQLite, bind publicly,
  expose PostgreSQL, or treat systemd as research authority.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --release-id) RELEASE_ID="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --migrate) DO_MIGRATE=1; shift ;;
    --install-units) INSTALL_UNITS=1; shift ;;
    --enable) ENABLE=1; INSTALL_UNITS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "must run as root" >&2
  exit 2
fi

if [[ -z "$SOURCE" || -z "$RELEASE_ID" || -z "$PYTHON" ]]; then
  echo "--source, --release-id, and --python are required" >&2
  usage >&2
  exit 2
fi

if [[ "$RELEASE_ID" == *"/"* || "$RELEASE_ID" == "." || "$RELEASE_ID" == ".." ]]; then
  echo "release-id must be a single path segment" >&2
  exit 2
fi

if [[ ! -d "$SOURCE" ]]; then
  echo "source is not a directory: $SOURCE" >&2
  exit 2
fi

if [[ ! -f "$SOURCE/pyproject.toml" || ! -f "$SOURCE/alembic.ini" ]]; then
  echo "source is not a Zest tree (missing pyproject.toml or alembic.ini)" >&2
  exit 2
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "python is not executable: $PYTHON" >&2
  exit 2
fi

PY_VERSION="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJOR="$("$PYTHON" -c 'import sys; print(sys.version_info[0])')"
PY_MINOR="$("$PYTHON" -c 'import sys; print(sys.version_info[1])')"
if [[ "$PY_MAJOR" -lt "$MIN_PY_MAJOR" || ( "$PY_MAJOR" -eq "$MIN_PY_MAJOR" && "$PY_MINOR" -lt "$MIN_PY_MINOR" ) ]]; then
  echo "Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} is required; got ${PY_VERSION}" >&2
  echo "Ubuntu 22.04 default python3.10 is insufficient; install python3.11 (deadsnakes) first." >&2
  exit 2
fi

for path in "$OPT_ROOT" "$RELEASES_DIR" "$ETC_DIR" "$STATE_DIR" "$LOG_DIR"; do
  if [[ ! -d "$path" ]]; then
    echo "required directory missing: $path" >&2
    exit 2
  fi
done

if ! getent passwd "$SERVICE_USER" >/dev/null; then
  echo "service user missing: $SERVICE_USER" >&2
  exit 2
fi
if ! getent group "$SERVICE_GROUP" >/dev/null; then
  echo "service group missing: $SERVICE_GROUP" >&2
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing EnvironmentFile: $ENV_FILE" >&2
  echo "copy config/zestd.env.example and set the real DSN locally" >&2
  exit 2
fi

RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
if [[ -e "$RELEASE_DIR" ]]; then
  echo "release already exists (immutable): $RELEASE_DIR" >&2
  exit 2
fi

VERIFY_PY="${SOURCE}/scripts/verify_zest_release.py"
if [[ ! -f "$VERIFY_PY" ]]; then
  echo "missing verifier: $VERIFY_PY" >&2
  exit 2
fi

"$PYTHON" "$VERIFY_PY" --assets-root "$SOURCE" --env-file "$ENV_FILE"

umask 027
mkdir -p "$RELEASE_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.venv/' \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'htmlcov/' \
    --exclude '.cursor/' \
    --exclude 'var/' \
    "${SOURCE}/" "${RELEASE_DIR}/"
else
  tar -C "$SOURCE" \
    --exclude '.venv' \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude 'htmlcov' \
    --exclude '.cursor' \
    --exclude 'var' \
    -cf - . | tar -C "$RELEASE_DIR" -xf -
fi

"$PYTHON" -m venv "${RELEASE_DIR}/.venv"
# shellcheck disable=SC1091
source "${RELEASE_DIR}/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install "${RELEASE_DIR}"
deactivate

VERIFY_ARGS=(
  --release-root "$RELEASE_DIR"
  --env-file "$ENV_FILE"
  --check-import
  --check-entrypoint
  --check-alembic-heads
  --check-db
)
if [[ "$DO_MIGRATE" -eq 1 ]]; then
  VERIFY_ARGS+=(--migrate)
fi
"${RELEASE_DIR}/.venv/bin/python" "${RELEASE_DIR}/scripts/verify_zest_release.py" "${VERIFY_ARGS[@]}"

chown -R "root:${SERVICE_GROUP}" "$RELEASE_DIR"
find "$RELEASE_DIR" -type d -exec chmod 0750 {} +
find "$RELEASE_DIR" -type f -exec chmod 0640 {} +
if [[ -d "${RELEASE_DIR}/.venv/bin" ]]; then
  find "${RELEASE_DIR}/.venv/bin" -type f -exec chmod 0750 {} +
fi
chmod 0750 "${RELEASE_DIR}/scripts/"*.sh "${RELEASE_DIR}/scripts/"*.py 2>/dev/null || true

ln -sfn "releases/${RELEASE_ID}" "$CURRENT_LINK"
chown -h "root:${SERVICE_GROUP}" "$CURRENT_LINK"

if [[ "$INSTALL_UNITS" -eq 1 ]]; then
  install -m 0644 -o root -g root \
    "${CURRENT_LINK}/deploy/systemd/zestd.service" \
    /etc/systemd/system/zestd.service
  install -m 0644 -o root -g root \
    "${CURRENT_LINK}/deploy/systemd/zest-dashboard.service" \
    /etc/systemd/system/zest-dashboard.service
  if [[ -d /etc/logrotate.d ]]; then
    install -m 0644 -o root -g root \
      "${CURRENT_LINK}/deploy/logrotate/zest" \
      /etc/logrotate.d/zest
  fi
  systemctl daemon-reload
fi

if [[ "$ENABLE" -eq 1 ]]; then
  systemctl enable zestd.service
  systemctl start zestd.service
fi

echo "installed release ${RELEASE_ID}"
echo "current -> ${CURRENT_LINK} -> ${RELEASE_DIR}"
echo "python ${PY_VERSION}"
echo "systemd units installed=${INSTALL_UNITS} enabled=${ENABLE}"
