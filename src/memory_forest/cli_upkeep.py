"""The ``forest record``, ``forest numbers``, ``forest credentials``, ``forest check`` and ``forest lint`` commands.

``cli`` imports this module after its own helpers are defined and adds
``COMMANDS`` to its command table. ``forest check`` runs every check of
``checks.FOREST_CHECKS``, or the ones it names; with ``--sync`` it first
brings the derived lists up to the pages under the writer lock.
"""

from __future__ import annotations

import argparse

from . import credentials as forest_credentials
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
    root = forest_root(arguments)
    report = forest_record.kinds(root, examples=arguments.examples)
    config = load_config(root)
    report.examples = {label: [(path, number, forest_credentials.shown(text, config=config))
                               for path, number, text in items] for label, items in report.examples.items()}
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


def cmd_record_history(arguments: argparse.Namespace) -> int:
    lines = [line.render() for line in forest_record.archived_record_lines(forest_root(arguments), arguments.path)]
    if arguments.json:
        _print_json({"lines": lines, "ok": True, "path": arguments.path})
    else:
        _print_lines(lines or ["no archived record lines"])
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


# ─────────────────────────────── forest credentials ───────────────────────────────


def _print_credentials(found: list[forest_credentials.PageCredentials], *, json_out: bool, extra: dict,
                       lanes: list[dict] | None = None) -> None:
    if json_out:
        _print_json({**extra, "lanes": lanes or [], "pages": [item.as_dict() for item in found]})
        return
    lines = []
    for item in found:
        state = f" -> masked, copy {item.archive_path}" if item.written else ""
        lines.append(f"{item.path}: a sign-in value in full (lines {', '.join(map(str, item.lines))}){state}")
    lines.append(f"{len(found)} pages")
    for lane in lanes or []:
        line = f"{lane['lane']}: {lane['lines']} lines that today's rules would mask"
        if lane.get("written"):
            line += f" -> {lane['written']} rewritten"
        if lane.get("kept"):
            line += f"; {lane['kept']} kept as written (not in the writer's encoding)"
        lines.append(line)
    _print_lines(lines)


def cmd_credentials_check(arguments: argparse.Namespace) -> int:
    from . import intake

    root = forest_root(arguments)
    found = forest_credentials.check(root, arguments.paths, all_pages=arguments.all)
    lanes = intake.lane_masking(root) if arguments.all or not arguments.paths else []
    _print_credentials(found, json_out=arguments.json, extra={"ok": not found and not lanes}, lanes=lanes)
    return 1 if found or lanes else 0


def cmd_credentials_fix(arguments: argparse.Namespace) -> int:
    from . import intake

    root = forest_root(arguments)
    found = forest_credentials.fix(root, _pages(arguments), all_pages=arguments.all,
                                   dry_run=arguments.dry_run, lock=arguments.lock_token)
    lanes = intake.lane_masking(root, fix=not arguments.dry_run) if arguments.all else []
    _print_credentials(found, json_out=arguments.json, extra={"dry_run": arguments.dry_run, "ok": True}, lanes=lanes)
    return 0


def cmd_credentials_candidates(arguments: argparse.Namespace) -> int:
    from . import credential_ledger
    from .view import ForestView

    view = ForestView.open(forest_root(arguments))
    found = credential_ledger.unjudged(view.root, ((path, view.text(path)) for path in view.paths), view.config)
    if arguments.json:
        _print_json({"candidates": [item.as_dict() for item in found], "ok": not found})
    else:
        _print_lines([item.name for item in found] + [f"{len(found)} candidates wait for a reading"])
    return 1 if found else 0


def cmd_credentials_judge(arguments: argparse.Namespace) -> int:
    from . import credential_ledger

    if arguments.sign_in == arguments.not_sign_in:
        raise MemoryForestError("invalid_arguments", "Pass exactly one of --sign-in and --not-sign-in.")
    added = credential_ledger.judge(forest_root(arguments), arguments.candidates, sign_in=arguments.sign_in,
                                    lock=arguments.lock_token)
    verdict = "sign-in values" if arguments.sign_in else "not sign-in values"
    if arguments.json:
        _print_json({"added": added, "ok": True, "verdict": verdict})
    else:
        _print_lines([f"{added} judged {verdict}" + ("; `forest credentials fix --all` masks them on the pages"
                                                     if arguments.sign_in and added else "")])
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
                  "the page's record history file.", (_PATHS, _ALL, _DRY, _LOCK)),
    ForestCommand(("record", "history"), cmd_record_history, "Print a page's record lines that its record history "
                  "file and archive copies keep, oldest first.", (arg("path", metavar="PATH"),)),
    ForestCommand(("numbers", "check"), cmd_numbers_check, "List the pages that write a number in full (lines and "
                  "kinds, never digits); exit 1 when there is one.", (_PATHS, _ALL)),
    ForestCommand(("numbers", "star"), cmd_numbers_star, "Star the numbers written in full on the named pages, "
                  "or on every page with --all (one of the two is required), archiving each page first.", (_PATHS, _ALL, _DRY, _LOCK)),
    ForestCommand(("credentials", "check"), cmd_credentials_check, "List the pages that hold a value sent to sign in "
                  "or to pay (line numbers, never the value), and how many lane lines today's rules would mask; exit 1 "
                  "when there is one.", (_PATHS, _ALL)),
    ForestCommand(("credentials", "fix"), cmd_credentials_fix, "Replace each sign-in value with [REDACTED] on the "
                  "named pages, or on every page with --all (one of the two is required), archiving each page first. "
                  "--all also rewrites the lane lines today's rules would mask, in place.", (_PATHS, _ALL, _DRY, _LOCK)),
    ForestCommand(("credentials", "candidates"), cmd_credentials_candidates, "List the codes in quoted turns that "
                  "no reader has judged, as PATH:LINE:PLACE (never the value); exit 1 when there is one."),
    ForestCommand(("credentials", "judge"), cmd_credentials_judge, "Record a reader's judgement of candidates: "
                  "--sign-in masks the value wherever it appears, --not-sign-in stops counting it. Only keyed "
                  "digests are kept.",
                  (arg("candidates", nargs="+", metavar="PATH:LINE:PLACE"),
                   arg("--sign-in", action="store_true", help="the values are sign-in values"),
                   arg("--not-sign-in", action="store_true", help="the values are not sign-in values"), _LOCK)),
    ForestCommand(("check",), cmd_check, "Run every check (or those named): journal, structure, derived lists, pointers, "
                  "agents, levels, categories; one line per problem; exit 1 when there is one. --sync first brings "
                  "the derived lists up to the pages.", _check_arguments()),
    ForestCommand(("lint",), cmd_lint, "Check the contract's rules; exit 1 when the run must fix something.",
                  (arg("--baseline", default=None, metavar="FILE", help="write the present hits to FILE"),
                   arg("--against", default=None, metavar="FILE",
                       help="report only what is new since the baseline in FILE"),
                   arg("--codes", nargs="+", default=None, metavar="CODE", help="run only these codes"))),
]

