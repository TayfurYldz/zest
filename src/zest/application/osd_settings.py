"""Operational zestd settings. Secrets stay in env, not in VCS.

Linux-native layout (Checkpoint 16 deploy assets; this module still only
reads env and documents paths):

    /opt/zest/releases/<immutable-release-id>/
    /opt/zest/current -> releases/<immutable-release-id>
    /etc/zest/zest.env
    /var/lib/zest/
    /var/log/zest/

Units live in deploy/systemd/. This module does not create directories,
install units, or bind publicly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from zest.application.runtime_instance import ENGINE_VERSION

LINUX_OPT_DIR = "/opt/zest"
LINUX_CONFIG_DIR = "/etc/zest"
LINUX_STATE_DIR = "/var/lib/zest"
LINUX_LOG_DIR = "/var/log/zest"
LINUX_ENV_FILE = "/etc/zest/zest.env"

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8766
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_LEASE_TTL_SECONDS = 90.0
DEFAULT_ENVIRONMENT_NAME = "local"
DATABASE_URL_ENV = "ZEST_DATABASE_URL"

BIND_HOST_ENV = "ZEST_BIND_HOST"
API_PORT_ENV = "ZEST_API_PORT"
HEARTBEAT_ENV = "ZEST_HEARTBEAT_INTERVAL_SECONDS"
LEASE_TTL_ENV = "ZEST_LEASE_TTL_SECONDS"
ENVIRONMENT_ENV = "ZEST_ENVIRONMENT_NAME"
LOG_PATH_ENV = "ZEST_LOG_PATH"
RELEASE_VERSION_ENV = "ZEST_RELEASE_VERSION"
MODEL_RUNTIME_REF_ENV = "ZEST_MODEL_RUNTIME_REF"
WORKER_RUNTIME_REF_ENV = "ZEST_WORKER_RUNTIME_REF"
ALEMBIC_INI_ENV = "ZEST_ALEMBIC_INI"


@dataclass(frozen=True)
class OsdSettings:
    """Daemon operational config. database_url is a reference, not a log field."""

    database_url: str
    bind_host: str = DEFAULT_BIND_HOST
    api_port: int = DEFAULT_API_PORT
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS
    environment_name: str = DEFAULT_ENVIRONMENT_NAME
    release_version: str = ENGINE_VERSION
    log_path: str | None = None
    model_runtime_ref: str | None = None
    worker_runtime_ref: str | None = None

    def public_mapping(self) -> dict[str, object]:
        return {
            "bind_host": self.bind_host,
            "api_port": self.api_port,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "environment_name": self.environment_name,
            "release_version": self.release_version,
            "log_path": self.log_path,
            "model_runtime_ref": self.model_runtime_ref,
            "worker_runtime_ref": self.worker_runtime_ref,
            "linux_paths": {
                "opt": LINUX_OPT_DIR,
                "config": LINUX_CONFIG_DIR,
                "state": LINUX_STATE_DIR,
                "log": LINUX_LOG_DIR,
                "env_file": LINUX_ENV_FILE,
            },
            "database_url_configured": bool(self.database_url),
        }


def load_osd_settings(env: Mapping[str, str]) -> OsdSettings:
    url = env.get(DATABASE_URL_ENV, "").strip()
    if not url:
        raise ValueError(f"{DATABASE_URL_ENV} is required")
    host = env.get(BIND_HOST_ENV, DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST
    port = int(env.get(API_PORT_ENV, str(DEFAULT_API_PORT)))
    heartbeat = float(env.get(HEARTBEAT_ENV, str(DEFAULT_HEARTBEAT_INTERVAL_SECONDS)))
    ttl = float(env.get(LEASE_TTL_ENV, str(DEFAULT_LEASE_TTL_SECONDS)))
    environment = env.get(ENVIRONMENT_ENV, DEFAULT_ENVIRONMENT_NAME).strip() or DEFAULT_ENVIRONMENT_NAME
    log_path = env.get(LOG_PATH_ENV, "").strip() or None
    release = env.get(RELEASE_VERSION_ENV, ENGINE_VERSION).strip() or ENGINE_VERSION
    model_ref = env.get(MODEL_RUNTIME_REF_ENV, "").strip() or None
    worker_ref = env.get(WORKER_RUNTIME_REF_ENV, "").strip() or None
    return OsdSettings(
        database_url=url,
        bind_host=host,
        api_port=port,
        heartbeat_interval_seconds=heartbeat,
        lease_ttl_seconds=ttl,
        environment_name=environment,
        release_version=release,
        log_path=log_path,
        model_runtime_ref=model_ref,
        worker_runtime_ref=worker_ref,
    )


def resolve_alembic_ini(
    env: Mapping[str, str],
    *,
    cwd: Path | None = None,
    source_file: Path | None = None,
) -> Path:
    """Locate alembic.ini for a source-tree release.

    Order: ZEST_ALEMBIC_INI, $PWD/alembic.ini (systemd
    WorkingDirectory=/opt/zest/current), then the repository root
    relative to this module when running from a checkout.

    Wheel-only installs without a release tree are not a supported
    migration layout. Do not invent a second Alembic config.
    """

    configured = env.get(ALEMBIC_INI_ENV, "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError(f"{ALEMBIC_INI_ENV} is set but is not a file")
        return path.resolve()
    working = (cwd or Path.cwd()) / "alembic.ini"
    if working.is_file():
        return working.resolve()
    origin = Path(source_file or __file__).resolve()
    fallback = origin.parents[3] / "alembic.ini"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        "alembic.ini not found; set ZEST_ALEMBIC_INI or run from a source release"
    )
