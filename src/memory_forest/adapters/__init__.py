"""Transcript adapters, one module per tool.

The package ships one, ``jsonl``, behind the entry point group
``memory_forest.adapters``: it reads records already in the lane's shape. A
reader of a particular program's own transcripts is another module in that
group, written by whoever knows that program (``docs/adapters.md``). When the
package is imported from a checkout that has not been installed, the built-in
adapter is used directly, because the entry points are registered by installing
it.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Protocol

from ..config import IntakeToolSettings
from .common import ReadBatch, Transcript


class Adapter(Protocol):
    """A reader of one tool's transcripts, as ``forest intake`` uses it (``docs/adapters.md``).

    Required, called without a fallback: ``name``, ``discover``, ``read`` (intake always passes ``carry=``
    and ``max_bytes=``), ``open_path`` and ``hook``.

    Optional, looked up with a default:

    - ``describe(transcript, settings) -> Transcript``: the transcript with what its head states. Called on
      a transcript's first read and after a rewrite; without it, the transcript stands as found.
    - ``transcript_root(settings) -> Path | None``: the folder transcript keys are relative to; without it,
      ``settings.path``.
    - ``id_stands_alone(turn_id) -> bool``: whether this one id is unique without its session; asked
      before ``ids_stand_alone``.
    - ``ids_stand_alone: bool``: whether every id is unique without its session; default false.
    - ``ReadBatch.carry``: what a read could not finish, handed back as ``carry=`` on the next read.
    - the code ``transcript_deferred``, which ``hook`` may raise to leave its session for a sweep.
    """

    name: str

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        """Required. The transcripts this tool keeps."""

    def read(
        self,
        transcript: Transcript,
        offset: int,
        carry: dict | None = None,
        max_bytes: int | None = None,
    ) -> ReadBatch:
        """Required. Turns after ``offset``, and the cursor the next read should keep.

        ``carry`` is the previous read's ``ReadBatch.carry``; ``max_bytes`` bounds a hook's read.
        """

    def open_path(self, path, settings: IntakeToolSettings) -> Transcript:
        """Required. One transcript named by ``forest intake --transcript``."""

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        """Required. The transcript a hook's JSON on stdin names. May raise ``transcript_deferred``."""


_BUILTINS: dict[str, str] = {
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
    """Every adapter the entry point group names, plus the one this package ships.

    A third-party module that registers a built-in name is an error that names both.
    A plugin that fails to load disables only itself.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    found: dict[str, Adapter] = {}
    failed: dict[str, str] = {}
    try:
        group = entry_points(group="memory_forest.adapters")
    except TypeError:  # pragma: no cover - older importlib used a dict of groups
        group = entry_points().get("memory_forest.adapters", ())  # type: ignore[attr-defined]
    for item in group:
        try:
            loaded = item.load()
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            failed[item.name] = exc.__class__.__name__
            continue
        previous = found.get(item.name)
        if previous is not None and not _same_adapter(previous, loaded):
            # The plugin that arrived second is off. The first one, or the built-in below, stays.
            failed[item.name] = "intake_adapter_clash"
            continue
        found[item.name] = loaded
    for name, module_name in _BUILTINS.items():
        builtin = importlib.import_module(module_name).adapter
        other = found.get(name)
        if other is not None and not _same_adapter(other, builtin):
            failed[name] = "intake_adapter_clash"
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
