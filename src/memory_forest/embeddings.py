"""The embedding provider interface (design, section 10).

Vector search runs only when ``[embeddings] provider`` is not ``none`` and a
provider of that name is registered in this process. The package registers
none. It ships no client and it never opens a network connection. A test, or
a later package, registers a provider with ``register``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Provider(Protocol):
    """Turns texts into vectors of one fixed length."""

    name: str

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """One vector per text, in the same order. No network call belongs here."""


_PROVIDERS: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """Remember ``provider`` under ``provider.name``. A second call replaces it."""
    _PROVIDERS[provider.name] = provider


def unregister(name: str) -> None:
    _PROVIDERS.pop(name, None)


def get(name: str) -> Provider | None:
    """The registered provider of ``name``, or None when nothing registered one."""
    if not name or name == "none":
        return None
    return _PROVIDERS.get(name)
