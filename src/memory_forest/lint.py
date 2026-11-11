"""The lint (contract, section 8): every rule that has a shape, each a code with a small check.

Each code is one function ``(forest) -> list[Hit]`` registered once in
``CODES`` with its class (violation, upkeep or due work), the rule that fixes
it, and whether a run may add a hit of it. The codes whose detection belongs
to modules that are not here yet are registered with ``supplied_later`` and
listed in ``SUPPLIED_LATER``: the lint reports them as not checked.

A baseline (``write_baseline``) keeps the hits of a run's start; ``lint``
given that baseline marks each hit the run added, and ``LintReport`` splits
them into what the run must fix (violations and the upkeep kinds a run may
not add) and what is the upkeep run's (everything that was there before).
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Final

from . import audit as forest_audit
from . import credentials as forest_credentials
from . import layout
from . import movelog
from . import numbers as forest_numbers
from . import record as forest_record
from . import repeats as forest_repeats
from .config import Config
from .errors import MemoryForestError
from .locking import atomic_write
from .model import (
    RECORD_SECTION,
    CurrentStateLine,
    TimelineLine,
    code_spans,
    split_line_ending,
)
from .registry import COLLECTION_TYPE, LEAF_KINDS, Problem, about_ids, parse_collection_page
from .view import ForestView, body_line_numbers, page_line_numbers, section_start, updated_date


VIOLATION: Final[str] = "violation"
UPKEEP: Final[str] = "upkeep"
DUE: Final[str] = "due"
CLASSES: Final[tuple[str, ...]] = (VIOLATION, UPKEEP, DUE)
BASELINE_SCHEMA: Final[str] = "memory-forest-lint-baseline/1"
REPORT_SCHEMA: Final[str] = "memory-forest-lint/1"
UPKEEP_EXAMPLES: Final[int] = 5
# Exit codes a stage acts on.
EXIT_CLEAN: Final[int] = 0
EXIT_RUN_MUST_FIX: Final[int] = 1


@dataclass(frozen=True, slots=True)
class Hit:
    """One lint finding: the code, its class, the page, the line (when there is one) and what is wrong."""

    code: str
    class_: str
    path: str
    line: int | None
    message: str
    size: int | None = None
    new: bool = False

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "class": self.class_,
            "code": self.code,
            "line": self.line,
            "message": self.message,
            "new": self.new,
            "path": self.path,
        }
        if self.size is not None:
            data["size"] = self.size
        return data

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{self.code}  {where} — {self.message}"


Check = Callable[[ForestView], list[Hit]]


@dataclass(frozen=True, slots=True)
class LintCode:
    code: str
    class_: str
    rule: str
    check: Check
    run_may_add: bool = True
    sized: bool = False


class NotChecked(NotImplementedError):
    """A code whose detection another module supplies; the lint reports it as not checked."""


def supplied_later(code: str) -> Check:
    def check(_forest: ForestView) -> list[Hit]:
        raise NotChecked(code)

    check.__name__ = code
    return check


# ─────────────────────────────── helpers ───────────────────────────────


def _hit(code: str, path: str, line: int | None, message: str, *, size: int | None = None) -> Hit:
    return Hit(code, CODES[code].class_ if code in CODES else VIOLATION, path, line, message, size)


def _from_problems(problems: Iterable[Problem]) -> list[Hit]:
    return [_hit(problem.code, problem.path, problem.line, problem.detail) for problem in problems]


def hits_from(problems: Iterable[Problem]) -> list[Hit]:
    """Hits made from ``forest check`` problems of the same codes (a module that registers a code uses it)."""
    return _from_problems(problems)


def _section_lines(forest: ForestView, path: str, name: str) -> list[tuple[int, str]]:
    """(line number, bare line) of every line of the named H2 sections of a page."""
    page = forest.page(path)
    found = []
    lines = page_line_numbers(page)
    for section in page.sections:
        if not section.is_named(name):
            continue
        start = lines.get(section.heading.casefold(), 0) + 1
        for offset, raw in enumerate(section.lines):
            found.append((start + offset, split_line_ending(raw)[0]))
    return found


def _body_lines(forest: ForestView, path: str, *, skip: Iterable[str] = (RECORD_SECTION,)) -> list[tuple[int, str]]:
    """(line number, bare line) of a page's head and sections, outside fenced blocks and the named sections."""
    return body_line_numbers(forest.page(path), tuple(skip))


def _leaf_pages(forest: ForestView) -> list[str]:
    return forest.with_role(layout.PageRole.LEAF)


def _is_collection(forest: ForestView, path: str) -> bool:
    return forest.role(path) is layout.PageRole.COLLECTION_PAGE


# ─────────────────────────────── violations ───────────────────────────────


def leaf_path_shape(forest: ForestView) -> list[Hit]:
    found = []
    for path in forest.paths:
        parts = PurePosixPath(path).parts
        if parts[0] != layout.STM:
            continue
        if len(parts) != 4:
            found.append(_hit("leaf_path_shape", path, None, f"{len(parts) - 2} folders below `{layout.STM}`, not 2"))
            continue
        odd = [name for name in (parts[1], parts[2], parts[3][:-3]) if not layout.is_slug(name)]
        if odd:
            found.append(_hit("leaf_path_shape", path, None,
                              "not a slug: " + ", ".join(f"`{name}`" for name in odd)))
    return found


def leaf_entity_missing(forest: ForestView) -> list[Hit]:
    return _from_problems(forest_audit.leaf_entity_missing(forest))


def leaf_entity_unknown(forest: ForestView) -> list[Hit]:
    return _from_problems(forest_audit.leaf_entity_unknown(forest))


def leaf_misplaced(forest: ForestView) -> list[Hit]:
    return _from_problems(forest_audit.leaf_misplaced(forest))


def leaf_too_large(forest: ForestView) -> list[Hit]:
    limit = forest.config.limits.leaf_max_bytes
    return [
        _hit("leaf_too_large", path, None, f"{size} bytes, over {limit}", size=size)
        for path in forest.leaves
        if (size := forest.page_bytes(path)) > limit
    ]


def leaf_dated_name(forest: ForestView) -> list[Hit]:
    found = []
    stems = layout.folder_stems(forest.paths)
    for path in forest.leaves:
        name = PurePosixPath(path).name
        if not layout.is_slug(name[:-3]):
            continue  # leaf_path_shape's
        problem = layout.name_problem(name, config=forest.config, siblings=stems[str(PurePosixPath(path).parent)])
        if problem:
            found.append(_hit("leaf_dated_name", path, None, problem))
    return found


_SECONDS_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])")
_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])"
)
_HASH_RE: Final[re.Pattern[str]] = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{32,}(?![0-9A-Za-z])")
_URL_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
# Clock times to the second in one line from which it counts as a capture series.
RECORDER_SECONDS_PER_LINE: Final[int] = 3


def recorder_rows(forest: ForestView, path: str) -> list[tuple[int, str]]:
    """(line, what) of each recorder row in a page outside its record: clock series to the second, ids, hashes."""
    found = []
    for number, line in _body_lines(forest, path):
        plain = _URL_RE.sub("", line)
        if len(_SECONDS_RE.findall(plain)) >= RECORDER_SECONDS_PER_LINE:
            found.append((number, "clock times to the second, one capture after another"))
        elif _UUID_RE.search(plain):
            found.append((number, "a message, run or attachment id"))
        elif _HASH_RE.search(plain):
            found.append((number, "a hash"))
    return found


def leaf_recorder_dump(forest: ForestView) -> list[Hit]:
    return [
        _hit("leaf_recorder_dump", path, number, f"recorder rows: {what}")
        for path in forest.leaves
        for number, what in recorder_rows(forest, path)
    ]


def leaf_kind_invalid(forest: ForestView) -> list[Hit]:
    found = []
    entities = forest.registry.entities
    for path in forest.leaves:
        kind = forest.page(path).field("kind").strip()
        if not kind:
            continue
        if kind not in LEAF_KINDS:
            found.append(_hit("leaf_kind_invalid", path, None, f"`kind: {kind}` is not one of the kinds of section 4"))
            continue
        subject = entities.get(forest.page(path).field("entity").strip())
        is_collection_subject = subject is not None and subject["type"] == COLLECTION_TYPE
        if (kind == "collection") != (_is_collection(forest, path) or is_collection_subject):
            found.append(_hit("leaf_kind_invalid", path, None,
                              "`kind: collection` is for a collection's pages, and a collection's pages carry it"))
    return found


def about_unknown(forest: ForestView) -> list[Hit]:
    found = []
    registry = forest.registry
    known = set(registry.entities) | {item["id"] for subject in registry.entities.values()
                                      for item in subject.get("entries", ())}
    excluded = forest.config.about_excluded_ids
    limit = forest.config.limits.about_max_ids
    for path in forest.leaves:
        page = forest.page(path)
        if page.frontmatter is None or "about" not in page.frontmatter:
            continue
        ids = about_ids(page.frontmatter.get("about"))
        own = page.field("entity").strip()
        if len(ids) > limit:
            found.append(_hit("about_unknown", path, None, f"`about:` names {len(ids)} ids, more than {limit}"))
        for subject_id in ids:
            if subject_id == own:
                found.append(_hit("about_unknown", path, None, f"`about:` names `{subject_id}`, the leaf's own entity"))
            elif subject_id in excluded:
                found.append(_hit("about_unknown", path, None,
                                  f"`about:` names `{subject_id}`, which no leaf names (the owner or the system)"))
            elif subject_id not in known:
                found.append(_hit("about_unknown", path, None, f"`about:` names `{subject_id}`, which is no registry id"))
    return found


def _registry_code(code: str) -> Check:
    def check(forest: ForestView) -> list[Hit]:
        return _from_problems(problem for problem in forest.registry.problems if problem.code == code)

    check.__name__ = code
    return check


def collection_entry_malformed(forest: ForestView) -> list[Hit]:
    """The registry's entry problems, and Timeline lines of a collection page that open with no entry's name."""
    found = _registry_code("collection_entry_malformed")(forest)
    for path in forest.with_role(layout.PageRole.COLLECTION_PAGE):
        text = forest.text(path)
        entries, _problems = parse_collection_page(text)
        names = [entry.name for entry in entries]
        start = section_start(forest.page(path), "Timeline") or 0
        section = forest.page(path).section("Timeline")
        if section is None or not names:
            continue
        for index, raw in section.items():
            line = TimelineLine.match(raw)
            if line is None:
                continue
            if not any(line.text.startswith(name + ":") or line.text.startswith(name + " ") for name in names):
                found.append(_hit("collection_entry_malformed", path, start + index,
                                  "a Timeline line of a collection page opens with its entry's name"))
    return found


_JSON_LINE_RE: Final[re.Pattern[str]] = re.compile(r'^\s*[\[{]\s*"[^"\n]+"\s*:')
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(```|~~~)\s*(?P<lang>[A-Za-z0-9_-]*)")


def machine_page(forest: ForestView) -> list[Hit]:
    """A merged original, a fenced JSON block, JSON object lines, or a table of ids and hashes as a forest page."""
    found = []
    for path in forest.paths:
        page = forest.page(path)
        reasons = []
        if page.field("status").strip() == "merged":
            reasons.append("a merged original (`status: merged`), which belongs in the archive")
        text_lines = forest.text(path).splitlines()
        if any((fence := _FENCE_RE.match(line)) and fence["lang"].casefold() in ("json", "jsonl") for line in text_lines):
            reasons.append("a fenced JSON block")
        elif sum(1 for line in text_lines if _JSON_LINE_RE.match(line)) >= 1:
            reasons.append("JSON objects")
        rows = [line for line in text_lines if line.lstrip().startswith("|")]
        id_rows = [line for line in rows if _UUID_RE.search(line) or _HASH_RE.search(line)]
        if len(id_rows) >= 3 and len(id_rows) * 2 >= len(rows):
            reasons.append("a table of ids or hashes")
        if reasons:
            found.append(_hit("machine_page", path, None, "a machine record: " + "; ".join(reasons)))
    return found


def current_state_lines(forest: ForestView) -> list[tuple[str, int, str]]:
    """(path, line, bare line) of every list line under ``## Current state`` of the tree pages and boards."""
    found = []
    for path in forest.with_role(layout.PageRole.TREE_PAGE, layout.PageRole.BOARD):
        for number, line in _section_lines(forest, path, "Current state"):
            if line.lstrip().startswith(("- ", "* ", "+ ")):
                found.append((path, number, line))
    return found


def current_state_untagged(forest: ForestView) -> list[Hit]:
    return [
        _hit("current_state_untagged", path, number, "no `(as of YYYY-MM-DD; source `path`)`")
        for path, number, line in current_state_lines(forest)
        if CurrentStateLine.match(line.strip()) is None
    ]


def current_state_stale(forest: ForestView) -> list[Hit]:
    """A current-state line whose source changed after its `as of`, or is gone, or is an entry moved elsewhere.

    An entry source (`<collection page>#<id>`) is dated by that entry's own lines, so another entry's later
    line leaves the line fresh; an entry the move log sends elsewhere is reported with its place now, so the
    line is restated or its source corrected."""
    found = []
    for path, number, line in current_state_lines(forest):
        state = CurrentStateLine.match(line.strip())
        if state is None:
            continue
        for source in state.sources:
            status, updated = forest.source_state(source)
            if status == "gone":
                found.append(_hit("current_state_stale", path, number, f"the source `{source}` is not there"))
            elif status == "moved":
                found.append(_hit("current_state_stale", path, number,
                                  f"the source `{source}` moved to `{forest.moved_to(source)}`"))
            elif updated is not None and updated.isoformat() > state.date:
                found.append(_hit("current_state_stale", path, number,
                                  f"the source `{source}` changed {updated.isoformat()}, after {state.date}"))
    return found


def _reference_spans(line: str) -> list[str]:
    paths = []
    for span in code_spans(line):
        text = span.text.strip().partition("#")[0].rstrip("/")
        if not layout.is_forest_path(text) or any(mark in text for mark in "<>*…{}[]"):
            continue
        if line[span.end : span.end + len(" (archived)")] == " (archived)":
            continue
        paths.append(text)
    return paths


def reference_stale(forest: ForestView) -> list[Hit]:
    """Code-span paths to forest pages or folders that are not there (records and registry lines aside)."""
    found = []
    for path in forest.paths:
        skip = [RECORD_SECTION, "Subjects"]
        for number, line in _body_lines(forest, path, skip=skip):
            if CurrentStateLine.match(line.strip()) is not None:
                line = line[: line.rfind(" (as of ")]
            for target in _reference_spans(line):
                if not forest.exists(target):
                    found.append(_hit("reference_stale", path, number,
                                      f"`{target}` is not there and is not marked ` (archived)`"))
    return found


def account_number_in_full(forest: ForestView) -> list[Hit]:
    found = []
    for path in forest.paths:
        text = forest.text(path)
        for span in forest_numbers.find_numbers(text, config=forest.config):
            found.append(_hit("account_number_in_full", path, span.line(text), f"a {span.kind} number in full"))
    return found


def credential_in_full(forest: ForestView) -> list[Hit]:
    """A line that holds a value sent to sign in or to pay (contract, section 4): the line, never the value."""
    found = []
    for path in forest.paths:
        for number in forest_credentials.changed_lines(forest.text(path), config=forest.config):
            found.append(_hit("credential_in_full", path, number, "a sign-in value stands in full"))
    return found


# ─────────────────────────────── upkeep ───────────────────────────────


def credential_candidate_unjudged(forest: ForestView) -> list[Hit]:
    """A code-shaped token in a quoted turn that no reader has judged (contract, section 4, Sign-in values):
    named by its line and place, never by its value."""
    from . import credential_ledger

    found = credential_ledger.unjudged(forest.root, ((path, forest.text(path)) for path in forest.paths),
                                       forest.config)
    return [_hit("credential_candidate_unjudged", item.path, item.line,
                 f"candidate {item.place} of the line waits for a reading (`forest credentials judge "
                 f"{item.name} --sign-in | --not-sign-in`)") for item in found]


def leaf_over_cap(forest: ForestView) -> list[Hit]:
    cap = forest.config.limits.leaf_cap_bytes
    return [
        _hit("leaf_over_cap", path, None, f"{size} bytes of content, over {cap}", size=size)
        for path in forest.leaves
        if (size := forest.content_bytes(path)) > cap
    ]


def record_over_budget(forest: ForestView) -> list[Hit]:
    found = []
    for path in forest.paths:
        plan = forest_record.plan_trim(forest.page(path), path, config=forest.config)
        if plan.over_budget and plan.applies:
            found.append(_hit("record_over_budget", path, None,
                              f"the record holds {plan.record_bytes} bytes, over {plan.budget}; a trim moves "
                              f"{len(plan.moved)} lines", size=plan.record_bytes))
    return found


def record_run_note(forest: ForestView) -> list[Hit]:
    return [
        _hit("record_run_note", path, number, "a record line that is none of the three kinds")
        for path in forest.paths
        for number, _line in forest_record.run_notes(forest.page(path), config=forest.config)
    ]


def _missing_key(code: str, key: str, *, collections: bool) -> Check:
    def check(forest: ForestView) -> list[Hit]:
        pages = forest.leaves if collections else _leaf_pages(forest)
        return [
            _hit(code, path, None, f"no `{key}:` in the frontmatter")
            for path in pages
            if not forest.page(path).field(key).strip()
        ]

    check.__name__ = code
    return check


def leaf_about_missing(forest: ForestView) -> list[Hit]:
    """A leaf with no ``about:`` (``about: none`` says it was read); collection pages carry none."""
    return [
        _hit("leaf_about_missing", path, None, "no `about:` in the frontmatter")
        for path in _leaf_pages(forest)
        if not forest.page(path).field("about").strip()
    ]


def leaf_lines_repeated(forest: ForestView) -> list[Hit]:
    """A leaf sharing ``limits.repeat_min_lines`` or more fact lines with another leaf of its subject; a line
    the judged ledger holds for the subject does not count (``repeats``)."""
    by_subject: dict[str, list[str]] = defaultdict(list)
    for path in forest.leaves:
        entity = forest.page(path).field("entity").strip()
        if entity:
            by_subject[entity].append(path)
    found = []
    limits = forest.config.limits
    judged = forest_repeats.judged(forest.root)
    for subject, paths in by_subject.items():
        if len(paths) < 2:
            continue
        pairs, _lines = forest_repeats.shared_lines(subject, {path: forest.page(path) for path in paths},
                                                    minimum=limits.repeat_min_chars, least=limits.repeat_min_lines,
                                                    judged_lines=judged)
        for first, second, count in pairs:
            for path, other in ((first, second), (second, first)):
                found.append(_hit("leaf_lines_repeated", path, None, f"{count} lines are also in `{other}`"))
    return found


def board_entity_missing(forest: ForestView) -> list[Hit]:
    found = []
    entities = forest.registry.entities
    for path in forest.boards:
        if forest.page(path).field("entity").strip():
            continue
        info = layout.classify(path)
        homed = [subject_id for subject_id, subject in entities.items() if subject["home"] == path]
        named = info.branch if info.branch in entities and entities[info.branch]["type"] != COLLECTION_TYPE else None
        subject = homed[0] if homed else named
        if subject:
            found.append(_hit("board_entity_missing", path, None, f"the board of the subject `{subject}` has no `entity:`"))
    return found


def board_state_empty(forest: ForestView) -> list[Hit]:
    """A board with no line under ``## Current state`` while its folder holds a leaf (collection pages aside)."""
    found = []
    leaves = forest.with_role(layout.PageRole.LEAF)
    for path in forest.boards:
        info = layout.classify(path)
        prefix = layout.leaf_folder(info.tree or "", info.branch or "") + "/"
        held = [leaf for leaf in leaves if leaf.startswith(prefix) and "/" not in leaf[len(prefix):]]
        if not held:
            continue
        if not any(line.strip() for _number, line in _section_lines(forest, path, "Current state")):
            found.append(_hit("board_state_empty", path, None,
                              f"`## Current state` holds no line while the folder holds {len(held)} leaves"))
    return found


def current_state_date_ahead(forest: ForestView) -> list[Hit]:
    found = []
    for path, number, line in current_state_lines(forest):
        state = CurrentStateLine.match(line.strip())
        if state is None:
            continue
        dated = [forest.source_state(source) for source in state.sources]
        if not dated or any(status != "page" or updated is None for status, updated in dated):
            continue
        latest = max(updated for _status, updated in dated if updated is not None).isoformat()
        if state.date > latest:
            found.append(_hit("current_state_date_ahead", path, number,
                              f"as of {state.date}, after the latest evidence of its source {latest}"))
    return found


def _channel_only(value: str, words: frozenset[str]) -> bool:
    parts = [part for part in value.split("-") if part]
    return bool(parts) and any(part in words for part in parts) and all(part in words or part.isdigit() for part in parts)


def leaf_channel_subtopic(forest: ForestView) -> list[Hit]:
    words = frozenset(word.casefold() for word in forest.config.lint.channel_words)
    found = []
    for path in forest.leaves:
        page = forest.page(path)
        entity = page.field("entity").strip()
        if any(part in words for part in entity.split("-")):
            continue  # the subject is a channel itself: its leaves are about it
        subtopic = page.field("subtopic").strip()
        stem = PurePosixPath(path).stem
        if entity and stem.startswith(entity + "-"):
            stem = stem[len(entity) + 1 :]
        named = []
        if subtopic and _channel_only(subtopic.casefold(), words):
            named.append(f"the sub-topic `{subtopic}`")
        if stem != entity and _channel_only(stem, words):
            named.append(f"the name `{PurePosixPath(path).stem}`")
        if named:
            found.append(_hit("leaf_channel_subtopic", path, None, " and ".join(named) + " names a channel"))
    return found


def timeline_zone_missing(forest: ForestView) -> list[Hit]:
    found = []
    for path in forest.leaves:
        page = forest.page(path)
        if page.field("timezone").strip():
            continue
        for number, line in _section_lines(forest, path, "Timeline"):
            parsed = TimelineLine.match(line)
            if parsed is not None and parsed.start and not parsed.zone:
                found.append(_hit("timeline_zone_missing", path, number,
                                  "a timed Timeline line with no zone, and no `timezone:`"))
    return found


_QUOTED_RE: Final[re.Pattern[str]] = re.compile(r'"[^"\n]*"|“[^”\n]*”|«[^»\n]*»')


def leaf_writer_remark(forest: ForestView) -> list[Hit]:
    phrases = [phrase.casefold() for phrase in forest.config.lint.remark_phrases if phrase.strip()]
    found = []
    for path in forest.leaves:
        for number, line in _body_lines(forest, path):
            if line.lstrip().startswith(">"):
                continue
            plain = _QUOTED_RE.sub('""', line).casefold()
            hit = next((phrase for phrase in phrases if phrase in plain), None)
            if hit is not None:
                found.append(_hit("leaf_writer_remark", path, number, f"a remark on verification (`{hit}`)"))
    return found


# ─────────────────────────────── the table ───────────────────────────────

_MISSING_KIND = _missing_key("leaf_kind_missing", "kind", collections=False)
_MISSING_UPDATED = _missing_key("leaf_updated_missing", "updated_at", collections=True)

_TABLE: Final[tuple[LintCode, ...]] = (
    # Violations.
    LintCode("leaf_path_shape", VIOLATION, "a leaf is `04 stm/<tree>/<branch>/<leaf>.md`, every name a slug",
             leaf_path_shape, False),
    LintCode("leaf_entity_missing", VIOLATION, "a leaf names its subject in `entity:`", leaf_entity_missing, False),
    LintCode("leaf_entity_unknown", VIOLATION, "`entity:` names a registered subject", leaf_entity_unknown, False),
    LintCode("leaf_misplaced", VIOLATION, "a leaf lives in its subject's home", leaf_misplaced, False),
    LintCode("leaf_too_large", VIOLATION, "a leaf page stays within `limits.leaf_max_bytes`; split it",
             leaf_too_large, False),
    LintCode("leaf_dated_name", VIOLATION, "a leaf is named by its sub-topic: no date, period or counter "
             "outside the allowed suffixes", leaf_dated_name, False),
    LintCode("leaf_recorder_dump", VIOLATION, "recorder rows stay in the lanes; write what happened once",
             leaf_recorder_dump, False),
    LintCode("leaf_kind_invalid", VIOLATION, "`kind:` is one of the kinds of section 4", leaf_kind_invalid, False),
    LintCode("about_unknown", VIOLATION, "`about:` names registered ids, never the leaf's own, the owner's or the "
             "system's, and at most `limits.about_max_ids`", about_unknown, False),
    LintCode("collection_entry_malformed", VIOLATION, "a collection entry keeps the shape of section 3",
             collection_entry_malformed, False),
    LintCode("machine_page", VIOLATION, "machine records live in the lanes, run directories or the archive",
             machine_page, False),
    LintCode("registry_line_invalid", VIOLATION, "a registry line keeps the shape of section 2",
             _registry_code("registry_line_invalid"), False),
    LintCode("registry_duplicate_id", VIOLATION, "one id names one subject", _registry_code("registry_duplicate_id"),
             False),
    LintCode("registry_duplicate_alias", VIOLATION, "an alias belongs to one subject",
             _registry_code("registry_duplicate_alias"), False),
    LintCode("registry_home_missing", VIOLATION, "a subject's home is there", _registry_code("registry_home_missing"),
             False),
    LintCode("registry_entry_is_subject", VIOLATION, "a collection entry's name belongs to no subject",
             _registry_code("registry_entry_is_subject"), False),
    LintCode("board_leaves_stale", VIOLATION, "`## Leaves` lists the board's folder (`forest check`)",
             supplied_later("board_leaves_stale"), False),
    LintCode("branch_list_stale", VIOLATION, "`## Branches` lists the tree's boards (`forest check`)",
             supplied_later("branch_list_stale"), False),
    LintCode("current_state_untagged", VIOLATION, "a current-state line ends with `(as of DATE; source `path`)`",
             current_state_untagged, False),
    LintCode("current_state_stale", VIOLATION, "a current-state line's source is there and has not changed since",
             current_state_stale, False),
    LintCode("reference_stale", VIOLATION, "a code-span path names a page or folder that is there, or is marked "
             "` (archived)`", reference_stale, False),
    LintCode("account_number_in_full", VIOLATION, "an account, IBAN, card, identity or person's phone number is "
             "written starred (`forest numbers star`)", account_number_in_full, False),
    LintCode("credential_in_full", VIOLATION, "a value sent to sign in or to pay stands as `[REDACTED]` "
             "(`forest credentials fix`)", credential_in_full, False),
    # Upkeep.
    LintCode("leaf_over_cap", UPKEEP, "a leaf's content stays within `limits.leaf_cap_bytes`; split it by sub-topic",
             leaf_over_cap, False, sized=True),
    LintCode("record_over_budget", UPKEEP, "a record over `limits.record_keep_bytes` is trimmed "
             "(`forest record trim`)", record_over_budget, False, sized=True),
    LintCode("record_run_note", UPKEEP, "a record holds only the three kinds of line; a run's note moves to the "
             "record history file (`forest record trim`)", record_run_note, False),
    LintCode("leaf_kind_missing", UPKEEP, "every leaf has `kind:`", _MISSING_KIND),
    LintCode("leaf_about_missing", UPKEEP, "every leaf but a collection page has `about:`", leaf_about_missing, False),
    LintCode("leaf_updated_missing", UPKEEP, "every leaf has `updated_at:`", _MISSING_UPDATED),
    LintCode("leaf_lines_repeated", UPKEEP, "a split moves lines; each fact line has one home "
             "(`forest repeats apply`; a line rightly in two leaves is judged once)", leaf_lines_repeated, False),
    LintCode("board_pointers_stale", UPKEEP, "`## Pointers` is what the `about:` lines elsewhere make of it",
             supplied_later("board_pointers_stale")),
    LintCode("board_pointer_stale", UPKEEP, "a pointer names another branch's page by its path",
             supplied_later("board_pointer_stale")),
    LintCode("board_entity_missing", UPKEEP, "a subject's board has `entity:`", board_entity_missing),
    LintCode("board_state_empty", UPKEEP, "a board's current state gathers what its leaves hold, by kind",
             board_state_empty),
    LintCode("current_state_date_ahead", UPKEEP, "`as of` is the date of the evidence, never later than its source",
             current_state_date_ahead),
    LintCode("leaf_channel_subtopic", UPKEEP, "a channel is never a sub-topic", leaf_channel_subtopic),
    LintCode("timeline_zone_missing", UPKEEP, "a timed Timeline line names its zone, or the leaf has `timezone:`",
             timeline_zone_missing),
    LintCode("credential_candidate_unjudged", UPKEEP, "a reader judges each code in a quoted turn once "
             "(`forest credentials judge`)", credential_candidate_unjudged),
    LintCode("leaf_writer_remark", UPKEEP, "remarks become open points in the source's words", leaf_writer_remark),
    LintCode("daily_section_unfiled", UPKEEP, "the leaves state the specifics of a filed daily section",
             supplied_later("daily_section_unfiled")),
    # Due work.
    LintCode("level_due", DUE, "`forest levels apply` carries the level out", supplied_later("level_due")),
    LintCode("category_too_large", DUE, "a category over `limits.category_max_leaves` splits by kind of subject",
             supplied_later("category_too_large")),
)
CODES: Final[dict[str, LintCode]] = {entry.code: entry for entry in _TABLE}
# The codes whose detection a later milestone supplies (the stage programs); each is registered above
# with a check that raises `NotChecked`. The derived lists (`maps`), the level rule (`levels`), the
# category limit (`collections`) and the agents (`agents`) register their codes with `register` when
# `lint` loads them.
SUPPLIED_LATER: Final[tuple[str, ...]] = ("daily_section_unfiled",)
# The modules that register codes of their own.
REGISTERING_MODULES: Final[tuple[str, ...]] = ("maps", "levels", "collections", "agents")
# Upkeep a run may not add (contract, section 8, How the lint holds a run).
RUN_MAY_NOT_ADD: Final[frozenset[str]] = frozenset(
    entry.code for entry in _TABLE if entry.class_ == UPKEEP and not entry.run_may_add
)
# Measured on the daily record: never held against a run.
SOURCE_LANE_CODES: Final[frozenset[str]] = frozenset({"daily_section_unfiled"})


def register(entry: LintCode) -> None:
    """Supply or replace a code's check (the modules that own a code's detection call this when loaded)."""
    if entry.code in CODES and CODES[entry.code].class_ != entry.class_:
        raise MemoryForestError("lint_code_invalid", "A code keeps its class.", details={"code": entry.code})
    CODES[entry.code] = entry
    if entry.class_ == UPKEEP and not entry.run_may_add:
        _RUN_MAY_NOT_ADD_EXTRA.add(entry.code)


_RUN_MAY_NOT_ADD_EXTRA: set[str] = set()


def load_registering_modules() -> None:
    """Import the modules that register codes (each registers once, at its import)."""
    import importlib

    for name in REGISTERING_MODULES:
        importlib.import_module(f"{__package__}.{name}")


# ─────────────────────────────── baselines ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Baseline:
    """The hits a run started from, per (code, path): how many, their messages and the largest size."""

    taken_at: str
    codes: frozenset[str]
    counts: Mapping[tuple[str, str], int]
    messages: Mapping[tuple[str, str], Counter[str]]
    sizes: Mapping[tuple[str, str], int] = field(default_factory=dict)

    @classmethod
    def from_hits(cls, hits: Iterable[Hit], *, codes: Iterable[str], taken_at: str) -> Baseline:
        counts: Counter[tuple[str, str]] = Counter()
        messages: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        sizes: dict[tuple[str, str], int] = {}
        for hit in hits:
            key = (hit.code, hit.path)
            counts[key] += 1
            messages[key][hit.message] += 1
            if hit.size is not None:
                sizes[key] = max(hit.size, sizes.get(key, 0))
        return cls(taken_at, frozenset(codes), dict(counts), dict(messages), sizes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "codes": sorted(self.codes),
            "hits": [
                {
                    "code": code,
                    "count": count,
                    "messages": sorted(self.messages.get((code, path), Counter()).elements()),
                    "path": path,
                    **({"size": self.sizes[(code, path)]} if (code, path) in self.sizes else {}),
                }
                for (code, path), count in sorted(self.counts.items())
            ],
            "schema": BASELINE_SCHEMA,
            "taken_at": self.taken_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Baseline:
        if not isinstance(data, dict) or data.get("schema") != BASELINE_SCHEMA:
            raise MemoryForestError("baseline_invalid", "The file is not a forest lint baseline.")
        counts: dict[tuple[str, str], int] = {}
        messages: dict[tuple[str, str], Counter[str]] = {}
        sizes: dict[tuple[str, str], int] = {}
        try:
            for item in data["hits"]:
                key = (str(item["code"]), str(item["path"]))
                counts[key] = int(item["count"])
                messages[key] = Counter(str(text) for text in item.get("messages", ()))
                if "size" in item:
                    sizes[key] = int(item["size"])
            return cls(str(data["taken_at"]), frozenset(str(code) for code in data["codes"]), counts, messages, sizes)
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryForestError("baseline_invalid", "The baseline file is damaged.") from exc


def write_baseline(path: str | os.PathLike[str], baseline: Baseline) -> None:
    atomic_write(Path(path), (json.dumps(baseline.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"))


def load_baseline(path: str | os.PathLike[str]) -> Baseline:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MemoryForestError("baseline_invalid", "The baseline file cannot be read.",
                                details={"path": str(path)}) from exc
    return Baseline.from_dict(data)


def _origins(root: Path, since: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Since ``since``: each page's earlier paths (moves), and the pages each page was split from."""
    earlier: dict[str, set[str]] = defaultdict(set)
    split_from: dict[str, set[str]] = defaultdict(set)
    for item in movelog.read(root, include_non_moving=True):
        if item.time < since or item.to_path is None:
            continue
        source, target = item.from_path.partition("#")[0], item.to_path.partition("#")[0]
        if item.operation in movelog.NON_MOVING_OPERATIONS:
            split_from[target].add(source)
        else:
            earlier[target].update({source, *earlier.get(source, ())})
    return earlier, split_from


def mark_new(hits: list[Hit], baseline: Baseline, root: Path) -> list[Hit]:
    """The hits with ``new`` set on each one the run added since the baseline.

    A page's hits are matched against the baseline's hits of its code on the
    page, on the paths the move log says it had since, and on the pages it
    was split from. A sized hit (a leaf over the cap, a record over its
    budget) is new when it grew; others are new past the baseline's count,
    those whose message the baseline does not hold first. A code the baseline
    was not taken with never makes a hit new.
    """
    earlier, split_from = _origins(root, baseline.taken_at)

    def sources(path: str) -> set[str]:
        found, todo = set(), [path]
        while todo:
            page = todo.pop()
            if page in found:
                continue
            found.add(page)
            todo.extend(earlier.get(page, ()))
            todo.extend(split_from.get(page, ()))
        return found

    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, hit in enumerate(hits):
        groups[(hit.code, hit.path)].append(index)
    new: set[int] = set()
    for (code, path), indexes in groups.items():
        if code not in baseline.codes or code in SOURCE_LANE_CODES:
            continue
        keys = [(code, source) for source in sources(path)]
        allowed = sum(baseline.counts.get(key, 0) for key in keys)
        sizes = [baseline.sizes[key] for key in keys if key in baseline.sizes]
        if CODES[code].sized and sizes:
            new.update(index for index in indexes if (hits[index].size or 0) > max(sizes))
            continue
        if len(indexes) <= allowed:
            continue
        held: Counter[str] = Counter()
        for key in keys:
            held += baseline.messages.get(key, Counter())
        unmatched = []
        for index in indexes:
            if held[hits[index].message] > 0:
                held[hits[index].message] -= 1
            else:
                unmatched.append(index)
        excess = len(indexes) - allowed
        new.update(unmatched[-excess:] if excess <= len(unmatched) else unmatched)
    return [replace(hit, new=index in new) for index, hit in enumerate(hits)]


# ─────────────────────────────── running ───────────────────────────────


@dataclass(slots=True)
class LintReport:
    hits: list[Hit]
    checked: list[str]
    not_checked: list[str]
    against: Baseline | None = None
    taken_at: str = ""

    @property
    def violations(self) -> list[Hit]:
        return [hit for hit in self.hits if hit.class_ == VIOLATION]

    @property
    def upkeep(self) -> list[Hit]:
        return [hit for hit in self.hits if hit.class_ == UPKEEP]

    @property
    def due(self) -> list[Hit]:
        return [hit for hit in self.hits if hit.class_ == DUE]

    @property
    def must_fix(self) -> list[Hit]:
        """What the run must fix before it completes: every violation without a baseline; with one, the
        violations and the upkeep kinds a run may not add that the run added."""
        if self.against is None:
            return self.violations
        return [
            hit for hit in self.hits
            if hit.new and (hit.class_ == VIOLATION or hit.code in RUN_MAY_NOT_ADD
                            or hit.code in _RUN_MAY_NOT_ADD_EXTRA)
        ]

    @property
    def upkeep_run(self) -> list[Hit]:
        """The upkeep run's work: the rest of the violations and upkeep."""
        fixed = {id(hit) for hit in self.must_fix}
        return [hit for hit in self.hits if hit.class_ != DUE and id(hit) not in fixed]

    @property
    def exit_code(self) -> int:
        return EXIT_RUN_MUST_FIX if self.must_fix else EXIT_CLEAN

    def baseline(self) -> Baseline:
        return Baseline.from_hits(self.hits, codes=self.checked, taken_at=self.taken_at)

    def as_dict(self) -> dict[str, Any]:
        upkeep_codes: dict[str, dict[str, Any]] = {}
        for hit in self.upkeep:
            entry = upkeep_codes.setdefault(
                hit.code, {"count": 0, "examples": [], "new": 0, "rule": CODES[hit.code].rule}
            )
            entry["count"] += 1
            entry["new"] += int(hit.new)
            if len(entry["examples"]) < UPKEEP_EXAMPLES:
                entry["examples"].append(hit.as_dict())
        return {
            "against": self.against.taken_at if self.against else None,
            "checked": self.checked,
            "counts": {
                "due": len(self.due),
                "must_fix": len(self.must_fix),
                "upkeep": len(self.upkeep),
                "violations": len(self.violations),
            },
            "due": [hit.as_dict() for hit in self.due],
            "exit_code": self.exit_code,
            "must_fix": [hit.as_dict() for hit in self.must_fix],
            "not_checked": self.not_checked,
            "schema": REPORT_SCHEMA,
            "taken_at": self.taken_at,
            "upkeep": {"codes": dict(sorted(upkeep_codes.items())), "items": [hit.as_dict() for hit in self.upkeep]},
            "upkeep_run": [hit.as_dict() for hit in self.upkeep_run],
            "violations": [hit.as_dict() for hit in self.violations],
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def lint(
    root: str | os.PathLike[str],
    *,
    config: Config | None = None,
    codes: Iterable[str] | None = None,
    against: Baseline | None = None,
) -> LintReport:
    """Run the named codes (every code by default) on the forest at ``root``."""
    load_registering_modules()
    forest = ForestView.open(root, config=config)
    wanted = list(CODES) if codes is None else list(dict.fromkeys(codes))
    unknown = [code for code in wanted if code not in CODES]
    if unknown:
        raise MemoryForestError("lint_code_unknown", "No such lint code.", details={"codes": unknown})
    hits: list[Hit] = []
    checked: list[str] = []
    not_checked: list[str] = []
    for code in wanted:
        try:
            found = CODES[code].check(forest)
        except NotImplementedError:
            not_checked.append(code)
            continue
        checked.append(code)
        hits.extend(found)
    hits.sort(key=lambda hit: (CLASSES.index(hit.class_), hit.code, hit.path, hit.line or 0, hit.message))
    if against is not None:
        hits = mark_new(hits, against, forest.root)
    else:
        hits = [replace(hit, new=hit.class_ == VIOLATION) for hit in hits]
    return LintReport(hits, checked, not_checked, against, _now())


def render(report: LintReport, *, limit: int = 200) -> list[str]:
    """The text report: a summary, what the run must fix, the upkeep counted with examples, due work."""
    lines = [
        f"forest lint: {len(report.violations)} violations, {len(report.upkeep)} upkeep, {len(report.due)} due"
        + (f"; {len(report.must_fix)} the run must fix (against the baseline of {report.against.taken_at})"
           if report.against else "")
    ]
    shown = report.must_fix if report.against else report.violations
    label = "must fix" if report.against else "violation"
    for hit in shown[:limit]:
        lines.append(f"{label} {hit.render()}")
    if len(shown) > limit:
        lines.append(f"… {len(shown) - limit} more (--json lists all)")
    if report.against:
        for hit in [item for item in report.upkeep_run if item.class_ == VIOLATION][:limit]:
            lines.append(f"upkeep run: violation {hit.render()}")
    counts: Counter[str] = Counter(hit.code for hit in report.upkeep)
    for code in sorted(counts):
        example = next(hit for hit in report.upkeep if hit.code == code)
        lines.append(f"upkeep {code} {counts[code]}: {CODES[code].rule}; e.g. {example.render()}")
    for hit in report.due[:limit]:
        lines.append(f"due {hit.render()}")
    if report.not_checked:
        lines.append("not checked: " + ", ".join(report.not_checked))
    return lines
