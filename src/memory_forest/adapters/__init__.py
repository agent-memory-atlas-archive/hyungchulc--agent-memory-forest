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


def load_adapters() -> dict[str, Adapter]:
    """Every adapter the entry point group names, plus the four this package ships."""
    found: dict[str, Adapter] = {}
    try:
        group = entry_points(group="memory_forest.adapters")
    except TypeError:  # pragma: no cover - older importlib used a dict of groups
        group = entry_points().get("memory_forest.adapters", ())  # type: ignore[attr-defined]
    for item in group:
        found[item.name] = item.load()
    for name, module_name in _BUILTINS.items():
        found.setdefault(name, importlib.import_module(module_name).adapter)
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
