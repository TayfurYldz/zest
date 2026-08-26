"""Clean-install smoke. Mandatory for final GATE 13 PASS. Does not fabricate PASS."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=str(cwd) if cwd else None, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _has_module(python: str, module: str) -> bool:
    probe = subprocess.run(
        [python, "-c", f"import {module}"],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


def build_wheel(python: str, dist: Path) -> None:
    if _has_module(python, "build"):
        run([python, "-m", "build", "--wheel", "--outdir", str(dist)], cwd=ROOT)
        return
    uv = shutil.which("uv")
    if uv:
        run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=ROOT)
        return
    print(
        "VALIDATION_PENDING: neither python -m build nor uv build is available",
        file=sys.stderr,
    )
    raise SystemExit(3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build wheel, install into empty venv, smoke from empty CWD")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="zest-clean-install-") as tmp:
        tmp_path = Path(tmp)
        dist = tmp_path / "dist"
        venv = tmp_path / "venv"
        empty = tmp_path / "empty-cwd"
        dist.mkdir()
        empty.mkdir()
        build_wheel(args.python, dist)
        wheels = sorted(dist.glob("zest-*.whl")) + sorted(dist.glob("zest-*.whl".replace("_", "-")))
        wheels = list(dist.glob("*.whl"))
        if not wheels:
            print("wheel build produced no artifacts", file=sys.stderr)
            return 2
        wheel = wheels[0]
        run([args.python, "-m", "venv", str(venv)])
        python = venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"
        run([str(python), "-m", "pip", "install", str(wheel)])
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        probe = r"""
import sys
from pathlib import Path
repo = Path(r"%s")
assert str(repo) not in sys.path, sys.path
assert str(repo / "src") not in sys.path, sys.path
from zest.platform.contract_validation import ContractValidator
ContractValidator()
from zest.benchmark.runner import load_cli_scenarios
load_cli_scenarios(None)
from zest.integrations.models.discovery import discover_configured_runtimes
discover_configured_runtimes(env={})
from zest.platform.worker_health import probe_local_python_worker
from zest.platform.health import ComponentHealth
check = probe_local_python_worker()
print("worker", check.health.value)
print("ok")
""" % str(ROOT).replace("\\", "\\\\")
        probe_path = empty / "probe.py"
        probe_path.write_text(probe, encoding="utf-8")
        run([str(python), str(probe_path)], cwd=empty, env=env)
        zest = venv / "Scripts" / "zest.exe" if os.name == "nt" else venv / "bin" / "zest"
        run([str(zest), "status"], cwd=empty, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
