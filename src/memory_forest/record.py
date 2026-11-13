"""Update records: what a record line is, how big a record is, and the trim (contract, section 4).

A page's ``## Update record`` holds only the page's own history, in three
kinds of line (``Kind``). ``classify`` says which kind a line is; a line that
is none of them is a run's note, which belongs in the run's own directory.

``plan_trim`` decides what a trim keeps and moves; ``trim_page`` appends the
moved lines as one block to the page's record history file, proves the block
byte for byte, and only then writes the page (with its one pointer line at
the first trim). ``archived_record_lines`` reads the record lines back out of
the history file (and of whole-page copies of forests trimmed the old way), so
that a subject's history stays whole after a trim.
"""

from __future__ import annotations

import enum
import hashlib
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from . import archive as forest_archive
from . import layout, system
from . import credentials as forest_credentials
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import LockToken, atomic_write, writer_lock
from .model import (
    RECORD_SECTION,
    Page,
    RecordLine,
    RecordPointerLine,
    code_span_paths,
    code_spans,
    parse_page,
    split_line_ending,
)
from .safety import require_real_root
from .view import read_text, section_start


TRIM_REASON: Final[str] = "record-trim"
# The characters a name form may stand from the quotation that follows it, in one clause.
OWNER_WORD_GAP: Final[int] = 60
_REASON_OR_SOURCE_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\.\s+)(?:Why|Sources?):")


class Kind(enum.IntEnum):
    """The three kinds of record line (contract, section 4, What a record line is)."""

    CHANGE = 1
    OWNER_WORDS = 2
    PROVENANCE = 3

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS: Final[dict[Kind, str]] = {
    Kind.CHANGE: "change",
    Kind.OWNER_WORDS: "owner-words",
    Kind.PROVENANCE: "provenance",
}
RUN_NOTE: Final[str] = "run-note"
KIND_LABELS: Final[tuple[str, ...]] = (*(_LABELS[kind] for kind in Kind), RUN_NOTE)


def kind_label(kind: Kind | None) -> str:
    return RUN_NOTE if kind is None else kind.label


# ─────────────────────────────── what a line is ───────────────────────────────


@lru_cache(maxsize=32)
def _owner_words_re(names: tuple[str, ...], marks: tuple[str, ...]) -> re.Pattern[str] | None:
    forms = [re.escape(name) for name in sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)]
    quotes = []
    stops = ".;!?\n"
    for pair in marks:
        opening, closing = pair[0], pair[1]
        stops += opening
        quotes.append(f"{re.escape(opening)}[^{re.escape(closing)}\\n]+{re.escape(closing)}")
    if not forms or not quotes:
        return None
    return re.compile(
        r"(?<!\w)(?:" + "|".join(forms) + r")(?!\w)"
        + f"[^{re.escape(stops)}]{{0,{OWNER_WORD_GAP}}}"
        + "(?:" + "|".join(quotes) + ")"
    )


def owner_word_names(config: Config) -> tuple[str, ...]:
    """The name forms that mark the owner's words: the setting, else the owner's name and aliases."""
    return config.record.owner_word_names or config.owner.names


def carries_owner_words(text: str, *, config: Config) -> bool:
    """Whether ``text`` gives the owner's words: a name form, then in the same clause a quotation.

    The quotes of what was and what is, and of what was removed, are the
    page's own statements, never the owner's words, and are not read.
    """
    pattern = _owner_words_re(owner_word_names(config), config.record.quote_marks)
    if pattern is None:
        return False
    record = RecordLine("", text)
    if record.was is not None and record.now is not None:
        text = text.replace(f'was "{record.was}", now "{record.now}"', "", 1)
    if record.removed is not None:
        text = text.replace(f'removed "{record.removed}"', "", 1)
    return pattern.search(text) is not None


def _statement_part(text: str) -> str:
    """A record line's text before its ``Why:`` and ``Source:`` (where it says what happened)."""
    found = _REASON_OR_SOURCE_RE.search(text)
    return text if found is None else text[: found.start()]


def _names_place(text: str) -> bool:
    for span in code_spans(text):
        value = span.text.strip()
        if value.startswith(layout.ARCHIVE_DIR + "/"):
            return True
    return bool(code_span_paths(text))


def classify(line: str, *, config: Config) -> Kind | None:
    """Which of the three kinds a record line is, or None for a run's note.

    The kinds are read from the fixed words of the line grammar
    (``was "…", now "…"``, ``removed "…"``, ``Why:``, ``Source:``):

    1. ``Kind.CHANGE``: a dated line that quotes what a statement of the page
       was and what it became, ``was "…", now "…"``, without the owner's
       words. It is bounded like any other line.
    2. ``Kind.OWNER_WORDS``: a removal, a correction or an exclusion made at
       the owner's word. In what it says happened the line quotes what was
       taken out (``removed "…"``) or what the statement was and became
       (``was "…", now "…"``), or says what was not kept without restating
       it (``not kept: …``, running to its ``Why:``), AND gives the owner's
       own words: one of the name forms of
       ``record.owner_word_names`` (by default the owner's ``name`` and
       ``aliases``) followed, in the same clause, by a quotation between one
       of the ``record.quote_marks`` pairs, usually in its ``Why:``. A trim
       keeps it whatever its age, so a later run does not add back what the
       owner took out or excluded, or undo what the owner corrected. A
       ``not kept:`` line without the owner's words is a run's note.
    3. ``Kind.PROVENANCE``: the ``Earlier record lines (…)`` pointer line, or
       a dated line that names, in a code span in what it says happened
       (before its ``Why:`` and ``Source:``), the page or archive copy the
       page's lines came from or went to (a path of the forest or its lanes,
       or of ``.forest/archive/``).

    The owner's words on a line that has none of those three forms
    make nothing of it: such a line is kind 3 when it names where lines went,
    else a run's note, like every other line that is none of these (a count
    of what a run touched, a note that a page was rechecked, an undated line).
    """
    bare = split_line_ending(line)[0].strip()
    if RecordPointerLine.match(bare) is not None:
        return Kind.PROVENANCE
    record = RecordLine.match(bare)
    if record is None:
        return None
    statement = _statement_part(record.text)
    change = record.was is not None and record.now is not None
    removal = record.removed is not None and f'removed "{record.removed}"' in statement
    exclusion = record.not_kept is not None and f"not kept: {record.not_kept}" in statement
    if (change or removal or exclusion) and carries_owner_words(record.text, config=config):
        return Kind.OWNER_WORDS
    if exclusion:
        return None  # an exclusion is only ever at the owner's word
    if change:
        return Kind.CHANGE
    if _names_place(statement):
        return Kind.PROVENANCE
    return None


# ─────────────────────────────── a page's record ───────────────────────────────


def record_lines(page: Page) -> list[str]:
    """The non-blank lines of a page's ``## Update record``, without their line endings."""
    section = page.section(RECORD_SECTION)
    if section is None:
        return []
    return [bare for raw in section.lines if (bare := split_line_ending(raw)[0]).strip()]


def record_bytes(page: Page) -> int:
    """The size of a record as the budget counts it: its lines, one newline each."""
    return sum(len(line.encode("utf-8", "surrogateescape")) + 1 for line in record_lines(page))


def _line_date(line: str) -> str:
    record = RecordLine.match(line.strip())
    return record.date if record is not None else ""


@dataclass(frozen=True, slots=True)
class TrimPlan:
    """What a trim of one page keeps and moves."""

    path: str
    record_bytes: int
    budget: int
    pointers: tuple[str, ...]
    kept: tuple[str, ...]
    moved: tuple[str, ...]
    run_notes: int
    over_budget: bool

    @property
    def applies(self) -> bool:
        return bool(self.moved)

    @property
    def why(self) -> str:
        if not self.moved:
            return "nothing moves"
        if self.over_budget:
            return f"the record holds {self.record_bytes} bytes, over {self.budget}"
        return "the record holds run notes"

    @property
    def first(self) -> str:
        dates = sorted(day for line in self.moved if (day := _line_date(line)))
        return dates[0] if dates else "undated"

    @property
    def last(self) -> str:
        dates = sorted(day for line in self.moved if (day := _line_date(line)))
        return dates[-1] if dates else "undated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "applies": self.applies,
            "budget": self.budget,
            "kept": len(self.kept),
            "moved": len(self.moved),
            "over_budget": self.over_budget,
            "path": self.path,
            "record_bytes": self.record_bytes,
            "run_notes": self.run_notes,
            "why": self.why,
        }


def plan_trim(page: Page, path: str, *, config: Config) -> TrimPlan:
    """What a trim of ``page`` keeps and moves (contract, section 4, Record trim).

    Over ``limits.record_keep_bytes`` the record keeps the newest lines that
    fit in that budget and every line of kind 2 whatever its age, one copy of
    each, oldest first by leading date; the pointer lines stay on top and do
    not count against the budget. A run's note moves whatever the size.
    Everything that does not stay moves, exact duplicates included.
    """
    lines = record_lines(page)
    budget = config.limits.record_keep_bytes
    size = record_bytes(page)
    pointers = [line for line in lines if RecordPointerLine.match(line.strip()) is not None]
    entries = [(index, line) for index, line in enumerate(lines) if RecordPointerLine.match(line.strip()) is None]
    kinds = {index: classify(line, config=config) for index, line in entries}
    notes = sum(1 for kind in kinds.values() if kind is None)
    over = size > budget
    keep: set[int] = set()
    if over:
        seen: set[str] = set()
        used = 0
        newest_first = sorted(entries, key=lambda item: (_line_date(item[1]), item[0]), reverse=True)
        for index, line in newest_first:
            if kinds[index] is None or line in seen:
                continue
            cost = len(line.encode("utf-8", "surrogateescape")) + 1
            if used + cost > budget:
                break
            keep.add(index)
            seen.add(line)
            used += cost
        for index, line in entries:
            if kinds[index] is Kind.OWNER_WORDS and index not in keep and line not in seen:
                keep.add(index)
                seen.add(line)
        ordered = sorted((item for item in entries if item[0] in keep), key=lambda item: (_line_date(item[1]), item[0]))
    else:
        keep = {index for index, _line in entries if kinds[index] is not None}
        ordered = [item for item in entries if item[0] in keep]
    moved = tuple(line for index, line in entries if index not in keep)
    return TrimPlan(
        path=path,
        record_bytes=size,
        budget=budget,
        pointers=tuple(pointers),
        kept=tuple(line for _index, line in ordered),
        moved=moved,
        run_notes=notes,
        over_budget=over,
    )


def trimmed_text(page: Page, plan: TrimPlan, pointer: str | None) -> str:
    """The page's text with its record replaced by the plan's pointers and kept lines."""
    section = page.section(RECORD_SECTION)
    if section is None:
        raise MemoryForestError("record_missing", "The page has no update record.", details={"path": plan.path})
    ending = section.ending()
    body = [*plan.pointers, *([pointer] if pointer else []), *plan.kept]
    is_last = page.sections[-1] is section
    lines = [ending, *(line + ending for line in body)]
    if not is_last:
        lines.append(ending)
    rebuilt = parse_page(page.render())
    target = rebuilt.section(RECORD_SECTION)
    assert target is not None
    if not split_line_ending(target.heading_line)[1]:
        target.heading_line += ending
    target.lines = lines
    return rebuilt.render()


@dataclass(slots=True)
class TrimResult:
    path: str
    plan: TrimPlan
    written: bool = False
    archive_path: str | None = None
    pointer: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**self.plan.as_dict(), "archive": self.archive_path, "pointer": self.pointer, "written": self.written}


def _check_rewrite(original: Page, new_text: str, plan: TrimPlan, pointer: str | None) -> None:
    rewritten = parse_page(new_text)
    if rewritten.content_text() != original.content_text():
        raise MemoryForestError(
            "record_trim_unsafe",
            "A trim would change the page outside its update record; nothing was written.",
            details={"path": plan.path},
        )
    before = Counter(record_lines(original))
    after = Counter(record_lines(rewritten))
    if pointer is not None:
        after[pointer] -= 1
    after += Counter(plan.moved)
    if +after != before:
        raise MemoryForestError(
            "record_trim_unsafe",
            "A trim would lose or add a record line; nothing was written.",
            details={"path": plan.path},
        )


def history_pointer(page: Page) -> RecordPointerLine | None:
    """The first pointer line of a page that names a record history file (the one a trim appends to)."""
    prefix = f"{layout.ARCHIVE_DIR}/{forest_archive.HISTORY_DIR}/"
    for line in record_lines(page):
        pointer = RecordPointerLine.match(line.strip())
        if pointer is not None and pointer.path.startswith(prefix):
            return pointer
    return None


def trim_page(
    root: str | os.PathLike[str],
    path: str,
    *,
    config: Config | None = None,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
) -> TrimResult:
    """Trim one page's record; a page that nothing moves on is never written.

    The moved lines are appended as one block to the page's record history
    file (the file its pointer line names, or, at the first trim, a new one),
    and proved there byte for byte and in order; only then is the page
    written, atomically: at the first trim with the one pointer line it keeps
    for life, at every later trim with only the moved lines taken out. The
    page is read back as written.
    """
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    relative = layout.normalize(path)
    if not layout.classify(relative).in_forest:
        raise MemoryForestError("record_path_invalid", "Only pages of the four forest layers have a record to trim.",
                                details={"path": relative})
    target = layout.resolve(safe_root, relative, must_exist=True)
    text = read_text(target)
    page = parse_page(text)
    plan = plan_trim(page, relative, config=settings)
    result = TrimResult(relative, plan)
    if not plan.applies or dry_run:
        return result
    raw = text.encode("utf-8", "surrogateescape")
    page_sha = hashlib.sha256(raw).hexdigest()
    with writer_lock(safe_root, token=lock, label="record trim") as held:
        if read_text(target) != text:
            raise MemoryForestError("record_page_changed", "The page changed while it was read; nothing was written.",
                                    details={"path": relative})
        existing = history_pointer(page)
        if existing is not None:
            history_file = existing.path.removeprefix(layout.ARCHIVE_DIR + "/")
        else:
            named = [pointer.path for line in record_lines(page) if (pointer := RecordPointerLine.match(line.strip()))]
            history_file = forest_archive.history_file_for(safe_root, relative, page_sha, named)
        block = forest_archive.append_block(safe_root, history_file, page=relative, page_sha256=page_sha,
                                            lines=list(plan.moved), first=plan.first, last=plan.last, lock=held)
        pointer = None if existing is not None else RecordPointerLine(plan.first, plan.last, len(plan.moved),
                                                                     block.archive_path).render()
        new_text = trimmed_text(page, plan, pointer)
        _check_rewrite(page, new_text, plan, pointer)
        if read_text(target) != text:
            raise MemoryForestError("record_page_changed", "The page changed while it was trimmed; nothing was written.",
                                    details={"path": relative})
        payload = new_text.encode("utf-8", "surrogateescape")
        atomic_write(target, payload)
        if system.read_bytes(target) != payload:
            raise MemoryForestError("write_readback_failed", "The page did not read back as written.",
                                    details={"path": relative})
    result.written = True
    result.archive_path = block.archive_path
    result.pointer = pointer
    return result


def trim(
    root: str | os.PathLike[str],
    paths: Iterable[str] = (),
    *,
    all_pages: bool = False,
    config: Config | None = None,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
) -> list[TrimResult]:
    """Trim the named pages, or with ``all_pages`` every page of the four layers, under one writer lock."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    chosen = [layout.normalize(path) for path in paths]
    if all_pages:
        chosen += [path for path in layout.iter_pages(safe_root) if path not in chosen]
    if dry_run:
        return [trim_page(safe_root, path, config=settings, dry_run=True) for path in chosen]
    results = []
    with writer_lock(safe_root, token=lock, label="record trim") as held:
        for path in chosen:
            results.append(trim_page(safe_root, path, config=settings, lock=held))
    return results


# ─────────────────────────────── sizes and kinds ───────────────────────────────


def kind_counts(page: Page, *, config: Config) -> dict[str, int]:
    counts = {label: 0 for label in KIND_LABELS}
    for line in record_lines(page):
        counts[kind_label(classify(line, config=config))] += 1
    return counts


def size(root: str | os.PathLike[str], path: str, *, config: Config | None = None) -> dict[str, Any]:
    """A page's content bytes, record bytes, record lines by kind and what is due (``split``, ``trim``)."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    relative = layout.normalize(path)
    text = read_text(layout.resolve(safe_root, relative, must_exist=True))
    page = parse_page(text)
    content = len(page.content_text().encode("utf-8", "surrogateescape"))
    role = layout.classify(relative).role
    due = []
    if role in (layout.PageRole.LEAF, layout.PageRole.COLLECTION_PAGE) and content > settings.limits.leaf_cap_bytes:
        due.append("split")
    if plan_trim(page, relative, config=settings).applies:
        due.append("trim")
    return {
        "content_bytes": content,
        "due": due,
        "kinds": kind_counts(page, config=settings),
        "page_bytes": len(text.encode("utf-8", "surrogateescape")),
        "path": relative,
        "record_bytes": record_bytes(page),
    }


@dataclass(slots=True)
class KindReport:
    pages: dict[str, dict[str, int]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=lambda: {label: 0 for label in KIND_LABELS})
    examples: dict[str, list[tuple[str, int, str]]] = field(default_factory=lambda: {label: [] for label in KIND_LABELS})

    def as_dict(self) -> dict[str, Any]:
        return {
            "examples": {
                label: [{"line": number, "path": path, "text": text} for path, number, text in items]
                for label, items in self.examples.items()
            },
            "pages": self.pages,
            "totals": self.totals,
        }


def kinds(root: str | os.PathLike[str], *, config: Config | None = None, examples: int = 0) -> KindReport:
    """Every record line of the forest counted by kind and page, with up to ``examples`` lines of each kind."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    report = KindReport()
    for path in layout.iter_pages(safe_root):
        page = parse_page(read_text(layout.resolve(safe_root, path)))
        section = page.section(RECORD_SECTION)
        if section is None:
            continue
        start = section_start(page, RECORD_SECTION) or 0
        counts = {label: 0 for label in KIND_LABELS}
        for offset, raw in enumerate(section.lines):
            line = split_line_ending(raw)[0]
            if not line.strip():
                continue
            label = kind_label(classify(line, config=settings))
            counts[label] += 1
            report.totals[label] += 1
            if len(report.examples[label]) < examples:
                report.examples[label].append((path, start + offset, line))
        if any(counts.values()):
            report.pages[path] = counts
    return report


def run_notes(page: Page, *, config: Config) -> list[tuple[int, str]]:
    """(line number in the page, line) of each record line that is none of the three kinds."""
    section = page.section(RECORD_SECTION)
    if section is None:
        return []
    start = section_start(page, RECORD_SECTION) or 0
    found = []
    for offset, raw in enumerate(section.lines):
        line = split_line_ending(raw)[0]
        if line.strip() and classify(line, config=config) is None:
            found.append((start + offset, line))
    return found


# ─────────────────────────────── archived lines ───────────────────────────────


def _entry_for_copy(root: Path, archive_path: str) -> forest_archive.ArchiveEntry | None:
    wanted = archive_path.strip().removeprefix(layout.ARCHIVE_DIR + "/")
    for entry in forest_archive.entries(root):
        if entry.archive_file == wanted:
            return entry
    return None


def _history_lines(root: Path, history_file: str) -> list[str]:
    """The record lines a history file holds, in their order (its block headers aside); one read."""
    data = forest_archive.read_history(root, history_file).decode("utf-8", "surrogateescape")
    return [line for line in data.split("\n") if line.strip() and not line.startswith("<!-- record lines moved ")]


def archived_record_lines(root: str | os.PathLike[str], path: str) -> list[RecordLine]:
    """The dated record lines of a page that its record history file and archive copies hold and the page
    no longer does. A number written in full in them is starred, as on a page.

    A pointer to a record history file is read once. A pointer to a
    whole-page copy (a forest trimmed before history files) is followed, and
    every pointer line in the copies it reaches. Exact duplicates are returned
    once, oldest first by date (lines of one date in the order found).
    """
    safe_root = require_real_root(root)
    settings = load_config(safe_root)
    relative = layout.normalize(path)
    page = parse_page(read_text(layout.resolve(safe_root, relative, must_exist=True)))
    on_page = set(record_lines(page))
    queue = [pointer.path for line in record_lines(page) if (pointer := RecordPointerLine.match(line.strip()))]
    visited: set[str] = set()
    found: list[RecordLine] = []
    seen: set[str] = set()
    history_prefix = f"{layout.ARCHIVE_DIR}/{forest_archive.HISTORY_DIR}/"

    def take(line: str) -> None:
        line = forest_credentials.shown(line, config=settings)  # what is shown is masked and starred
        record = RecordLine.match(line.strip())
        if record is None or line in on_page or line in seen:
            return
        seen.add(line)
        found.append(record)

    while queue:
        copy_path = queue.pop(0)
        if copy_path in visited:
            continue
        visited.add(copy_path)
        if copy_path.startswith(history_prefix):
            for line in _history_lines(safe_root, copy_path.removeprefix(layout.ARCHIVE_DIR + "/")):
                take(line)
            continue
        entry = _entry_for_copy(safe_root, copy_path)
        if entry is None:
            raise MemoryForestError(
                "archive_not_found",
                "A pointer line names an archive copy the manifest does not list.",
                details={"archive": copy_path, "path": relative},
            )
        copy = parse_page(forest_archive.read_copy(safe_root, entry).decode("utf-8", "surrogateescape"))
        for line in record_lines(copy):
            pointer = RecordPointerLine.match(line.strip())
            if pointer is not None:
                queue.append(pointer.path)
                continue
            take(line)
    return sorted(found, key=lambda record: record.date)
