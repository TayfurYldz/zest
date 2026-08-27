from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.platform.zestd_cgroup_launcher import (
    CgroupLaunchError,
    delegated_cgroup_for_process,
    prepare_zestd_cgroup,
)
from zest.platform.browser_resource_control import (
    BrowserResourceLimits,
    LinuxCgroupV2ResourceController,
)


class ZestdCgroupLauncherTests(unittest.TestCase):
    def _fake_cgroup(self, *, members: str = "101\n") -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="zestd-cgroup-launcher-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "cgroup.controllers").write_text("memory pids cpu\n", encoding="utf-8")
        parent = root / "system.slice" / "zestd.service"
        parent.mkdir(parents=True)
        (parent / "cgroup.controllers").write_text("memory pids cpu\n", encoding="utf-8")
        (parent / "cgroup.subtree_control").write_text("\n", encoding="utf-8")
        (parent / "cgroup.procs").write_text(members, encoding="utf-8")
        sibling = parent.parent / "unrelated.scope"
        sibling.mkdir()
        (sibling / "cgroup.procs").write_text("202\n", encoding="utf-8")
        return root, parent, sibling

    @staticmethod
    def _creator(path: Path) -> None:
        path.mkdir()
        (path / "cgroup.controllers").write_text("memory pids cpu\n", encoding="utf-8")
        (path / "cgroup.subtree_control").write_text("\n", encoding="utf-8")
        (path / "cgroup.procs").write_text("\n", encoding="utf-8")
        (path / "memory.max").write_text("max\n", encoding="utf-8")
        (path / "pids.max").write_text("max\n", encoding="utf-8")

    def test_launcher_moves_daemon_child_and_enables_only_required_controllers(self) -> None:
        root, parent, sibling = self._fake_cgroup()

        def move(pid: int, destination: Path) -> None:
            for procs in (parent / "cgroup.procs", destination / "cgroup.procs"):
                members = {
                    item for item in procs.read_text(encoding="utf-8").split() if item != str(pid)
                }
                if procs == destination / "cgroup.procs":
                    members.add(str(pid))
                procs.write_text("\n".join(sorted(members)) + "\n", encoding="utf-8")

        _parent, child, browser_root = prepare_zestd_cgroup(
            parent,
            pid=101,
            child_name="zestd-daemon",
            child_creator=self._creator,
            move_process=move,
        )
        self.assertEqual(_parent, parent)
        self.assertEqual((parent / "cgroup.procs").read_text(encoding="utf-8").strip(), "")
        self.assertEqual((child / "cgroup.procs").read_text(encoding="utf-8").strip(), "101")
        self.assertEqual((browser_root / "cgroup.procs").read_text(encoding="utf-8").strip(), "")
        self.assertEqual(
            (parent / "cgroup.subtree_control").read_text(encoding="utf-8").split(),
            ["+memory", "+pids"],
        )
        self.assertEqual((sibling / "cgroup.procs").read_text(encoding="utf-8"), "202\n")
        controller = LinuxCgroupV2ResourceController(
            BrowserResourceLimits(max_memory_bytes=1024, max_tasks=4),
            cgroup_root=root,
            root_override=str(browser_root),
            child_factory=self._creator,
            access=lambda _path, _mode: True,
            child_name="zest-browser-test",
        )
        self.assertTrue(controller.readiness().ready)
        self.assertIsNone(controller.prepare())
        browser_child = controller.owned_cgroup
        self.assertIsNotNone(browser_child)
        self.assertEqual((browser_child / "memory.max").read_text(encoding="utf-8"), "1024")
        self.assertEqual((browser_child / "pids.max").read_text(encoding="utf-8"), "4")
        self.assertEqual((browser_root / "cgroup.procs").read_text(encoding="utf-8").strip(), "")
        self.assertTrue(root.is_dir())

    def test_launcher_keeps_parent_process_free_when_controllers_are_already_enabled(self) -> None:
        _root, parent, _sibling = self._fake_cgroup()
        (parent / "cgroup.subtree_control").write_text(
            "memory pids\n", encoding="utf-8"
        )

        def move(pid: int, destination: Path) -> None:
            for procs in (parent / "cgroup.procs", destination / "cgroup.procs"):
                members = {
                    item
                    for item in procs.read_text(encoding="utf-8").split()
                    if item != str(pid)
                }
                if procs == destination / "cgroup.procs":
                    members.add(str(pid))
                procs.write_text(
                    "\n".join(sorted(members)) + "\n",
                    encoding="utf-8",
                )

        _parent, child, browser_root = prepare_zestd_cgroup(
            parent,
            pid=101,
            child_name="zestd-daemon-restart",
            child_creator=self._creator,
            move_process=move,
        )

        self.assertEqual(
            (parent / "cgroup.procs").read_text(encoding="utf-8").strip(),
            "",
        )
        self.assertEqual(
            (child / "cgroup.procs").read_text(encoding="utf-8").strip(),
            "101",
        )
        self.assertEqual(
            (browser_root / "cgroup.procs").read_text(encoding="utf-8").strip(),
            "",
        )

    def test_launcher_refuses_parent_with_another_member_process(self) -> None:
        _root, parent, _sibling = self._fake_cgroup(members="101\n202\n")
        with self.assertRaisesRegex(CgroupLaunchError, "only the launcher process"):
            prepare_zestd_cgroup(
                parent,
                pid=101,
                child_creator=self._creator,
            )
        self.assertFalse((parent / "zestd-daemon-101").exists())

    def test_process_cgroup_parser_requires_a_non_root_v2_membership(self) -> None:
        root, _parent, _sibling = self._fake_cgroup()
        proc = root / "proc-self-cgroup"
        proc.write_text("0::/system.slice/zestd.service\n", encoding="utf-8")
        self.assertEqual(
            delegated_cgroup_for_process(cgroup_root=root, proc_cgroup_file=proc),
            root / "system.slice/zestd.service",
        )
        proc.write_text("1:name=systemd:/system.slice/zestd.service\n", encoding="utf-8")
        with self.assertRaisesRegex(CgroupLaunchError, "delegated cgroup-v2"):
            delegated_cgroup_for_process(cgroup_root=root, proc_cgroup_file=proc)


if __name__ == "__main__":
    unittest.main()
