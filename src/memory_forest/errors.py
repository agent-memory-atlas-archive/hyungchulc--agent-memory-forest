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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        # Section 15.4: a refusal a program can follow has ``next`` of ``run`` and the command.
        self.next_step = next_step
        self.command = command

    def as_dict(self) -> dict[str, Any]:
        body = {
            "code": self.code,
            "details": dict(sorted(self.details.items())),
            "message": self.message,
        }
        if self.command:
            body["command"] = self.command
        if self.next_step:
            body["next"] = self.next_step
        return body
