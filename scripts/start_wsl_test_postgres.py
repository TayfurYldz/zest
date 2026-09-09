"""Start the isolated WSL user-space PostgreSQL used by GATE 01 tests.

Not architecture. Not a production database. Strips CRLF so the bash
helper can run from a Windows checkout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("start_wsl_test_postgres.sh")


def main() -> int:
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    command = "cat > /tmp/start_wsl_test_postgres.sh && chmod +x /tmp/start_wsl_test_postgres.sh && /tmp/start_wsl_test_postgres.sh"
    extra = sys.argv[1:]
    if extra:
        command += " " + " ".join(extra)
    completed = subprocess.run(
        ["wsl", "-e", "bash", "-lc", command],
        input=text.encode("utf-8"),
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
