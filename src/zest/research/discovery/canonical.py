"""Canonical discovery identities. Operates on already-normalized origin/path.

Does not parse URLs. Does not widen scope. RouteTemplate is never OBSERVED.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from zest.research.types import ResearchInputError

UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DECIMAL_SEGMENT_RE = re.compile(r"^[0-9]+$")
MIN_TEMPLATE_PATHS = 3


def canonical_key(*parts: object) -> str:
    """Stable tuple -> SHA-256. Empty parts are invalid."""

    encoded = json.dumps(list(parts), sort_keys=False, separators=(",", ":"), ensure_ascii=True)
    if not parts:
        raise ResearchInputError("canonical_key requires at least one part")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def path_segments(normalized_path: str) -> tuple[str, ...]:
    if not isinstance(normalized_path, str) or not normalized_path.startswith("/"):
        raise ResearchInputError("normalized_path must start with /")
    if normalized_path == "/":
        return ()
    stripped = normalized_path[1:]
    if stripped.endswith("/"):
        stripped = stripped[:-1]
    if not stripped:
        return ()
    return tuple(stripped.split("/"))


def instance_token_from_segment(segment: str) -> str | None:
    """Numeric/UUID path token. Not an ObjectInstance and not ownership."""

    if not isinstance(segment, str) or not segment:
        return None
    if DECIMAL_SEGMENT_RE.fullmatch(segment) or UUID_SEGMENT_RE.fullmatch(segment):
        return segment
    return None


def _placeholder_for(segment: str) -> str | None:
    if DECIMAL_SEGMENT_RE.fullmatch(segment):
        return "{n}"
    if UUID_SEGMENT_RE.fullmatch(segment):
        return "{uuid}"
    return None


@dataclass(frozen=True)
class RouteTemplateAdmission:
    origin: str
    http_method: str
    template_path: str
    exact_paths: tuple[str, ...]
    varying_index: int
    token_kind: str


def route_template_from_paths(
    origin: str,
    http_method: str,
    exact_paths: tuple[str, ...],
) -> RouteTemplateAdmission | None:
    """Admit a template only from >=3 compatible exact paths. Never OBSERVED."""

    if not isinstance(origin, str) or not origin.strip():
        raise ResearchInputError("origin must be a non-empty string")
    if not isinstance(http_method, str) or not http_method.strip():
        raise ResearchInputError("http_method must be a non-empty string")
    distinct = tuple(sorted(set(exact_paths)))
    if len(distinct) < MIN_TEMPLATE_PATHS:
        return None
    segmented = [path_segments(path) for path in distinct]
    length = len(segmented[0])
    if length == 0 or any(len(item) != length for item in segmented):
        return None
    differing: list[int] = []
    for index in range(length):
        values = {item[index] for item in segmented}
        if len(values) > 1:
            differing.append(index)
    if len(differing) != 1:
        return None
    varying = differing[0]
    placeholders = [_placeholder_for(item[varying]) for item in segmented]
    if any(item is None for item in placeholders):
        return None
    kinds = set(placeholders)
    if len(kinds) != 1:
        return None
    prefix_ok = all(
        item[:varying] == segmented[0][:varying] for item in segmented
    )
    suffix_ok = all(
        item[varying + 1 :] == segmented[0][varying + 1 :] for item in segmented
    )
    if not prefix_ok or not suffix_ok:
        return None
    template_parts = list(segmented[0])
    template_parts[varying] = placeholders[0] or "{n}"
    template_path = "/" + "/".join(template_parts)
    return RouteTemplateAdmission(
        origin=origin,
        http_method=http_method,
        template_path=template_path,
        exact_paths=distinct,
        varying_index=varying,
        token_kind="decimal" if placeholders[0] == "{n}" else "uuid",
    )


def admit_route_templates(
    *,
    origin: str,
    http_method: str,
    exact_paths: tuple[str, ...],
) -> tuple[RouteTemplateAdmission, ...]:
    families: dict[str, list[str]] = {}
    for path in exact_paths:
        family = _template_family(path)
        if family is None:
            continue
        families.setdefault(family, []).append(path)
    admitted: list[RouteTemplateAdmission] = []
    for paths in families.values():
        item = route_template_from_paths(origin, http_method, tuple(paths))
        if item is not None:
            admitted.append(item)
    return tuple(admitted)


def _template_family(path: str) -> str | None:
    segments = path_segments(path)
    if not segments:
        return None
    parts: list[str] = []
    saw_token = False
    for segment in segments:
        placeholder = _placeholder_for(segment)
        if placeholder is None:
            parts.append(segment)
            continue
        saw_token = True
        parts.append(placeholder)
    if not saw_token:
        return None
    return "/" + "/".join(parts)
