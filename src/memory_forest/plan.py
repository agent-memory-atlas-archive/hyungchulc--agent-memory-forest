"""A planned change of the forest, computed whole in memory before anything is written.

Every tool that moves things (``move``, ``collections``, ``levels``, ``maps``)
works on a ``Plan``: an overlay of the forest that holds, for each page an
operation touches, its text before and after. Operations read the planned
forest, so several of them compose in one plan. When the plan is complete:

1. ``finish`` rewrites every reference to what moved (code-span paths,
   wikilinks, ``superseded_by:``), never inside an ``## Update record``, a
   quoted line or a fenced block; marks references to archived pages
   `` (archived)``; brings the derived lists of the touched trees up to the
   pages (``maps``); and appends the record lines the operations wrote;
2. ``check`` proves in memory that the planned forest is sound: no registry
   problem the forest did not already have, every up-link names an existing
   parent, no reference to a page the plan removed is left unmarked, every
   line an operation promised to keep is on the pages that took it, and every
   new name keeps the name rules;
3. ``commit``, under the forest writer lock, stores the archive copies, writes
   the pages, reads them back (putting every file back when one differs),
   appends the move log and rebuilds the registry.

A check that fails raises ``plan_check_failed`` with the problems, and
nothing is written.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn

from . import archive, layout, movelog, registry
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import LockToken, atomic_write, writer_lock
from .model import (
    CODE_SPAN_RE,
    RECORD_SECTION,
    WIKILINK_RE,
    RecordLine,
    RegistryLine,
    UpLink,
    link_path,
    looks_like_forest_path,
    parse_page,
    split_line_ending,
)
from .safety import require_real_root


ARCHIVED_MARK: Final[str] = " (archived)"
LINKS_SECTION: Final[str] = "Links"
SUBJECTS_SECTION: Final[str] = "Subjects"
BRANCHES_SECTION: Final[str] = "Branches"
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]{0,3}(```|~~~)")
_H2_LINE_RE: Final[re.Pattern[str]] = re.compile(r"##[ \t]+(?P<text>.*?)[ \t]*")
_FM_PATH_KEYS: Final[tuple[str, ...]] = ("superseded_by",)
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"(?P<marks>#{1,6})[ \t]+(?P<text>.*)")


def refuse(code: str, message: str, **details: Any) -> NoReturn:
    """Stop an operation with a named error; nothing has been written."""
    raise MemoryForestError(code, message, details=details)


# ═══════════════════════════════ page text helpers ═══════════════════════════════


def encode(text: str) -> bytes:
    return text.encode("utf-8", errors="surrogateescape")


def field_of(text: str, key: str) -> str:
    """A frontmatter value as one string ('' when the page has none)."""
    return parse_page(text).field(key).strip()


def with_field(text: str, key: str, value: str | Iterable[str]) -> str:
    """``text`` with one frontmatter key set (the key added last when new; a frontmatter made when missing)."""
    page = parse_page(text)
    if page.frontmatter is None:
        ending = page.ending()
        page = parse_page(f"---{ending}---{ending}" + text)
    assert page.frontmatter is not None
    page.frontmatter.set(key, value)
    return page.render()


def without_field(text: str, key: str) -> str:
    page = parse_page(text)
    if page.frontmatter is None or not page.frontmatter.remove(key):
        return text
    return page.render()


def title_of(text: str) -> str | None:
    """The page's ``# Title``."""
    return parse_page(text).title


def first_prose(text: str, section: str | None = None) -> str | None:
    """The first sentence of the first paragraph in the page's head (after its title) or in one section."""
    page = parse_page(text)
    if section is None:
        lines = page.head
    else:
        found = page.section(section)
        lines = found.lines if found is not None else []
    paragraph: list[str] = []
    for raw in lines:
        bare = split_line_ending(raw)[0].strip()
        if not bare:
            if paragraph:
                break
            continue
        if bare.startswith("#"):
            if paragraph:
                break
            continue
        if paragraph and bare.startswith(("- ", "* ", "+ ")):
            break
        paragraph.append(bare.lstrip("-*+ ").strip() if not paragraph else bare)
    if not paragraph:
        return None
    joined = " ".join(paragraph)
    sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", joined, maxsplit=1)[0]
    return sentence.rstrip(".") or None


def up_label(parent: str) -> str:
    """The label an up-link to ``parent`` carries: ``XLTM``, ``<tree> LTM`` or ``<branch> MTM``."""
    info = layout.classify(parent)
    if info.role is layout.PageRole.ROOT_INDEX:
        return "XLTM"
    if info.role is layout.PageRole.TREE_PAGE:
        return f"{info.tree} LTM"
    if info.role is layout.PageRole.BOARD:
        return f"{info.branch} MTM"
    if info.role is layout.PageRole.TREE_INDEX:
        return f"{info.tree} MTM"
    return PurePosixPath(parent).stem


def up_line(path: str) -> str:
    """The up-link line a page at ``path`` carries (contract, section 4, Shape)."""
    parent = layout.parent_page(path)
    if parent is None:
        refuse("path_has_no_parent", "This page has no parent page.", path=path)
    return UpLink(layout.up_link_target(parent), up_label(parent)).render()


def with_up_link(text: str, path: str) -> str:
    """``text`` with its single up-link naming the parent of ``path``."""
    page = parse_page(text)
    wanted = up_line(path)
    links = page.section(LINKS_SECTION)
    if links is None:
        page.add_section(LINKS_SECTION, [wanted])
        return page.render()
    for index, line in links.items():
        if UpLink.match(line) is not None:
            links.replace_line(index, wanted)
            return page.render()
    links.append_item(wanted)
    return page.render()


def with_records(text: str, lines: Iterable[str]) -> str:
    """``text`` with record lines added at the end of its ``## Update record`` (made when missing)."""
    items = [line for line in lines if line]
    if not items:
        return text
    page = parse_page(text)
    record = page.section(RECORD_SECTION)
    if record is None:
        record = page.add_section(RECORD_SECTION)
    for line in items:
        record.append_item(line)
    return page.render()


def section_items(text: str, name: str) -> list[str]:
    section = parse_page(text).section(name)
    return section.item_lines() if section is not None else []


def edit_items(
    text: str,
    name: str,
    keep: Callable[[str], str | None],
) -> str:
    """Each list line of one section passed through ``keep``: its new text, or None to drop it."""
    page = parse_page(text)
    section = page.section(name)
    if section is None:
        return text
    for index, line in reversed(section.items()):
        new = keep(line)
        if new is None:
            del section.lines[index]
        elif new != line:
            section.replace_line(index, new)
    return page.render()


def add_items(text: str, name: str, lines: Iterable[str], *, before: Iterable[str] = ()) -> str:
    """List lines added at the end of one section's list (the section made, before the first of ``before`` present)."""
    items = list(lines)
    if not items:
        return text
    page = parse_page(text)
    section = page.section(name)
    if section is None:
        anchor = next((other for other in before if page.section(other) is not None), None)
        section = page.add_section(name, before=anchor)
    for line in items:
        section.append_item(line)
    return page.render()


def shift_headings(lines: Iterable[str], by: int) -> list[str]:
    """Lines with every Markdown heading outside fences moved ``by`` levels (kept between 2 and 6)."""
    out: list[str] = []
    fenced = False
    for raw in lines:
        bare, ending = split_line_ending(raw)
        if _FENCE_RE.match(bare):
            fenced = not fenced
        elif not fenced:
            heading = _HEADING_RE.fullmatch(bare)
            if heading is not None:
                level = min(6, max(1, len(heading["marks"]) + by))
                bare = "#" * level + " " + heading["text"]
        out.append(bare + ending)
    return out


def masked(line: str) -> str:
    """A line as the keep-checks compare it: stripped, headings without their marks, forest paths and links masked."""
    value = split_line_ending(line)[0].strip()
    heading = _HEADING_RE.fullmatch(value)
    if heading is not None:
        value = heading["text"].strip()
    value = WIKILINK_RE.sub("[[]]", value)
    value = CODE_SPAN_RE.sub(lambda match: "``" if looks_like_forest_path(match["text"]) else match[0], value)
    return value.replace(ARCHIVED_MARK, "")


def body_lines(text: str) -> list[str]:
    """A page's non-blank lines outside its frontmatter."""
    page = parse_page(text)
    lines = [*page.head, *(line for section in page.sections for line in (section.heading_line, *section.lines))]
    return [split_line_ending(line)[0] for line in lines if line.strip()]


def day_runs(
    first: str,
    days: Iterable[tuple[str, list[Any]]],
    name: Callable[[str, int], str],
    fits: Callable[[str, list[Any]], bool],
) -> list[tuple[str, list[Any]]]:
    """A month's items cut into runs of whole days (contract, sections 3 and 4): the pages and what each holds.

    ``days`` gives each day's items in order, the days in order. The first run
    keeps the name ``first``; each later run is the page ``name(day, 1)`` of
    its first day, filled while ``fits`` holds; a day whose items alone do not
    fit takes further pages ``name(day, 2)``, ``name(day, 3)``, … in item
    order. An item is never divided, and each is on exactly one page: an item
    that alone does not fit has a page to itself.
    """
    groups: list[tuple[str, list[Any]]] = []
    current: tuple[str, list[Any]] | None = (first, [])
    for day, items in days:
        if current is None:
            current = (name(day, 1), [])
        if fits(current[0], [*current[1], *items]):
            current[1].extend(items)
            continue
        if current[1]:
            groups.append(current)
            current = (name(day, 1), [])
            if fits(current[0], items):
                current[1].extend(items)
                continue
        number = 1
        for item in items:
            if current[1] and not fits(current[0], [*current[1], item]):
                groups.append(current)
                number += 1
                current = (name(day, number), [])
            current[1].append(item)
        groups.append(current)
        current = None
    if current is not None and (current[1] or current[0] == first):
        groups.append(current)
    return groups


def script_of(text: str) -> str | None:
    """The script of a name's letters (``LATIN``, ``CYRILLIC``, …), or None when it mixes scripts or has none."""
    scripts = set()
    for character in text:
        if character.isalpha():
            name = unicodedata.name(character, "")
            scripts.add(name.split(" ", 1)[0] if name else "")
    return scripts.pop() if len(scripts) == 1 else None


# ═══════════════════════════════ references ═══════════════════════════════


def _mapped(path: str, mapping: Mapping[str, str]) -> str | None:
    """Where a reference to ``path`` points after the moves of ``mapping``, or None when nothing moved it.

    An address ``<page>#<id>`` follows its own mapping (an entry cut, moved, admitted), else its page's; an
    address reached that the same plan moves again (an entry demoted onto a page that is then cut) follows on.
    """
    found = _mapped_once(path.strip(), mapping)
    seen: set[str] = set()
    while found is not None and "#" in found and found in mapping and found not in seen:
        seen.add(found)
        found = mapping[found]
    return found


def _mapped_once(value: str, mapping: Mapping[str, str]) -> str | None:
    if value in mapping:
        return mapping[value]
    page, mark, anchor = value.partition("#")
    if mark and page in mapping:
        return f"{mapping[page].partition('#')[0]}#{anchor}"
    best: tuple[int, str] | None = None
    for old, new in mapping.items():
        if old.endswith(".md") or "#" in old:
            continue
        folder = old.rstrip("/")
        if value.startswith(folder + "/") and (best is None or len(folder) > best[0]):
            best = (len(folder), new.rstrip("/") + value[len(folder) :])
    return best[1] if best else None


def _archived(path: str, archived: Iterable[str]) -> bool:
    value = path.strip()
    for item in archived:
        if value == item or (not item.endswith(".md") and value.startswith(item.rstrip("/") + "/")):
            return True
    return False


def _rewrite_line(line: str, mapping: Mapping[str, str], archived: frozenset[str]) -> tuple[str, int]:
    count = 0

    def link(match: re.Match[str]) -> str:
        nonlocal count
        target = match["target"].strip()
        page = link_path(target)
        if _archived(page, archived):
            count += 1
            return f"`{page}`{ARCHIVED_MARK}"
        new = _mapped(page, mapping)
        if new is None:
            return match[0]
        count += 1
        label = match["label"]
        if label is not None and label == up_label(page):
            label = up_label(new)
        shown = layout.up_link_target(new)
        return f"[[{shown}|{label}]]" if label is not None else f"[[{shown}]]"

    if "[[" in line:
        line = WIKILINK_RE.sub(link, line)
    if "`" not in line:
        return line, count
    pieces: list[str] = []
    cursor = 0
    for match in CODE_SPAN_RE.finditer(line):
        text = match["text"]
        if not looks_like_forest_path(text):
            continue
        new = _mapped(text, mapping)
        if new is not None:
            pieces.append(line[cursor : match.start()])
            pieces.append(f"`{new}`")
            cursor = match.end()
            count += 1
        elif _archived(text, archived) and not line[match.end() :].startswith(ARCHIVED_MARK):
            pieces.append(line[cursor : match.end()])
            pieces.append(ARCHIVED_MARK)
            cursor = match.end()
            count += 1
    pieces.append(line[cursor:])
    return "".join(pieces), count


def iter_reference_lines(text: str) -> Iterator[tuple[int, str, str]]:
    """(index, bare line, where) for each line of a page: ``where`` is ``body`` or ``frontmatter`` for a line
    whose references follow a move, and '' for one they never touch.

    A body line counts when it is outside an ``## Update record``, a quoted
    (``>``) line and a fenced block; in the frontmatter only path keys count.
    """
    lines = text.splitlines(keepends=True)
    in_frontmatter = bool(lines) and split_line_ending(lines[0])[0].strip() == "---"
    fenced = False
    in_record = False
    for index, raw in enumerate(lines):
        bare = split_line_ending(raw)[0]
        if in_frontmatter:
            if index > 0 and bare.strip() == "---":
                in_frontmatter = False
                yield index, bare, ""
                continue
            key = bare.split(":", 1)[0].strip()
            yield index, bare, "frontmatter" if index > 0 and key in _FM_PATH_KEYS else ""
            continue
        if _FENCE_RE.match(bare):
            fenced = not fenced
            yield index, bare, ""
            continue
        if fenced:
            yield index, bare, ""
            continue
        heading = _H2_LINE_RE.fullmatch(bare)
        if heading is not None and not bare.startswith("###"):
            in_record = heading["text"].casefold() == RECORD_SECTION.casefold()
        yield index, bare, "" if in_record or bare.lstrip().startswith(">") else "body"


def rewrite_references(text: str, mapping: Mapping[str, str], archived: Iterable[str] = ()) -> tuple[str, int]:
    """``text`` with every reference to a moved page or folder following it, and those to archived ones marked."""
    marks = frozenset(archived)
    if not mapping and not marks:
        return text, 0
    lines = text.splitlines(keepends=True)
    total = 0
    for index, bare, where in iter_reference_lines(text):
        if not where:
            continue
        if where == "frontmatter":
            key, _, value = bare.partition(":")
            new = _mapped(value.strip(), mapping)
            if new is not None:
                lines[index] = f"{key}: {new}" + split_line_ending(lines[index])[1]
                total += 1
            continue
        new_line, count = _rewrite_line(bare, mapping, marks)
        if count:
            lines[index] = new_line + split_line_ending(lines[index])[1]
            total += count
    return "".join(lines), total


def reference_paths(text: str) -> list[str]:
    """The forest paths a page names in code spans and wikilinks where references count (unmarked ones)."""
    found: list[str] = []
    for _index, bare, where in iter_reference_lines(text):
        if where != "body":
            continue
        for match in WIKILINK_RE.finditer(bare):
            found.append(link_path(match["target"].strip()))
        for match in CODE_SPAN_RE.finditer(bare):
            if looks_like_forest_path(match["text"]) and not bare[match.end() :].startswith(ARCHIVED_MARK):
                found.append(match["text"].strip())
    return found


# ═══════════════════════════════ the plan ═══════════════════════════════


@dataclass(frozen=True, slots=True)
class SubjectRef:
    """One registry line of the planned forest and where it stands."""

    tree: str
    index: str
    line: RegistryLine

    @property
    def id(self) -> str:
        return self.line.id

    @property
    def level(self) -> str:
        return self.line.level

    @property
    def home(self) -> str:
        return self.line.home

    @property
    def type(self) -> str:
        return self.line.type

    @property
    def name(self) -> str:
        return self.line.name


@dataclass(slots=True)
class ArchiveItem:
    path: str
    data: bytes
    reason: str
    successor: str | None


@dataclass(slots=True)
class Expectation:
    """Lines an operation promises to keep, and the pages that must hold them after the plan."""

    what: str
    lines: list[str]
    targets: list[str]
    multiset: bool = False


@dataclass(slots=True)
class Plan:
    """The planned forest: what each touched page was and will be, and what the change logs."""

    root: Path
    config: Config
    today: str
    why: str | None = None
    operations: list[str] = field(default_factory=list)
    before: dict[str, str | None] = field(default_factory=dict)
    after: dict[str, str | None] = field(default_factory=dict)
    archives: list[ArchiveItem] = field(default_factory=list)
    moves: list[movelog.MoveRecord] = field(default_factory=list)
    records: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    hints: dict[str, str] = field(default_factory=dict)
    scopes: set[str] = field(default_factory=set)
    root_lists: bool = False
    expectations: list[Expectation] = field(default_factory=list)
    made_dirs: list[str] = field(default_factory=list)
    removed_dirs: list[str] = field(default_factory=list)
    appends: dict[str, list[str]] = field(default_factory=dict)
    moved: dict[str, str] = field(default_factory=dict)
    archived_paths: set[str] = field(default_factory=set)
    rewritten: Counter[str] = field(default_factory=Counter)
    _pending_mapping: dict[str, str] = field(default_factory=dict)
    _pending_archived: set[str] = field(default_factory=set)
    _disk: dict[str, str] = field(default_factory=dict)
    _disk_pages: set[str] | None = None
    _finished: bool = False

    @classmethod
    def open(
        cls,
        root: str | os.PathLike[str],
        *,
        config: Config | None = None,
        today: str | None = None,
        why: str | None = None,
    ) -> Plan:
        safe_root = require_real_root(root)
        settings = config if config is not None else load_config(safe_root)
        day = today or datetime.now(settings.zone).date().isoformat()
        return cls(root=safe_root, config=settings, today=day, why=" ".join(why.split()) if why else None)

    # ─────────────────────────── reading the planned forest ───────────────────────────

    def _on_disk(self) -> set[str]:
        if self._disk_pages is None:
            self._disk_pages = set(layout.iter_pages(self.root))
        return self._disk_pages

    def disk_text(self, path: str) -> str | None:
        """A page as it is on disk (None when it is not there)."""
        if path not in self._on_disk():
            return None
        if path not in self._disk:
            with open(layout.resolve(self.root, path), "r", encoding="utf-8", errors="surrogateescape", newline="") as handle:
                self._disk[path] = handle.read()
        return self._disk[path]

    def exists(self, path: str) -> bool:
        if path in self.after:
            return self.after[path] is not None
        return path in self._on_disk()

    def read(self, path: str) -> str:
        if path in self.after:
            text = self.after[path]
            if text is None:
                refuse("page_removed", "This page is removed earlier in the plan.", path=path)
            return text
        text = self.disk_text(path)
        if text is None:
            refuse("page_missing", "There is no such page in the forest.", path=path)
        return text

    def pages(self) -> list[str]:
        found = set(self._on_disk())
        for path, text in self.after.items():
            (found.discard if text is None else found.add)(path)
        return sorted(found)

    def folder_pages(self, folder: str) -> list[str]:
        prefix = folder.rstrip("/") + "/"
        return [path for path in self.pages() if path.startswith(prefix) and "/" not in path[len(prefix) :]]

    def under(self, folder: str) -> list[str]:
        prefix = folder.rstrip("/") + "/"
        return [path for path in self.pages() if path.startswith(prefix)]

    def folder_exists(self, folder: str) -> bool:
        if self.under(folder):
            return True
        return layout.resolve(self.root, folder).is_dir() and folder not in self.removed_dirs

    def trees(self) -> list[str]:
        """The trees that have an index in the planned forest."""
        found = []
        for path in self.pages():
            info = layout.classify(path)
            if info.role is layout.PageRole.TREE_INDEX and info.tree:
                found.append(info.tree)
        return sorted(found)

    # ─────────────────────────── changing the planned forest ───────────────────────────

    def _remember(self, path: str) -> None:
        if path not in self.before:
            self.before[path] = self.disk_text(path)

    def write(self, path: str, text: str) -> None:
        info = layout.classify(path)
        if not info.in_forest:
            refuse("path_not_in_forest", "Only pages of the four forest layers are written.", path=path)
        self._remember(path)
        self.after[path] = text

    def update(self, path: str, text: str) -> None:
        if text != self.read(path):
            self.write(path, text)

    def delete(self, path: str) -> None:
        self._remember(path)
        self.after[path] = None

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def reason(self, default: str) -> str:
        return self.why or default

    def log(self, operation: str, source: str, target: str | None, *, subject: str | None = None, why: str = "") -> None:
        self.moves.append(movelog.record(operation, source, target, subject=subject, reason=why))

    def record(self, path: str, line: str) -> None:
        """A record line for ``path``'s ``## Update record``, added when the plan finishes; a sign-in value in it
        is replaced by ``[REDACTED]`` and a number written in full is starred (contract, section 4)."""
        from . import credentials, numbers

        line = credentials.mask(line, config=self.config)
        self.records.setdefault(path, []).append(numbers.star_numbers(line, config=self.config)[0])

    def star_in_records(self, values: Iterable[str]) -> None:
        """Every record line this plan writes names each of ``values`` (a subject's number-name) only starred."""
        from . import numbers

        forms = sorted({value for value in values if value}, key=len, reverse=True)
        for path, lines in self.records.items():
            for index, line in enumerate(lines):
                for value in forms:
                    line = line.replace(value, numbers.star_middle(value))
                lines[index] = line

    def record_change(self, path: str, what: str, was: str, now: str, why: str) -> None:
        """A change line (the first kind): what a statement or frontmatter value of the page was and became."""
        self.record(path, RecordLine.make(self.today, what, was=was, now=now, why=why).render())

    def record_provenance(self, path: str, what: str, why: str) -> None:
        """A line of the third kind: where the page's own lines came from or went, ``what`` naming that page or
        copy by its path in a code span."""
        self.record(path, RecordLine.make(self.today, what, why=why).render())

    def map(self, old: str, new: str) -> None:
        """References to ``old`` (a page, or a folder without ``.md``) follow it to ``new``."""
        self._pending_mapping[old] = new

    def mark_archived(self, path: str) -> None:
        self._pending_archived.add(path)
        self.archived_paths.add(path)

    def touch(self, *trees: str | None) -> None:
        """Bring the derived lists of these trees up to the pages when the plan settles."""
        self.scopes.update(tree for tree in trees if tree)

    def move_page(self, old: str, new: str, *, operation: str, why: str, subject: str | None = None,
                  record: bool = True) -> str:
        """Move one page: written at ``new`` (its up-link naming its new parent), removed from ``old``, logged."""
        if old == new:
            refuse("move_to_same_place", "The page is already there.", path=old)
        if not self.exists(old):
            refuse("page_missing", "There is no such page in the forest.", path=old)
        if self.exists(new):
            refuse("target_exists", "A page already exists where this one would go.", path=new)
        text = self.read(old)
        from . import maps

        listed = maps.listed_description(self, old)
        info = layout.classify(new)
        if listed and info.role is layout.PageRole.BOARD:
            self.hints.setdefault(f"branch:{info.tree}/{info.branch}", listed)
        elif listed:
            self.hints.setdefault(new, listed)
        has_up = any(UpLink.match(line) for line in section_items(text, LINKS_SECTION))
        if layout.parent_page(new) is not None and (not has_up or layout.parent_page(new) != layout.parent_page(old)):
            text = with_up_link(text, new)
        self.write(new, text)
        self.delete(old)
        self.map(old, new)
        origin = next((first for first, last in self.moved.items() if last == old), old)
        self.moved[origin] = new
        if old in self.records:
            self.records[new] = self.records.pop(old) + self.records.get(new, [])
        if old in self.hints:
            self.hints.setdefault(new, self.hints[old])
        self.log(operation, old, new, subject=subject, why=why)
        if record:
            self.record_change(new, "Path changed", old, new, why)
        for path in (old, new):
            self.touch(layout.classify(path).tree)
        return text

    def archive_page(self, path: str, *, reason: str, successor: str | None = None, subject: str | None = None,
                     operation: str = "archive") -> None:
        """Take a page out of the forest into the archive; references to it are marked `` (archived)``."""
        text = self.read(path)
        self.archives.append(ArchiveItem(path, encode(text), reason, successor))
        self.delete(path)
        self.mark_archived(path)
        self.log(operation, path, successor, subject=subject, why=reason)
        self.touch(layout.classify(path).tree)

    def fold_page(self, path: str, successor: str, *, reason: str, operation: str, subject: str | None = None) -> None:
        """Take a page whose lines went to ``successor`` into the archive; references to it follow to ``successor``."""
        text = self.read(path)
        self.archives.append(ArchiveItem(path, encode(text), reason, successor))
        self.delete(path)
        self.map(path, successor)
        self.log(operation, path, successor, subject=subject, why=reason)
        self.touch(layout.classify(path).tree)

    def keep_copy(self, path: str, *, reason: str, successor: str | None = None) -> None:
        """Keep the page's current text in the archive while it is rewritten (``archive put --keep``)."""
        self.archives.append(ArchiveItem(path, encode(self.read(path)), reason, successor))

    def expect(self, what: str, lines: Iterable[str], targets: Iterable[str], *, multiset: bool = False) -> None:
        self.expectations.append(Expectation(what, [line for line in lines if line.strip()], list(targets), multiset))

    def append_state(self, relative: str, lines: Iterable[str]) -> None:
        """Lines appended at commit to an append-only state file under ``.forest/`` (the judged ledger),
        covered by the undo journal like the move log."""
        layout.state_path(self.root, relative)
        self.appends.setdefault(relative, []).extend(lines)

    def make_dir(self, folder: str) -> None:
        if folder not in self.made_dirs:
            self.made_dirs.append(folder)

    def remove_dir(self, folder: str) -> None:
        if folder not in self.removed_dirs:
            self.removed_dirs.append(folder)

    # ─────────────────────────── the registry of the planned forest ───────────────────────────

    def subjects(self) -> list[SubjectRef]:
        found: list[SubjectRef] = []
        for tree in self.trees():
            index = layout.tree_index(tree)
            for line in section_items(self.read(index), SUBJECTS_SECTION):
                parsed = RegistryLine.match(line)
                if parsed is not None:
                    found.append(SubjectRef(tree, index, parsed))
        return found

    def find_subject(self, subject_id: str) -> SubjectRef | None:
        return next((item for item in self.subjects() if item.id == subject_id), None)

    def subject(self, subject_id: str) -> SubjectRef:
        found = self.find_subject(subject_id)
        if found is None:
            refuse("subject_unknown", "No registry line has this id.", subject=subject_id)
        return found

    def replace_subject(self, ref: SubjectRef, new: RegistryLine | None) -> None:
        """Change or drop one registry line where it stands."""
        text = self.read(ref.index)
        done = False

        def keep(line: str) -> str | None:
            nonlocal done
            parsed = RegistryLine.match(line)
            if done or parsed is None or parsed.id != ref.id:
                return line
            done = True
            return None if new is None else new.render()

        self.update(ref.index, edit_items(text, SUBJECTS_SECTION, keep))
        if not done:
            refuse("subject_unknown", "The registry line was not found where the registry put it.", subject=ref.id)

    def add_subject(self, tree: str, line: RegistryLine) -> None:
        index = layout.tree_index(tree)
        if not self.exists(index):
            refuse("tree_unknown", "The tree has no index.", tree=tree)
        self.update(index, add_items(self.read(index), SUBJECTS_SECTION, [line.render()],
                                     before=(BRANCHES_SECTION, LINKS_SECTION, RECORD_SECTION)))

    def collection_pages(self) -> list[str]:
        return [path for path in self.pages() if layout.page_role(path) is layout.PageRole.COLLECTION_PAGE]

    def entries(self) -> list[tuple[str, registry.CollectionEntry]]:
        found = []
        for path in self.collection_pages():
            entries, _problems = registry.parse_collection_page(self.read(path))
            found.extend((path, entry) for entry in entries)
        return found

    def name_holders(self, names: Iterable[str], *, besides: str | None = None) -> dict[str, str]:
        """For each name a subject or entry other than ``besides`` already holds: the id that holds it."""
        held: dict[str, str] = {}
        for ref in self.subjects():
            if ref.id == besides:
                continue
            for name in (ref.id, ref.name, *ref.line.aliases):
                for key in (registry.normalize_name(name), registry.loose_name(name)):
                    if key:
                        held.setdefault(key, ref.id)
        for _page, entry in self.entries():
            if entry.id == besides:
                continue
            for name in (entry.id, entry.name, *entry.aliases):
                for key in (registry.normalize_name(name), registry.loose_name(name)):
                    if key:
                        held.setdefault(key, entry.id)
        found: dict[str, str] = {}
        for name in names:
            holder = held.get(registry.normalize_name(name)) or held.get(registry.loose_name(name))
            if holder:
                found[name] = holder
        return found

    def known_ids(self) -> set[str]:
        return {ref.id for ref in self.subjects()} | {entry.id for _page, entry in self.entries()}

    def leaves_of(self, subject_id: str) -> list[str]:
        """Every leaf (collection pages aside) whose ``entity:`` is the subject."""
        found = []
        for path in self.pages():
            if layout.page_role(path) is layout.PageRole.LEAF and field_of(self.read(path), "entity") == subject_id:
                found.append(path)
        return found

    def replace_about(self, old: str, new: str | None) -> None:
        """Every ``about:`` that names ``old`` names ``new`` instead (or drops it when ``new`` is None)."""
        for path in self.pages():
            if layout.page_role(path) is not layout.PageRole.LEAF:
                continue
            text = self.read(path)
            page = parse_page(text)
            if page.frontmatter is None or "about" not in page.frontmatter:
                continue
            ids = registry.about_ids(page.frontmatter.get("about"))
            if old not in ids:
                continue
            entity = page.field("entity")
            changed: list[str] = []
            for value in ids:
                value = new if value == old else value
                if value and value != entity and value not in changed:
                    changed.append(value)
            self.update(path, with_field(text, "about", ", ".join(changed) if changed else registry.ABOUT_NONE))

    # ─────────────────────────── settling and finishing ───────────────────────────

    def settle(self) -> None:
        """Rewrite the references to what moved since the last settle, then the touched derived lists."""
        mapping, archived = dict(self._pending_mapping), set(self._pending_archived)
        self._pending_mapping.clear()
        self._pending_archived.clear()
        if mapping or archived:
            needles = [*mapping, *archived]
            for path in self.pages():
                text = self.read(path)
                if not any(needle in text for needle in needles):
                    continue
                new, count = rewrite_references(text, mapping, archived)
                if count:
                    self.update(path, new)
                    self.rewritten[path] += count
        if self.scopes or self.root_lists:
            from . import maps

            maps.sync(self, trees=sorted(self.scopes), root_index=True)
            self.scopes.clear()
            self.root_lists = False

    def finish(self) -> None:
        """Settle, then add every record line; note leaves this plan took over the content cap."""
        if self._finished:
            return
        self.settle()
        for path, lines in sorted(self.records.items()):
            if self.exists(path):
                self.update(path, with_records(self.read(path), lines))
        cap = self.config.limits.leaf_cap_bytes
        for path, text in sorted(self.after.items()):
            if text is None or layout.page_role(path) is not layout.PageRole.LEAF:
                continue
            size = len(encode(parse_page(text).content_text()))
            origin = next((first for first, last in self.moved.items() if last == path), path)
            before = self.before.get(origin)
            if size > cap and (before is None or len(encode(parse_page(before).content_text())) <= cap):
                self.note(f"leaf_over_cap: {path} holds {size} bytes of content after this plan; split it by sub-topic")
        self._finished = True

    # ─────────────────────────── checks ───────────────────────────

    def registry_problems(self) -> set[tuple[str, str, str]]:
        """(code, page, detail) of every registry problem of the planned forest, read the way ``registry.build`` reads it."""
        problems: set[tuple[str, str, str]] = set()
        seen: dict[str, str] = {}
        names: dict[str, str] = {}
        for tree in self.trees():
            index = layout.tree_index(tree)
            for raw in section_items(self.read(index), SUBJECTS_SECTION):
                try:
                    line = RegistryLine.parse(raw)
                except ValueError as exc:
                    problems.add(("registry_line_invalid", index, f"{exc}: {raw.strip()}"))
                    continue
                if not layout.is_slug(line.id) or line.type not in self.config.subject_types:
                    problems.add(("registry_line_invalid", index, f"`{line.id}`: id or type out of shape"))
                    continue
                if line.level not in registry.LEVELS:
                    problems.add(("registry_line_invalid", index, f"`{line.id}`: level `{line.level}`"))
                    continue
                if line.id in seen:
                    problems.add(("registry_duplicate_id", index, f"`{line.id}`"))
                    continue
                seen[line.id] = index
                subject = {"id": line.id, "type": line.type, "level": line.level, "home": line.home, "tree": tree}
                reason = registry.home_problem(subject)
                if reason is not None:
                    problems.add(("registry_line_invalid", index, reason))
                elif not self.exists(line.home):
                    problems.add(("registry_home_missing", index, f"`{line.id}`: `{line.home}`"))
                for name in {registry.normalize_name(item) for item in (line.id, line.name, *line.aliases)}:
                    if name and names.setdefault(name, line.id) != line.id:
                        problems.add(("registry_duplicate_alias", index, f"`{name}` of `{line.id}` and `{names[name]}`"))
        for page, entry in self.entries():
            for name in {registry.normalize_name(item) for item in (entry.id, entry.name, *entry.aliases)}:
                holder = names.get(name)
                if holder is not None and holder != entry.id:
                    problems.add(("registry_entry_is_subject", page, f"`{name}` of entry `{entry.id}` names `{holder}`"))
            if entry.id in seen:
                problems.add(("registry_entry_is_subject", page, f"entry `{entry.id}` is also a registered id"))
        return problems

    def check(self) -> list[str]:
        """Every problem of the planned forest that the forest on disk does not have; empty when sound."""
        self.finish()
        problems: list[str] = []
        before = Plan.open(self.root, config=self.config, today=self.today).registry_problems()
        for code, page, detail in sorted(self.registry_problems() - before):
            problems.append(f"{code}: {page}: {detail}")
        for path, text in sorted(self.after.items()):
            if text is None:
                continue
            info = layout.classify(path)
            if not info.in_forest:
                problems.append(f"path_shape: {path}: {info.problem or 'not a forest page'}")
                continue
            if self.before.get(path) is None and info.role is layout.PageRole.LEAF:
                trouble = layout.name_problem(PurePosixPath(path).name, config=self.config,
                                              siblings=self.folder_pages(str(PurePosixPath(path).parent)))
                if trouble is not None:
                    problems.append(f"leaf_dated_name: {path}: {trouble}")
            parent = layout.parent_page(path)
            if parent is None:
                continue
            ups = [UpLink.match(line) for line in section_items(text, LINKS_SECTION)]
            ups = [link for link in ups if link is not None]
            if not ups:
                if self.before.get(path) is None:
                    problems.append(f"up_link_missing: {path}")
                continue
            if ups[0].path != parent and self.before.get(path) is None:
                problems.append(f"up_link_wrong: {path}: names `{ups[0].path}`, not `{parent}`")
            if not self.exists(ups[0].path):
                problems.append(f"up_link_dangling: {path}: `{ups[0].path}` is not there")
        gone = {path for path, text in self.after.items() if text is None and self.disk_text(path) is not None}
        gone_folders = {folder for folder in self.removed_dirs if not self.under(folder)}
        if gone or gone_folders:
            for path in self.pages():
                text = self.read(path)
                if not any(item in text for item in (*gone, *gone_folders)):
                    continue
                for target in reference_paths(text):
                    value = target.rstrip("/")
                    if (value in gone or value in gone_folders) and not self.exists(value):
                        problems.append(f"reference_stale: {path}: `{target}` names a page or folder this plan removes")
        for expectation in self.expectations:
            held: Counter[str] = Counter()
            for target in expectation.targets:
                if self.exists(target):
                    held.update(masked(line) for line in body_lines(self.read(target)))
            wanted = Counter(masked(line) for line in expectation.lines)
            if expectation.multiset:
                missing = list((wanted - held).elements())
            else:
                missing = [line for line in wanted if line not in held]
            if missing:
                problems.append(
                    f"lines_lost: {expectation.what}: {len(missing)} line(s) are not on "
                    f"{', '.join(expectation.targets)}, e.g. {missing[0][:120]!r}"
                )
        return problems

    def verify(self) -> None:
        problems = self.check()
        if problems:
            refuse(
                "plan_check_failed",
                "The planned change would break the forest; nothing was written.",
                problems=problems[:40],
                problem_count=len(problems),
                operations=self.operations,
            )

    # ─────────────────────────── reporting and writing ───────────────────────────

    def report(self) -> dict[str, Any]:
        def pages(kind: str) -> list[str]:
            if kind == "created":
                return sorted(p for p, t in self.after.items() if t is not None and self.before.get(p) is None)
            if kind == "changed":
                return sorted(p for p, t in self.after.items() if t is not None and self.before.get(p) is not None
                              and self.before.get(p) != t)
            return sorted(p for p, t in self.after.items() if t is None and self.before.get(p) is not None)

        return {
            "archived": [item.path for item in self.archives if item.path in self.archived_paths],
            "changed": pages("changed"),
            "copies_kept": [item.path for item in self.archives if item.path not in self.archived_paths],
            "created": pages("created"),
            "moves": [item.as_dict() for item in self.moves],
            "notes": list(self.notes),
            "operations": list(self.operations),
            "records": {path: list(lines) for path, lines in sorted(self.records.items())},
            "appended": {path: len(lines) for path, lines in sorted(self.appends.items()) if lines},
            "references_rewritten": dict(sorted(self.rewritten.items())),
            "removed": pages("removed"),
            "today": self.today,
        }

    def commit(self, *, lock: LockToken | str | None = None) -> dict[str, Any]:
        """Write the checked plan under the writer lock: all of it, or the forest stays as it was.

        Before the first write an undo journal (``journal``) records every
        changed page's text before, the folders made and removed and the move
        log's length. A failure in this process rolls the pages and the move
        log back at once; a kill leaves the journal, which the next taker of
        the writer lock rolls back. The journal is removed once the pages, the
        read-back and the move-log append are done; the registry, which is
        derived, is rebuilt after that.
        """
        from . import journal

        self.verify()
        report = self.report()
        changed = {path: text for path, text in self.after.items() if text != self.before.get(path)}
        with writer_lock(self.root, token=lock, label="forest move") as held:
            for path, before in self.before.items():
                if self.disk_text_fresh(path) != before:
                    refuse("page_changed_meanwhile", "A page changed on disk while the plan was made.", path=path)
            for item in self.archives:
                archive.store(self.root, item.path, item.data, reason=item.reason, successor=item.successor, lock=held)
            new_folders = self._folders_to_make(changed)
            standing = [folder for folder in self.removed_dirs if layout.resolve(self.root, folder).is_dir()]
            appends_before = {relative: _file_size(layout.state_path(self.root, relative))
                              for relative, lines in self.appends.items() if lines}
            journal.write(self.root, label=" ; ".join(self.operations) or "forest move",
                          before={path: self.before.get(path) for path in changed},
                          made_dirs=new_folders, removed_dirs=standing, appends=appends_before)
            moves_before = _file_size(layout.moves_path(self.root))
            made: list[Path] = []
            written: list[str] = []
            try:
                for folder in self.made_dirs:
                    made.extend(_make_folders(self.root, folder))
                for path, text in sorted(changed.items()):
                    target = layout.resolve(self.root, path)
                    if text is None:
                        continue
                    made.extend(_make_folders(self.root, str(PurePosixPath(path).parent)))
                    written.append(path)
                    atomic_write(target, encode(text))
                for path, text in sorted(changed.items()):
                    if text is None:
                        target = layout.resolve(self.root, path)
                        written.append(path)
                        if target.exists():
                            target.unlink()
                for path, text in changed.items():
                    target = layout.resolve(self.root, path)
                    if text is None:
                        if target.exists():
                            refuse("write_readback_failed", "A removed page is still there.", path=path)
                    elif target.read_bytes() != encode(text):
                        refuse("write_readback_failed", "A page did not read back as written.", path=path)
                for folder in sorted(self.removed_dirs, key=len, reverse=True):
                    _remove_empty_folder(self.root, folder)
                movelog.append(self.root, self.moves, lock=held)
                for relative in appends_before:
                    _append_lines(layout.state_path(self.root, relative), self.appends[relative])
            except BaseException:
                for folder in standing:
                    _make_folders(self.root, folder)
                self._roll_back(written, made)
                _cut_file(layout.moves_path(self.root), moves_before)
                for relative, length in appends_before.items():
                    _cut_file(layout.state_path(self.root, relative), length)
                journal.remove(self.root)
                raise
            journal.remove(self.root)
            try:
                built = registry.build_and_write(self.root, config=self.config)
                report["registry_problems"] = len(built.problems)
            except (MemoryForestError, OSError) as exc:
                report["registry_problems"] = None
                self.note(f"registry_not_rebuilt: {exc.__class__.__name__}; `forest registry build` repairs it")
                report["notes"] = list(self.notes)
        report["written"] = True
        return report

    def _folders_to_make(self, changed: Mapping[str, str | None]) -> list[str]:
        """The forest folders this commit creates (made folders and new pages' parents) that are not there now."""
        wanted: list[str] = []
        for folder in [*self.made_dirs, *(str(PurePosixPath(path).parent) for path, text in changed.items()
                                          if text is not None)]:
            parts = layout.normalize(folder).split("/")
            for depth in range(1, len(parts) + 1):
                relative = "/".join(parts[:depth])
                if relative not in wanted and not layout.resolve(self.root, relative).exists():
                    wanted.append(relative)
        return wanted

    def disk_text_fresh(self, path: str) -> str | None:
        try:
            target = layout.resolve(self.root, path)
        except MemoryForestError:
            return None
        if not target.is_file():
            return None
        with open(target, "r", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            return handle.read()

    def _roll_back(self, written: list[str], made: list[Path]) -> None:
        for path in written:
            target = layout.resolve(self.root, path)
            before = self.before.get(path)
            if before is None:
                if target.exists():
                    target.unlink()
            else:
                _make_folders(self.root, str(PurePosixPath(path).parent))
                atomic_write(target, encode(before))
        for folder in reversed(made):
            try:
                folder.rmdir()
            except OSError:
                pass


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def _append_lines(path: Path, lines: Iterable[str]) -> None:
    """Lines appended durably to an append-only state file (made private when it is new)."""
    data = "".join(f"{line}\n" for line in lines).encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cut_file(path: Path, length: int) -> None:
    """An append-only file cut back to ``length`` bytes (what a failed commit appended goes)."""
    if path.is_file() and path.stat().st_size > length:
        with open(path, "r+b") as handle:
            handle.truncate(length)
            handle.flush()
            os.fsync(handle.fileno())


def _make_folders(root: Path, folder: str) -> list[Path]:
    """Create a forest folder and its missing parents; the folders made, outermost first."""
    made: list[Path] = []
    parts = layout.normalize(folder).split("/")
    for depth in range(1, len(parts) + 1):
        relative = "/".join(parts[:depth])
        target = layout.resolve(root, relative)
        if not target.exists():
            target.mkdir(mode=0o700)
            made.append(target)
        elif target.is_symlink() or not target.is_dir():
            refuse("unsafe_existing_path", "An expected folder is a symlink or a file.", path=relative)
    return made


def _remove_empty_folder(root: Path, folder: str) -> None:
    target = layout.resolve(root, folder)
    if not target.is_dir():
        return
    leftovers = [entry for entry in target.iterdir() if entry.name != ".DS_Store"]
    if leftovers:
        return
    for entry in target.iterdir():
        entry.unlink()
    target.rmdir()


def run(
    root: str | os.PathLike[str],
    build: Callable[[Plan], Any],
    *,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
    today: str | None = None,
    why: str | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Plan with ``build``, check, and (unless ``dry_run``) write under the writer lock.

    The plan is made while the lock is held, so it reads the forest it writes.
    """
    safe_root = require_real_root(root)
    if dry_run:
        plan = Plan.open(safe_root, config=config, today=today, why=why)
        result = build(plan)
        plan.verify()
        report = plan.report()
        report["written"] = False
        if result is not None:
            report["result"] = result
        return report
    with writer_lock(safe_root, token=lock, label="forest move") as held:
        plan = Plan.open(safe_root, config=config, today=today, why=why)
        result = build(plan)
        report = plan.commit(lock=held)
        if result is not None:
            report["result"] = result
        return report
