"""The ``forest`` subcommands that move things: ``move …``, ``levels …``, ``collections …`` and ``repeats …``.

Each writing command takes ``--dry-run`` (print the plan as JSON, write
nothing), ``--why`` (the reason its move-log and record lines give) and
``--lock-token`` (the token of a caller that already holds the writer lock).
"""

from __future__ import annotations

import argparse
from typing import Any

from . import collections as forest_collections
from . import levels as forest_levels
from . import move as forest_move
from . import repeats as forest_repeats
from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root

_DRY = arg("--dry-run", action="store_true", help="print the plan as JSON and write nothing")
_WHY = arg("--why", default=None, help="the reason the move log and record lines give")
_LOCK = arg("--lock-token", default=None, help="the writer lock token of a caller that already holds the lock")
_WRITING = (_DRY, _WHY, _LOCK)
_DECISIONS = arg("--decisions", default=None, metavar="FILE",
                 help="a JSON list of {subject, digest, home, reason}: the leaf that keeps a line, or `both`")


def _report(arguments: argparse.Namespace, result: dict[str, Any]) -> int:
    if arguments.json or getattr(arguments, "dry_run", False):
        _print_json({"ok": True, **result})
        return 0
    lines = [f"{' ; '.join(result.get('operations') or ['nothing to do'])}"]
    for kind in ("created", "changed", "removed", "archived"):
        for path in result.get(kind) or ():
            lines.append(f"  {kind}: {path}")
    for note in result.get("notes") or ():
        lines.append(f"  note: {note}")
    _print_lines(lines)
    return 0


def _move(operation: str, **fixed: Any):
    def command(arguments: argparse.Namespace) -> int:
        values = {key: vars(arguments)[key] for key in fixed}
        result = forest_move.execute(
            forest_root(arguments), operation, dry_run=arguments.dry_run, lock=arguments.lock_token,
            why=arguments.why, **{fixed[key]: value for key, value in values.items()},
        )
        return _report(arguments, result)

    return command


def cmd_move_plan(arguments: argparse.Namespace) -> int:
    steps = forest_move.read_plan_file(arguments.file)
    result = forest_move.execute(forest_root(arguments), "plan", dry_run=arguments.dry_run, lock=arguments.lock_token,
                                 why=arguments.why, operations=steps)
    return _report(arguments, result)


def cmd_levels_check(arguments: argparse.Namespace) -> int:
    found = forest_levels.check(forest_root(arguments), arguments.subjects)
    if arguments.json:
        _print_json({"due": [item.as_dict() for item in found], "ok": not found})
    else:
        _print_lines(item.render() for item in found)
    return 1 if found else 0


def cmd_levels_apply(arguments: argparse.Namespace) -> int:
    result = forest_levels.apply(forest_root(arguments), arguments.subjects, dry_run=arguments.dry_run,
                                 lock=arguments.lock_token)
    if arguments.json or arguments.dry_run:
        _print_json({"ok": not result["refused"], **result})
    else:
        lines = [f"{item['subject']}: {item['level']} -> {item['new_level']} ({item['why']})"
                 for item in result["changes"]]
        lines += [f"{item['subject']}: refused: {item['error']['code']}: {item['error']['message']}"
                  for item in result["refused"]]
        _print_lines(lines or ["nothing is due"])
    return 1 if result["refused"] else 0


def cmd_repeats_apply(arguments: argparse.Namespace) -> int:
    decisions = forest_repeats.read_decisions(arguments.decisions)
    result = forest_repeats.execute(forest_root(arguments), decisions, dry_run=arguments.dry_run,
                                    lock=getattr(arguments, "lock_token", None), why=arguments.why)
    if arguments.json:
        _print_json({"ok": True, **result})
        return 0
    done = result.get("result") or {}
    verb = "would be " if arguments.dry_run else ""
    lines = [f"{' ; '.join(result.get('operations') or ['nothing to do'])}"]
    for path, items in (done.get("removed") or {}).items():
        homes = sorted({item["home"] for item in items})
        lines.append(f"  {path}: {len(items)} lines {verb}taken out, kept in {', '.join(homes)}")
    for path, index in (done.get("folded") or {}).items():
        lines.append(f"  {path}: {verb}archived, every line standing in {index}")
    for path, why in (done.get("shells_kept") or {}).items():
        lines.append(f"  {path}: left with no fact line, kept ({why})")
    lines.append(f"  judged: {len(done.get('judged') or [])}; waiting for a reading: "
                 f"{len(done.get('undecided') or [])}; hits after: {done.get('hits_after')}")
    lines += forest_repeats.problems_text({"hits": done.get("hits_after"), "pending": 0, "folds": 0,
                                           "undecided": done.get("undecided") or []})[1:]
    _print_lines(lines)
    return 0


def cmd_repeats_plan(arguments: argparse.Namespace) -> int:
    arguments.dry_run = True
    return cmd_repeats_apply(arguments)


def cmd_repeats_check(arguments: argparse.Namespace) -> int:
    result = forest_repeats.check(forest_root(arguments))
    if arguments.json:
        _print_json(result)
    else:
        _print_lines(forest_repeats.problems_text(result))
    return 0 if result["ok"] and not result["undecided"] else 1


def _collections(action: str):
    def command(arguments: argparse.Namespace) -> int:
        result = forest_collections.execute(forest_root(arguments), action, arguments.target,
                                            dry_run=arguments.dry_run, lock=arguments.lock_token, why=arguments.why)
        return _report(arguments, result)

    return command


def cmd_collections_cite_entries(arguments: argparse.Namespace) -> int:
    result = forest_collections.cite_entries(forest_root(arguments), dry_run=arguments.dry_run,
                                             lock=arguments.lock_token)
    if arguments.json or arguments.dry_run:
        _print_json({"ok": True, **result})
        return 0
    outcomes = result["outcomes"]
    lines = [f"{result['lines_with_collection_source']} of {result['lines']} current-state lines cite a collection "
             f"page: {outcomes['one']} name one entry, {outcomes['several']} several, {outcomes['none']} none",
             f"{result['references_rewritten']} sources now cite entries; {result['dates_corrected']} dates brought "
             f"back; {result['lines_left_stale']} lines left stale"]
    lines += [f"  changed: {path}" for path in result["pages"]]
    _print_lines(lines)
    return 0


def cmd_collections_list(arguments: argparse.Namespace) -> int:
    found = forest_collections.pages(forest_root(arguments))
    if arguments.json:
        _print_json({"ok": True, "pages": found})
    else:
        lines = []
        for page in found:
            lines.append(f"{page['path']} ({page['bytes']} B, {page['collection']})")
            for entry in page["entries"]:
                days = ", ".join(entry["days"]) or "no counted day"
                lines.append(f"  {entry['id']} — {entry['name']}: {days}")
        _print_lines(lines)
    return 0


TOOL_COMMANDS: list[ForestCommand] = [
    ForestCommand(("move", "leaf"), _move("leaf", source="source", target="target", entity="entity"),
                  "Move a leaf to another folder or name.",
                  (arg("source"), arg("target", help="a leaf path, or a branch folder"),
                   arg("--entity", default=None, help="the subject the leaf belongs to after the move"), *_WRITING)),
    ForestCommand(("move", "rename"), _move("rename", target="target", new="new", name="name"),
                  "Give a subject a new id or name, or a branch a new name.",
                  (arg("target", help="a subject id, or <tree>/<branch>"), arg("new", nargs="?", default=None),
                   arg("--name", default=None, help="the subject's new name"), *_WRITING)),
    ForestCommand(("move", "promote"), _move("promote", subject="subject"),
                  "Raise a subject one level: a leaf subject to a branch, a branch to a tree.",
                  (arg("subject"), *_WRITING)),
    ForestCommand(("move", "merge"), _move("merge", keep="keep", drop="drop"),
                  "Merge two subjects that are one: DROP's lines go to KEEP.",
                  (arg("keep"), arg("drop"), *_WRITING)),
    ForestCommand(("move", "archive"), _move("archive", path="path", reason="reason", successor="successor"),
                  "Take a page out of the forest into the archive; references are marked (archived).",
                  (arg("path"), arg("--reason", required=True), arg("--successor", default=None), *_WRITING)),
    ForestCommand(("move", "move-branch"), _move("move-branch", branch="branch", tree="tree", name="name"),
                  "Move a branch with its board, folder and registry lines to another tree.",
                  (arg("branch", help="<tree>/<branch>"), arg("tree", help="the tree it goes to"),
                   arg("--as", dest="name", default=None, help="the branch's name there"), *_WRITING)),
    ForestCommand(("move", "new-tree"), _move("new-tree", tree="tree", name="name", description="description"),
                  "Create an empty area tree.",
                  (arg("tree"), arg("--name", default=None), arg("--description", required=True), *_WRITING)),
    ForestCommand(("move", "new-agent"), _move("new-agent", id="id", name="name", role="role", aliases="aliases"),
                  "Create an agent's tree: registry line, tree page, index and its line under ## Agents.",
                  (arg("id"), arg("--name", default=None, help="its name (default: the id)"),
                   arg("--role", default=None, help="one sentence on what it is for (default: not set)"),
                   arg("--alias", dest="aliases", action="append", default=[]), *_WRITING)),
    ForestCommand(("move", "retire-tree"), _move("retire-tree", tree="tree", into="into"),
                  "Retire an agent's tree (status ended) or an emptied tree into the page that took its rules.",
                  (arg("tree"), arg("--into", default=None), *_WRITING)),
    ForestCommand(("move", "fold-tree"), _move("fold-tree", tree="tree", into="into", name="name", type="type"),
                  "Fold a tree back into a branch of another tree.",
                  (arg("tree"), arg("--into", required=True), arg("--as", dest="name", default=None),
                   arg("--type", default=None, help="the subject's type as a branch (an agent's default: component)"),
                   *_WRITING)),
    ForestCommand(("move", "set-type"), _move("set-type", subject="subject", type="type"),
                  "Retype a subject (set-type <id> agent makes it an agent).",
                  (arg("subject"), arg("type"), *_WRITING)),
    ForestCommand(("move", "set"), _move("set", path="path", kind="kind", about="about", standing="standing"),
                  "Set a leaf's kind, about or standing.",
                  (arg("path"), arg("--kind", default=None), arg("--about", default=None),
                   arg("--standing", default=None), *_WRITING)),
    ForestCommand(("move", "recut-stream"), _move("recut-stream", subject="subject", leaves="leaves"),
                  "Cut a stream of event pages of one subject again by period.",
                  (arg("subject"), arg("leaves", nargs="+"), *_WRITING)),
    ForestCommand(("move", "plan"), cmd_move_plan, "Run many operations from one JSON plan, all or nothing.",
                  (arg("file"), *_WRITING)),
    ForestCommand(("levels", "check"), cmd_levels_check, "List every level change that is due; exit 1 when one is.",
                  (arg("--subjects", nargs="+", default=None),)),
    ForestCommand(("levels", "apply"), cmd_levels_apply, "Carry out every level change that is due.",
                  (arg("--subjects", nargs="+", default=None), _DRY, _LOCK)),
    ForestCommand(("collections", "demote"), _collections("demote"), "Fold a leaf subject into its collection.",
                  (arg("target", metavar="subject"), *_WRITING)),
    ForestCommand(("collections", "admit"), _collections("admit"), "Move a collection entry out into a leaf.",
                  (arg("target", metavar="entry"), *_WRITING)),
    ForestCommand(("collections", "recut"), _collections("recut"), "Cut a collection page past the cap.",
                  (arg("target", metavar="page"), *_WRITING)),
    ForestCommand(("collections", "cite-entries"), cmd_collections_cite_entries, "Make each current-state line that "
                  "cites a bare collection page cite the entries it names, archiving each page first.",
                  (_DRY, _LOCK)),
    ForestCommand(("collections", "list"), cmd_collections_list, "List every collection page with its entries."),
    ForestCommand(("repeats", "plan"), cmd_repeats_plan, "Print what `repeats apply` would take out, move, fold and "
                  "judge, with the lines still waiting for a reading; write nothing.", (_DECISIONS, _WHY)),
    ForestCommand(("repeats", "apply"), cmd_repeats_apply, "Take each repeated fact line to its one home, fold the "
                  "pages left empty into the index leaf, and judge the lines the decisions keep in both.",
                  (_DECISIONS, *_WRITING)),
    ForestCommand(("repeats", "check"), cmd_repeats_check, "Count the repeated lines left; exit 1 while a run would "
                  "still change something or a line waits for a reading."),
]

