"""Verify a Research OS source release and host EnvironmentFile.

Does not print secret values. Does not treat systemd as research authority.
SQLite is rejected. Public bind is rejected.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DATABASE_URL_ENV = "RESEARCH_OS_DATABASE_URL"
BIND_HOST_ENV = "RESEARCH_OSD_BIND_HOST"
DEFAULT_BIND_HOST = "127.0.0.1"
LINUX_ENV_FILE = "/etc/research-os/research-os.env"
ALEMBIC_INI_ENV = "RESEARCH_OS_ALEMBIC_INI"
LOCAL_BIND_VALUES = frozenset({"127.0.0.1", "localhost", "::1"})
FORBIDDEN_BIND_TOKENS = ("0.0.0.0", "::", "[::]")
MIN_PYTHON = (3, 11)
EXPECTED_SCRIPTS = ("research-osd", "research-os-dashboard", "research-os")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid EnvironmentFile line in {path.name}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key.startswith(" "):
            raise ValueError(f"invalid EnvironmentFile key in {path.name}")
        values[key] = value
    return values


def assert_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        raise SystemExit(
            f"Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} is required; "
            f"got {sys.version_info[0]}.{sys.version_info[1]}"
        )


def assert_env_file(values: dict[str, str]) -> None:
    if DATABASE_URL_ENV not in values or not values[DATABASE_URL_ENV].strip():
        raise SystemExit(f"{DATABASE_URL_ENV} is required in {LINUX_ENV_FILE}")
    url = values[DATABASE_URL_ENV].strip()
    if "sqlite" in url.lower():
        raise SystemExit("SQLite is not an allowed SoR")
    if not url.startswith("postgresql"):
        raise SystemExit("database URL must be postgresql")
    host = values.get(BIND_HOST_ENV, DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST
    if host not in LOCAL_BIND_VALUES:
        raise SystemExit(f"{BIND_HOST_ENV} must be a local bind host")
    bind_keys = (
        BIND_HOST_ENV,
        "RESEARCH_OSD_URL",
        "RESEARCH_OS_DASHBOARD_HOST",
    )
    for key in bind_keys:
        value = values.get(key, "")
        for token in FORBIDDEN_BIND_TOKENS:
            if token in value:
                raise SystemExit(f"public bind token {token} is forbidden in {key}")


def assert_assets(root: Path) -> None:
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    if 'research-osd = "research_os.interface.research_osd:main"' not in pyproject:
        raise SystemExit("pyproject.toml is missing the research-osd console script")
    if 'research-os-dashboard = "research_os.interface.dashboard:main"' not in pyproject:
        raise SystemExit("pyproject.toml is missing the dashboard console script")
    if 'requires-python = ">=3.11"' not in pyproject:
        raise SystemExit("requires-python must remain >=3.11")
    unit = (root / "deploy/systemd/research-osd.service").read_text(encoding="utf-8")
    active = "\n".join(
        line for line in unit.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    required = (
        "User=research-os",
        "Group=research-os",
        "WorkingDirectory=/opt/research-os/current",
        "EnvironmentFile=/etc/research-os/research-os.env",
        "ExecStart=/opt/research-os/current/.venv/bin/research-osd",
        "Delegate=yes",
    )
    for item in required:
        if item not in active:
            raise SystemExit(f"research-osd.service missing {item}")
    forbidden = (
        "0.0.0.0",
        "ProtectControlGroups=true",
        "MemoryDenyWriteExecute=true",
        "PrivateDevices=true",
        "ProtectKernelTunables=true",
        "Requires=postgresql",
    )
    for item in forbidden:
        if item in active:
            raise SystemExit(f"research-osd.service must not contain {item}")
    dashboard = (root / "deploy/systemd/research-os-dashboard.service").read_text(encoding="utf-8")
    if "--host 127.0.0.1" not in dashboard:
        raise SystemExit("dashboard unit must bind 127.0.0.1")
    if "User=research-os" not in dashboard:
        raise SystemExit("dashboard unit must run as research-os")
    if "0.0.0.0" in dashboard:
        raise SystemExit("dashboard unit must not bind publicly")
    example = (root / "config/research-osd.env.example").read_text(encoding="utf-8")
    if "127.0.0.1" not in example:
        raise SystemExit("env example must default to 127.0.0.1")
    if "/home/tayfur" in example or "password=" in example.lower():
        raise SystemExit("env example must stay secret-free and portable")
    fixture = (root / "scripts/vds_checkpoint16_fixture.py").read_text(encoding="utf-8")
    if "integration.harness" in fixture or "from integration" in fixture:
        raise SystemExit("vds fixture must not import tests/integration")
    if "research_os.qualification.staging_spine" not in fixture:
        raise SystemExit("vds fixture must import shipped qualification primitives")
    if "research_os.qualification.j11_fencing" not in fixture:
        raise SystemExit("vds fixture must import shipped J11 fencing primitives")
    if "j11-cleanup" not in fixture or "cleanup_j11_owner" not in fixture:
        raise SystemExit("vds fixture must ship J11 cleanup action")


def _merged_env(values: dict[str, str]) -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(values)
    return merged


def check_import(release_root: Path) -> None:
    python = release_root / ".venv" / "bin" / "python"
    if not python.is_file():
        raise SystemExit(f"venv python missing: {python}")
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import research_os, research_os.interface.research_osd, "
            "research_os.qualification.staging_spine, "
            "research_os.qualification.j11_fencing; "
            "from research_os.qualification.j11_fencing import cleanup_j11_owner",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        raise SystemExit("application import failed")
    print("import=ok")


def check_entrypoint(release_root: Path) -> None:
    for name in EXPECTED_SCRIPTS:
        path = release_root / ".venv" / "bin" / name
        if not path.is_file():
            raise SystemExit(f"missing console script: {name}")
        if not os.access(path, os.X_OK):
            raise SystemExit(f"console script is not executable: {name}")
    help_cmd = subprocess.run(
        [str(release_root / ".venv" / "bin" / "research-osd"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if help_cmd.returncode != 0:
        raise SystemExit("research-osd --help failed")
    print("entrypoint=research-osd")


def resolve_alembic_ini(values: dict[str, str], *, release_root: Path) -> Path:
    explicit = values.get(ALEMBIC_INI_ENV) or os.environ.get(ALEMBIC_INI_ENV)
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return candidate
    candidate = (release_root / "alembic.ini").resolve()
    if candidate.is_file():
        return candidate
    candidate = (release_root / "src/research_os/application/osd_settings.py").resolve()
    if candidate.is_file():
        from research_os.application.osd_settings import resolve_alembic_ini as _resolve
        return _resolve(_merged_env(values), cwd=release_root, source_file=candidate)
    raise SystemExit(f"alembic.ini not found under release root: {release_root}")


def check_alembic_heads(release_root: Path, values: dict[str, str]) -> str:
    ini = resolve_alembic_ini(values, release_root=release_root)
    alembic = release_root / ".venv" / "bin" / "alembic"
    completed = subprocess.run(
        [str(alembic), "-c", str(ini), "heads"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(release_root),
    )
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        raise SystemExit("alembic heads failed")
    heads = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(heads) != 1:
        raise SystemExit(f"expected exactly one Alembic head, got {len(heads)}")
    head = heads[0].split()[0]
    print(f"alembic_head={head}")
    return head


def check_db(values: dict[str, str]) -> None:
    from research_os.application.osd_settings import load_osd_settings
    from research_os.data.postgres.engine import (
        create_sync_engine,
        ping_database,
        redacted_database_url,
    )

    settings = load_osd_settings(_merged_env(values))
    print(f"database={redacted_database_url(settings.database_url)}")
    engine = create_sync_engine(settings.database_url)
    try:
        ping_database(engine)
    finally:
        engine.dispose()
    print("database_ping=ok")


def migrate(release_root: Path, values: dict[str, str]) -> None:
    from research_os.application.osd_settings import load_osd_settings

    settings = load_osd_settings(_merged_env(values))
    ini = resolve_alembic_ini(values, release_root=release_root)
    env = _merged_env(values)
    env[DATABASE_URL_ENV] = settings.database_url
    env[ALEMBIC_INI_ENV] = str(ini)
    alembic = release_root / ".venv" / "bin" / "alembic"
    completed = subprocess.run(
        [str(alembic), "-c", str(ini), "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(release_root),
        env=env,
    )
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        raise SystemExit("alembic upgrade head failed")
    print("alembic_upgrade=ok")


def check_single_alembic_version(values: dict[str, str], expected_head: str) -> None:
    from sqlalchemy import text
    from research_os.application.osd_settings import load_osd_settings
    from research_os.data.postgres.engine import create_sync_engine

    settings = load_osd_settings(_merged_env(values))
    engine = create_sync_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            rows = list(connection.execute(text("SELECT version_num FROM alembic_version")))
    finally:
        engine.dispose()
    if len(rows) != 1:
        raise SystemExit(f"expected exactly one alembic_version row, got {len(rows)}")
    current = rows[0][0]
    expected = expected_head.split()[0]
    if current != expected:
        raise SystemExit(f"alembic_version={current!r} does not match heads {expected!r}")
    print(f"alembic_version={current}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_research_os_release")
    parser.add_argument("--assets-root", type=Path)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--check-import", action="store_true")
    parser.add_argument("--check-entrypoint", action="store_true")
    parser.add_argument("--check-alembic-heads", action="store_true")
    parser.add_argument("--check-db", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    args = parser.parse_args(argv)
    assert_python_version()
    if args.assets_root is not None:
        assert_assets(args.assets_root)
        print("assets=ok")
    values: dict[str, str] = {}
    if args.env_file is not None:
        values = parse_env_file(args.env_file)
        assert_env_file(values)
        print("env_file=ok")
        print(f"bind_host={values.get(BIND_HOST_ENV, DEFAULT_BIND_HOST)}")
    release = args.release_root
    if args.check_import:
        if release is None:
            raise SystemExit("--release-root is required for --check-import")
        check_import(release)
    if args.check_entrypoint:
        if release is None:
            raise SystemExit("--release-root is required for --check-entrypoint")
        check_entrypoint(release)
    head = ""
    if args.check_alembic_heads:
        if release is None or not values:
            raise SystemExit("--release-root and --env-file are required for Alembic checks")
        head = check_alembic_heads(release, values)
    if args.check_db:
        if not values:
            raise SystemExit("--env-file is required for --check-db")
        check_db(values)
    if args.migrate:
        if release is None or not values:
            raise SystemExit("--release-root and --env-file are required for --migrate")
        migrate(release, values)
        if head:
            check_single_alembic_version(values, head)
        else:
            head = check_alembic_heads(release, values)
            check_single_alembic_version(values, head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
