"""Transcript adapters, one module per tool.

The package ships four, behind the entry point group ``memory_forest.adapters``.
Another tool is another module in that group. When the package is imported from
a checkout that has not been installed, the four built-in adapters are used
directly, because the entry points are registered by installing it.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Protocol

from ..config import IntakeToolSettings
from .common import ReadBatch, Transcript


class Adapter(Protocol):
    name: str

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        """The transcripts this tool keeps."""

    def read(self, transcript: Transcript, offset: int) -> ReadBatch:
        """Turns after ``offset``, and the cursor the next read should keep."""

    def open_path(self, path, settings: IntakeToolSettings) -> Transcript:
        """One transcript named by ``forest intake --transcript``."""

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        """The transcript a hook's JSON on stdin names."""


_BUILTINS: dict[str, str] = {
    "claude-code": "memory_forest.adapters.claude_code",
    "codex": "memory_forest.adapters.codex",
    "grok": "memory_forest.adapters.grok",
    "jsonl": "memory_forest.adapters.jsonl",
}


_CACHE: dict[str, Adapter] | None = None
_FAILED: dict[str, str] = {}


def load_failures() -> dict[str, str]:
    """Plugins that did not load, by name and the exception class. The others still load."""
    load_adapters()
    return dict(_FAILED)


def clear_adapter_cache() -> None:
    """Drop the cached adapters. Tests use this when they change the entry points."""
    global _CACHE
    _CACHE = None
    _FAILED.clear()


def _same_adapter(left: Adapter, right: Adapter) -> bool:
    return left is right or type(left) is type(right)


def load_adapters() -> dict[str, Adapter]:
    """Every adapter the entry point group names, plus the four this package ships.

    A third-party module that registers a built-in name is an error that names both.
    A plugin that fails to load disables only itself.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    from ..errors import MemoryForestError

    found: dict[str, Adapter] = {}
    failed: dict[str, str] = {}
    try:
        group = entry_points(group="memory_forest.adapters")
    except TypeError:  # pragma: no cover - older importlib used a dict of groups
        group = entry_points().get("memory_forest.adapters", ())  # type: ignore[attr-defined]
    for item in group:
        try:
            loaded = item.load()
        except Exception as exc:
            failed[item.name] = exc.__class__.__name__
            continue
        previous = found.get(item.name)
        if previous is not None and not _same_adapter(previous, loaded):
            raise MemoryForestError(
                "intake_adapter_clash",
                f"{item.name} is claimed by {type(previous).__module__} and {type(loaded).__module__}.",
                details={"adapter": item.name},
            )
        found[item.name] = loaded
    for name, module_name in _BUILTINS.items():
        builtin = importlib.import_module(module_name).adapter
        other = found.get(name)
        if other is not None and not _same_adapter(other, builtin):
            raise MemoryForestError(
                "intake_adapter_clash",
                f"{name} is claimed by {type(other).__module__} and {type(builtin).__module__}.",
                details={"adapter": name},
            )
        found[name] = builtin
    _FAILED.clear()
    _FAILED.update(failed)
    _CACHE = found
    return found


def get_adapter(name: str) -> Adapter:
    found = load_adapters()
    if name not in found:
        known = ", ".join(sorted(found))
        from ..errors import MemoryForestError

        raise MemoryForestError(
            "intake_adapter_unknown",
            f"No adapter is named {name}. The adapters are: {known}.",
            details={"adapter": name},
        )
    return found[name]
