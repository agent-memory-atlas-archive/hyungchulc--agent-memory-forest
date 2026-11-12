"""``forest index``, ``forest search``, ``forest read``, ``forest timeline`` and ``forest mcp``."""

from __future__ import annotations

from typing import Any

from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root
from .config import load_config
from . import mcp, search, timeline
from .errors import MemoryForestError


def cmd_index_build(arguments: Any) -> int:
    result = search.build(forest_root(arguments), verify=bool(arguments.verify))
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
    verb = "would remove" if result.get("dry_run") else "removed"
    removed = result.get("removed") or []
    _print_lines([f"{verb}: {path}" for path in removed] or [f"{verb}: (nothing)"])
    return 0


def cmd_search(arguments: Any) -> int:
    result = search.query(
        forest_root(arguments),
        arguments.words,
        reader=arguments.reader,
        named=arguments.named or (),
        limit=arguments.limit,
    )
    _show_pages(arguments, result)
    return 0


def cmd_read(arguments: Any) -> int:
    result = search.read(
        forest_root(arguments),
        arguments.path,
        reader=arguments.reader,
        named=arguments.named or (),
        lines=search.parse_lines(arguments.lines),
    )
    _show_pages(arguments, result)
    return 0


def cmd_timeline_query(arguments: Any) -> int:
    result = timeline.query(
        forest_root(arguments),
        arguments.phrase,
        reader=arguments.reader,
        named=arguments.named or (),
        limit=arguments.limit,
    )
    _show_pages(arguments, result)
    return 0


def cmd_timeline_subject(arguments: Any) -> int:
    result = timeline.subject(
        forest_root(arguments),
        arguments.subject_id,
        reader=arguments.reader,
        named=arguments.named or (),
        limit=arguments.limit,
    )
    _show_pages(arguments, result)
    return 0


def cmd_mcp(arguments: Any) -> int:
    if not arguments.reader:
        raise MemoryForestError(
            "mcp_agent_unset",
            "Name the agent with --as. The reader id comes from the connection, not from a tool argument.",
        )
    return mcp.run(forest_root(arguments), arguments.reader, arguments.http)


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
_NAMED = arg("--named", action="append", default=None, help="an agent this task names; not a way for the question to widen scope")
_LIMIT = arg("--limit", type=int, default=10, help="how many pages to return")

COMMANDS: list[ForestCommand] = [
    ForestCommand(
        ("index", "build"),
        cmd_index_build,
        "Build or update .forest/index.sqlite. A reader never waits for this.",
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
        "Remove the derived index and its -wal and -shm files, and nothing else. The next query builds it again.",
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
        (arg("words", help="the words to look for"), _READER, _NAMED, _LIMIT),
    ),
    ForestCommand(
        ("read",),
        cmd_read,
        "Print a page or an addressed section, with the same masking as every other read.",
        (
            arg("path", help="a forest path, optionally with #address"),
            arg("--lines", default=None, help="only lines A-B"),
            _READER,
            _NAMED,
        ),
    ),
    ForestCommand(
        ("timeline", "query"),
        cmd_timeline_query,
        "What falls in a time phrase or a YYYY-MM-DD..YYYY-MM-DD range, including hours not yet filed.",
        (arg("phrase"), _READER, _NAMED, arg("--limit", type=int, default=50, help="how many pages to return")),
    ),
    ForestCommand(
        ("timeline", "subject"),
        cmd_timeline_subject,
        "The indexed sections of one subject, in time order.",
        (arg("subject_id"), _READER, _NAMED, arg("--limit", type=int, default=50, help="how many pages to return")),
    ),
    ForestCommand(
        ("mcp",),
        cmd_mcp,
        "Serve memory_search, memory_read, memory_timeline, memory_navigate and memory_log. "
        "Stdio is one JSON message per line. HTTP, including loopback, uses .forest/mcp/token in mode 0600.",
        (
            arg("--as", dest="reader", required=True, help="the agent this connection reads and logs as"),
            arg("--http", default=None, help="PORT, or HOST:PORT. PORT alone binds to loopback"),
        ),
    ),
]
