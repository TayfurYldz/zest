"""Place ``zestd`` in a delegated cgroup before it creates browser Workers.

systemd's ``Delegate=yes`` gives the service a writable cgroup subtree, but
cgroup v2 only permits enabling a controller in a parent with no member
processes.  The launcher moves itself into a daemon child first, enables only
the browser-required controllers in the now-empty delegated parent, creates a
separate empty browser delegation root below that parent, exports the browser
root to the daemon, and then replaces itself with the real ``zestd``.

This module is deliberately narrow: it never accepts a cgroup path from the
Worker or an operator request, never traverses outside the current v2 cgroup,
and never changes unrelated cgroups.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

CGROUP_V2_ROOT = Path("/sys/fs/cgroup")
CGROUP_PROC_FILE = Path("/proc/self/cgroup")
BROWSER_CGROUP_ROOT_ENV = "ZEST_BROWSER_CGROUP_ROOT"
REQUIRED_CONTROLLERS = ("memory", "pids")
DAEMON_CGROUP_PREFIX = "zestd-daemon"
BROWSER_ROOT_NAME = "zest-browser-root"


class CgroupLaunchError(RuntimeError):
    """The service is not running in a safe delegated cgroup topology."""


def _read_pids(path: Path) -> set[int]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CgroupLaunchError(f"cannot read {path.name}") from exc
    return {int(item) for item in text.split() if item.isdigit()}


def _read_tokens(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CgroupLaunchError(f"cannot read {path.name}") from exc
    return {item.lstrip("+-") for item in text.split()}


def delegated_cgroup_for_process(
    *,
    cgroup_root: Path = CGROUP_V2_ROOT,
    proc_cgroup_file: Path = CGROUP_PROC_FILE,
) -> Path:
    """Resolve only the current process's cgroup-v2 path under ``cgroup_root``."""

    try:
        lines = proc_cgroup_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CgroupLaunchError("cannot read the process cgroup membership") from exc
    matches = []
    for line in lines:
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            matches.append(fields[2].strip().lstrip("/"))
    if len(matches) != 1 or not matches[0]:
        raise CgroupLaunchError("the service is not in a delegated cgroup-v2 child")
    root = cgroup_root.resolve()
    parent = (root / matches[0]).resolve()
    if root not in parent.parents:
        raise CgroupLaunchError("the service cgroup resolved outside cgroup-v2 root")
    if not parent.is_dir():
        raise CgroupLaunchError("the delegated service cgroup does not exist")
    return parent


def _default_child_creator(path: Path) -> None:
    path.mkdir(mode=0o755)


def _move_process(pid: int, destination: Path) -> None:
    try:
        (destination / "cgroup.procs").write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        raise CgroupLaunchError(f"cannot move service process into {destination.name}") from exc


def prepare_zestd_cgroup(
    parent: Path,
    *,
    pid: int,
    child_name: str | None = None,
    browser_root_name: str = BROWSER_ROOT_NAME,
    child_creator: Callable[[Path], None] = _default_child_creator,
    move_process: Callable[[int, Path], None] = _move_process,
) -> tuple[Path, Path, Path]:
    """Prepare daemon and browser siblings inside the delegated service cgroup.

    The caller is always moved into a daemon child so the delegated service
    parent remains process-free regardless of whether the required controllers
    were already enabled by an earlier lifecycle.  A separate empty browser
    root is returned; the browser controller owns disposable children below it.
    """

    if pid < 1:
        raise CgroupLaunchError("service process id is invalid")
    controllers = parent / "cgroup.controllers"
    subtree = parent / "cgroup.subtree_control"
    parent_procs = parent / "cgroup.procs"
    if not parent.is_dir() or not controllers.is_file() or not subtree.is_file() or not parent_procs.is_file():
        raise CgroupLaunchError("delegated cgroup-v2 control files are incomplete")
    if not set(REQUIRED_CONTROLLERS).issubset(_read_tokens(controllers)):
        raise CgroupLaunchError("delegated cgroup lacks memory/pids controllers")
    if _read_pids(parent_procs) != {pid}:
        raise CgroupLaunchError("delegated parent must contain only the launcher process")

    missing = [item for item in REQUIRED_CONTROLLERS if item not in _read_tokens(subtree)]
    if missing and not os.access(subtree, os.W_OK):
        raise CgroupLaunchError("delegated parent subtree_control is not writable")
    if not os.access(parent, os.W_OK):
        raise CgroupLaunchError("delegated parent cgroup is not writable")

    name = child_name or f"{DAEMON_CGROUP_PREFIX}-{pid}"
    if not name or name in {".", ".."} or "/" in name:
        raise CgroupLaunchError("daemon cgroup name is invalid")
    if not browser_root_name or browser_root_name in {".", ".."} or "/" in browser_root_name:
        raise CgroupLaunchError("browser cgroup root name is invalid")
    child = parent / name
    browser_root = parent / browser_root_name
    if child.exists():
        child_procs = child / "cgroup.procs"
        if not child.is_dir() or not child_procs.is_file():
            raise CgroupLaunchError("daemon cgroup is not usable")
        if _read_pids(child_procs):
            raise CgroupLaunchError("daemon cgroup already has member processes")
    if browser_root.exists() and browser_root.resolve().parent != parent.resolve():
        raise CgroupLaunchError("browser cgroup root is outside the delegated service cgroup")

    moved = False
    created_child = False
    created_browser_root = False
    try:
        if not child.exists():
            child_creator(child)
            created_child = True

        child_procs = child / "cgroup.procs"
        if not child_procs.is_file():
            raise CgroupLaunchError("daemon cgroup did not expose cgroup.procs")

        move_process(pid, child)
        moved = True

        if pid not in _read_pids(child_procs) or _read_pids(parent_procs):
            raise CgroupLaunchError("daemon process was not isolated from delegated parent")

        if missing:
            try:
                subtree.write_text(
                    " ".join(f"+{item}" for item in missing),
                    encoding="utf-8",
                )
            except OSError as exc:
                raise CgroupLaunchError("cannot enable browser cgroup controllers") from exc

            enabled = _read_tokens(subtree)
            if not set(REQUIRED_CONTROLLERS).issubset(enabled):
                raise CgroupLaunchError("browser cgroup controllers were not enabled")

        if not browser_root.exists():
            child_creator(browser_root)
            created_browser_root = True

        browser_procs = browser_root / "cgroup.procs"
        if not browser_procs.is_file() or _read_pids(browser_procs):
            raise CgroupLaunchError("browser cgroup root must be empty and usable")

        return parent, child, browser_root
    except Exception:
        if moved:
            try:
                move_process(pid, parent)
            except Exception:
                pass
        if created_browser_root:
            try:
                browser_root.rmdir()
            except OSError:
                pass
        if created_child:
            try:
                child.rmdir()
            except OSError:
                pass
        raise


def launch_zestd(command: list[str]) -> None:
    """Prepare the service topology and replace this process with ``zestd``."""

    if not command or not os.path.isabs(command[0]):
        raise CgroupLaunchError("zestd command must use an absolute executable path")
    parent = delegated_cgroup_for_process()
    _parent, _daemon_child, browser_root = prepare_zestd_cgroup(parent, pid=os.getpid())
    environment = os.environ.copy()
    environment[BROWSER_CGROUP_ROOT_ENV] = str(browser_root)
    os.execvpe(command[0], command, environment)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        separator = values.index("--")
    except ValueError:
        print("zestd cgroup launcher requires -- <absolute zestd command>", file=sys.stderr)
        return 78
    try:
        launch_zestd(values[separator + 1 :])
    except CgroupLaunchError as exc:
        print(f"zestd cgroup launcher: {exc}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
