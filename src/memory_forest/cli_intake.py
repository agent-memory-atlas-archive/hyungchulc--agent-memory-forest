"""``forest intake`` and ``forest intake status``."""

from __future__ import annotations

import json
import select
import sys
import time
from typing import Any, BinaryIO, Callable

from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root
from .errors import MemoryForestError
from .intake import Result, rename_agent, reread, run, status


_HOOK_LIMIT = 8 * 1024 * 1024
# A hook does not wait on a payload that never closes. Two seconds is enough for a client
# that writes the object and closes, and short enough that a stuck pipe ends the hook.
HOOK_PAYLOAD_SECONDS = 2.0


def cmd_intake(arguments: Any) -> int:
    root = forest_root(arguments)
    if arguments.action not in (None, "status", "reread", "rename-agent"):
        raise MemoryForestError(
            "intake_arguments",
            "The word after `intake` is `status`, `reread` or `rename-agent`.",
        )
    if arguments.action == "rename-agent":
        if not arguments.left or not arguments.right:
            raise MemoryForestError(
                "intake_arguments",
                "`forest intake rename-agent` takes the current agent id and the new one.",
            )
        result = rename_agent(root, arguments.left, arguments.right)
        _show(arguments, result)
        return 0
    if arguments.action == "reread":
        if not arguments.adapter:
            raise MemoryForestError("intake_adapter_unset", "Name the adapter with --from.")
        result = reread(root, arguments.adapter)
        _show(arguments, result)
        return 0
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
    return read_hook_payload(sys.stdin.buffer)


def read_hook_payload(
    stream: BinaryIO,
    *,
    limit: int = _HOOK_LIMIT,
    seconds: float = HOOK_PAYLOAD_SECONDS,
    wait: Callable[[BinaryIO, float], bool] | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """The hook's JSON object. A stream that is not a pipe is read up to ``limit``.

    A pipe is read until the object is complete, the byte limit, or ``seconds``. A payload
    that does not arrive is ``hook_timeout``.
    """
    try:
        stream.fileno()
        piped = True
    except (AttributeError, OSError, ValueError):
        piped = False
    if not piped:
        return _parse_hook(stream.read(limit + 1), limit)
    now = clock or time.monotonic
    deadline = now() + seconds
    waiting = wait or _wait_for_pipe
    buf = b""
    while len(buf) <= limit:
        remaining = deadline - now()
        if remaining <= 0:
            raise MemoryForestError("hook_timeout", "The hook payload did not arrive.")
        if not waiting(stream, remaining):
            raise MemoryForestError("hook_timeout", "The hook payload did not arrive.")
        chunk = stream.read(min(65536, limit + 1 - len(buf)))
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise MemoryForestError("hook_too_large", "The hook payload is larger than intake reads.")
        try:
            value = json.loads(buf.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return _parse_hook(buf, limit)


def _wait_for_pipe(stream: BinaryIO, remaining: float) -> bool:
    ready, _ignored, _also = select.select([stream], [], [], remaining)
    return bool(ready)


def _parse_hook(raw: bytes, limit: int) -> dict[str, Any]:
    if len(raw) > limit:
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
                help="`status`, `reread` or `rename-agent`",
            ),
            arg("left", nargs="?", default=None, help="with rename-agent, the current agent id"),
            arg("right", nargs="?", default=None, help="with rename-agent, the new agent id"),
            arg("--from", dest="adapter", default=None, help="which adapter reads the transcripts"),
            arg("--as", dest="agent", default=None, help="the agent these turns belong to"),
            arg("--transcript", default=None, help="read this transcript file only"),
            arg("--hook", action="store_true", help="read the hook's JSON object from stdin"),
            arg("--dry-run", action="store_true", help="print how many turns would be taken in, and write nothing"),
            arg("--once", action="store_true", help="take turns in even when this adapter is not enabled"),
        ),
    ),
]
