"""Containment handshake. Chromium must not exist before the supervising parent
confirms kernel-enforced resource containment.

The Worker announces itself, then blocks. The parent places the Worker pid under
a kernel resource boundary and only then sends the acknowledgement. Because every
Chromium process is a descendant of this Worker, refusing engine creation until
the acknowledgement arrives removes the window in which an uncontained browser
tree could exist.

This is local Platform/Worker transport. It is not authorization and it carries
no scope, identity, or budget authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

HELLO_MESSAGE_TYPE = "hello"
CONTAINMENT_MESSAGE_TYPE = "containment_ready"
BROWSER_WORKER_PROTOCOL = "browser.worker.v2"
CONTAINMENT_NOT_ESTABLISHED = "CONTAINMENT_NOT_ESTABLISHED"

PID_CEILING_TASKS = "tasks"
PID_CEILING_PROCESSES = "processes"
PID_CEILING_KINDS = frozenset({PID_CEILING_TASKS, PID_CEILING_PROCESSES})

MECHANISM_LINUX_CGROUP_V2 = "linux.cgroup2"
MECHANISM_WINDOWS_JOB_OBJECT = "windows.jobobject"
MECHANISM_PID_KIND = {
    MECHANISM_LINUX_CGROUP_V2: PID_CEILING_TASKS,
    MECHANISM_WINDOWS_JOB_OBJECT: PID_CEILING_PROCESSES,
}


@dataclass(frozen=True)
class ContainmentAck:
    mechanism: str
    max_memory_bytes: int
    pid_ceiling_kind: str
    pid_ceiling_value: int


_ACK: ContainmentAck | None = None


def hello_document(pid: int) -> dict[str, Any]:
    return {
        "message_type": HELLO_MESSAGE_TYPE,
        "protocol": BROWSER_WORKER_PROTOCOL,
        "pid": pid,
    }


def accept_containment(
    message: Mapping[str, Any],
    *,
    max_memory_bytes: int,
    max_descendant_processes: int,
    max_descendant_tasks: int,
) -> tuple[ContainmentAck | None, str | None]:
    """Validate the parent acknowledgement against the Worker's declared limits.

    The parent may enforce a tighter ceiling than the Worker declares. It may
    never enforce a wider one, because the declared limit would then be a claim
    the kernel does not back. The PID ceiling kind must match the mechanism:
    Linux cgroup v2 enforces tasks, Windows Job Objects enforce processes.
    """

    if message.get("message_type") != CONTAINMENT_MESSAGE_TYPE:
        return None, "the first message must be the containment acknowledgement"
    if message.get("protocol") != BROWSER_WORKER_PROTOCOL:
        return None, "containment acknowledgement protocol mismatch"
    mechanism = message.get("mechanism")
    memory = message.get("max_memory_bytes")
    kind = message.get("pid_ceiling_kind")
    value = message.get("pid_ceiling_value")
    if not isinstance(mechanism, str) or not mechanism.strip():
        return None, "containment acknowledgement has no enforcement mechanism"
    if not isinstance(memory, int) or isinstance(memory, bool) or memory < 1:
        return None, "containment acknowledgement has no enforced memory ceiling"
    if not isinstance(kind, str) or kind not in PID_CEILING_KINDS:
        return None, "containment acknowledgement has an unknown pid ceiling kind"
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return None, "containment acknowledgement has no enforced pid ceiling"
    expected_kind = MECHANISM_PID_KIND.get(mechanism)
    if expected_kind is None:
        return None, "containment acknowledgement has an unknown enforcement mechanism"
    if kind != expected_kind:
        return None, "containment acknowledgement mechanism and pid ceiling kind do not match"
    if memory > max_memory_bytes:
        return None, "the enforced memory ceiling is wider than the declared limit"
    declared = (
        max_descendant_tasks if kind == PID_CEILING_TASKS else max_descendant_processes
    )
    if value > declared:
        return None, f"the enforced {kind} ceiling is wider than the declared limit"
    return ContainmentAck(
        mechanism=mechanism,
        max_memory_bytes=memory,
        pid_ceiling_kind=kind,
        pid_ceiling_value=value,
    ), None


def set_containment(ack: ContainmentAck) -> None:
    global _ACK
    _ACK = ack


def containment() -> ContainmentAck | None:
    return _ACK


def reset_containment() -> None:
    global _ACK
    _ACK = None
