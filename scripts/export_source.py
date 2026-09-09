"""Export tracked project source without local secrets, .git, or .venv."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zest.source_export import export_source_archive, find_source_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Zest source export")
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-untracked-source", action="store_true")
    args = parser.parse_args(argv)
    archive, manifest = export_source_archive(
        args.output,
        root=find_source_root(ROOT),
        include_untracked_source=args.include_untracked_source,
    )
    print(archive)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
