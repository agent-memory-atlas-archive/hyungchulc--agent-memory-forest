"""``forest index``, ``forest search``, ``forest read``, ``forest timeline``, ``forest navigate``,
``forest log``, ``forest agents`` and ``forest mcp``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .cli import ForestCommand, _print_json, _print_lines, _shown, arg, forest_root
from .config import load_config
from . import groups, intake, mcp, search, timeline
from .errors import MemoryForestError


def cmd_index_build(arguments: Any) -> int:
    result = search.build(forest_root(arguments), verify=bool(arguments.verify))
    if result.get("busy"):
        raise search.index_busy_error(
            str(result.get("message") or "The index was not built."),
            waited_seconds=float(result.get("waited_seconds") or 0.0),
            path=str(result.get("path") or search.index_relative(forest_root(arguments))),
        )
    _show_index(arguments, result)
    return 0


def cmd_index_status(arguments: Any) -> int:
    result = search.status(forest_root(arguments))
    _show_index(arguments, result)
    return 0


def cmd_index_reset(arguments: Any) -> int:
    result = search.reset(forest_root(arguments), dry_run=bool(arguments.dry_run))
    if arguments.json:
        _print_json(result)
        return 0
    dry_run = bool(result.get("dry_run"))
    verb = "would remove" if dry_run else "removed"
    point = "would point at" if dry_run else "points at"
    removed = result.get("removed") or []
    lines = [f"{point}: {result['path']}"]
    lines.extend(f"{verb}: {path}" for path in removed)
    if not removed:
        lines.append(f"{verb}: (nothing)")
    left = "would leave" if dry_run else "orphan"
    lines.extend(f"{left}: {path}" for path in result.get("orphans") or [])
    if result.get("command"):
        lines.append(f"next: {result['command']}")
    _print_lines(lines)
    return 0


# A flag that chooses whose pages a reader sees. Refused on an agent's door.
# Anything else on an agent's command cannot change what the reader may see. ``root`` names the forest the
# command runs on and ``json`` the form of its answer: a command door is the tools' scope for an agent its owner
# lets run commands, not a wall (an agent that can run commands can read files).
SCOPE_DESTS = frozenset({"named"})
PLAIN_DESTS = frozenset({
    "caller",
    "group",
    "help",
    "http",
    "json",
    "root",
    "turn_id",
    "limit",
    "lines",
    "origin",
    "path",
    "phrase",
    "reader",
    "role",
    "session",
    "source",
    "subject_id",
    "text",
    "text_flag",
    "words",
})


def argument_dest(argument: Any) -> str:
    """The destination an ``Argument`` writes, the same name argparse would use."""
    if "dest" in argument.options:
        return str(argument.options["dest"])
    flag = argument.flags[0]
    if flag.startswith("--"):
        return flag[2:].replace("-", "_")
    return flag.lstrip("-")


def agent_door_flags(parser: Any = None) -> list[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    """Every flag of every agent's command: each command that takes ``--as-group``, with the flags every
    command has (``--root``, ``--json``, ``--help``), as ``(command words, destination, option strings)``."""
    import argparse

    from .cli import build_forest_parser

    found: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = []

    def walk(current: argparse.ArgumentParser, words: tuple[str, ...]) -> None:
        children = [action for action in current._actions if isinstance(action, argparse._SubParsersAction)]
        if not children:
            if any(action.dest == "group" for action in current._actions):
                for action in current._actions:
                    found.append((words, action.dest, tuple(action.option_strings)))
            return
        for child in children:
            for name, sub in child.choices.items():
                walk(sub, (*words, name))

    walk(parser if parser is not None else build_forest_parser(), ())
    return found


def flag_effect(dest: str) -> str:
    """``refused`` when the flag widens a scope, ``plain`` when it cannot.

    A destination that is neither fails the walk, so a flag added later is seen.
    """
    if dest in SCOPE_DESTS:
        return "refused"
    if dest in PLAIN_DESTS:
        return "plain"
    raise KeyError(dest)


def _agent_door(arguments: Any) -> bool:
    return bool(getattr(arguments, "reader", None) or getattr(arguments, "group", None))


def _refuse_widening(arguments: Any) -> None:
    """An agent's command takes no flag the tools do not take."""
    if _agent_door(arguments) and getattr(arguments, "named", None):
        raise MemoryForestError(
            "scope_widened",
            "This command is an agent's door, so it takes no flag that chooses a wider scope. "
            "Leave out --named. The tools take no such argument.",
            next_step="fix",
        )


def _caller(arguments: Any, *, required: bool = False, door: str = "") -> str | None:
    """The reader: ``--as``, or the member ``--agent`` names inside ``--as-group``.

    ``--as`` is unchanged by ``--agent``. A command that logs requires a reader.
    ``door`` is ``log`` when the refusal must name ``forest log``.
    """
    reader = getattr(arguments, "reader", None) or None
    group = getattr(arguments, "group", None) or None
    caller = getattr(arguments, "caller", None) or None
    log = door == "log"
    if reader and group:
        raise MemoryForestError(
            "reader_conflict",
            "Pass --as or --as-group on `forest log`, not both." if log else "Pass --as or --as-group, not both.",
            next_step="fix",
        )
    if caller and not group:
        raise MemoryForestError(
            "agent_needs_group",
            "`forest log --as` is the reader. A caller's name belongs with `forest log --as-group`, "
            "and it cannot change --as."
            if log
            else "--as is the reader. A caller's name belongs with --as-group, and it cannot change --as.",
            next_step="fix",
        )
    if group and not caller:
        raise MemoryForestError(
            "agent_unset",
            "Name the caller with --agent.",
            next_step="fix",
        )
    if not group:
        if required and not reader:
            raise MemoryForestError(
                "log_agent_unset",
                "Name the agent with `forest log --as`, or the group and the caller with "
                "`forest log --as-group` and `--agent`.",
                next_step="fix",
            )
        return reader
    resolved = groups.resolve_caller(forest_root(arguments), group, caller)
    if resolved.wrote:
        arguments._admitted = resolved.id
    return resolved.id


@contextmanager
def _admitted(arguments: Any) -> Iterator[None]:
    """A refusal after this command admitted its caller to the group says so: that write took effect."""
    try:
        yield
    except MemoryForestError as exc:
        raise mcp.admitted_on(exc, getattr(arguments, "_admitted", "")) from None
    except OSError as exc:
        if not getattr(arguments, "_admitted", ""):
            raise
        raise mcp.admitted_on(search.edge_refusal(exc, forest_root(arguments)), arguments._admitted) from None


def cmd_search(arguments: Any) -> int:
    _refuse_widening(arguments)
    reader = _caller(arguments)
    with _admitted(arguments):
        result = search.query(
            forest_root(arguments),
            arguments.words,
            reader=reader,
            named=arguments.named or (),
            limit=arguments.limit,
            source=arguments.source,
        )
    if getattr(arguments, "_admitted", ""):
        result = {**result, "admitted": arguments._admitted}
    _show_pages(arguments, result)
    return 0


def cmd_read(arguments: Any) -> int:
    _refuse_widening(arguments)
    reader = _caller(arguments)
    with _admitted(arguments):
        result = search.read(
            forest_root(arguments),
            arguments.path,
            reader=reader,
            named=arguments.named or (),
            lines=search.parse_lines(arguments.lines),
            source=arguments.source,
        )
    if getattr(arguments, "_admitted", ""):
        result = {**result, "admitted": arguments._admitted}
    _show_pages(arguments, result)
    return 0


def cmd_timeline_query(arguments: Any) -> int:
    _refuse_widening(arguments)
    reader = _caller(arguments)
    with _admitted(arguments):
        result = timeline.query(
            forest_root(arguments),
            arguments.phrase,
            reader=reader,
            named=arguments.named or (),
            limit=arguments.limit,
            source=arguments.source,
        )
    if getattr(arguments, "_admitted", ""):
        result = {**result, "admitted": arguments._admitted}
    _show_pages(arguments, result)
    return 0


def cmd_timeline_subject(arguments: Any) -> int:
    _refuse_widening(arguments)
    reader = _caller(arguments)
    with _admitted(arguments):
        result = timeline.subject(
            forest_root(arguments),
            arguments.subject_id,
            reader=reader,
            named=arguments.named or (),
            limit=arguments.limit,
            source=arguments.source,
        )
    if getattr(arguments, "_admitted", ""):
        result = {**result, "admitted": arguments._admitted}
    _show_pages(arguments, result)
    return 0


def cmd_navigate(arguments: Any) -> int:
    _refuse_widening(arguments)
    reader = _caller(arguments)
    with _admitted(arguments):
        result = search.navigate(
            forest_root(arguments),
            reader=reader,
            path=arguments.path,
        )
    if getattr(arguments, "_admitted", ""):
        result = {**result, "admitted": arguments._admitted}
    _show_pages(arguments, result)
    return 0


def _log_text() -> str:
    """The turn on stdin, bounded by the same number of bytes as ``memory_log``'s words.

    A pipe has a deadline; a terminal has none, since a person types there.
    """
    raw = getattr(sys.stdin, "buffer", None)
    if raw is None:
        text = sys.stdin.read(mcp.TURN_MAX_BYTES + 1)
        if len(text.encode("utf-8", "surrogatepass")) > mcp.TURN_MAX_BYTES:
            raise MemoryForestError(
                "log_too_large",
                f"The turn is longer than {mcp.TURN_MAX_BYTES} bytes. `forest log` did not store it.",
                details={"limit": mcp.TURN_MAX_BYTES},
                next_step="fix",
            )
        return text
    terminal = bool(getattr(sys.stdin, "isatty", lambda: False)())
    return intake.read_bounded(
        raw, limit=mcp.TURN_MAX_BYTES, seconds=None if terminal else intake.LOG_READ_SECONDS
    )


def cmd_log(arguments: Any) -> int:
    """The command-line twin of ``memory_log``. The text is stdin, never an argument."""
    if arguments.text or arguments.text_flag is not None:
        raise MemoryForestError(
            "log_text_from_stdin",
            "The turn is read from stdin, never from an argument. An argument would show in the process list. "
            "Write the turn on stdin of `forest log --as <agent>`.",
            next_step="fix",
        )
    if arguments.role is not None or arguments.origin is not None:
        raise MemoryForestError(
            "log_caller_fields",
            "`forest log` sets the role and the origin. A caller cannot. Leave out --role and --origin.",
            next_step="fix",
        )
    _refuse_widening(arguments)
    reader = _caller(arguments, required=True, door="log")
    if not reader:
        raise MemoryForestError(
            "log_agent_unset",
            "Name the agent with `forest log --as`, or the group and the caller with "
            "`forest log --as-group` and `--agent`.",
            next_step="fix",
        )
    with _admitted(arguments):
        body = mcp.record_turn(
            forest_root(arguments),
            reader,
            _log_text(),
            origin="interactive",
            session=arguments.session or "cli",
            door="cli",
            group=getattr(arguments, "group", None) or "",
            turn_id=arguments.turn_id,
        )
    if getattr(arguments, "_admitted", ""):
        body["admitted"] = arguments._admitted
    # The turn is written: an answer that cannot be masked keeps its fixed words, never a refusal.
    try:
        shown = _shown(body)
    except (MemoryForestError, OSError) as exc:
        shown = mcp.withheld(body, exc)
    if arguments.json:
        sys.stdout.write(json.dumps(shown, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0
    lines = [f"path: {shown['path']}"] if "path" in shown else []
    lines += [f"wrote: {str(shown['wrote']).lower()}", f"indexed: {str(shown['indexed']).lower()}"]
    lines += [f"{key}: {shown[key]}" for key in ("stored", "tree", "index", "reason", "withheld", "admitted")
              if shown.get(key)]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_agents(arguments: Any) -> int:
    report = groups.listing(forest_root(arguments))
    if arguments.json:
        _print_json(report)
        return 0
    _print_lines(groups.listing_lines(report))
    return 0


def cmd_mcp(arguments: Any) -> int:
    return mcp.run(forest_root(arguments), arguments.reader, arguments.http, group=arguments.group)


def _show_index(arguments: Any, result: dict[str, Any]) -> None:
    masked = search.mask_result(result, config=load_config(forest_root(arguments)))
    if arguments.json:
        _print_json(masked)
        return
    if result.get("busy"):
        _print_lines([result["message"]])
        return
    if result.get("exists") and not result.get("available", True) and "units" not in result:
        _print_lines([f"index: {result['path']}", "not available yet"])
        return
    if "tokenizer" in result and "units" in result:
        _print_lines(
            [
                f"index: {result['path']}",
                f"tokenizer: {result.get('tokenizer', '')}",
                f"files: {result.get('files', 0)}  units: {result.get('units', 0)}  updated: {result.get('updated', 0)}",
            ]
        )
        return
    if not result.get("exists"):
        _print_lines([f"index: {result['path']}", "not built"])
        return
    stale = result.get("stale") or []
    lines = [
        f"index: {result['path']}",
        f"tokenizer: {result.get('tokenizer', '')}",
        f"files: {result.get('files', 0)}  units: {result.get('units', 0)}",
        f"stale: {len(stale)}",
        *(f"  {path}" for path in stale),
    ]
    for label in ("undecodable", "torn", "verify_due"):
        named = result.get(label) or []
        if named:
            lines.append(f"{label}: {len(named)}")
            lines.extend(f"  {path}" for path in named)
    _print_lines(lines)


def _show_pages(arguments: Any, result: dict[str, Any]) -> None:
    masked = search.mask_result(result, config=load_config(forest_root(arguments)))
    if arguments.json:
        _print_json(masked)
        return
    lines: list[str] = []
    for page in masked["results"]:
        lines.append(page["path"])
        for why in page["why"]:
            lines.append(f"  why: {why}")
        for section in page["sections"]:
            start, end = section["lines"]
            lines.append(f"  {section['address']} lines {start}-{end}")
            for row in section["text"].splitlines() or [""]:
                lines.append(f"    {row}")
        lines.append("")
    trees = ", ".join(masked["trees"]) if masked["trees"] else "(none)"
    lines.append(f"trees: {trees}")
    if masked["lanes"]:
        lines.append("lanes: " + ", ".join(masked["lanes"]))
    if masked.get("vectors") not in (None, "off"):
        lines.append(f"vectors: {masked['vectors']}")
    _print_lines(lines)


_READER = arg("--as", dest="reader", default=None, help="the reader id, from the connection, not from the question")
_GROUP = arg("--as-group", dest="group", default=None, help="one connection shared by a group of the owner's agents")
_CALLER = arg("--agent", dest="caller", default=None, help="the caller's own name, resolved only inside --as-group")
_NAMED = arg("--named", action="append", default=None, help="an agent this task names; refused on an agent's door")
_SOURCE = arg("--source", default=None, help="one source of the turns: group:<id>, door:<name> or adapter:<name>")
_LIMIT = arg("--limit", type=int, default=10, help="how many pages to return")
# The help of every command an agent runs names the table of refusals and their next moves.
_REFUSALS = (
    "A refusal names its next move in next: fix, retry, run or report. docs/refusals.md lists every code, "
    "its move and when it is given."
)

COMMANDS: list[ForestCommand] = [
    ForestCommand(
        ("index", "build"),
        cmd_index_build,
        "Build or update the search index. A reader never waits for this. A damaged index is `forest index reset`.",
        (
            arg(
                "--verify",
                action="store_true",
                help="Hash every file, including one whose size and time are unchanged.",
            ),
        ),
    ),
    ForestCommand(("index", "status"), cmd_index_status, "Show the index tokenizer, counts and files changed since the build."),
    ForestCommand(
        ("index", "reset"),
        cmd_index_reset,
        "Point the index at a new file, then remove the old one. A call already open keeps its own file.",
        (
            arg(
                "--dry-run",
                action="store_true",
                help="Name the files that would be removed, and remove nothing.",
            ),
        ),
    ),
    ForestCommand(
        ("search",),
        cmd_search,
        "Find pages: registry names and aliases first, then words, then vectors when a provider is registered.",
        (arg("words", help="the words to look for"), _READER, _GROUP, _CALLER, _NAMED, _SOURCE, _LIMIT),
        epilog=_REFUSALS,
    ),
    ForestCommand(
        ("read",),
        cmd_read,
        "Print a page or an addressed section, with the same masking as every other read.",
        (
            arg("path", help="a forest path, optionally with #address"),
            arg("--lines", default=None, help="only lines A-B"),
            _READER,
            _GROUP,
            _CALLER,
            _NAMED,
            _SOURCE,
        ),
        epilog=_REFUSALS,
    ),
    ForestCommand(
        ("timeline", "query"),
        cmd_timeline_query,
        "What falls in a time phrase or a YYYY-MM-DD..YYYY-MM-DD range, including hours not yet filed.",
        (
            arg("phrase"),
            _READER,
            _GROUP,
            _CALLER,
            _NAMED,
            _SOURCE,
            arg("--limit", type=int, default=50, help="how many pages to return"),
        ),
        epilog=_REFUSALS,
    ),
    ForestCommand(
        ("timeline", "subject"),
        cmd_timeline_subject,
        "The indexed sections of one subject, in time order.",
        (
            arg("subject_id"),
            _READER,
            _GROUP,
            _CALLER,
            _NAMED,
            _SOURCE,
            arg("--limit", type=int, default=50, help="how many pages to return"),
        ),
        epilog=_REFUSALS,
    ),
    ForestCommand(
        ("navigate",),
        cmd_navigate,
        "One level down from the root index, or from a path a previous call showed. The same scope as memory_navigate.",
        (
            arg("path", nargs="?", default=None, help="a path a previous call showed; omit it to start at the root index"),
            _READER,
            _GROUP,
            _CALLER,
        ),
        epilog=_REFUSALS,
    ),
    ForestCommand(
        ("log",),
        cmd_log,
        "Append this agent's own turn, read from stdin. The command-line twin of memory_log.",
        (
            arg(
                "text",
                nargs="*",
                default=None,
                help="refused: write the turn on stdin, never as an argument",
            ),
            arg("--text", dest="text_flag", default=None, help="refused: write the turn on stdin, never as an argument"),
            arg("--role", default=None, help="refused: the command sets the role"),
            arg("--origin", default=None, help="refused: the command sets the origin"),
            arg("--session", default=None, help="the session name; the role and the origin stay the command's"),
            arg("--turn-id", dest="turn_id", default=None,
                help="your own id for this turn, when you may send it twice: the same id in the session is stored once"),
            _READER,
            _GROUP,
            _CALLER,
        ),
        epilog=_REFUSALS,
    ),
    ForestCommand(
        ("agents",),
        cmd_agents,
        "List each group, its members, whether a member has written a lane, and when it was last seen.",
    ),
    ForestCommand(
        ("mcp",),
        cmd_mcp,
        "Serve memory_search, memory_read, memory_timeline, memory_navigate and memory_log. "
        "Stdio is one JSON message per line. HTTP, including loopback, uses .forest/mcp/token in mode 0600. "
        "Pass --as or --as-group, not both.",
        (
            arg("--as", dest="reader", default=None, help="the agent this connection reads and logs as"),
            arg("--as-group", dest="group", default=None, help="one connection shared by the agents of this group"),
            arg("--http", default=None, help="PORT, or HOST:PORT. PORT alone binds to loopback"),
        ),
        epilog=_REFUSALS,
    ),
]
