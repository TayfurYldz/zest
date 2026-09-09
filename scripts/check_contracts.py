"""Contract lint / structural checks for canonical JSON Schema files.

This is not a Draft 2020-12 semantic validator. It does not evaluate
instances against schemas, does not implement JSON Schema keywords, and
does not fetch $schema or $ref over the network.

A real JSON Schema validator library remains a later decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CANONICAL_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
CONTRACT_ID_PREFIX = "urn:zest:contracts:v1:"
REQUIRED_IDS = {
    "urn:zest:contracts:v1:correlation-context",
    "urn:zest:contracts:v1:execution-budget",
    "urn:zest:contracts:v1:secret-reference",
    "urn:zest:contracts:v1:worker-request",
    "urn:zest:contracts:v1:worker-result",
    "urn:zest:contracts:v1:reauthorization-request",
}
SECRET_VALUE_KEYS = {
    "token",
    "password",
    "api_key",
    "apiKey",
    "raw_secret",
    "credential",
    "secret_value",
    "secretValue",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def iter_schema_files(root: Path) -> list[Path]:
    return sorted((root / "contracts").rglob("*.schema.json"))


def walk(node: object):
    yield node
    if isinstance(node, dict):
        for child in node.values():
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def property_names(node: object, acc: set[str]) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            acc.update(props.keys())
            for child in props.values():
                property_names(child, acc)
        for key, child in node.items():
            if key != "properties":
                property_names(child, acc)
    elif isinstance(node, list):
        for child in node:
            property_names(child, acc)


def collect_refs(node: object) -> list[str]:
    refs: list[str] = []
    for item in walk(node):
        if isinstance(item, dict) and "$ref" in item:
            refs.append(item["$ref"])
    return refs


def contract_id_from_ref(ref: str) -> str | None:
    if ref.startswith("#"):
        return None
    if "#" in ref:
        return ref.split("#", 1)[0]
    return ref


def check_ref(ref: object, path: Path, known_ids: set[str]) -> list[str]:
    if not isinstance(ref, str) or not ref:
        return [f"{path}: $ref must be a non-empty string"]

    if ref.startswith("#"):
        return []

    contract_id = contract_id_from_ref(ref)
    if contract_id is None:
        return [f"{path}: invalid $ref {ref!r}"]

    if contract_id.startswith("http://") or contract_id.startswith("https://"):
        return [f"{path}: network/external $ref is not allowed: {ref}"]

    if contract_id.startswith("file:") or "://" in contract_id:
        return [f"{path}: unknown external $ref is not allowed: {ref}"]

    if "/" in contract_id or contract_id.startswith("."):
        return [f"{path}: filesystem-relative $ref is not allowed: {ref}"]

    if not contract_id.startswith(CONTRACT_ID_PREFIX):
        return [f"{path}: $ref must be a canonical contract URN or #fragment: {ref}"]

    if contract_id not in known_ids:
        return [f"{path}: $ref URN is not in the local schema set: {ref}"]

    return []


def check_file(path: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [f"{path}: invalid JSON ({exc})"]

    if not isinstance(data, dict):
        return {}, [f"{path}: top-level value must be an object"]

    if data.get("$schema") != CANONICAL_SCHEMA:
        errors.append(f"{path}: $schema must be {CANONICAL_SCHEMA}")

    contract_id = data.get("$id")
    if not isinstance(contract_id, str) or not contract_id.startswith(
        CONTRACT_ID_PREFIX
    ):
        errors.append(f"{path}: $id must be a v1 zest URN")

    if data.get("type") != "object":
        errors.append(f"{path}: top-level type must be object")

    if data.get("additionalProperties") is not False:
        errors.append(f"{path}: top-level additionalProperties must be false")

    if not isinstance(data.get("title"), str) or not data["title"]:
        errors.append(f"{path}: title is required")

    names: set[str] = set()
    property_names(data, names)
    leaked = names & SECRET_VALUE_KEYS
    if leaked:
        errors.append(f"{path}: forbidden secret-value field names {sorted(leaked)}")

    return data, errors


def main() -> int:
    root = repo_root()
    files = iter_schema_files(root)
    if not files:
        print("no contract schema files found", file=sys.stderr)
        return 1

    errors: list[str] = []
    ids: dict[str, Path] = {}
    parsed: list[tuple[Path, dict]] = []

    for path in files:
        data, file_errors = check_file(path)
        errors.extend(file_errors)
        contract_id = data.get("$id") if data else None
        if isinstance(contract_id, str):
            if contract_id in ids:
                errors.append(
                    f"duplicate $id {contract_id}: {ids[contract_id]} and {path}"
                )
            else:
                ids[contract_id] = path
        if data:
            parsed.append((path, data))

    known_ids = set(ids)
    missing = REQUIRED_IDS - known_ids
    if missing:
        errors.append(f"missing required $id values: {sorted(missing)}")

    unexpected = known_ids - REQUIRED_IDS
    if unexpected:
        errors.append(f"unexpected $id values in A1 set: {sorted(unexpected)}")

    for path, data in parsed:
        for ref in collect_refs(data):
            errors.extend(check_ref(ref, path, known_ids))

    if errors:
        for item in errors:
            print(item, file=sys.stderr)
        return 1

    print("ok: contract lint / structural checks passed")
    print(f"  {len(files)} schema files, {len(ids)} unique contract ids")
    print("  not a Draft 2020-12 semantic validator; no network fetch")
    for contract_id in sorted(ids):
        print(f"  {contract_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
