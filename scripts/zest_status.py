"""Operator status entrypoint. Does not print secrets."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from zest.interface.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
