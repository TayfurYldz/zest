from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_UNIT = Path(__file__).resolve().parents[1]
if str(_UNIT) not in sys.path:
    sys.path.insert(0, str(_UNIT))

import pathsetup  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "scripts/vds_checkpoint16_fixture.py"
COLLECTOR = REPO / "scripts/vds_checkpoint16_collect.sh"
RESEARCH_OSD = REPO / "src/research_os/interface/research_osd.py"
OPERATOR_API = REPO / "src/research_os/interface/operator_api.py"


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    env.pop("RESEARCH_OSD_ALLOW_SPINE_TRUNCATE", None)
    env.pop("RESEARCH_OS_DATABASE_URL", None)
    return env


class VdsFixtureSourceTests(unittest.TestCase):
    def test_fixture_imports_shipped_qualification_only(self) -> None:
        source = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("integration.harness", source)
        self.assertNotIn("from integration", source)
        self.assertNotIn("sys.path", source)
        self.assertIn("research_os.qualification.staging_spine", source)
        self.assertIn("research_os.qualification.j11_fencing", source)
        self.assertIn("require_explicit_spine_truncate", source)
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".", 1)[0])
        self.assertNotIn("integration", imported)
        self.assertIn("research_os", imported)

    def test_runtime_entrypoint_does_not_import_qualification(self) -> None:
        osd = RESEARCH_OSD.read_text(encoding="utf-8")
        api = OPERATOR_API.read_text(encoding="utf-8")
        self.assertNotIn("research_os.qualification", osd)
        self.assertNotIn("research_os.qualification", api)

    def test_collector_does_not_hack_pythonpath_or_tests_tree(self) -> None:
        source = COLLECTOR.read_text(encoding="utf-8")
        self.assertNotIn("PYTHONPATH", source)
        self.assertNotIn("tests/integration", source)
        self.assertIn("vds_checkpoint16_fixture.py --help", source)
        completed = subprocess.run(
            ["bash", "-n", str(COLLECTOR)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_j8_j10_fixture_actions_are_unchanged(self) -> None:
        source = FIXTURE.read_text(encoding="utf-8")
        self.assertIn('state="AUTHORIZED", side_effect_level=0', source)
        self.assertIn('state="DISPATCHING", side_effect_level=1', source)
        self.assertIn('state="UNKNOWN_OUTCOME", side_effect_level=2', source)
        self.assertIn("ClassifyRuntimeRecovery", source)
        self.assertIn("Does not auto-retry DISPATCHING", source)
        self.assertIn("research_orchestrations.insert", source)
        self.assertIn("OrchestrationState.RUNNING.value", source)
        self.assertIn('"j11-prepare"', source)
        self.assertIn('"j11-race"', source)
        self.assertIn('"j11-stale-proof"', source)
        self.assertIn('"j11-cleanup"', source)
        self.assertIn("run_two_process_owner_race", source)
        self.assertIn("cleanup_j11_owner", source)

    def test_fixture_does_not_create_orchestration_via_checkpoint_save(self) -> None:
        source = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("if existing is None:", source)
        none_block = source.split("if existing is None:", 1)[1].split("if existing.state", 1)[0]
        self.assertIn(".insert(", none_block)
        self.assertNotIn(".save(", none_block)

    def test_fixture_has_no_secret_literals(self) -> None:
        source = FIXTURE.read_text(encoding="utf-8")
        lowered = source.lower()
        self.assertNotIn("password=", lowered)
        self.assertNotIn("api_key=", lowered)
        self.assertNotIn("postgresql+psycopg://", lowered)


class VdsFixtureReleaseImportTests(unittest.TestCase):
    def test_help_and_fail_closed_from_installed_wheel_without_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            dist = work / "dist"
            dist.mkdir()
            isolated = work / "isolated"
            isolated.mkdir()
            venv = work / "venv"
            create = subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            python = venv / "bin" / "python"
            pip = venv / "bin" / "pip"
            wheel = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--wheel-dir",
                    str(dist),
                    str(REPO),
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(work),
            )
            self.assertEqual(wheel.returncode, 0, wheel.stderr)
            wheels = sorted(
                path
                for path in dist.glob("*.whl")
                if path.name.startswith("research_os-")
            )
            self.assertEqual(len(wheels), 1, list(dist.iterdir()))
            install = subprocess.run(
                [
                    str(pip),
                    "install",
                    "--find-links",
                    str(dist),
                    str(wheels[0]),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            fixture_copy = isolated / "vds_checkpoint16_fixture.py"
            fixture_copy.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            env = _subprocess_env()
            help_cmd = subprocess.run(
                [str(python), "-I", str(fixture_copy), "--help"],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertEqual(help_cmd.returncode, 0, help_cmd.stderr)
            self.assertIn("seed", help_cmd.stdout)
            self.assertNotIn("Traceback", help_cmd.stderr)

            denied = subprocess.run(
                [str(python), "-I", str(fixture_copy), "seed"],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("--truncate", denied.stderr)
            self.assertNotIn("Traceback", denied.stderr)
            self.assertNotIn("database=", denied.stdout)

            no_guard = subprocess.run(
                [str(python), "-I", str(fixture_copy), "seed", "--truncate"],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertNotEqual(no_guard.returncode, 0)
            self.assertIn("RESEARCH_OSD_ALLOW_SPINE_TRUNCATE=YES", no_guard.stderr)
            self.assertNotIn("database=", no_guard.stdout)

            missing = subprocess.run(
                [str(python), "-I", "-c", "import integration"],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("No module named 'integration'", missing.stderr)

            shipped = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "from research_os.qualification.staging_spine import "
                    "seed_authorized_spine, truncate_spine; print('import=ok')",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertEqual(shipped.returncode, 0, shipped.stderr)
            self.assertEqual(shipped.stdout.strip(), "import=ok")

            j11 = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "from research_os.qualification.j11_fencing import "
                    "cleanup_j11_owner, prepare_j11_run, "
                    "run_two_process_owner_race; print('j11=ok')",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertEqual(j11.returncode, 0, j11.stderr)
            self.assertEqual(j11.stdout.strip(), "j11=ok")

            tests_probe = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import research_os, pathlib; "
                    "root=pathlib.Path(research_os.__file__).resolve().parent; "
                    "print(root); "
                    "print((root.parent / 'tests').exists())",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(isolated),
                env=env,
            )
            self.assertEqual(tests_probe.returncode, 0, tests_probe.stderr)
            self.assertIn("False", tests_probe.stdout.splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
