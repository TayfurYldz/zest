#!/usr/bin/env bash
# Collect Checkpoint 16 evidence without printing secrets.
# Run on the VDS. Does not mark the checkpoint PASS.

set -euo pipefail

OUT="${1:-/tmp/checkpoint16-evidence.txt}"
umask 077
: > "$OUT"

note() {
  printf '\n===== %s =====\n' "$1" | tee -a "$OUT"
}

safe() {
  "$@" >>"$OUT" 2>&1
}

note "host"
safe uname -a || true
safe cat /etc/os-release || true
safe timedatectl show -p NTPSynchronized -p Timezone --value || true
safe bash -c 'systemctl --version | head -n 1' || true

note "layout-permissions"
safe namei -l /opt/zest/current || true
safe ls -ld /opt/zest /opt/zest/releases /opt/zest/current \
  /etc/zest /etc/zest/zest.env \
  /var/lib/zest /var/log/zest || true
safe ls -l /opt/zest/current/.venv/bin/zestd || true
safe ls -l /etc/systemd/system/zestd.service \
  /etc/systemd/system/zest-dashboard.service || true

note "python-release"
safe /opt/zest/current/.venv/bin/python -c 'import sys; print(sys.version)' || true
safe /opt/zest/current/.venv/bin/python -c 'import zest; print(zest.__file__)' || true
safe /opt/zest/current/.venv/bin/python -c \
  'from zest.qualification.staging_spine import seed_authorized_spine, truncate_spine; print("qualification=ok")' || true
safe bash -c '/opt/zest/current/.venv/bin/zestd --help | head -n 5' || true
safe /opt/zest/current/.venv/bin/python \
  /opt/zest/current/scripts/vds_checkpoint16_fixture.py --help || true
safe readlink -f /opt/zest/current || true

note "alembic"
safe sudo -u zest /opt/zest/current/.venv/bin/python \
  /opt/zest/current/scripts/verify_zest_release.py \
  --release-root /opt/zest/current \
  --env-file /etc/zest/zest.env \
  --check-alembic-heads --check-db || true

note "listen"
safe bash -c "ss -ltnp | sed -n '1p;/8766\\|8765\\|5432/p'" || true
safe bash -c "ss -ltn | sed -n '1p;/0.0.0.0:8766\\|:::8766\\|0.0.0.0:5432/p'" || true

note "systemd"
safe systemctl is-enabled zestd.service || true
safe systemctl is-active zestd.service || true
safe systemctl show zestd.service -p User -p Group -p EnvironmentFile \
  -p WorkingDirectory -p ExecStart -p FragmentPath -p MainPID || true
safe bash -c 'systemd-cgls --unit=zestd.service | head -n 40' || true

note "health-sanitized"
if command -v curl >/dev/null; then
  safe curl -sS http://127.0.0.1:8766/health || true
  echo >>"$OUT"
  safe curl -sS http://127.0.0.1:8766/api/console || true
  echo >>"$OUT"
fi

note "journal-sanitized"
safe journalctl -u zestd.service -n 80 --no-pager || true
if journalctl -u zestd.service -n 200 --no-pager 2>/dev/null | \
  grep -Ei 'password|api[_-]?key|authorization:|cookie=|token=' >>"$OUT" 2>&1; then
  echo "secret_scan=WARNING_MATCHES_FOUND" | tee -a "$OUT"
else
  echo "secret_scan=none" | tee -a "$OUT"
fi

note "permissions-as-service-user"
if sudo -u zest test -w /etc/zest >>"$OUT" 2>&1; then
  echo "etc_writable=FAIL" | tee -a "$OUT"
else
  echo "etc_writable=denied" | tee -a "$OUT"
fi

if sudo -u zest test -w /opt/zest/current >>"$OUT" 2>&1; then
  echo "release_writable=FAIL" | tee -a "$OUT"
else
  echo "release_writable=denied" | tee -a "$OUT"
fi

if sudo -u zest test -w /var/lib/zest >>"$OUT" 2>&1; then
  echo "state_writable=ok" | tee -a "$OUT"
else
  echo "state_writable=FAIL" | tee -a "$OUT"
fi

if sudo -u zest test -w /var/log/zest >>"$OUT" 2>&1; then
  echo "log_writable=ok" | tee -a "$OUT"
else
  echo "log_writable=FAIL" | tee -a "$OUT"
fi

echo "wrote $OUT"
echo "review this file before sharing; redact any unexpected secret if present"
