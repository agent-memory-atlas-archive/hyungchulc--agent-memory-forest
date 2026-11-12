"""``forest intake`` and ``forest intake status``."""

from __future__ import annotations

import json
import sys
from typing import Any

from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root
from .errors import MemoryForestError
from .intake import Result, run, status


_HOOK_LIMIT = 8 * 1024 * 1024


def cmd_intake(arguments: Any) -> int:
    root = forest_root(arguments)
    if arguments.action not in (None, "status"):
        raise MemoryForestError(
            "intake_arguments",
            "The only word after `intake` is `status`.",
        )
    if arguments.action == "status":
        if any((arguments.adapter, arguments.agent, arguments.transcript, arguments.hook, arguments.dry_run, arguments.once)):
            raise MemoryForestError(
                "intake_arguments",
                "`forest intake status` takes no other options.",
            )
        result = status(root)
    else:
        if not arguments.adapter:
            raise MemoryForestError(
                "intake_adapter_unset",
                "Name the adapter with --from.",
            )
        hook = _hook_payload() if arguments.hook else None
        result = run(
            root,
            arguments.adapter,
            agent=arguments.agent,
            transcript=arguments.transcript,
            hook=hook,
            dry_run=arguments.dry_run,
            once=arguments.once,
        )
    # A hook's stdout is added to the model's context by at least one client, and a
    # non-zero exit can block the turn. Hook mode stays silent.
    if arguments.hook:
        return 0
    _show(arguments, result)
    return 0


def _hook_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(_HOOK_LIMIT + 1)
    if len(raw) > _HOOK_LIMIT:
        raise MemoryForestError("hook_too_large", "The hook payload is larger than intake reads.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryForestError("hook_invalid", "The hook payload is not a JSON object.") from exc
    if not isinstance(value, dict):
        raise MemoryForestError("hook_invalid", "The hook payload is not a JSON object.")
    return value


def _show(arguments: Any, result: Result) -> None:
    if arguments.json:
        _print_json(result.data)
    else:
        _print_lines(result.lines)


COMMANDS: list[ForestCommand] = [
    ForestCommand(
        ("intake",),
        cmd_intake,
        "Take new turns from a tool into its agent's raw lane. `intake status` counts lines of unknown shape.",
        (
            arg(
                "action",
                nargs="?",
                default=None,
                help="`status` shows each adapter, its agent and how many lines it did not understand",
            ),
            arg("--from", dest="adapter", default=None, help="which adapter reads the transcripts"),
            arg("--as", dest="agent", default=None, help="the agent these turns belong to"),
            arg("--transcript", default=None, help="read this transcript file only"),
            arg("--hook", action="store_true", help="read the hook's JSON object from stdin"),
            arg("--dry-run", action="store_true", help="print how many turns would be taken in, and write nothing"),
            arg("--once", action="store_true", help="take turns in even when this adapter is not enabled"),
        ),
    ),
]
