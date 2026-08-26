"""Ports Application depends on. Concrete adapters are injected from below."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from zest.data.unit_of_work import UnitOfWork


class Clock(Protocol):
    def now(self) -> datetime: ...


class UnitOfWorkFactory(Protocol):
    def open(self) -> UnitOfWork: ...


class SystemClock:
    def now(self) -> datetime:
        from datetime import timezone

        return datetime.now(timezone.utc)
