"""Packaged Research OS HQ static assets."""

from __future__ import annotations

from importlib.resources import files
from mimetypes import guess_type
from pathlib import PurePosixPath


_STATIC = files(__name__).joinpath("static")


def _asset_names() -> frozenset[str]:
    names: set[str] = {"index.html"}
    for directory in ("css", "js", "js/views"):
        root = _STATIC.joinpath(directory)
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_file():
                names.add(str(PurePosixPath(directory) / child.name))
    return frozenset(names)


def read_static_asset(asset: str) -> tuple[bytes, str]:
    """Read one known asset; traversal and unknown paths are rejected."""

    if not asset or asset.startswith("/") or any(part in {"", ".", ".."} for part in asset.split("/")):
        raise FileNotFoundError(asset)
    normalized = str(PurePosixPath(asset))
    if normalized.startswith("../") or normalized == ".." or normalized not in _asset_names():
        raise FileNotFoundError(asset)
    resource = _STATIC.joinpath(*PurePosixPath(normalized).parts)
    if not resource.is_file():
        raise FileNotFoundError(asset)
    content_type = guess_type(normalized)[0] or "application/octet-stream"
    if normalized.endswith(".js"):
        content_type = "text/javascript"
    return resource.read_bytes(), content_type


def read_index() -> str:
    return read_static_asset("index.html")[0].decode("utf-8")


__all__ = ["read_index", "read_static_asset"]
