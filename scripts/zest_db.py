"""Database operational helpers. Not a DBA product. SQLite is not a substitute."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from zest.data.postgres.engine import (
    DATABASE_URL_ENV,
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    ping_database,
    redacted_database_url,
)


def _url(*, testing: bool) -> str:
    name = TEST_DATABASE_URL_ENV if testing else DATABASE_URL_ENV
    url = os.environ.get(name)
    if not url or not url.strip():
        raise SystemExit(f"{name} is required")
    return url.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zest-db")
    parser.add_argument("command", choices=("migrate", "version", "ping"))
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args(argv)
    url = _url(testing=args.test)
    print(f"target={redacted_database_url(url)}", flush=True)
    if args.command == "migrate":
        cfg = Config(str(_REPO / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        return 0
    engine = create_sync_engine(url)
    try:
        if args.command == "ping":
            ping_database(engine)
            print("HEALTHY")
            return 0
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        print(version)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
