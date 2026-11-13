"""``forest index``, ``forest search``, ``forest read``, ``forest timeline``, ``forest navigate``,
``forest log``, ``forest agents`` and ``forest mcp``.
"""

from __future__ import annotations

import sys
from typing import Any

from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root
from .config import load_config
from . import groups, mcp, search, timeline
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
    _print_lines(lines)
    return 0


def _caller(arguments: Any, *, required: bool = False) -> str | None:
    """The reader: ``--as``, or the member ``--agent`` names inside ``--as-group``.

    ``--as`` is unchanged by ``--agent``. A command that logs requires a reader.
    """
    reader = getattr(arguments, "reader", None) or None
    group = getattr(arguments, "group", None) or None
    caller = getattr(arguments, "caller", None) or None
    if reader and group:
        raise MemoryForestError(
            "reader_conflict",
            "Pass --as or --as-group, not both.",
            next_step="run",
            command="forest search --as <agent>",
        )
    if caller and not group:
        raise MemoryForestError(
            "agent_needs_group",
            "--as is the reader. A caller's name belongs with --as-group, and it cannot change --as.",
            next_step="run",
            command="forest search --as <agent>",
        )
    if group and not caller:
        raise MemoryForestError(
            "agent_unset",
            "Name the caller with --agent.",
            next_step="run",
            command="forest search --as-group <group> --agent <name>",
        )
    if not group:
        if required and not reader:
            raise MemoryForestError(
                "log_agent_unset",
                "Name the agent with --as, or the group and the caller with --as-group and --agent.",
                next_step="run",
                command="forest log --as <agent>",
            )
        return reader
    resolved = groups.resolve_caller(forest_root(arguments), group, caller)
    return resolved.id


def _with_next(exc: MemoryForestError, command: str) -> MemoryForestError:
    if exc.next_step:
        return exc
    return MemoryForestError(
        exc.code,
        exc.message,
        details=exc.details,
        next_step="run",
        command=command,
        after_seconds=exc.after_seconds,
        waited_seconds=exc.waited_seconds,
        stop_after_seconds=exc.stop_after_seconds,
    )


def cmd_search(arguments: Any) -> int:
    result = search.query(
        forest_root(arguments),
        arguments.words,
        reader=_caller(arguments),
        named=arguments.named or (),
        limit=arguments.limit,
    )
    _show_pages(arguments, result)
    return 0


def cmd_read(arguments: Any) -> int:
    result = search.read(
        forest_root(arguments),
        arguments.path,
        reader=_caller(arguments),
        named=arguments.named or (),
        lines=search.parse_lines(arguments.lines),
    )
    _show_pages(arguments, result)
    return 0


def cmd_timeline_query(arguments: Any) -> int:
    result = timeline.query(
        forest_root(arguments),
        arguments.phrase,
        reader=_caller(arguments),
        named=arguments.named or (),
        limit=arguments.limit,
    )
    _show_pages(arguments, result)
    return 0


def cmd_timeline_subject(arguments: Any) -> int:
    result = timeline.subject(
        forest_root(arguments),
        arguments.subject_id,
        reader=_caller(arguments),
        named=arguments.named or (),
        limit=arguments.limit,
    )
    _show_pages(arguments, result)
    return 0


def cmd_navigate(arguments: Any) -> int:
    result = search.navigate(
        forest_root(arguments),
        reader=_caller(arguments),
        path=arguments.path,
    )
    _show_pages(arguments, result)
    return 0


def cmd_log(arguments: Any) -> int:
    """The command-line twin of ``memory_log``. The text is stdin, never an argument."""
    command = "forest log --as <agent>"
    if arguments.text or arguments.text_flag is not None:
        raise MemoryForestError(
            "log_text_from_stdin",
            "The turn is read from stdin, never from an argument. An argument would show in the process list.",
            next_step="run",
            command=command,
        )
    if arguments.role is not None or arguments.origin is not None:
        raise MemoryForestError(
            "log_caller_fields",
            "The command sets the role and the origin. A caller cannot.",
            next_step="run",
            command=command,
        )
    try:
        reader = _caller(arguments, required=True)
    except MemoryForestError as exc:
        raise _with_next(exc, command) from exc
    if not reader:
        raise MemoryForestError(
            "log_agent_unset",
            "Name the agent with --as, or the group and the caller with --as-group and --agent.",
            next_step="run",
            command=command,
        )
    try:
        body = mcp.record_turn(
            forest_root(arguments),
            reader,
            sys.stdin.read(),
            origin="interactive",
            session=arguments.session or "cli",
        )
    except MemoryForestError as exc:
        raise _with_next(exc, command) from exc
    if arguments.json:
        _print_json(body)
        return 0
    lines = [
        f"path: {body['path']}",
        f"wrote: {str(body['wrote']).lower()}",
        f"indexed: {str(body['indexed']).lower()}",
    ]
    if body.get("reason"):
        lines.append(f"reason: {body['reason']}")
    _print_lines(lines)
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
_NAMED = arg("--named", action="append", default=None, help="an agent this task names; not a way for the question to widen scope")
_LIMIT = arg("--limit", type=int, default=10, help="how many pages to return")

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
        (arg("words", help="the words to look for"), _READER, _GROUP, _CALLER, _NAMED, _LIMIT),
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
        ),
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
            arg("--limit", type=int, default=50, help="how many pages to return"),
        ),
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
            arg("--limit", type=int, default=50, help="how many pages to return"),
        ),
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
            _READER,
            _GROUP,
            _CALLER,
        ),
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
    ),
]
