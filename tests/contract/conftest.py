"""Contract-test isolation: keep integration-side modules from leaking into e2e."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest

_INTEGRATION_MODULES = (
    "zest.integrations.models.cli_session",
    "zest.integrations.strix.adapter",
)


@pytest.fixture(autouse=True)
def _unload_integration_modules_after_contract_test() -> Iterator[None]:
    """Teardown-only cleanup so later e2e "no model runtime" claims stay valid."""

    yield
    for name in _INTEGRATION_MODULES:
        sys.modules.pop(name, None)
