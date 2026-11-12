from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MemoryForestError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        next_step: str | None = None,
        command: str | None = None,
        after_seconds: float | None = None,
        waited_seconds: float | None = None,
        stop_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        # Section 15.4: a refusal a program can follow says its next move. ``run`` carries the command,
        # ``retry`` the seconds to wait before the same call again, and ``report`` means the message is
        # what to tell the owner, because no command helps. A refusal with no next move has none of them.
        # On retry, ``waited_seconds`` is how long this call already waited and ``stop_after_seconds`` is
        # the total after which the caller reports instead of calling again.
        self.next_step = next_step
        self.command = command
        self.after_seconds = after_seconds
        self.waited_seconds = waited_seconds
        self.stop_after_seconds = stop_after_seconds

    def as_dict(self) -> dict[str, Any]:
        """The one shape of a refusal: ``code``, ``message``, ``details`` and, when there is one, the next move."""
        body: dict[str, Any] = {
            "code": self.code,
            "details": dict(sorted(self.details.items())),
            "message": self.message,
        }
        if self.command:
            body["command"] = self.command
        if self.next_step:
            body["next"] = self.next_step
        if self.after_seconds is not None:
            body["after_seconds"] = self.after_seconds
        if self.stop_after_seconds is not None:
            body["stop_after_seconds"] = self.stop_after_seconds
        if self.waited_seconds is not None:
            body["waited_seconds"] = self.waited_seconds
        return body
