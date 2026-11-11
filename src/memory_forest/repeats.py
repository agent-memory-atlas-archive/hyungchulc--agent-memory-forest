"""Repeated lines: one home for each fact (contract, section 3, One home for each fact; section 8).

The lint's ``leaf_lines_repeated`` counts the **fact lines** two leaves of one
subject share. A fact line is a line of a leaf's head or free sections (its
``## Links`` and ``## Update record`` aside) of ``limits.repeat_min_chars``
or more, that is neither inside a fenced block (a document kept as written)
nor a bare reference (a list item that is nothing but code spans, links or
URLs). A line that a reading found rightly held by more than one leaf is
judged once, in the judged ledger ``.forest/repeats-judged.jsonl`` (one JSON
row per subject and line digest), and the count leaves it out.

``forest repeats plan | apply | check`` takes the count to zero:

1. **Reference lines, by code.** Within one subject a fact recorded elsewhere
   has one reference line, in the subject's **index leaf**: the leaf whose
   reference lines name the most distinct pages (ties: a page without a
   period suffix first, then a ``kind: profile`` page, then the shorter
   path). A reference line of another leaf of the subject whose text stands
   in the index leaf leaves that leaf; a reference line on a period page that
   names a sibling period page of its stream moves to the index leaf. A
   reference line never has its home in the page it names.
2. **The other shared lines, by a reading.** A decisions file names, per
   subject and line digest, the leaf that keeps the line (it leaves the
   others) or ``both``: the line is rightly in more than one leaf, and the
   apply appends one row to the judged ledger.
3. **Shells.** A leaf left with nothing but its title, its description, its
   headings, ``## Links`` and ``## Update record`` is folded into the index
   leaf (archived, its references and old path following to the index leaf)
   when each line taken from it stands there; otherwise it stays, listed.

Every change goes through one checked plan (``plan.Plan``) under the writer
lock and its undo journal, the ledger rows included: each removed line must
stand in its home, and apart from the removed lines, the headings of the free
sections they empty and those sections' blank lines (and the one record line
naming where the lines are kept) no byte of a changed page differs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any, Final

from . import layout
from .locking import LockToken
from .model import CODE_SPAN_RE, RECORD_SECTION, Page, ReferenceLine, parse_page, split_line_ending
from .plan import LINKS_SECTION, Plan, refuse, run
from .view import body_line_numbers

LEDGER_SCHEMA: Final[str] = "memory-forest-repeats-judged/1"
BOTH: Final[str] = "both"
# A line held by more leaves of one subject than this is a form line, not a copied fact.
REPEAT_MAX_LEAVES: Final[int] = 6
# Sections a removal never drops when it empties them: the page's frame.
FRAME_SECTIONS: Final[tuple[str, ...]] = ("Daily totals", "Timeline", LINKS_SECTION, RECORD_SECTION)
_NOT_FACTS: Final[tuple[str, ...]] = (RECORD_SECTION, LINKS_SECTION)
_BULLET_RE: Final[re.Pattern[str]] = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+")
_LINK_RE: Final[re.Pattern[str]] = re.compile(
    r"\[\[[^\]\n]*\]\]|\[[^\]\n]*\]\([^)\n]*\)|<[a-z][a-z0-9+.-]*:[^>\s]*>|[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE
)


# ═══════════════════════════════ fact lines ═══════════════════════════════


def folded(line: str) -> str:
    """A line as the count compares it: white space collapsed."""
    return " ".join(line.split())


def digest(line: str) -> str:
    """The digest of a line that the judged ledger and the decisions file name it by."""
    return hashlib.sha256(folded(line).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def is_bare_reference(line: str) -> bool:
    """A list item that is nothing but code spans, links or URLs (with punctuation between them)."""
    bullet = _BULLET_RE.match(line)
    if bullet is None:
        return False
    rest = line[bullet.end():]
    stripped = _LINK_RE.sub("", CODE_SPAN_RE.sub("", rest))
    return stripped != rest and not any(character.isalnum() for character in stripped)


def fact_lines(page: Page, minimum: int) -> list[tuple[int, str, str]]:
    """(line number, bare line, folded line) of each fact line of a leaf."""
    found = []
    for number, line in body_line_numbers(page, _NOT_FACTS):
        text = folded(line)
        if len(text) < minimum or text.startswith("#") or is_bare_reference(line):
            continue
        found.append((number, line, text))
    return found


# ═══════════════════════════════ the judged ledger ═══════════════════════════════


def ledger_path(root: str | os.PathLike[str]) -> Path:
    return layout.state_path(root, layout.REPEATS_JUDGED_FILE)


def judged(root: str | os.PathLike[str]) -> set[tuple[str, str]]:
    """(subject, line digest) of every judged line; a row that cannot be read judges nothing."""
    try:
        text = ledger_path(root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    found = set()
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("subject"), str) and isinstance(row.get("digest"), str):
            found.add((row["subject"], row["digest"]))
    return found


def ledger_row(subject: str, line_digest: str, *, reason: str, day: str, paths: Iterable[str]) -> str:
    return json.dumps({"digest": line_digest, "judged_at": day, "paths": sorted(paths), "reason": reason,
                       "schema": LEDGER_SCHEMA, "subject": subject}, ensure_ascii=False, sort_keys=True)


# ═══════════════════════════════ shared lines ═══════════════════════════════


@dataclass(frozen=True, slots=True)
class SharedLine:
    """A fact line the lint counts: held by two or more leaves of one subject."""

    subject: str
    digest: str
    holders: dict[str, list[int]]

    def as_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "holders": {path: numbers for path, numbers in sorted(self.holders.items())},
                "subject": self.subject}


def shared_lines(subject: str, pages: Mapping[str, Page], *, minimum: int, least: int,
                 judged_lines: set[tuple[str, str]], skip: Mapping[str, Iterable[int]] | None = None,
                 ) -> tuple[list[tuple[str, str, int]], list[SharedLine]]:
    """The lint's count for one subject: each pair of leaves sharing ``least`` or more fact lines (with how
    many), and each line such a pair shares. Judged lines, lines more than ``REPEAT_MAX_LEAVES`` leaves hold and
    the line numbers ``skip`` names per page do not count."""
    numbers: dict[str, dict[str, list[int]]] = {}
    for path, page in pages.items():
        held: dict[str, list[int]] = {}
        skipped = set((skip or {}).get(path, ()))
        for number, _line, text in fact_lines(page, minimum):
            if number not in skipped and (subject, digest(text)) not in judged_lines:
                held.setdefault(text, []).append(number)
        numbers[path] = held
    holders: Counter[str] = Counter(text for held in numbers.values() for text in held)
    pairs: list[tuple[str, str, int]] = []
    lines: dict[str, SharedLine] = {}
    for first, second in combinations(sorted(pages), 2):
        shared = [text for text in numbers[first].keys() & numbers[second].keys()
                  if holders[text] <= REPEAT_MAX_LEAVES]
        if len(shared) < least:
            continue
        pairs.append((first, second, len(shared)))
        for text in shared:
            lines.setdefault(text, SharedLine(subject, digest(text), {
                path: held[text] for path, held in numbers.items() if text in held}))
    return pairs, sorted(lines.values(), key=lambda item: item.digest)


# ═══════════════════════════════ the index leaf ═══════════════════════════════


def _references(page: Page) -> list[tuple[int, str, ReferenceLine]]:
    return [(number, line, reference) for number, line in body_line_numbers(page, _NOT_FACTS)
            if (reference := ReferenceLine.match(line.strip())) is not None]


def _period(path: str, siblings: Iterable[str]) -> str | None:
    return layout.split_period(PurePosixPath(path).stem, siblings)[1]


def index_leaf(pages: Mapping[str, Page], siblings: Mapping[str, list[str]]) -> str | None:
    """The leaf whose reference lines name the most distinct pages (ties: a page without a period suffix, then a
    profile page, then the shorter path), or None when no leaf holds a reference line."""
    best: tuple[Any, ...] | None = None
    chosen = None
    for path, page in sorted(pages.items()):
        named = {reference.path for _n, _l, reference in _references(page) if reference.path != path}
        if not named:
            continue
        folder = siblings.get(str(PurePosixPath(path).parent), [])
        key = (-len(named), _period(path, folder) is not None, page.field("kind").strip() != "profile",
               len(path), path)
        if best is None or key < best:
            best, chosen = key, path
    return chosen


def _sibling_period(path: str, target: str, siblings: Mapping[str, list[str]]) -> bool:
    """Whether ``target`` is another period page of the stream ``path`` is a period page of."""
    folder = str(PurePosixPath(path).parent)
    if str(PurePosixPath(target).parent) != folder or target == path:
        return False
    stems = siblings.get(folder, [])
    own = layout.split_period(PurePosixPath(path).stem, stems)
    other = layout.split_period(PurePosixPath(target).stem, stems)
    return own[1] is not None and other[1] is not None and own[0] == other[0]


# ═══════════════════════════════ editing a page ═══════════════════════════════


def _section_spans(page: Page) -> list[tuple[str, int, int]]:
    """(heading, index of its heading line, index after its last line) of each H2 section, 0-based lines."""
    index = len(page.frontmatter.render().splitlines(keepends=True)) if page.frontmatter is not None else 0
    index += len(page.head)
    spans = []
    for section in page.sections:
        spans.append((section.heading, index, index + 1 + len(section.lines)))
        index += 1 + len(section.lines)
    return spans


def without_lines(text: str, numbers: Iterable[int]) -> str:
    """The page without the given 1-based lines, and without each free section they leave with no non-blank
    line (its heading and its blank lines go too; a frame section stays)."""
    lines = text.splitlines(keepends=True)
    gone = {number - 1 for number in numbers}
    frames = {name.casefold() for name in FRAME_SECTIONS}
    for heading, start, end in _section_spans(parse_page(text)):
        if heading.casefold() in frames or not any(index in gone for index in range(start + 1, end)):
            continue
        if all(index in gone or not lines[index].strip() for index in range(start + 1, end)):
            gone.update(range(start, end))
    return "".join(line for index, line in enumerate(lines) if index not in gone)


def removal_proof(before: str, after: str, removed: Iterable[str]) -> list[str]:
    """What breaks the rule that ``after`` is ``before`` less whole lines, each a removed line, a heading or a
    blank line; empty when it holds."""
    allowed = {folded(line) for line in removed}
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    problems = []
    position = 0
    for line in old:
        if position < len(new) and new[position] == line:
            position += 1
            continue
        bare = split_line_ending(line)[0]
        if bare.strip() and not bare.lstrip().startswith("## ") and folded(bare) not in allowed:
            problems.append(f"a line that is not a removed one went: {folded(bare)[:80]!r}")
    if position != len(new):
        problems.append("the page gained or changed a line")
    return problems


def _holds_material(page: Page) -> bool:
    """Whether a leaf holds anything outside its title, description, headings, Links and Update record."""
    skipped = {LINKS_SECTION.casefold(), RECORD_SECTION.casefold()}
    return any(split_line_ending(raw)[0].strip() for section in page.sections
               if section.heading.casefold() not in skipped for raw in section.lines)


# ═══════════════════════════════ the plan ═══════════════════════════════


@dataclass(frozen=True, slots=True)
class Decision:
    subject: str
    digest: str
    home: str
    reason: str


def read_decisions(path: str | os.PathLike[str] | None) -> list[Decision]:
    """A decisions file: a JSON list (or an object with ``decisions``) of ``{"subject", "digest", "home",
    "reason"}``, ``home`` being the leaf that keeps the line or ``both``."""
    if path is None:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        refuse("decisions_invalid", "The decisions file is not readable JSON.", reason=exc.__class__.__name__)
    if isinstance(data, Mapping):
        data = data.get("decisions")
    if not isinstance(data, list):
        refuse("decisions_invalid", "A decisions file is a list of decisions (or an object with `decisions`).")
    found = []
    for number, item in enumerate(data, start=1):
        if not isinstance(item, Mapping) or not all(isinstance(item.get(key), str) and item.get(key).strip()
                                                    for key in ("subject", "digest", "home", "reason")):
            refuse("decisions_invalid", "Each decision names `subject`, `digest`, `home` and `reason`.",
                   number=number)
        found.append(Decision(item["subject"].strip(), item["digest"].strip(), layout.normalize(item["home"])
                              if item["home"].strip() != BOTH else BOTH, " ".join(item["reason"].split())))
    return found


@dataclass(slots=True)
class Outcome:
    """What one run takes out, moves, folds and judges, and what is left for a reading."""

    index: dict[str, str] = field(default_factory=dict)
    removed: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    moved_in: dict[str, list[str]] = field(default_factory=dict)
    folded: dict[str, str] = field(default_factory=dict)
    shells_kept: dict[str, str] = field(default_factory=dict)
    judged: list[dict[str, Any]] = field(default_factory=list)
    undecided: list[dict[str, Any]] = field(default_factory=list)
    hits: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "folded": dict(sorted(self.folded.items())),
            "hits": self.hits,
            "index": dict(sorted(self.index.items())),
            "judged": self.judged,
            "moved_in": dict(sorted(self.moved_in.items())),
            "removed": dict(sorted(self.removed.items())),
            "shells_kept": dict(sorted(self.shells_kept.items())),
            "undecided": self.undecided,
        }


def _leaves_by_subject(plan: Plan) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in plan.pages():
        if layout.page_role(path) not in (layout.PageRole.LEAF, layout.PageRole.COLLECTION_PAGE):
            continue
        entity = parse_page(plan.read(path)).field("entity").strip()
        if entity:
            found.setdefault(entity, []).append(path)
    return found


def build(plan: Plan, decisions: Iterable[Decision] = ()) -> Outcome:
    """Plan the run on ``plan``: every removal, move into an index leaf, fold and ledger row."""
    settings = plan.config.limits
    already = judged(plan.root)
    chosen = {(item.subject, item.digest): item for item in decisions}
    why = plan.reason("each fact has one home; a repeated line stays in its home")
    outcome = Outcome()
    homes = {ref.home for ref in plan.subjects()}
    siblings = layout.folder_stems(plan.pages())
    for subject, paths in sorted(_leaves_by_subject(plan).items()):
        if len(paths) < 2:
            continue
        pages = {path: parse_page(plan.read(path)) for path in paths}
        gone: dict[str, dict[int, tuple[str, str]]] = {path: {} for path in paths}  # number -> (line, home)
        index = index_leaf(pages, siblings)
        additions: list[tuple[str, str]] = []
        if index is not None:
            outcome.index[subject] = index
            held = {folded(line) for _n, line in body_line_numbers(pages[index], _NOT_FACTS) if line.strip()}
            for path in paths:
                if path == index:
                    continue
                for number, line, reference in _references(pages[path]):
                    if reference.path == index:
                        continue  # never at home in the page it names
                    if folded(line) in held:
                        gone[path][number] = (line, index)
                    elif _sibling_period(path, reference.path, siblings) and reference.path != index:
                        additions.append((line, path))
                        held.add(folded(line))
                        gone[path][number] = (line, index)
        _pairs, lines = shared_lines(subject, pages, minimum=settings.repeat_min_chars,
                                     least=settings.repeat_min_lines, judged_lines=already, skip=gone)
        for shared in lines:
            decision = chosen.get((subject, shared.digest))
            if decision is None:
                outcome.undecided.append(shared.as_dict())
                continue
            if decision.home == BOTH:
                if (subject, shared.digest) not in already:
                    already.add((subject, shared.digest))
                    outcome.judged.append({"digest": shared.digest, "paths": sorted(shared.holders),
                                           "reason": decision.reason, "subject": subject})
                continue
            if decision.home not in shared.holders:
                refuse("decision_home_invalid", "A decision's home is one of the leaves that hold the line.",
                       subject=subject, digest=shared.digest, home=decision.home)
            for path, numbers in shared.holders.items():
                if path == decision.home:
                    continue
                for number in numbers:
                    line = split_line_ending(plan.read(path).splitlines(keepends=True)[number - 1])[0]
                    gone[path][number] = (line, decision.home)
        _write(plan, subject, index, pages, gone, additions, homes, why, outcome)
    for row in outcome.judged:
        plan.append_state(layout.REPEATS_JUDGED_FILE, [ledger_row(row["subject"], row["digest"], reason=row["reason"],
                                                                   day=plan.today, paths=row["paths"])])
    if outcome.judged:
        plan.operations.append(f"repeats: {len(outcome.judged)} lines judged rightly held by more than one leaf")
    return outcome


def _write(plan: Plan, subject: str, index: str | None, pages: Mapping[str, Page],
           gone: Mapping[str, Mapping[int, tuple[str, str]]], additions: list[tuple[str, str]], homes: set[str],
           why: str, outcome: Outcome) -> None:
    for path, removed in sorted(gone.items()):
        if not removed:
            continue
        before = plan.read(path)
        after = without_lines(before, removed)
        problems = removal_proof(before, after, [line for line, _home in removed.values()])
        if problems:
            refuse("repeats_removal_unsafe", "A removal would change more than the repeated lines.", path=path,
                   problems=problems[:5])
        by_home: dict[str, list[str]] = {}
        for _number, (line, home) in sorted(removed.items()):
            by_home.setdefault(home, []).append(line)
        outcome.removed[path] = [{"home": home, "line": number, "digest": digest(line)}
                                 for number, (line, home) in sorted(removed.items())]
        for home, lines in sorted(by_home.items()):
            plan.expect(f"repeated lines of {path}", lines, [home])
        is_shell = not _holds_material(parse_page(after))
        if is_shell and path != index:
            reason = None
            if set(by_home) != {index}:
                reason = "some of its lines are kept in another leaf than the index leaf"
            elif path in homes:
                reason = "it is a subject's registry home"
            elif layout.page_role(path) is not layout.PageRole.LEAF:
                reason = "it is a collection page"
            if reason is None and index is not None:
                outcome.folded[path] = index
                continue
            outcome.shells_kept[path] = reason or "no index leaf"
        plan.update(path, after)
        homes_named = ", ".join(f"`{home}`" for home in sorted(by_home))
        plan.record_provenance(path, f"Repeated lines taken out, each kept in {homes_named}", why)
        plan.touch(layout.classify(path).tree)
    if additions and index is not None:
        page = parse_page(plan.read(index))
        target = max((section for section in page.sections if _references_in(section)), key=_references_in,
                     default=None)
        if target is None:
            refuse("index_leaf_without_references", "The index leaf keeps its reference lines in no section.",
                   path=index)
        for line, _source in additions:
            target.append_item(line.strip())
        plan.update(index, page.render())
        outcome.moved_in[index] = [line.strip() for line, _source in additions]
        for source in dict.fromkeys(source for _line, source in additions):
            plan.record_provenance(index, f"Reference lines moved in from `{source}`", why)
    # The folds last: archiving a page marks every reference to it, which the removals above must not see.
    from . import move

    for path, home in sorted(outcome.folded.items()):
        if home == index and plan.exists(path):
            move.archive(plan, path, reason=f"{why}; every line of the page stands in its index leaf",
                         successor=index)


def _references_in(section: Any) -> int:
    return sum(1 for _index, line in section.items() if ReferenceLine.match(line.strip()) is not None)


# ═══════════════════════════════ running ═══════════════════════════════


def _count(plan: Plan) -> int:
    """The lint's ``leaf_lines_repeated`` hits on the planned forest."""
    settings = plan.config.limits
    already = judged(plan.root)
    hits = 0
    for subject, paths in _leaves_by_subject(plan).items():
        if len(paths) < 2:
            continue
        pages = {path: parse_page(plan.read(path)) for path in paths}
        pairs, _lines = shared_lines(subject, pages, minimum=settings.repeat_min_chars,
                                     least=settings.repeat_min_lines, judged_lines=already)
        hits += 2 * len(pairs)
    return hits


def execute(
    root: str | os.PathLike[str],
    decisions: Iterable[Decision] = (),
    *,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
    today: str | None = None,
    why: str | None = None,
) -> dict[str, Any]:
    """``forest repeats apply`` (``plan`` is its dry run): every step as one checked plan, written whole or not
    at all."""
    chosen = list(decisions)

    def step(plan: Plan) -> dict[str, Any]:
        outcome = build(plan, chosen)
        if any(outcome.removed.values()) or outcome.moved_in or outcome.folded:
            plan.operations.append(f"repeats: lines taken to their homes in {len(outcome.removed)} leaves")
        result = outcome.as_dict()
        plan.finish()
        result["hits_after"] = _count(plan)
        return result

    return run(root, step, dry_run=dry_run, lock=lock, today=today, why=why)


def check(root: str | os.PathLike[str]) -> dict[str, Any]:
    """``forest repeats check``: the lint's count now, what a run would still take out, and what waits for a
    reading. Writes nothing."""
    plan = Plan.open(root)
    hits = _count(plan)
    outcome = build(plan)
    pending = sum(len(items) for items in outcome.removed.values())
    return {"hits": hits, "pending": pending, "folds": len(outcome.folded), "undecided": outcome.undecided,
            "ok": hits == 0 and pending == 0 and not outcome.folded}


def problems_text(result: Mapping[str, Any]) -> list[str]:
    lines = [f"leaf_lines_repeated: {result['hits']} hits; {result['pending']} lines a run takes out; "
             f"{result['folds']} pages it folds; {len(result['undecided'])} lines wait for a reading"]
    for item in result["undecided"]:
        where = ", ".join(f"{path}:{','.join(map(str, numbers))}" for path, numbers in item["holders"].items())
        lines.append(f"  {item['subject']} {item['digest']}: {where}")
    return lines

