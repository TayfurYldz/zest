"""Integration-test isolation: keep integration-side modules from leaking into e2e."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

_INTEGRATION_MODULES = (
    "zest.integrations.models.cli_session",
    "zest.integrations.strix.adapter",
)


def _unload_integration_modules() -> None:
    for name in _INTEGRATION_MODULES:
        mod = sys.modules.pop(name, None)
        if mod is not None:
            try:
                importlib.reload(mod)
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _refresh_integration_modules_around_integration_test() -> Iterator[None]:
    """Setup/teardown cleanup so later e2e "no model runtime" claims stay valid."""

    _unload_integration_modules()
    yield
    _unload_integration_modules()
