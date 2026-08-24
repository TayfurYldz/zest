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
safe namei -l /opt/research-os/current || true
safe ls -ld /opt/research-os /opt/research-os/releases /opt/research-os/current \
  /etc/research-os /etc/research-os/research-os.env \
  /var/lib/research-os /var/log/research-os || true
safe ls -l /opt/research-os/current/.venv/bin/research-osd || true
safe ls -l /etc/systemd/system/research-osd.service \
  /etc/systemd/system/research-os-dashboard.service || true

note "python-release"
safe /opt/research-os/current/.venv/bin/python -c 'import sys; print(sys.version)' || true
safe /opt/research-os/current/.venv/bin/python -c 'import research_os; print(research_os.__file__)' || true
safe /opt/research-os/current/.venv/bin/python -c \
  'from research_os.qualification.staging_spine import seed_authorized_spine, truncate_spine; print("qualification=ok")' || true
safe bash -c '/opt/research-os/current/.venv/bin/research-osd --help | head -n 5' || true
safe /opt/research-os/current/.venv/bin/python \
  /opt/research-os/current/scripts/vds_checkpoint16_fixture.py --help || true
safe readlink -f /opt/research-os/current || true

note "alembic"
safe sudo -u research-os /opt/research-os/current/.venv/bin/python \
  /opt/research-os/current/scripts/verify_research_os_release.py \
  --release-root /opt/research-os/current \
  --env-file /etc/research-os/research-os.env \
  --check-alembic-heads --check-db || true

note "listen"
safe bash -c "ss -ltnp | sed -n '1p;/8766\\|8765\\|5432/p'" || true
safe bash -c "ss -ltn | sed -n '1p;/0.0.0.0:8766\\|:::8766\\|0.0.0.0:5432/p'" || true

note "systemd"
safe systemctl is-enabled research-osd.service || true
safe systemctl is-active research-osd.service || true
safe systemctl show research-osd.service -p User -p Group -p EnvironmentFile \
  -p WorkingDirectory -p ExecStart -p FragmentPath -p MainPID || true
safe bash -c 'systemd-cgls --unit=research-osd.service | head -n 40' || true

note "health-sanitized"
if command -v curl >/dev/null; then
  safe curl -sS http://127.0.0.1:8766/health || true
  echo >>"$OUT"
  safe curl -sS http://127.0.0.1:8766/api/console || true
  echo >>"$OUT"
fi

note "journal-sanitized"
safe journalctl -u research-osd.service -n 80 --no-pager || true
if journalctl -u research-osd.service -n 200 --no-pager 2>/dev/null | \
  grep -Ei 'password|api[_-]?key|authorization:|cookie=|token=' >>"$OUT" 2>&1; then
  echo "secret_scan=WARNING_MATCHES_FOUND" | tee -a "$OUT"
else
  echo "secret_scan=none" | tee -a "$OUT"
fi

note "permissions-as-service-user"
if sudo -u research-os test -w /etc/research-os >>"$OUT" 2>&1; then
  echo "etc_writable=FAIL" | tee -a "$OUT"
else
  echo "etc_writable=denied" | tee -a "$OUT"
fi

if sudo -u research-os test -w /opt/research-os/current >>"$OUT" 2>&1; then
  echo "release_writable=FAIL" | tee -a "$OUT"
else
  echo "release_writable=denied" | tee -a "$OUT"
fi

if sudo -u research-os test -w /var/lib/research-os >>"$OUT" 2>&1; then
  echo "state_writable=ok" | tee -a "$OUT"
else
  echo "state_writable=FAIL" | tee -a "$OUT"
fi

if sudo -u research-os test -w /var/log/research-os >>"$OUT" 2>&1; then
  echo "log_writable=ok" | tee -a "$OUT"
else
  echo "log_writable=FAIL" | tee -a "$OUT"
fi

echo "wrote $OUT"
echo "review this file before sharing; redact any unexpected secret if present"
