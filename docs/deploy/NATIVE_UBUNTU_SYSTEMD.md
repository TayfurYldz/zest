# Native Ubuntu systemd deployment (Checkpoint 16A)

This is the portable install model for research-osd as a local Ubuntu
service. Checkpoint 16 Phase 16B qualified this local native-Ubuntu
systemd/reboot deployment on the real VDS evidence recorded in
`docs/plans/audit/CHECKPOINT_16_PHASE_16B_CLOSURE.md`. It does **not**
qualify remote operator access, live model readiness, real-target field
research, or production.

Systemd is only a process supervisor. PostgreSQL remains the sole
authoritative SoR. research-osd continues to own RuntimeInstance,
lease/fencing, Preflight, Operator API, and recovery classification.

## Entrypoint

Real console scripts from `pyproject.toml`:

| command | module |
|---|---|
| `research-osd` | `research_os.interface.research_osd:main` |
| `research-os-dashboard` | `research_os.interface.dashboard:main` |
| `research-os` | `research_os.interface.cli:main` |

Do not invent another daemon entrypoint. Dashboard is a disposable local
HTTP client (`127.0.0.1:8765`) and does not own supervisors.

## Layout

```text
/opt/research-os/releases/<immutable-release-id>/
/opt/research-os/current -> releases/<immutable-release-id>
/etc/research-os/research-os.env
/var/lib/research-os/
/var/log/research-os/
```

Release files are `root:research-os` and not writable by `research-os`.
Runtime state stays under `/var/lib/research-os` and `/var/log/research-os`.
Secrets stay in `/etc/research-os/research-os.env` (not git).

## Packaging note

`requires-python = ">=3.11"`. Ubuntu 22.04's `python3.10` is not sufficient.
Install Python 3.11+ on the host (for example deadsnakes) before running
the installer. Do not lower the package floor.

Alembic lives in the **source release tree**. `research-osd` resolves
`alembic.ini` as:

1. `RESEARCH_OS_ALEMBIC_INI` if set
2. `$PWD/alembic.ini` (systemd `WorkingDirectory=/opt/research-os/current`)
3. repository root relative to the module when running from a checkout

Wheel-only installs without a release tree are not a supported migration
layout. The sdist now includes `alembic.ini`, `alembic/`, and `deploy/`.

## Install

As root, after the host directories, service account, PostgreSQL, and
`/etc/research-os/research-os.env` exist:

```bash
sudo ./scripts/install_research_os_release.sh \
  --source /path/to/research-os \
  --release-id <immutable-release-id> \
  --python /usr/bin/python3.11 \
  --migrate \
  --install-units
```

The installer is fail-fast, local-only, and does not `curl | bash`.
It refuses SQLite, public bind tokens, and a missing EnvironmentFile.

Foreground smoke before enable:

```bash
sudo -u research-os \
  /opt/research-os/current/.venv/bin/research-osd
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now research-osd.service
```

Dashboard is optional:

```bash
sudo systemctl enable --now research-os-dashboard.service
```

## Hardening

Applied: `NoNewPrivileges`, `PrivateTmp`, `ProtectHome`, `ProtectSystem=strict`
with `ReadWritePaths=/var/lib/research-os /var/log/research-os`, `UMask=0027`,
`Delegate=yes` (cgroup v2 child for PersistentBrowserWorkerAdapter).

Omitted because they break Worker/browser/cgroup:

- `ProtectControlGroups`
- `ProtectKernelTunables`
- `PrivateDevices`
- `MemoryDenyWriteExecute`

PostgreSQL is `After=` / not `Requires=`, so a running daemon can report
`DATABASE_UNAVAILABLE` instead of failing to start.

## Bind

Default and required: `RESEARCH_OSD_BIND_HOST=127.0.0.1`.
Operator API rejects `0.0.0.0`. Dashboard `--host` must be local.

Do not add Cloudflare Tunnel, reverse proxy, public TLS, VPN, Docker,
Kubernetes, Tailscale, or a public `:443` listener here.

## Logging

journald is the process log (`StandardOutput=journal`).
`RESEARCH_OSD_LOG_PATH` is optional. If used, install
`deploy/logrotate/research-os`.

## Operational Flags

```
SYSTEMD_STAGING_READY=YES
MACHINE_REBOOT_QUALIFIED=YES
24_7_READY=YES
REMOTE_OPERATOR_READY=NO
LIVE_MODEL_VALIDATED=NO
SECURITY_RESEARCH_VALIDATED=NO
PRODUCTION_READY=NO
```
