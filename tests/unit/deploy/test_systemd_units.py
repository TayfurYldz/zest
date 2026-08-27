from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_UNIT = Path(__file__).resolve().parents[1]
if str(_UNIT) not in sys.path:
    sys.path.insert(0, str(_UNIT))

import pathsetup  # noqa: F401

from zest.application.osd_settings import LINUX_ENV_FILE, resolve_alembic_ini
from zest.interface.zestd import _alembic_ini

REPO = Path(__file__).resolve().parents[3]
OSD_UNIT = REPO / "deploy/systemd/zestd.service"
DASHBOARD_UNIT = REPO / "deploy/systemd/zest-dashboard.service"
ENV_EXAMPLE = REPO / "config/zestd.env.example"
PYPROJECT = REPO / "pyproject.toml"
INSTALL_SH = REPO / "scripts/install_zest_release.sh"
VERIFY_PY = REPO / "scripts/verify_zest_release.py"
MATURITY = REPO / "src/zest/maturity.py"


def _active_unit_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


class SystemdUnitTests(unittest.TestCase):
    def test_osd_unit_uses_real_entrypoint_and_local_env(self) -> None:
        text = OSD_UNIT.read_text(encoding="utf-8")
        active = _active_unit_lines(text)
        self.assertIn("User=zest", active)
        self.assertIn("Group=zest", active)
        self.assertIn("WorkingDirectory=/opt/zest/current", active)
        self.assertIn(f"EnvironmentFile={LINUX_ENV_FILE}", active)
        self.assertIn(
            "ExecStart=/opt/zest/current/.venv/bin/python -m zest.platform.zestd_cgroup_launcher -- /opt/zest/current/.venv/bin/zestd",
            active,
        )
        self.assertIn("Restart=on-failure", active)
        self.assertIn("RestartSec=5", active)
        self.assertIn("TimeoutStopSec=30", active)
        self.assertIn("KillSignal=SIGTERM", active)
        self.assertIn("After=network-online.target", active)
        self.assertIn("postgresql", active)
        self.assertNotIn("Requires=postgresql", active)
        self.assertIn("Delegate=yes", active)
        self.assertIn("NoNewPrivileges=true", active)
        self.assertIn("PrivateTmp=true", active)
        self.assertIn("ProtectHome=true", active)
        self.assertIn("UMask=0027", active)
        self.assertNotIn("0.0.0.0", active)
        self.assertNotIn("/home/tayfur", text)
        self.assertNotIn("ProtectControlGroups=true", active)
        self.assertNotIn("MemoryDenyWriteExecute=true", active)
        self.assertNotIn("PrivateDevices=true", active)
        self.assertNotIn("ProtectKernelTunables=true", active)
        self.assertNotIn("password", text.lower())
        self.assertNotIn("ZEST_DATABASE_URL=", active)

    def test_dashboard_unit_is_local_client_only(self) -> None:
        text = DASHBOARD_UNIT.read_text(encoding="utf-8")
        self.assertIn("User=zest", text)
        self.assertIn(f"EnvironmentFile={LINUX_ENV_FILE}", text)
        self.assertIn("zest-dashboard --host 127.0.0.1 --port 8765", text)
        self.assertIn("Wants=zestd.service", text)
        self.assertNotIn("Requires=zestd.service", text)
        self.assertNotIn("0.0.0.0", text)
        self.assertNotIn("/home/tayfur", text)


class PackagingAndEnvTests(unittest.TestCase):
    def test_pyproject_scripts_and_python_floor(self) -> None:
        text = PYPROJECT.read_text(encoding="utf-8")
        self.assertIn('zestd = "zest.interface.zestd:main"', text)
        self.assertIn('zest-dashboard = "zest.interface.dashboard:main"', text)
        self.assertIn('requires-python = ">=3.11"', text)
        self.assertIn('"/alembic.ini"', text)

    def test_env_example_is_secret_free_and_loopback(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("ZEST_BIND_HOST=127.0.0.1", text)
        self.assertIn("ZEST_URL=http://127.0.0.1:8766", text)
        self.assertIn("/etc/zest/zest.env", text)
        self.assertNotIn("ZEST_BIND_HOST=0.0.0.0", text)
        self.assertNotIn("/home/tayfur", text)
        self.assertNotIn("password=", text.lower())
        self.assertNotIn("api_key=", text.lower())

    def test_install_script_is_fail_fast_and_local(self) -> None:
        text = INSTALL_SH.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn("Python >=", text)
        self.assertNotIn("curl |", text)
        self.assertNotIn("/home/tayfur", text)

    def test_install_script_verifies_before_updating_current_symlink(self) -> None:
        text = INSTALL_SH.read_text(encoding="utf-8")
        verify_pos = text.find('verify_zest_release.py" "${VERIFY_ARGS[@]}"')
        chown_pos = text.find('chown -R "root:${SERVICE_GROUP}" "$RELEASE_DIR"')
        link_pos = text.find('ln -sfn "releases/${RELEASE_ID}" "$CURRENT_LINK"')
        self.assertNotEqual(verify_pos, -1, "release verification step not found")
        self.assertNotEqual(chown_pos, -1, "release ownership step not found")
        self.assertNotEqual(link_pos, -1, "current symlink step not found")
        self.assertLess(
            verify_pos,
            link_pos,
            "verification must execute BEFORE updating current symlink",
        )
        self.assertLess(
            chown_pos,
            link_pos,
            "release ownership must be finalized BEFORE updating current symlink",
        )
        self.assertIn('"${RELEASE_DIR}/.venv/bin/python"', text)
        self.assertIn('--release-root "$RELEASE_DIR"', text)

    def test_install_script_bootstrap_order_uses_release_venv_for_project_checks(self) -> None:
        text = INSTALL_SH.read_text(encoding="utf-8")
        source_verify_pos = text.find('"$PYTHON" "$VERIFY_PY" --assets-root "$SOURCE" --env-file "$ENV_FILE"')
        mkdir_pos = text.find('mkdir -p "$RELEASE_DIR"')
        venv_pos = text.find('"$PYTHON" -m venv "${RELEASE_DIR}/.venv"')
        install_pos = text.find('python -m pip install "${RELEASE_DIR}"')
        release_verify_pos = text.find('"${RELEASE_DIR}/.venv/bin/python" "${RELEASE_DIR}/scripts/verify_zest_release.py"')
        link_pos = text.find('ln -sfn "releases/${RELEASE_ID}" "$CURRENT_LINK"')
        for label, pos in {
            "source validation": source_verify_pos,
            "release directory": mkdir_pos,
            "venv": venv_pos,
            "project install": install_pos,
            "release verifier": release_verify_pos,
            "current link": link_pos,
        }.items():
            self.assertNotEqual(pos, -1, f"{label} step not found")
        self.assertLess(source_verify_pos, mkdir_pos)
        self.assertLess(mkdir_pos, venv_pos)
        self.assertLess(venv_pos, install_pos)
        self.assertLess(install_pos, release_verify_pos)
        self.assertLess(release_verify_pos, link_pos)
        pre_venv = text[source_verify_pos:venv_pos]
        self.assertNotIn("--check-import", pre_venv)
        self.assertNotIn("--check-entrypoint", pre_venv)
        self.assertNotIn("--check-alembic-heads", pre_venv)
        self.assertNotIn("--check-db", pre_venv)

    def test_verify_script_rejects_sqlite_and_public_bind(self) -> None:
        source = VERIFY_PY.read_text(encoding="utf-8")
        self.assertIn("SQLite is not an allowed SoR", source)
        self.assertIn("0.0.0.0", source)
        tree = ast.parse(source)
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "assert_env_file" for node in tree.body))

    def test_verify_script_has_no_top_level_zest_imports(self) -> None:
        source = VERIFY_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name.startswith("zest"),
                        f"top-level import of {alias.name} breaks stdlib bootstrap",
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.assertFalse(
                        node.module.startswith("zest"),
                        f"top-level import from {node.module} breaks stdlib bootstrap",
                    )

    def test_collector_permissions_and_secrets_are_truthful(self) -> None:
        collector = (REPO / "scripts/vds_checkpoint16_collect.sh").read_text(encoding="utf-8")
        self.assertIn("etc_writable=denied", collector)
        self.assertIn("release_writable=denied", collector)
        self.assertIn("state_writable=ok", collector)
        self.assertIn("log_writable=ok", collector)
        self.assertIn("secret_scan=none", collector)
        self.assertNotIn("safe sudo -u zest test -w", collector)

    def test_collector_safe_preserves_command_status(self) -> None:
        collector = (REPO / "scripts/vds_checkpoint16_collect.sh").read_text(encoding="utf-8")
        match = re.search(r"safe\(\) \{\n(?P<body>.*?)\n\}", collector, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertNotIn("|| true", match.group("body"))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "collector-safe.txt"
            probe = (
                "set -euo pipefail\n"
                f"OUT={out}\n"
                f"safe() {{\n{match.group('body')}\n}}\n"
                "safe true\n"
                "ok=$?\n"
                "if safe false; then bad=0; else bad=$?; fi\n"
                'printf "ok=%s bad=%s\\n" "$ok" "$bad"\n'
            )
            completed = subprocess.run(
                ["bash", "-c", probe],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok=0 bad=1")


class AlembicResolutionTests(unittest.TestCase):
    def test_cwd_release_tree_wins_over_missing_env(self) -> None:
        path = resolve_alembic_ini({}, cwd=REPO)
        self.assertEqual(path, (REPO / "alembic.ini").resolve())

    def test_entrypoint_helper_resolves_repo_ini(self) -> None:
        self.assertTrue(Path(_alembic_ini()).is_file())


class DashboardBindTests(unittest.TestCase):
    def test_dashboard_rejects_public_bind(self) -> None:
        from zest.interface.dashboard import main

        self.assertEqual(main(["--host", "0.0.0.0", "--port", "8765"]), 2)


class MaturityUnchangedTests(unittest.TestCase):
    def test_checkpoint16_does_not_promote_maturity(self) -> None:
        text = MATURITY.read_text(encoding="utf-8")
        self.assertNotIn("SYSTEMD_STAGING_READY", text)
        self.assertNotIn("MACHINE_REBOOT_QUALIFIED", text)
        self.assertIn("SECURITY_RESEARCH_VALIDATED = False", text)
        self.assertIn("PRODUCTION_READY = False", text)


if __name__ == "__main__":
    unittest.main()
