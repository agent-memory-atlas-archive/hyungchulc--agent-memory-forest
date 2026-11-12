from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn

from . import __version__
from . import archive as forest_archive
from . import layout as forest_layout
from . import credentials as forest_credentials
from . import movelog as forest_movelog
from . import registry as forest_registry
from .config import CONFIG_FILENAME, DEFAULT_CONFIG, Config, find_root, load_config
from .core import audit_forest, doctor_forest, initialize_forest, validate_forest
from .errors import MemoryForestError
from .index import index_forest, route_index, search_index
from .model import SCHEMA_VERSION
from .safety import require_real_root
from .retrieval import (
    read_query_plan_source,
    retrieve_index,
    structured_context_index,
)
from .writer import (
    apply_daily,
    apply_structured_sweep,
    promote,
    read_plan_source,
)


# The `memory-forest` command is the 0.3 CLI. It reports that release, not the package version
# (`memory_forest.__version__`) that the `forest` command reports.
LEGACY_VERSION: Final[str] = "0.3.0"


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise MemoryForestError(
            "invalid_arguments",
            "The command arguments are invalid.",
            details={"reason": message},
        )


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="memory-forest",
        description=(
            "Build and query a local, provenance-aware memory forest. "
            "Commands never use the network."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit compact deterministic JSON instead of indented JSON.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {LEGACY_VERSION}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser(
        "init", help="Safely initialize an empty local forest."
    )
    init_parser.add_argument(
        "root",
        help="New forest path under an existing real direct parent",
    )
    init_parser.add_argument(
        "--example",
        action="store_true",
        help="Create a private synthetic 00-06 demo forest.",
    )

    doctor_parser = commands.add_parser(
        "doctor", help="Check local configuration, SQLite FTS5, and index state."
    )
    doctor_parser.add_argument("root", help="Forest root")

    validate_parser = commands.add_parser(
        "validate", help="Validate paths, routes, encoding, limits, and permissions."
    )
    validate_parser.add_argument("root", help="Forest root")

    audit_parser = commands.add_parser(
        "audit", help="Validate the forest and enforce adjacent-layer wikilinks."
    )
    audit_parser.add_argument("root", help="Forest root")

    index_parser = commands.add_parser(
        "index", help="Atomically build the local SQLite FTS5 index."
    )
    index_parser.add_argument("root", help="Forest root")

    route_parser = commands.add_parser(
        "route", help="Search route metadata without returning memory bodies."
    )
    route_parser.add_argument("root", help="Forest root")
    route_parser.add_argument("query", help="Literal route query")
    route_parser.add_argument("--limit", type=int, default=10, help="Result limit, 1-100")

    search_parser = commands.add_parser(
        "search", help="Search the local index; bodies remain hidden by default."
    )
    search_parser.add_argument("root", help="Forest root")
    search_parser.add_argument("query", help="Literal full-text query")
    search_parser.add_argument("--limit", type=int, default=10, help="Result limit, 1-100")
    search_parser.add_argument(
        "--include-body",
        action="store_true",
        help="Explicitly include full memory bodies in results.",
    )

    retrieve_parser = commands.add_parser(
        "retrieve",
        help="Return a validated root-first structured trail without memory bodies.",
    )
    retrieve_parser.add_argument("root", help="Forest root")
    retrieve_parser.add_argument("query", help="Literal retrieval query")
    retrieve_parser.add_argument(
        "--limit", type=int, default=10, help="Trail limit, 1-100"
    )
    retrieve_parser.add_argument(
        "--query-plan",
        help=(
            "Strict query-only expansion plan as a regular JSON file, or '-' for stdin"
        ),
    )

    context_parser = commands.add_parser(
        "structured-context",
        help="Return bounded current Forest bodies for one integrated Structured sweep.",
    )
    context_parser.add_argument("root", help="Forest root")
    context_parser.add_argument("query", help="Literal context query")
    context_parser.add_argument(
        "--limit", type=int, default=3, help="Trail limit, 1-10"
    )

    daily_parser = commands.add_parser(
        "apply-daily",
        help="Apply one strict provenance-bound Daily transaction.",
    )
    daily_parser.add_argument("root", help="Forest root")
    daily_parser.add_argument(
        "plan",
        help="Private Daily plan JSON file, or '-' for standard input",
    )

    promote_parser = commands.add_parser(
        "promote",
        help="Apply reviewed semantic Daily-to-structured promotions.",
    )
    promote_parser.add_argument("root", help="Forest root")
    promote_parser.add_argument(
        "plan",
        help="Private promotion plan JSON file, or '-' for standard input",
    )

    structured_parser = commands.add_parser(
        "apply-structured",
        help="Apply one integrated whole-Forest Structured sweep.",
    )
    structured_parser.add_argument("root", help="Forest root")
    structured_parser.add_argument(
        "plan",
        help="Private Structured sweep plan JSON file, or '-' for standard input",
    )
    return parser


def run_command(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    command = arguments.command
    if command == "init":
        result = initialize_forest(arguments.root, example=arguments.example)
    elif command == "doctor":
        result = doctor_forest(arguments.root)
    elif command == "validate":
        result = validate_forest(arguments.root)
    elif command == "audit":
        result = audit_forest(arguments.root)
    elif command == "index":
        result = index_forest(arguments.root)
    elif command == "route":
        result = route_index(arguments.root, arguments.query, limit=arguments.limit)
    elif command == "search":
        result = search_index(
            arguments.root,
            arguments.query,
            include_body=arguments.include_body,
            limit=arguments.limit,
        )
    elif command == "retrieve":
        query_plan = (
            read_query_plan_source(arguments.query_plan, stdin=sys.stdin.buffer)
            if arguments.query_plan is not None
            else None
        )
        result = retrieve_index(
            arguments.root,
            arguments.query,
            query_plan=query_plan,
            limit=arguments.limit,
        )
    elif command == "structured-context":
        result = structured_context_index(
            arguments.root,
            arguments.query,
            limit=arguments.limit,
        )
    elif command == "apply-daily":
        plan = read_plan_source(arguments.plan, stdin=sys.stdin.buffer)
        result = apply_daily(arguments.root, plan)
    elif command == "promote":
        plan = read_plan_source(arguments.plan, stdin=sys.stdin.buffer)
        result = promote(arguments.root, plan)
    elif command == "apply-structured":
        plan = read_plan_source(arguments.plan, stdin=sys.stdin.buffer)
        result = apply_structured_sweep(arguments.root, plan)
    else:
        raise MemoryForestError(
            "unknown_command",
            "The requested command is not supported.",
            details={"command": command},
        )
    return result, 0 if result.get("ok") else 1


_READ_COMMANDS: Final = frozenset({"route", "search", "retrieve", "structured-context"})


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    compact_json = "--json" in raw_arguments
    if compact_json:
        raw_arguments = [argument for argument in raw_arguments if argument != "--json"]
    parser = build_parser()
    try:
        arguments = parser.parse_args(raw_arguments)
        arguments.json = compact_json
        result, exit_code = run_command(arguments)
        if arguments.command in _READ_COMMANDS:
            # What a read shows masks sign-in values and stars numbers (contract, section 4).
            result = forest_credentials.shown(result, config=load_config(arguments.root))
    except MemoryForestError as exc:
        result = {
            "error": exc.as_dict(),
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 2
    except OSError as exc:
        result = {
            "error": {
                "code": "filesystem_error",
                "details": {"reason": exc.__class__.__name__},
                "message": "A local filesystem operation failed safely.",
            },
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 2
    except Exception:
        result = {
            "error": {
                "code": "internal_error",
                "details": {},
                "message": "The command failed without exposing internal details.",
            },
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 2
    except KeyboardInterrupt:
        result = {
            "error": {
                "code": "interrupted",
                "details": {},
                "message": "The operation was interrupted.",
            },
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 130
    _emit(result, compact=compact_json)
    return exit_code


def _emit(result: dict[str, Any], *, compact: bool) -> None:
    if compact:
        text = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write(text + "\n")



# ═══════════════════════════════ the `forest` command (version 1) ═══════════════════════════════
#
# Each subcommand is one function `(arguments) -> exit code` and one line in
# FOREST_COMMANDS: its words, the function, its help and its arguments. Every
# command except `init` takes `--root` and `--json`. The forest root is `--root`
# when it is given, otherwise the nearest folder at or above the working
# directory that holds forest.toml. `init` is the only command that creates a
# forest. Any other command, when there is no such folder, stops before it
# writes anything.

_ROOT_HELP = (
    "the forest root; when omitted, the nearest folder at or above the current one that holds forest.toml"
)


@dataclass(frozen=True, slots=True)
class Argument:
    flags: tuple[str, ...]
    options: dict[str, Any]


def arg(*flags: str, **options: Any) -> Argument:
    return Argument(flags, options)


@dataclass(frozen=True, slots=True)
class ForestCommand:
    words: tuple[str, ...]
    run: Callable[[argparse.Namespace], int]
    help: str
    arguments: tuple[Argument, ...] = ()
    uses_root: bool = True


def _no_forest(root: str | None) -> MemoryForestError:
    """What is missing, and the ``forest init`` or ``--root`` line that supplies it."""
    if root is None:
        message = (
            "No forest.toml in this folder or any folder above it. "
            "Create one with `forest init my-forest`, or name one with `forest --root my-forest`."
        )
    else:
        message = (
            f"No forest.toml in {root}. "
            f"Create one with `forest init {root}`, or name a forest with `forest --root <folder>`."
        )
    return MemoryForestError("no_forest", message)


def forest_root(arguments: argparse.Namespace) -> Path:
    """``--root`` when given, else the nearest folder at or above the working directory with forest.toml.

    A folder without that file is not a forest. ``init`` is the only command that creates one, and it does
    not call this.
    """
    chosen = getattr(arguments, "root", None)
    if chosen:
        root = require_real_root(chosen)
        if not (root / CONFIG_FILENAME).is_file():
            raise _no_forest(shlex.quote(str(root)))
        return root
    found = find_root(os.getcwd())
    if found is None:
        raise _no_forest(None)
    return require_real_root(found)


# The settings of the forest a `forest` command works on, so that everything it prints shows a sign-in
# value masked and a number starred, as the write door would have written them (contract, section 4).
_SHOWN_CONFIG: Config | None = None


def _shown(value: Any) -> Any:
    return forest_credentials.shown(value, config=_SHOWN_CONFIG) if _SHOWN_CONFIG is not None else value


def _print_json(value: Any) -> None:
    sys.stdout.write(json.dumps(_shown(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        sys.stdout.write(_shown(line) + "\n")


def cmd_init(arguments: argparse.Namespace) -> int:
    result = forest_layout.initialize(arguments.directory)
    if arguments.json:
        _print_json({"ok": True, **result})
    else:
        _print_lines([f"created a forest at {result['root']}", *(f"  {item}" for item in result["created"])])
    return 0


def cmd_registry_build(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    built = forest_registry.build_and_write(root)
    problems = built.problems
    if arguments.json:
        _print_json(
            {
                "agents": built.agents,
                "names": len(built.alias_index),
                "ok": True,
                "path": forest_layout.REGISTRY_FILE,
                "problems": [problem.as_dict() for problem in problems],
                "subjects": len(built.entities),
            }
        )
    else:
        _print_lines(
            [
                f"registry: {len(built.entities)} subjects, {len(built.alias_index)} names, "
                f"{len(problems)} problems -> {forest_layout.REGISTRY_FILE}",
                *(problem.render() for problem in problems),
            ]
        )
    return 0


def cmd_registry_check(arguments: argparse.Namespace) -> int:
    problems = forest_registry.check(forest_root(arguments))
    if arguments.json:
        _print_json({"ok": not problems, "problems": [problem.as_dict() for problem in problems]})
    else:
        _print_lines(problem.render() for problem in problems)
    return 1 if problems else 0


def cmd_registry_show(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    built = forest_registry.load(root)
    found = built.subject(arguments.name) or built.entry(arguments.name) or built.lookup(arguments.name)
    if found is not None:
        _print_json(forest_credentials.shown(found, config=load_config(root)))
        return 0
    matches = built.find(arguments.name)
    if arguments.json:
        _print_json({"matches": [match.as_dict() for match in matches], "ok": False})
    else:
        if matches:
            sys.stderr.write(f"forest: no subject is named {arguments.name!r}; names that come close:\n")
        else:
            sys.stderr.write(f"forest: no subject is named {arguments.name!r}\n")
        _print_lines(
            f"{match.id} — {match.name} · {match.level} · {match.home}"
            + (f" (an entry of {match.collection})" if match.collection else "")
            for match in matches
        )
    return 1


def cmd_move_resolve(arguments: argparse.Namespace) -> int:
    resolution = forest_movelog.resolve(forest_root(arguments), arguments.old_path)
    if arguments.json:
        _print_json(resolution.as_dict())
    else:
        lines = [f"{resolution.status}: {resolution.path or resolution.query}"]
        if resolution.archive:
            lines.append(f"  archived as {forest_layout.ARCHIVE_DIR}/{resolution.archive['archive_file']}")
        if resolution.successor:
            lines.append(f"  its facts went to {resolution.successor}")
        for item in resolution.chain:
            lines.append(f"  {item.time} {item.operation}: {item.from_path} -> {item.to_path}")
        if resolution.note:
            lines.append(f"  note: {resolution.note}")
        _print_lines(lines)
    return 0 if resolution.status != "unknown" else 1


def cmd_archive_put(arguments: argparse.Namespace) -> int:
    entry = forest_archive.put(
        forest_root(arguments),
        arguments.path,
        reason=arguments.reason,
        successor=arguments.successor,
        keep=arguments.keep,
        lock=arguments.lock_token,
    )
    if arguments.json:
        _print_json({"entry": entry.as_dict(), "ok": True})
    else:
        verb = "copied" if arguments.keep else "archived"
        _print_lines([f"{verb} {entry.path} -> {entry.archive_path}"])
    return 0


def cmd_archive_show(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    text = forest_archive.show(root, arguments.path).decode("utf-8", "surrogateescape")
    data = forest_credentials.shown(text, config=load_config(root)).encode("utf-8", "surrogateescape")
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return 0


def cmd_archive_list(arguments: argparse.Namespace) -> int:
    found = forest_archive.listing(forest_root(arguments), arguments.path)
    if arguments.json:
        _print_json({"entries": [{"kind": forest_archive.COPY_KIND, **row.as_dict()}
                                 if isinstance(row, forest_archive.ArchiveEntry) else row.as_dict()
                                 for row in found], "ok": True})
        return 0
    lines = []
    for row in found:
        if isinstance(row, forest_archive.ArchiveEntry):
            lines.append(f"{row.time}  {forest_archive.COPY_KIND:<14}  {row.path}  {row.bytes} B  {row.reason}"
                         + (f" (successor {row.successor})" if row.successor else ""))
        else:
            lines.append(f"{row.time}  {forest_archive.HISTORY_KIND:<14}  {row.path}  {row.lines} lines "
                         f"{row.first} to {row.last}  {row.archive_path}")
    _print_lines(lines)
    return 0


def cmd_recover(arguments: argparse.Namespace) -> int:
    from . import journal
    from .locking import writer_lock

    root = forest_root(arguments)
    standing = journal.read(root)
    with writer_lock(root, label="forest recover"):  # taking the lock rolls a standing journal back
        journal.roll_back(root)
    if arguments.json:
        _print_json({"ok": True, "rolled_back": standing is not None,
                     "label": standing.get("label") if standing else None})
    elif standing is None:
        _print_lines(["no unfinished commit"])
    return 0


def cmd_archive_check(arguments: argparse.Namespace) -> int:
    problems = forest_archive.verify(forest_root(arguments))
    if arguments.json:
        _print_json({"ok": not problems, "problems": [problem.as_dict() for problem in problems]})
    else:
        _print_lines(problem.render() for problem in problems)
    return 1 if problems else 0


_LOCK_TOKEN = arg(
    "--lock-token",
    default=None,
    help="The writer lock token of a caller that already holds the lock.",
)

FOREST_COMMANDS: list[ForestCommand] = [
    ForestCommand(("init",), cmd_init, "Create a new forest in an empty folder.",
                  (arg("directory", help="the new forest's folder"),), uses_root=False),
    ForestCommand(("registry", "build"), cmd_registry_build, "Compile .forest/registry.json from the pages."),
    ForestCommand(("registry", "check"), cmd_registry_check, "List every registry problem; exit 1 when there is one."),
    ForestCommand(("registry", "show"), cmd_registry_show, "Show a subject or entry by id or by any name.",
                  (arg("name", help="an id, a name or an alias"),)),
    ForestCommand(("recover",), cmd_recover, "Roll back a commit a stopped writer left unfinished (the next "
                  "writer does it too)."),
    ForestCommand(("move", "resolve"), cmd_move_resolve, "Follow the move log from an old path.",
                  (arg("old_path", help="a forest path as it once was"),)),
    ForestCommand(("archive", "put"), cmd_archive_put, "Archive a page (with --keep: copy it and leave it).",
                  (arg("path"), arg("--reason", required=True), arg("--successor", default=None),
                   arg("--keep", action="store_true"), _LOCK_TOKEN)),
    ForestCommand(("archive", "show"), cmd_archive_show, "Print the newest archived copy of a page.",
                  (arg("path"),)),
    ForestCommand(("archive", "check"), cmd_archive_check, "Verify every archive copy and record history block "
                  "against the manifest; exit 1 when one differs."),
    ForestCommand(("archive", "list"), cmd_archive_list, "List the archive manifest: whole-page copies and record "
                  "history blocks, with their kind.",
                  (arg("--path", default=None, help="only this page's entries"),)),
]

from .tool_commands import TOOL_COMMANDS  # noqa: E402  (move, levels, collections)

FOREST_COMMANDS.extend(TOOL_COMMANDS)


# The record, numbers, check and lint commands live in their own module, which uses the helpers above.
from . import cli_upkeep as _cli_upkeep  # noqa: E402

FOREST_COMMANDS += _cli_upkeep.COMMANDS

from .cli_intake import COMMANDS as _INTAKE_COMMANDS  # noqa: E402

FOREST_COMMANDS += _INTAKE_COMMANDS

from .cli_find import COMMANDS as _FIND_COMMANDS  # noqa: E402

FOREST_COMMANDS += _FIND_COMMANDS


def build_forest_parser(commands: Sequence[ForestCommand] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forest",
        description="Keep an Agent Memory Forest: a local, plain-Markdown memory. Commands never use the network.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print JSON")
    rooted = argparse.ArgumentParser(add_help=False, parents=[common])
    rooted.add_argument("--root", default=argparse.SUPPRESS, help=_ROOT_HELP)
    parser.add_argument("--json", action="store_true", default=False, help="print JSON")
    parser.add_argument("--root", default=None, help=_ROOT_HELP)
    groups: dict[tuple[str, ...], Any] = {(): parser.add_subparsers(dest="command", required=True, metavar="COMMAND")}
    parsers: dict[tuple[str, ...], argparse.ArgumentParser] = {}
    for command in commands if commands is not None else FOREST_COMMANDS:
        for depth in range(1, len(command.words)):
            prefix = command.words[:depth]
            if prefix not in parsers:
                group_parser = groups[prefix[:-1]].add_parser(prefix[-1], help=f"{prefix[-1]} commands")
                parsers[prefix] = group_parser
                groups[prefix] = group_parser.add_subparsers(
                    dest="_".join(("command", *prefix)), required=True, metavar="COMMAND"
                )
        leaf = groups[command.words[:-1]].add_parser(
            command.words[-1],
            help=command.help,
            description=command.help,
            parents=[rooted if command.uses_root else common],
        )
        for argument in command.arguments:
            leaf.add_argument(*argument.flags, **argument.options)
        leaf.set_defaults(_run=command.run, _uses_root=command.uses_root)
    return parser


def _config_for_output(arguments: argparse.Namespace) -> Config:
    """The settings of the forest the command names, or the defaults when it names none that can be read."""
    try:
        return load_config(forest_root(arguments))
    except (MemoryForestError, OSError):
        return DEFAULT_CONFIG


def _intake_hook(argv: Sequence[str]) -> bool:
    """Whether this invocation is ``forest intake --hook`` (not ``intake status``)."""
    if "--hook" not in argv or "intake" not in argv:
        return False
    index = argv.index("intake")
    return not (index + 1 < len(argv) and argv[index + 1] == "status")


def _hook_place(argv: Sequence[str]) -> str:
    if "--from" in argv:
        index = argv.index("--from")
        if index + 1 < len(argv) and argv[index + 1].replace("-", "").isalnum():
            return argv[index + 1][:80]
    return "intake"


def _root_for_hook_log(argv: Sequence[str]):
    from pathlib import Path

    candidate = None
    if "--root" in argv:
        index = argv.index("--root")
        if index + 1 < len(argv):
            candidate = Path(argv[index + 1])
    if candidate is None:
        candidate = Path.cwd()
    if (candidate / "forest.toml").is_file():
        return candidate
    return None


def _silent_hook_failure(argv: Sequence[str], class_name: str) -> int:
    root = _root_for_hook_log(argv)
    if root is not None:
        from .intake import log_failure

        log_failure(root, class_name, _hook_place(argv))
    return 0


def forest_main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    hook = _intake_hook(raw)
    parser = build_forest_parser()
    try:
        arguments = parser.parse_args(raw)
    except SystemExit:
        if hook:
            return _silent_hook_failure(raw, "intake_arguments")
        raise
    global _SHOWN_CONFIG
    _SHOWN_CONFIG = None
    try:
        # Resolve the forest before the command runs, so a new command cannot write into a folder that
        # is not one. `init` is the only command registered with uses_root false.
        if getattr(arguments, "_uses_root", True):
            forest_root(arguments)
        _SHOWN_CONFIG = _config_for_output(arguments)
        return arguments._run(arguments)
    except MemoryForestError as exc:
        if hook:
            return _silent_hook_failure(raw, exc.code)
        if arguments.json:
            _print_json({"error": exc.as_dict(), "ok": False})
        else:
            sys.stderr.write(_shown(f"forest: {exc.code}: {exc.message}\n"))
            for key, value in sorted(exc.details.items()):
                sys.stderr.write(_shown(f"  {key}: {value}\n"))
            if exc.next_step:
                sys.stderr.write(_shown(f"  next: {exc.next_step}\n"))
            if exc.command:
                sys.stderr.write(_shown(f"  command: {exc.command}\n"))
            if exc.after_seconds is not None:
                sys.stderr.write(f"  after_seconds: {exc.after_seconds:g}\n")
        return 2
    except UnicodeError:
        if hook:
            return _silent_hook_failure(raw, "UnicodeError")
        sys.stderr.write("forest: read_failed: a file could not be read as text\n")
        return 2
    except OSError as exc:
        if hook:
            return _silent_hook_failure(raw, exc.__class__.__name__)
        sys.stderr.write(f"forest: filesystem_error: {exc.__class__.__name__}\n")
        return 2
    except KeyboardInterrupt:
        if hook:
            return _silent_hook_failure(raw, "KeyboardInterrupt")
        return 130
    except Exception as exc:
        if hook:
            return _silent_hook_failure(raw, exc.__class__.__name__)
        sys.stderr.write(f"forest: failed: {exc.__class__.__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
