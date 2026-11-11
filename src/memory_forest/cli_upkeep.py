"""The ``forest record``, ``forest numbers``, ``forest check`` and ``forest lint`` commands.

``cli`` imports this module after its own helpers are defined and adds
``COMMANDS`` to its command table. ``forest check`` runs every check of
``checks.FOREST_CHECKS``, or the ones it names; with ``--sync`` it first
brings the derived lists up to the pages under the writer lock.
"""

from __future__ import annotations

import argparse

from . import lint as forest_lint
from . import maps as forest_maps
from . import numbers as forest_numbers
from . import record as forest_record
from .checks import FOREST_CHECKS, run_checks
from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root
from .config import load_config
from .errors import MemoryForestError


# ─────────────────────────────── forest check ───────────────────────────────


def cmd_check(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    config = load_config(root)
    synced: list[dict] = []
    if arguments.sync:
        result = forest_maps.apply(root, lock=arguments.lock_token, config=config)
        synced = result.get("result") or []
    chosen, problems = run_checks(root, config, arguments.names)
    if arguments.json:
        _print_json({"checks": [check.name for check in chosen], "ok": not problems, "synced": synced,
                     "problems": [problem.as_dict() for problem in problems]})
    else:
        _print_lines(f"{change['path']}: ## {change['section']} brought up to the pages" for change in synced)
        _print_lines(problem.render() for problem in problems)
    return 1 if problems else 0


def _check_arguments() -> tuple:
    names = ", ".join(check.name for check in FOREST_CHECKS)
    return (
        arg("names", nargs="*", metavar="NAME", help=f"run only these checks ({names})"),
        arg("--sync", action="store_true", help="first bring the derived lists up to the pages, under the writer lock"),
        arg("--lock-token", default=None, help="The writer lock token of a caller that already holds the lock."),
    )


# ─────────────────────────────── forest lint ───────────────────────────────


def cmd_lint(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    against = forest_lint.load_baseline(arguments.against) if arguments.against else None
    report = forest_lint.lint(root, codes=arguments.codes, against=against)
    if arguments.baseline:
        forest_lint.write_baseline(arguments.baseline, report.baseline())
    if arguments.json:
        _print_json(report.as_dict())
    else:
        _print_lines(forest_lint.render(report))
    return report.exit_code


# ─────────────────────────────── forest record ───────────────────────────────


def _pages(arguments: argparse.Namespace) -> list[str]:
    if not arguments.paths and not arguments.all:
        raise MemoryForestError("invalid_arguments", "Name the pages, or pass --all for every page.")
    return list(arguments.paths)


def cmd_record_size(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    found = [forest_record.size(root, path) for path in arguments.paths]
    if arguments.json:
        _print_json({"ok": True, "pages": found})
    else:
        for item in found:
            kinds = ", ".join(f"{label} {count}" for label, count in item["kinds"].items())
            due = ", ".join(item["due"]) or "nothing"
            _print_lines([f"{item['path']}: content {item['content_bytes']} B, record {item['record_bytes']} B "
                          f"({kinds}); due: {due}"])
    return 0


def cmd_record_kinds(arguments: argparse.Namespace) -> int:
    report = forest_record.kinds(forest_root(arguments), examples=arguments.examples)
    if arguments.json:
        _print_json({"ok": True, **report.as_dict()})
        return 0
    lines = []
    for path, counts in sorted(report.pages.items()):
        lines.append(f"{path}: " + ", ".join(f"{label} {count}" for label, count in counts.items() if count))
    lines.append("total: " + ", ".join(f"{label} {count}" for label, count in report.totals.items()))
    for label, items in report.examples.items():
        for path, number, text in items:
            lines.append(f"  {label}  {path}:{number}  {text}")
    _print_lines(lines)
    return 0


def cmd_record_trim(arguments: argparse.Namespace) -> int:
    root = forest_root(arguments)
    results = forest_record.trim(root, _pages(arguments), all_pages=arguments.all, dry_run=arguments.dry_run,
                                 lock=arguments.lock_token)
    applied = [result for result in results if result.plan.applies]
    if arguments.json:
        _print_json({"dry_run": arguments.dry_run, "ok": True, "pages": [result.as_dict() for result in applied]})
    else:
        verb = "would move" if arguments.dry_run else "moved"
        lines = [
            f"{result.path}: {verb} {len(result.plan.moved)} lines, kept {len(result.plan.kept)} ({result.plan.why})"
            + (f" -> {result.archive_path}" if result.archive_path else "")
            for result in applied
        ]
        lines.append(f"{len(applied)} of {len(results)} pages {'to trim' if arguments.dry_run else 'trimmed'}")
        _print_lines(lines)
    return 0


# ─────────────────────────────── forest numbers ───────────────────────────────


def _print_numbers(found: list[forest_numbers.PageNumbers], *, json_out: bool, extra: dict) -> None:
    if json_out:
        _print_json({**extra, "pages": [item.as_dict() for item in found]})
        return
    lines = []
    for item in found:
        kinds = ", ".join(f"{count} {kind}" for kind, count in item.kinds.items())
        state = f" -> starred, copy {item.archive_path}" if item.written else ""
        lines.append(f"{item.path}: {kinds} in full (lines {', '.join(map(str, item.lines))}){state}")
    lines.append(f"{len(found)} pages")
    _print_lines(lines)


def cmd_numbers_check(arguments: argparse.Namespace) -> int:
    found = forest_numbers.check(forest_root(arguments), arguments.paths, all_pages=arguments.all)
    _print_numbers(found, json_out=arguments.json, extra={"ok": not found})
    return 1 if found else 0


def cmd_numbers_star(arguments: argparse.Namespace) -> int:
    found = forest_numbers.star(forest_root(arguments), _pages(arguments), all_pages=arguments.all,
                                dry_run=arguments.dry_run, lock=arguments.lock_token)
    _print_numbers(found, json_out=arguments.json, extra={"dry_run": arguments.dry_run, "ok": True})
    return 0


_LOCK = arg("--lock-token", default=None, help="The writer lock token of a caller that already holds the lock.")
_PATHS = arg("paths", nargs="*", metavar="PATH", help="forest pages, as pages write them")
_ALL = arg("--all", action="store_true", help="every page of the four forest layers")
_DRY = arg("--dry-run", action="store_true", help="print the plan; write nothing")

COMMANDS: list[ForestCommand] = [
    ForestCommand(("record", "size"), cmd_record_size, "A page's content and record bytes, its record lines by kind "
                  "and what is due.", (arg("paths", nargs="+", metavar="PATH"),)),
    ForestCommand(("record", "kinds"), cmd_record_kinds, "Count the forest's record lines by kind and page.",
                  (arg("--examples", type=int, default=0, metavar="N", help="show N lines of each kind"),)),
    ForestCommand(("record", "trim"), cmd_record_trim, "Trim update records over their budget, moving lines to "
                  "the archive copy.", (_PATHS, _ALL, _DRY, _LOCK)),
    ForestCommand(("numbers", "check"), cmd_numbers_check, "List the pages that write a number in full (lines and "
                  "kinds, never digits); exit 1 when there is one.", (_PATHS, _ALL)),
    ForestCommand(("numbers", "star"), cmd_numbers_star, "Star the numbers written in full, archiving each page "
                  "first.", (_PATHS, _ALL, _DRY, _LOCK)),
    ForestCommand(("check",), cmd_check, "Run every check (or those named): structure, derived lists, pointers, "
                  "agents, levels, categories; one line per problem; exit 1 when there is one. --sync first brings "
                  "the derived lists up to the pages.", _check_arguments()),
    ForestCommand(("lint",), cmd_lint, "Check the contract's rules; exit 1 when the run must fix something.",
                  (arg("--baseline", default=None, metavar="FILE", help="write the present hits to FILE"),
                   arg("--against", default=None, metavar="FILE",
                       help="report only what is new since the baseline in FILE"),
                   arg("--codes", nargs="+", default=None, metavar="CODE", help="run only these codes"))),
]

