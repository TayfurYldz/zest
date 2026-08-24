from __future__ import annotations

import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from research_os.platform.contract_validation import ContractValidator
from research_os.platform.package_resources import contract_schema_documents, iter_packaged_scenario_json
from research_os.source_export import export_source_archive, find_source_root, iter_export_paths


REQUIRED_OPERATIONAL_EXPORT_PATHS = frozenset(
    {
        "deploy/systemd/research-osd.service",
        "deploy/systemd/research-os-dashboard.service",
        "deploy/logrotate/research-os",
        "scripts/install_research_os_release.sh",
        "scripts/vds_checkpoint16_collect.sh",
        "scripts/vds_checkpoint16_fixture.py",
        "scripts/verify_research_os_release.py",
        "src/research_os/qualification/j11_fencing.py",
        "src/research_os/qualification/staging_spine.py",
    }
)


class PackagedResourceTests(unittest.TestCase):
    def test_contract_validator_uses_packaged_schemas(self) -> None:
        validator = ContractValidator()
        schemas = contract_schema_documents()
        self.assertIn("urn:research-os:contracts:v1:worker-request", schemas)
        self.assertGreaterEqual(len(schemas), 2)

    def test_development_scenarios_are_packaged(self) -> None:
        names = [name for name, _ in iter_packaged_scenario_json()]
        self.assertTrue(any(name.endswith(".json") for name in names))
        self.assertFalse(any("holdout" in name.lower() and "sealed" in name.lower() for name in names))

    def test_source_export_excludes_git_and_venv(self) -> None:
        root = find_source_root()
        with tempfile.TemporaryDirectory() as tmp:
            archive, manifest = export_source_archive(Path(tmp) / "source.tar.gz", root=root)
            text = manifest.read_text(encoding="utf-8")
            self.assertNotIn("  .git/", text)
            self.assertNotIn("  .venv/", text)
            self.assertNotIn("  dist/", text)
            self.assertNotIn("  __pycache__/", text)
            self.assertNotIn("  .pytest_cache/", text)
            self.assertTrue(archive.is_file())
            self.assertIn("src/research_os/", text)

    def test_untracked_shell_scripts_are_source_export_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "scripts" / "vds_checkpoint16_collect.sh"
            script.parent.mkdir()
            script.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
            selected = iter_export_paths(
                root,
                include_untracked_source=True,
                tracked_files=[],
            )
        self.assertIn(script.resolve(), selected)

    def test_untracked_deploy_assets_are_source_export_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            osd = root / "deploy" / "systemd" / "research-osd.service"
            dashboard = root / "deploy" / "systemd" / "research-os-dashboard.service"
            logrotate = root / "deploy" / "logrotate" / "research-os"
            osd.parent.mkdir(parents=True)
            logrotate.parent.mkdir(parents=True)
            osd.write_text("[Service]\nExecStart=/opt/research-os/current/.venv/bin/research-osd\n", encoding="utf-8")
            dashboard.write_text("[Service]\nExecStart=/opt/research-os/current/.venv/bin/research-os-dashboard\n", encoding="utf-8")
            logrotate.write_text("/var/log/research-os/*.log {}\n", encoding="utf-8")
            selected = iter_export_paths(
                root,
                include_untracked_source=True,
                tracked_files=[],
            )
        self.assertIn(osd.resolve(), selected)
        self.assertIn(dashboard.resolve(), selected)
        self.assertIn(logrotate.resolve(), selected)

    def test_source_export_excludes_claude_and_agent_memory_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_memory = root / ".claude" / "agent-memory-local" / "brain" / "targets" / "target.md"
            standalone_session = root / "agent-memory-local" / "brain" / "sessions" / "session.md"
            source = root / "src" / "research_os" / "__init__.py"
            local_memory.parent.mkdir(parents=True)
            standalone_session.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            local_memory.write_text("local target memory\n", encoding="utf-8")
            standalone_session.write_text("local session memory\n", encoding="utf-8")
            source.write_text("", encoding="utf-8")
            selected = iter_export_paths(
                root,
                include_untracked_source=True,
                tracked_files=[],
            )
        exported = {path.relative_to(root).as_posix() for path in selected}
        self.assertNotIn(".claude/agent-memory-local/brain/targets/target.md", exported)
        self.assertNotIn("agent-memory-local/brain/sessions/session.md", exported)
        self.assertIn("src/research_os/__init__.py", exported)

    def test_checkpoint16_artifact_contains_installer_operational_assets(self) -> None:
        root = find_source_root()
        with tempfile.TemporaryDirectory() as tmp:
            archive, manifest = export_source_archive(
                Path(tmp) / "source.tar.gz",
                root=root,
                include_untracked_source=True,
            )
            manifest_paths = {
                line.split("  ", 1)[1]
                for line in manifest.read_text(encoding="utf-8").splitlines()
                if "  " in line
            }
            with tarfile.open(archive, "r:gz") as tar:
                tar_paths = set(tar.getnames())
        self.assertTrue(REQUIRED_OPERATIONAL_EXPORT_PATHS.issubset(manifest_paths))
        self.assertTrue(REQUIRED_OPERATIONAL_EXPORT_PATHS.issubset(tar_paths))
        forbidden_prefixes = (".claude/", "agent-memory-local/", "dist/")
        for path in tar_paths:
            self.assertFalse(path.startswith(forbidden_prefixes), path)

    def test_installer_referenced_optional_assets_are_exported(self) -> None:
        root = find_source_root()
        installer = (root / "scripts" / "install_research_os_release.sh").read_text(encoding="utf-8")
        referenced = {
            "deploy/systemd/research-osd.service",
            "deploy/systemd/research-os-dashboard.service",
            "deploy/logrotate/research-os",
        }
        for path in referenced:
            self.assertIn(path, installer)
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest = export_source_archive(
                Path(tmp) / "source.tar.gz",
                root=root,
                include_untracked_source=True,
            )
            text = manifest.read_text(encoding="utf-8")
        for path in referenced:
            self.assertIn(f"  {path}", text)

    def test_source_export_archive_bytes_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            root = workspace / "root"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'deterministic'\n", encoding="utf-8")
            package = root / "src" / "research_os"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            script = root / "scripts" / "install_research_os_release.sh"
            script.parent.mkdir()
            script.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
            first, first_manifest = export_source_archive(
                workspace / "first.tar.gz",
                root=root,
                include_untracked_source=True,
            )
            second, second_manifest = export_source_archive(
                workspace / "second.tar.gz",
                root=root,
                include_untracked_source=True,
            )
            first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
            second_digest = hashlib.sha256(second.read_bytes()).hexdigest()
            first_manifest_text = first_manifest.read_text(encoding="utf-8")
            second_manifest_text = second_manifest.read_text(encoding="utf-8")
        self.assertEqual(first_manifest_text, second_manifest_text)
        self.assertEqual(first_digest, second_digest)


if __name__ == "__main__":
    unittest.main()
