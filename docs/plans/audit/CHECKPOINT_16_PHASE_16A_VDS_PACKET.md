# Checkpoint 16 — Phase 16A VDS execution packet

**Status:** VDS EXECUTION COMPLETE; see Phase 16B closure record for the
evidence-backed seal decision. This packet remains the operator runbook and
does not by itself change global maturity flags.

Operational flag evaluation is recorded by Phase 16B closure:

```
SYSTEMD_STAGING_READY=YES
MACHINE_REBOOT_QUALIFIED=YES
24_7_READY=YES
```

Do not infer live model readiness, production readiness, or real-target
field validation from these operational flags.

Sealed baseline at 16A start:

- branch `campaign/canonical-mr5-mr6`
- HEAD `7c13ef251790dcbe69d36066124b5eabbeb7177b`
- tag `operator-staging-control-closed`
- Alembic head expected `a42_001_preflight_report` unless this tree adds a
  migration (16A does not)

---

## Host prerequisites (operator, once)

Ubuntu 22.04.5 default `python3.10` is below `requires-python = ">=3.11"`.
Install Python 3.11 on the host. Example (operator-controlled apt, not
application `curl | bash`):

```bash
sudo apt-get update
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev rsync
python3.11 --version
```

Confirm existing layout and account (already created on this VDS):

```bash
id research-os
namei -l /opt/research-os /etc/research-os /var/lib/research-os /var/log/research-os
ss -ltn | sed -n '1p;/5432/p'
sudo ss -ltnp | sed -n '1p;/5432/p'
```

PostgreSQL must remain `127.0.0.1:5432` only.

Create `/etc/research-os/research-os.env` from
`config/research-osd.env.example`. Set the real DSN locally. Do not paste
the password into chat, shell history, or git.

```bash
sudo install -m 0640 -o root -g research-os /dev/null /etc/research-os/research-os.env
sudo -e /etc/research-os/research-os.env
# required keys (values stay on the host):
# RESEARCH_OS_DATABASE_URL=postgresql+psycopg://...@127.0.0.1:5432/...
# RESEARCH_OSD_BIND_HOST=127.0.0.1
# RESEARCH_OSD_API_PORT=8766
# RESEARCH_OSD_URL=http://127.0.0.1:8766
# RESEARCH_OSD_ENVIRONMENT_NAME=staging
# RESEARCH_OSD_RELEASE_VERSION=<immutable-release-id>
```

Create an empty application database (do not import the test database):

```bash
sudo -u postgres psql -c "SELECT datname FROM pg_database;"
# if research_os does not exist:
# sudo -u postgres createdb research_os
# sudo -u postgres createuser research_os   # then GRANT as needed
```

---

## J1. Fresh release install

From an operator checkout of this tree (or a copied source directory):

```bash
RELEASE_ID="$(git -C /path/to/research-os rev-parse --short=12 HEAD)-cp16a"
sudo /path/to/research-os/scripts/install_research_os_release.sh \
  --source /path/to/research-os \
  --release-id "$RELEASE_ID" \
  --python /usr/bin/python3.11 \
  --migrate \
  --install-units
```

Capture:

```bash
readlink -f /opt/research-os/current
ls -ld /opt/research-os/releases/"$RELEASE_ID"
/opt/research-os/current/.venv/bin/python -c 'import research_os, research_os.interface.research_osd; print("import=ok")'
/opt/research-os/current/.venv/bin/research-osd --help
sudo -u research-os /opt/research-os/current/.venv/bin/python \
  /opt/research-os/current/scripts/verify_research_os_release.py \
  --release-root /opt/research-os/current \
  --env-file /etc/research-os/research-os.env \
  --check-import --check-entrypoint --check-alembic-heads --check-db
```

Expected: exactly one Alembic head, one `alembic_version` row after migrate.
Do not print `RESEARCH_OS_DATABASE_URL`.

---

## J2. Foreground smoke

Do not enable systemd yet.

```bash
sudo -u research-os \
  /opt/research-os/current/.venv/bin/research-osd
```

In another session:

```bash
curl -sS http://127.0.0.1:8766/health
curl -sS http://127.0.0.1:8766/api/console
curl -sS -X POST http://127.0.0.1:8766/api/runs/run-1/preflight || true
ss -ltn | sed -n '1p;/8766/p'
# prove not public:
curl -sS --connect-timeout 2 http://0.0.0.0:8766/health && echo PUBLIC_BIND_FAIL || echo public_bind_refused_or_loopback_only
```

Then SIGTERM the foreground process and capture clean drain.

Record `runtime_instance_id` from `/health`.

---

## J3. systemd start

```bash
sudo systemctl daemon-reload
sudo systemctl enable research-osd.service
sudo systemctl start research-osd.service
systemctl is-enabled research-osd.service
systemctl is-active research-osd.service
systemctl show research-osd.service -p User -p Group -p EnvironmentFile -p ExecStart -p MainPID
journalctl -u research-osd.service -n 80 --no-pager
curl -sS http://127.0.0.1:8766/health
ss -ltn | sed -n '1p;/8766\|5432/p'
```

Prove a **new** `runtime_instance_id` versus J2. Bind remains `127.0.0.1`.
No public Operator API.

---

## J4. graceful restart

```bash
curl -sS http://127.0.0.1:8766/health   # record runtime_instance_id = A
sudo systemctl restart research-osd.service
systemctl is-active research-osd.service
curl -sS http://127.0.0.1:8766/health   # record runtime_instance_id = B
```

A ≠ B. Capture orchestration owner/lease via fixture `show` (below).

---

## J5. hard process kill

```bash
MAINPID="$(systemctl show -p MainPID --value research-osd.service)"
echo "mainpid=$MAINPID"
sudo kill -9 "$MAINPID"
sleep 6
systemctl is-active research-osd.service
curl -sS http://127.0.0.1:8766/health
journalctl -u research-osd.service -n 40 --no-pager
```

Systemd must restart the process. New RuntimeInstance. No duplicate Worker
execution. PostgreSQL remains SoR.

---

## J6. dashboard death

```bash
sudo systemctl enable --now research-os-dashboard.service
systemctl is-active research-os-dashboard.service
curl -sS http://127.0.0.1:8765/healthz
# start or observe a run owned by research-osd, then:
sudo systemctl stop research-os-dashboard.service
systemctl is-active research-osd.service
curl -sS http://127.0.0.1:8766/health
curl -sS http://127.0.0.1:8766/api/runs
sudo systemctl start research-os-dashboard.service
curl -sS http://127.0.0.1:8765/healthz
```

Run/supervisor must continue on research-osd. Dashboard owns no supervisor.

---

## J7. PostgreSQL unavailable

Controlled, do not corrupt the cluster. After the J7 API-outage fix,
`/health` and `/start` must return bounded JSON. Empty reply (`curl 52`)
is a FAIL.

```bash
# baseline
systemctl is-active research-osd.service
curl -sS http://127.0.0.1:8766/health
# record runtime_instance_id

sudo systemctl stop postgresql.service
systemctl is-active postgresql.service
systemctl is-active postgresql@14-main.service
ss -ltn | sed -n '1p;/5432/p'
systemctl is-active research-osd.service
ss -ltn | sed -n '1p;/8766/p'

# MUST be JSON, not empty reply. Expect:
# pg_unavailable=true, ready_for_start=false,
# database.available_now=false, database.health=UNAVAILABLE
curl -sS -D- http://127.0.0.1:8766/health

# MUST be structured HTTP JSON, not empty reply. Expect DATABASE_UNAVAILABLE.
# No new START, no RAM fallback, no DSN/password in body.
curl -sS -D- -X POST http://127.0.0.1:8766/api/runs/run-1/start -H 'Content-Type: application/json' -d '{}'

sudo systemctl start postgresql.service
sleep 3
systemctl is-active postgresql.service

# same daemon, no mandatory restart
systemctl is-active research-osd.service
curl -sS http://127.0.0.1:8766/health
# expect pg_unavailable=false, database.available_now=true,
# database.health=HEALTHY, same runtime_instance_id
```

If research-osd exited, `systemctl status` plus journal. After PG returns,
recovery must follow existing semantics (no RAM fallback). Do not treat a
process-alive-only outcome as J7 PASS if `/health` dropped the connection.

---

## Fixture packaging (required before J8–J11)

The fixture is self-contained in the installed release
(`research_os.qualification.staging_spine`). It must not import
`integration.harness`, must not use `PYTHONPATH`, and must not require
the tests tree.

Already-collected J3/J4/J5 evidence remains valid if a later immutable
release is installed only to fix qualification tooling. Do not redo J3–J5
as a qualifier. A systemd restart after replacement creates a new
`RuntimeInstance`; that is expected and does not invalidate prior
captured J3–J5 records.

Do not:

```bash
# forbidden
export PYTHONPATH=/opt/research-os/current/tests
pip install -e tests
cp tests/integration/harness.py /opt/research-os/current/
```

Prove before J8:

```bash
/opt/research-os/current/.venv/bin/python \
  /opt/research-os/current/scripts/vds_checkpoint16_fixture.py --help
/opt/research-os/current/.venv/bin/python -c \
  'from research_os.qualification.staging_spine import seed_authorized_spine; print("ok")'
# fail-closed (must exit non-zero; must not open/truncate the DB):
/opt/research-os/current/.venv/bin/python \
  /opt/research-os/current/scripts/vds_checkpoint16_fixture.py seed
```

### Immutable replacement (fixture-packaging release)

Do not overwrite the already-installed SIGTERM-fixed directory. Install a
new release id, then point `current` at it.

```bash
# on the operator workstation, after transferring the new archive:
sha256sum -c research-os-checkpoint16a-fixture-packaging.tar.gz.sha256
sudo mkdir -p /tmp/research-os-fixture-packaging
sudo tar -xzf research-os-checkpoint16a-fixture-packaging.tar.gz \
  -C /tmp/research-os-fixture-packaging
SOURCE="$(find /tmp/research-os-fixture-packaging -maxdepth 2 -name pyproject.toml -printf '%h\n' | head -n 1)"
RELEASE_ID="checkpoint16a-fixture-packaging"
sudo "$SOURCE/scripts/install_research_os_release.sh" \
  --source "$SOURCE" \
  --release-id "$RELEASE_ID" \
  --python /usr/bin/python3.12
```

The installer is immutable: it refuses if `/opt/research-os/releases/$RELEASE_ID`
already exists. It updates `/opt/research-os/current` to the new tree and
creates a new venv. It does not restart systemd unless `--enable` is passed.

Preferred for J8–J11: do **not** restart research-osd solely for this
tooling fix. The running SIGTERM-fixed process stays the J3–J5 instance.
The new fixture is a separate process using `current/.venv`.

If you do restart, record the new `runtime_instance_id`. That does not
invalidate already-captured J3–J5 evidence.

Then prove `--help` as above. Do not set `PYTHONPATH`. Do not copy
`tests/integration`.

---

## J8–J10. crash classification fixtures

---

## J8–J10. crash classification fixtures

Use the dedicated staging DB only. `seed --truncate` destroys spine rows.

```bash
cd /opt/research-os/current
sudo -u research-os \
  RESEARCH_OSD_ALLOW_SPINE_TRUNCATE=YES \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py seed --truncate
```

The fixture loads `/etc/research-os/research-os.env` only if those
variables are already in the process environment. Prefer:

```bash
set -a
# do not use `set -a; source` in a recorded shell if the transcript is shared.
# Instead run under systemd EnvironmentFile via:
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current \
  --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py seed --truncate
```

Set `RESEARCH_OSD_ALLOW_SPINE_TRUNCATE=YES` in that transient unit
environment as well (`-E RESEARCH_OSD_ALLOW_SPINE_TRUNCATE=YES`).

### J8 AUTHORIZED-not-dispatched

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py authorized-not-dispatched
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py classify
sudo systemctl kill -s KILL research-osd.service
sleep 6
curl -sS http://127.0.0.1:8766/api/runs/run-1
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py classify
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py show
```

Expected classify: `SAFE_RETRY_AFTER_REAUTHORIZATION`.
No stale AuthorizationDecision reuse as START authority.

### J9 DISPATCHING

```bash
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py dispatching
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py classify
# expected: RECONCILIATION_REQUIRED
sudo systemctl restart research-osd.service
curl -sS http://127.0.0.1:8766/api/runs/run-1
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py show
```

Expected after recovery: `WAITING_HUMAN` / `RECONCILIATION_REQUIRED`.
No automatic attach, replay, or duplicate side effect.

### J10 UNKNOWN_OUTCOME

```bash
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py unknown-outcome
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py classify
# expected: HUMAN_REQUIRED
sudo systemctl restart research-osd.service
curl -sS http://127.0.0.1:8766/api/runs/run-1
```

Must remain HUMAN_REQUIRED / WAITING_HUMAN. No auto-retry.

---

## J11. two-runtime fencing race

J10 intentionally leaves `run-1` held for a human:

```text
state=WAITING_HUMAN
pause_reason=HUMAN_REQUIRED
owner_runtime_instance_id=NULL
```

That is the correct recovery result. It is not, by itself, a runnable
lease-race surface. Do not confuse the recovery classification
(`HUMAN_REQUIRED`) with the durable J11 lease/fencing transition being
qualified here.

Prepare only the bounded J11 fencing surface:

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py j11-prepare
```

Expected: `state=RUNNING`, `owner=None`, `side_effect_ceiling=0`.
The prepare command must fail closed if a live owner already exists.

First prove stale epoch fencing. This uses separate real Python child
processes for the stale and current runtime identities, real PostgreSQL,
`SingleRunFencedUowFactory`, and `LeaseFencedWorkerPort`:

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py j11-stale-proof
```

Expected:

```text
j11_stale=PASS
stale_save_blocked=True
stale_worker_blocked=True
loser_inner_dispatch_count=0
```

Then run the two-process race. This uses two real Python child processes,
real PostgreSQL, production lease CAS, and a bounded diagnostic Worker
request only as a fenced no-dispatch probe for the loser. It does not relax
Preflight and does not invoke external/network Workers:

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py j11-race --iterations 5
```

Expected:

```text
j11_race=PASS
final_owner_count=1
loser_worker_blocked=True
loser_inner_dispatch_count=0
lease_epoch monotonic across iterations
```

Capture final state:

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py show
```

Legacy direct-daemon probe remains optional context only. Do not use it to
fake PASS if Preflight blocks start correctly:

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/research-osd --port 8767
```

Capture both `/health` `runtime_instance_id` values and:

```bash
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py show
```

Exactly one `owner_runtime_instance_id` on `run-1`. The loser stays fenced.
Stop any extra process.

After the final race evidence has been captured, clear only the expired
stopped J11 qualification owner using the repository lease-release CAS:

```bash
sudo systemd-run --uid=research-os --gid=research-os \
  --property=EnvironmentFile=/etc/research-os/research-os.env \
  --working-directory=/opt/research-os/current --pipe --wait \
  /opt/research-os/current/.venv/bin/python \
  scripts/vds_checkpoint16_fixture.py j11-cleanup \
    --owner-runtime-instance-id <final_owner_runtime_instance_id> \
    --expected-lease-epoch <final_lease_epoch>
```

If the operator omits owner/epoch, `j11-cleanup` may derive them only from
the current owner when that owner is an expired, STOPPED
`checkpoint16-j11-fencing` qualification runtime. The command refuses live
owners, owner/epoch mismatch, missing runtime rows, and non-qualification
owners. Expected cleanup evidence:

```text
j11_cleanup=PASS
cleanup=RELEASED_EXPIRED_STOPPED_OWNER
release_cas_applied=True
lease_epoch_after=<same final epoch>
owner_after=None
lease_expires_at_after=None
```

---

## J12. ACTUAL machine reboot (mandatory)

Not a mocked reboot. Not only `systemctl restart`.

1. Record before:

```bash
curl -sS http://127.0.0.1:8766/health
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py show
systemctl is-enabled research-osd.service
```

2. Prefer a safe eligible state **or** proceed to J13 for unsafe hold.

3. Reboot:

```bash
sudo reboot
```

4. After the VDS returns:

```bash
systemctl is-active postgresql
systemctl is-active research-osd.service
curl -sS http://127.0.0.1:8766/health
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py show
journalctl -u research-osd.service -b --no-pager | tail -n 120
```

Capture:

- before `runtime_instance_id`
- after `runtime_instance_id` (must be new)
- run state, lease owner, lease epoch
- ExecutionAttempt phase/classification
- journal lines for `runtime.recovery` / lease

---

## J13. reboot with held/unsafe state

Before reboot, set DISPATCHING or UNKNOWN_OUTCOME (J9/J10), confirm
classify is `RECONCILIATION_REQUIRED` or `HUMAN_REQUIRED`, then
`sudo reboot`.

After return: state remains `WAITING_HUMAN` / HUMAN_REQUIRED.
No Worker replay.

---

## J14. repeated restart

```bash
for i in 1 2 3 4 5; do
  echo "restart=$i"
  sudo systemctl restart research-osd.service
  systemctl is-active research-osd.service
  curl -sS http://127.0.0.1:8766/health
  sleep 2
done
sudo systemd-run ... scripts/vds_checkpoint16_fixture.py show
```

No duplicate supervisor, attempt, dispatch, or lease split-brain.

---

## J15. permissions

```bash
sudo -u research-os test -w /etc/research-os && echo FAIL || echo etc_denied
sudo -u research-os test -w /etc/research-os/research-os.env && echo FAIL || echo env_denied
sudo -u research-os test -w /opt/research-os/current/pyproject.toml && echo FAIL || echo release_denied
sudo -u research-os test -w /etc/systemd/system/research-osd.service && echo FAIL || echo unit_denied
sudo -u research-os test -w /var/lib/research-os && echo state_ok || echo FAIL
sudo -u research-os test -w /var/log/research-os && echo log_ok || echo FAIL
sudo -n true && echo unexpected_sudo || echo sudo_denied_or_password
# research-os itself has no sudo; the last line is the operator shell.
```

---

## J16. secret sanitation

```bash
journalctl -u research-osd.service -n 200 --no-pager | \
  grep -Ei 'password|api[_-]?key|authorization:|cookie=|secret' || echo journal_clean
curl -sS http://127.0.0.1:8766/health
curl -sS http://127.0.0.1:8766/api/console
curl -sS http://127.0.0.1:8766/api/runs/run-1 || true
ps -o args= -C research-osd || pgrep -a research-osd
systemctl status research-osd.service --no-pager
```

No DB password, API key, token, Authorization header, or cookie.

Helper:

```bash
sudo ./scripts/vds_checkpoint16_collect.sh /tmp/checkpoint16-evidence.txt
```

Review before sharing. Redact anything unexpected.

---

## Evidence checklist to return

1. Python 3.11 version and install log
2. J1 installer + alembic head + single `alembic_version` row
3. J2 foreground health + SIGTERM
4. J3 enable/active/journal + bind `ss`
5. J4 runtime_instance_id before/after restart
6. J5 SIGKILL + systemd restart + new instance
7. J6 dashboard stop while osd owns the run
8. J7 PG stop/start + health/START refusal
9. J8 classify + post-crash API
10. J9 DISPATCHING hold
11. J10 UNKNOWN_OUTCOME hold
12. J11 two-process owner uniqueness
13. J12 **actual reboot** before/after instance ids + journal `-b`
14. J13 reboot of held/unsafe state
15. J14 five restarts
16. J15 permission probes
17. J16 secret scan

Phase 16B closure is recorded in
`docs/plans/audit/CHECKPOINT_16_PHASE_16B_CLOSURE.md`. Do not infer live
model readiness, production readiness, or real-target field validation from
this packet.
