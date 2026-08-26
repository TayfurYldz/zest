"""Package resource loading. Installed wheels must not infer repository-root paths."""

from __future__ import annotations

import json
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Iterator

CONTRACTS_PACKAGE = "zest.resources"
CONTRACTS_RELATIVE = ("contracts", "v1")
SCENARIOS_RELATIVE = ("benchmarks", "research", "scenarios")


def contract_schema_documents() -> dict[str, dict[str, Any]]:
    root = files(CONTRACTS_PACKAGE).joinpath(*CONTRACTS_RELATIVE)
    schemas: dict[str, dict[str, Any]] = {}
    for path in _iter_schema_files(root):
        contents = json.loads(path.read_text(encoding="utf-8"))
        schema_id = contents.get("$id")
        if isinstance(schema_id, str):
            schemas[schema_id] = contents
    return schemas


def _iter_schema_files(root) -> Iterator:
    if not root.is_dir():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            yield from _iter_schema_files(child)
        elif child.name.endswith(".schema.json"):
            yield child


def packaged_scenario_root():
    return files(CONTRACTS_PACKAGE).joinpath(*SCENARIOS_RELATIVE)


def packaged_scenario_directory() -> Path:
    """Materialize packaged development scenarios as a real directory.

    Zip/wheel installs may extract to a temporary path for the duration of the
    returned context manager is not used here; callers that need a durable Path
    should use `as_file` themselves. This helper is for tests that already open
    a context.
    """

    raise RuntimeError("use packaged_scenario_directory_context()")


def packaged_scenario_directory_context():
    return as_file(packaged_scenario_root())


def iter_packaged_scenario_json() -> Iterator[tuple[str, dict[str, Any]]]:
    root = packaged_scenario_root()
    if not root.is_dir():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name.endswith(".json"):
            yield child.name, json.loads(child.read_text(encoding="utf-8"))
