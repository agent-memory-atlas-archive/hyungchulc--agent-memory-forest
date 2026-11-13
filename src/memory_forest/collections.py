"""Collection pages: the subjects of a category seen on fewer days than a leaf needs (contract, section 3).

A category branch keeps such subjects as entries of its collection, one
subject of type ``collection`` (id ``<category>-seen-once``, or
``<tree>-<category>-seen-once`` when another tree has a category of that
name), whose pages are ``04 stm/<tree>/<category>/seen-once-<period>.md``:

- an entry goes on the page of the quarter of its first day, or of its month
  when the quarter's page would pass the leaf cap (the page counts whole);
- a month page past the cap is cut by its entries' first days into runs of
  consecutive days, the first run keeping the month page's name and each later
  run named by its first day; a day whose entries alone pass the cap takes
  numbered pages after its first (``-2``, ``-3``…), and an entry is never divided;
- a new entry of a month cut this way goes on the latest page of the month that
  starts on or before its first day, or, when that day has numbered pages, on
  the first of them with room, else on a new numbered page.

``demote`` folds a leaf subject into its category's collection; ``admit``
moves an entry out into a leaf of its own, the page keeping one reference line
under its description; ``recut`` cuts a page past the cap; ``pages`` lists
every page with its entries and their days. An entry's section holds its
material as a leaf would hold it, its headings one level down, its own record
lines under ``### Update record``; its dated lines are on the page's Timeline,
each opening with its name. Every move is logged with the entry's address
``<page>#<id>``, so ``forest move resolve`` follows an entry in and out.
"""

from __future__ import annotations

import os
import re
from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Final

from . import layout, registry, system
from .locking import LockToken
from .model import (
    RECORD_SECTION,
    CurrentStateLine,
    EntryLine,
    ReferenceLine,
    RegistryLine,
    Section,
    TimelineLine,
    UpLink,
    parse_page,
    split_line_ending,
)
from .plan import (
    LINKS_SECTION,
    Plan,
    SubjectRef,
    body_lines,
    day_runs,
    encode,
    field_of,
    refuse,
    run,
    script_of,
    shift_headings,
    up_label,
    with_field,
)

TIMELINE: Final[str] = "Timeline"
SEEN_ONCE: Final[str] = "seen-once"
ENTRY_FRAME: Final[tuple[str, ...]] = ("Links", RECORD_SECTION)
_OWN: Final[frozenset[str]] = frozenset(registry.COLLECTION_OWN_SECTIONS)


# ═══════════════════════════════ periods ═══════════════════════════════


def quarter_of(day: str) -> str:
    return f"{day[:4]}-q{(int(day[5:7]) - 1) // 3 + 1}"


def _month_end(month: str) -> str:
    return f"{month}-{monthrange(int(month[:4]), int(month[5:7]))[1]:02d}"


def period_start(period: str) -> tuple[str, int]:
    """The first day a page's period covers, and its page number (1 for all but a day's further pages)."""
    if "-q" in period:
        quarter = int(period[-1])
        return f"{period[:4]}-{(quarter - 1) * 3 + 1:02d}-01", 1
    if len(period) == 7:
        return f"{period}-01", 1
    if len(period) == 10:
        return period, 1
    return period[:10], int(period[11:])


def period_of(path: str) -> str | None:
    return layout.collection_period(PurePosixPath(path).stem)


def is_quarter(period: str) -> bool:
    return "-q" in period


# ═══════════════════════════════ reading a page ═══════════════════════════════


def entry_name(heading: str) -> str:
    """The name an entry's Timeline lines open with: its heading before any `` (``."""
    return heading.partition(" (")[0].strip()


@dataclass(slots=True)
class Entry:
    id: str
    heading: str
    line: EntryLine
    section: Section
    items: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return entry_name(self.heading)


def _is_entry(section: Section) -> EntryLine | None:
    if section.heading.casefold() in _OWN:
        return None
    for raw in section.lines:
        bare = split_line_ending(raw)[0].strip()
        if not bare:
            continue
        return EntryLine.match(bare)
    return None


def owner_of(line: str, names: Iterable[str]) -> str | None:
    """The entry name a collection Timeline line opens with (the longest that fits, followed by ``:``)."""
    parsed = TimelineLine.match(line)
    if parsed is None:
        return None
    text = parsed.text
    return next((name for name in sorted(names, key=len, reverse=True) if text.startswith(f"{name}:")), None)


def entries_of(text: str) -> list[Entry]:
    page = parse_page(text)
    found: list[Entry] = []
    for section in page.sections:
        line = _is_entry(section)
        if line is not None:
            found.append(Entry(line.id, section.heading, line, section))
    timeline = page.section(TIMELINE)
    names = {entry.name: entry for entry in found}
    for _index, line in timeline.items() if timeline is not None else []:
        owner = owner_of(line, names)
        if owner is not None:
            names[owner].items.append(line)
    return found


def first_day(entry: Entry, fallback: str) -> str:
    days = [parsed.day for line in entry.items if (parsed := TimelineLine.match(line)) is not None and parsed.day]
    return min(days) if days else fallback


def entry_dates(text: str) -> dict[str, str | None]:
    """Each entry's latest evidence on a collection page, by id: the latest day of the Timeline lines that open
    with its name (None for an entry with no dated line, which a reader dates by the page).

    Only the entry's own Timeline lines count. The lines under its ``### Update record`` say when a tool folded,
    cut or moved it, which is no evidence of the subject, and the page's ``updated_at`` follows every entry.
    """
    found: dict[str, str | None] = {}
    for entry in entries_of(text):
        days = [parsed.day for line in entry.items if (parsed := TimelineLine.match(line)) is not None and parsed.day]
        found[entry.id] = max(days) if days else None
    return found


def _material(entry: Entry) -> list[str]:
    """An entry's section lines after its heading, without trailing blank lines."""
    lines = [split_line_ending(raw)[0] for raw in entry.section.lines]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def material_bytes(entry: Entry) -> int:
    """An entry's own material: its section (its record lines are in it) and its Timeline lines."""
    section = len(encode(entry.section.render()))
    return section + sum(len(encode(line)) + 1 for line in entry.items)


def page_bytes(text: str) -> int:
    return len(encode(text))


# ═══════════════════════════════ writing a page ═══════════════════════════════


def category_words(category: str) -> str:
    return category.replace("-", " ").capitalize()


def collection_name(collection_id: str, category: str) -> str:
    return f"{category_words(category)} seen once"


def page_title(category: str, label: str) -> str:
    return f"# {category_words(category)} seen once ({label})"


def page_label(plan: Plan, path: str, firsts: list[str] | None = None) -> str:
    """What a page's title names: its period, or on a page of a month cut by days the span of first days it holds."""
    period = period_of(path) or ""
    if is_quarter(period) or (len(period) == 7 and not firsts):
        return period
    if not firsts:
        start = period_start(period)[0]
        return f"{start} to {start}"
    return f"{min(firsts)} to {max(firsts)}"


def render_page(
    *,
    entity: str,
    timezone: str,
    updated: str,
    title: str,
    head: list[str],
    entries: list[Entry],
    loose: list[str],
    up: str,
    links: list[str],
    record: list[str],
) -> str:
    """A collection page from its parts, in the contract's shape (section 3, Page shape)."""
    out = [
        "---", f"entity: {entity}", "subtopic: seen-once", "kind: collection",
        f"timezone: {timezone}", f"updated_at: {updated}", "---", "", title, "",
    ]
    out.extend(head)
    if head and head[-1].strip():
        out.append("")
    for entry in entries:
        out.append(f"## {entry.heading}")
        out.extend(_material(entry))
        out.append("")
    items = sorted([*(line for entry in entries for line in entry.items), *loose], key=_key)
    out.extend(["## Timeline", "", *items, ""] if items else ["## Timeline", ""])
    out.extend(["## Links", "", up, *links, ""])
    if record:
        out.extend(["## Update record", "", *record, ""])
    return "\n".join(out)


def _key(line: str) -> tuple[str, str]:
    parsed = TimelineLine.match(line)
    return (parsed.date, (parsed.start or "").zfill(5)) if parsed else ("", "")


@dataclass(slots=True)
class PageParts:
    """A collection page read into the parts ``render_page`` writes."""

    path: str
    entity: str
    timezone: str
    updated: str
    description: list[str]
    references: list[str]
    entries: list[Entry]
    loose: list[str]
    links: list[str]
    record: list[str]
    extra_fields: list[str]

    def render(self, plan: Plan, *, title: str | None = None) -> str:
        head = [*self.description]
        if self.references:
            if head:
                head.append("")
            head.extend(self.references)
        dates = [parsed.day for entry in self.entries for line in entry.items
                 if (parsed := TimelineLine.match(line)) is not None and parsed.day]
        dates += [parsed.day for line in self.loose if (parsed := TimelineLine.match(line)) and parsed.day]
        updated = max([self.updated, *dates]) if self.updated or dates else plan.today
        category = layout.classify(self.path).branch or ""
        firsts = [first_day(entry, self.updated or plan.today) for entry in self.entries]
        cut_month = _cut_title(plan, self.path)
        text = render_page(
            entity=self.entity, timezone=self.timezone, updated=updated,
            title=title or page_title(category, page_label(plan, self.path, firsts if cut_month else None)),
            head=head, entries=self.entries, loose=self.loose,
            up=UpLink(layout.up_link_target(layout.parent_page(self.path) or ""),
                      up_label(layout.parent_page(self.path) or "")).render(),
            links=self.links, record=self.record,
        )
        for line in self.extra_fields:
            key, _, value = line.partition(":")
            text = with_field(text, key.strip(), value.strip())
        return text


def _cut_title(plan: Plan, path: str) -> bool:
    """Whether a page belongs to a month cut by days (its title then names the span of first days it holds)."""
    period = period_of(path) or ""
    if len(period) >= 10:
        return True
    if len(period) != 7:
        return False
    info = layout.classify(path)
    folder = layout.leaf_folder(info.tree or "", info.branch or "")
    return any(len(period_of(other) or "") >= 10 and (period_of(other) or "")[:7] == period
               for other in plan.folder_pages(folder))


def read_parts(plan: Plan, path: str) -> PageParts:
    text = plan.read(path)
    page = parse_page(text)
    entries = entries_of(text)
    owned = {line for entry in entries for line in entry.items}
    timeline = page.section(TIMELINE)
    loose = [line for _i, line in timeline.items() if line not in owned] if timeline is not None else []
    description: list[str] = []
    references: list[str] = []
    for raw in page.head:
        bare = split_line_ending(raw)[0]
        if bare.lstrip().startswith("# "):
            continue
        if ReferenceLine.match(bare.strip()) is not None:
            references.append(bare.strip())
        elif bare.strip() or description:
            description.append(bare)
    while description and not description[-1].strip():
        description.pop()
    links = page.section(LINKS_SECTION)
    record = page.section(RECORD_SECTION)
    known = {"entity", "subtopic", "kind", "timezone", "updated_at"}
    extra = []
    if page.frontmatter is not None:
        extra = [f"{item.key}: {page.frontmatter.text(item.key)}" for item in page.frontmatter.fields
                 if item.key not in known]
    return PageParts(
        path=path,
        entity=page.field("entity"),
        timezone=page.field("timezone") or plan.config.timezone,
        updated=page.field("updated_at"),
        description=description,
        references=references,
        entries=entries,
        loose=loose,
        links=[line for _i, line in links.items() if UpLink.match(line) is None] if links else [],
        record=record.item_lines() if record is not None else [],
        extra_fields=extra,
    )


# ═══════════════════════════════ the collection subject ═══════════════════════════════


def collection_of(plan: Plan, tree: str, category: str) -> SubjectRef | None:
    folder = layout.leaf_folder(tree, category) + "/"
    return next((ref for ref in plan.subjects()
                 if ref.type == registry.COLLECTION_TYPE and ref.home.startswith(folder)), None)


def folder_pages(plan: Plan, tree: str, category: str) -> list[str]:
    """The collection pages of a category, in the order of the days they start on."""
    pages = [path for path in plan.folder_pages(layout.leaf_folder(tree, category))
             if layout.page_role(path) is layout.PageRole.COLLECTION_PAGE]
    return sorted(pages, key=lambda path: period_start(period_of(path) or ""))


def ensure_collection(plan: Plan, tree: str, category: str, first_page: str) -> str:
    """The id of the category's collection; a registry line is made for it when it has none."""
    found = collection_of(plan, tree, category)
    if found is not None:
        return found.id
    plain, prefixed = registry.collection_ids(tree, category)
    elsewhere = any(other != tree and plan.exists(layout.board(other, category)) for other in plan.trees())
    taken = plan.known_ids()
    collection_id = prefixed if elsewhere or plain in taken else plain
    if collection_id in taken:
        refuse("collection_id_taken", "The collection's id is held by another subject.", subject=collection_id)
    name = collection_name(collection_id, category)
    plan.add_subject(tree, RegistryLine(collection_id, name, registry.COLLECTION_TYPE, "leaf", first_page, []))
    return collection_id


def sync_home(plan: Plan, tree: str, category: str) -> None:
    """The collection's registry home is its first page."""
    ref = collection_of(plan, tree, category)
    pages = folder_pages(plan, tree, category)
    if ref is not None and pages and ref.home != pages[0]:
        plan.replace_subject(ref, replace(ref.line, home=pages[0]))


def new_parts(plan: Plan, path: str, entity: str) -> PageParts:
    category = layout.classify(path).branch or ""
    words = category.replace("-", " ")
    return PageParts(
        path=path, entity=entity, timezone=plan.config.timezone, updated="",
        description=[f"Subjects of {words} seen on fewer than {plan.config.levels.leaf_days} days, one section each; "
                     "their dated lines are in the Timeline."],
        references=[], entries=[], loose=[], links=[], record=[], extra_fields=[],
    )


# ═══════════════════════════════ placing and cutting ═══════════════════════════════


def _cap(plan: Plan) -> int:
    return plan.config.limits.leaf_cap_bytes


def _month_pages(plan: Plan, tree: str, category: str, month: str) -> list[str]:
    return [path for path in folder_pages(plan, tree, category)
            if not is_quarter(period_of(path) or "") and (period_of(path) or "")[:7] == month]


def _with_entry(plan: Plan, path: str, entry: Entry, entity: str) -> PageParts:
    parts = read_parts(plan, path) if plan.exists(path) else new_parts(plan, path, entity)
    parts.entries.append(entry)
    return parts


def _fits(plan: Plan, parts: PageParts) -> bool:
    return page_bytes(parts.render(plan)) <= _cap(plan)


def choose_page(plan: Plan, tree: str, category: str, day: str, entry: Entry, entity: str) -> str:
    """The page a new entry first seen on ``day`` goes on (contract, section 3, Collections)."""
    folder = layout.leaf_folder(tree, category)
    quarter_page = layout.collection_page(tree, category, quarter_of(day))
    month = day[:7]
    months = _month_pages(plan, tree, category, month)
    if not months:
        quarter_months = [path for path in folder_pages(plan, tree, category)
                          if not is_quarter(period_of(path) or "") and quarter_of(period_start(period_of(path) or "")[0])
                          == quarter_of(day)]
        if not quarter_months or plan.exists(quarter_page):
            if _fits(plan, _with_entry(plan, quarter_page, entry, entity)):
                return quarter_page
        return layout.collection_page(tree, category, month)
    starts = [(period_start(period_of(path) or "") , path) for path in months]
    runs = [(start, path) for (start, number), path in starts if number == 1 and start <= day]
    chosen = max(runs)[1] if runs else layout.collection_page(tree, category, month)
    numbered = sorted(path for (start, number), path in starts if start == day and number > 1)
    if not numbered:
        return chosen
    for path in [chosen, *numbered]:
        if _fits(plan, _with_entry(plan, path, entry, entity)):
            return path
    last = period_start(period_of(numbered[-1]) or "")[1]
    return f"{folder}/seen-once-{day}-{last + 1}.md"


def cut(plan: Plan, path: str, *, why: str) -> list[str]:
    """Cut a collection page past the cap (contract, section 3, Cutting a full month); the pages it ends as."""
    period = period_of(path)
    if period is None or not plan.exists(path):
        refuse("not_a_collection_page", "The page is not a collection page of the forest.", path=path)
    info = layout.classify(path)
    tree, category = info.tree or "", info.branch or ""
    parts = read_parts(plan, path)
    if page_bytes(plan.read(path)) <= _cap(plan):
        return [path]
    if len(parts.entries) < 2:
        plan.note(f"collection_entry_over_cap: {path} passes the cap with one entry, which is never divided")
        return [path]
    fallback = parts.updated or plan.today
    keyed = sorted(((first_day(entry, fallback), number, entry) for number, entry in enumerate(parts.entries)),
                   key=lambda item: (item[0], item[1]))
    folder = layout.leaf_folder(tree, category)
    groups: list[tuple[str, list[Entry]]] = []
    if is_quarter(period):
        by_month: dict[str, list[Entry]] = {}
        for day, _number, entry in keyed:
            by_month.setdefault(day[:7], []).append(entry)
        for month, entries in sorted(by_month.items()):
            target = layout.collection_page(tree, category, month)
            if plan.exists(target):
                refuse("collection_page_taken", "A month page the cut would make already exists.", path=target)
            groups.append((target, entries))
    else:
        days: dict[str, list[Entry]] = {}
        for day, _number, entry in keyed:
            days.setdefault(day, []).append(entry)

        def fits(name: str, entries: list[Entry]) -> bool:
            trial = _page_for(plan, parts, name, entries, name == path)
            return page_bytes(trial.render(plan)) <= _cap(plan)

        groups = day_runs(path, sorted(days.items()),
                          lambda day, number: f"{folder}/seen-once-{day}{'' if number == 1 else f'-{number}'}.md", fits)
        for name, _entries in groups:
            if name != path and plan.exists(name):
                refuse("collection_page_taken", "A page the cut would make already exists.", path=name)
    if len(groups) < 2 and not is_quarter(period):
        return [path]
    first_name = groups[0][0]
    made: list[str] = []
    # The later pages first, so that the first page's title knows its month is cut.
    for number, (name, entries) in reversed(list(enumerate(groups))):
        page = _page_for(plan, parts, name, entries, number == 0)
        if name == path:
            plan.update(name, page.render(plan))
        else:
            plan.write(name, page.render(plan))
        made.insert(0, name)
        for entry in entries:
            if name != path:
                plan.log("recut", f"{path}#{entry.id}", f"{name}#{entry.id}", subject=entry.id, why=why)
                plan.map(f"{path}#{entry.id}", f"{name}#{entry.id}")
        if name != path:
            plan.record_provenance(name, f"Entries came from `{path}`, cut by their first days", why)
    others = [name for name in made if name != path]
    if is_quarter(period):
        plan.fold_page(path, first_name, reason=why, operation="recut")
    else:
        plan.record_provenance(path, "Entries moved out by their first days to "
                               + ", ".join(f"`{other}`" for other in others), why)
    lines = [line for line in body_lines(parts.render(plan)) if not line.startswith("# ")]
    plan.expect(f"collection page {path}", lines, made)
    plan.touch(tree)
    sync_home(plan, tree, category)
    return made


def _page_for(plan: Plan, source: PageParts, name: str, entries: list[Entry], first: bool) -> PageParts:
    if first:
        return replace(source, path=name, entries=list(entries))
    return replace(source, path=name, entries=list(entries), references=[], loose=[], links=[], record=[],
                   updated="")


# ═══════════════════════════════ demote ═══════════════════════════════


def _other_name(name: str, aliases: Iterable[str]) -> str | None:
    """The first alias written in another script than the name (the heading's ``(<the name in another script>)``)."""
    own = script_of(name)
    if own is None:
        return None
    for alias in aliases:
        other = script_of(alias)
        if other is not None and other != own:
            return alias
    return None


def demote(plan: Plan, subject_id: str) -> str:
    """Fold a leaf subject into its category's collection; returns the page its entry is on."""
    ref = plan.subject(subject_id)
    if ref.type in (registry.COLLECTION_TYPE, registry.AGENT_TYPE):
        refuse("demote_unsupported", "A collection or an agent is never folded into a collection.", subject=subject_id)
    if ref.level != "leaf":
        refuse("demote_unsupported", "Only a leaf subject folds into its category's collection.", subject=subject_id)
    info = layout.classify(ref.home)
    if info.role is not layout.PageRole.LEAF or not info.tree or not info.branch:
        refuse("demote_unsupported", "The subject's home is not a leaf in a category branch.", subject=subject_id)
    tree, category = info.tree, info.branch
    sources = sorted(set(plan.leaves_of(subject_id)) | {ref.home})
    if len(sources) != 1:
        refuse("demote_several_leaves", "A subject of several leaves is merged into one leaf before it is demoted.",
               leaves=sources)
    source = sources[0]
    text = plan.read(source)
    page = parse_page(text)
    if page.field("standing"):
        refuse("demote_standing", "A standing line keeps the subject a leaf whatever its days.", subject=subject_id)
    name = ref.name
    if any(entry.name.casefold() == name.casefold() for path in folder_pages(plan, tree, category)
           for entry in entries_of(plan.read(path))):
        refuse("entry_name_taken", "An entry of this name is already on the collection.", name=name)
    why = plan.reason(f"`{subject_id}` is recorded on fewer than {plan.config.levels.leaf_days} days")
    other = _other_name(name, ref.line.aliases)
    heading = f"{name} ({other})" if other else name
    lines = [EntryLine(subject_id, ref.type, list(ref.line.aliases)).render()]
    if page.field("kind"):
        lines.append(f"- kind: {page.field('kind')}")
    lines.append("")
    description = [split_line_ending(raw)[0] for raw in page.head
                   if raw.strip() and not split_line_ending(raw)[0].lstrip().startswith("# ")]
    lines.extend(description)
    items: list[str] = []
    for section in page.sections:
        if section.is_named(TIMELINE):
            for _index, line in section.items():
                parsed = TimelineLine.parse(line)
                parsed.text = f"{name}: {parsed.text}"
                items.append(parsed.render())
            continue
        body = [split_line_ending(raw)[0] for raw in section.lines if raw.strip()]
        if section.is_named(LINKS_SECTION):
            body = [line for line in body if UpLink.match(line) is None]
        if not body and not section.is_named(RECORD_SECTION):
            continue
        lines.extend(["", f"### {section.heading}", *shift_headings(body, 1)])
    record_head = "### " + RECORD_SECTION
    fold_line = None
    entry_section = Section(f"## {heading}\n", [line + "\n" for line in lines] + ["\n"])
    entry = Entry(subject_id, heading, EntryLine(subject_id, ref.type, list(ref.line.aliases)), entry_section, items)
    day = first_day(entry, page.field("updated_at") or plan.today)
    collection = collection_of(plan, tree, category)
    entity = collection.id if collection is not None else "pending"
    target = choose_page(plan, tree, category, day, entry, entity)
    fold_line = f'- {plan.today} — Path changed: was "{source}", now "{target}#{subject_id}". Why: {why}.'
    if record_head not in lines:
        lines.extend(["", record_head])
    lines.append(fold_line)
    entry.section = Section(f"## {heading}\n", [line + "\n" for line in lines] + ["\n"])
    if not plan.exists(target):
        entity = ensure_collection(plan, tree, category, target)
        parts = new_parts(plan, target, entity)
    else:
        parts = read_parts(plan, target)
        entity = parts.entity
    parts.entries.append(entry)
    parts.updated = max(parts.updated or "", page.field("updated_at") or "")
    if plan.exists(target):
        plan.update(target, parts.render(plan))
    else:
        plan.write(target, parts.render(plan))
    plan.keep_copy(source, reason=why, successor=target)
    plan.delete(source)
    # A line that cited the leaf states this subject: it now cites the entry, not the whole page.
    plan.map(source, f"{target}#{subject_id}")
    plan.log("demote", source, f"{target}#{subject_id}", subject=subject_id, why=why)
    plan.replace_subject(ref, None)
    expected = [line for line in body_lines(text) if not line.lstrip().startswith("# ")]
    expected = [line for line in expected if TimelineLine.match(line) is None and UpLink.match(line) is None]
    plan.expect(f"leaf {source}", [*expected, *items], [target])
    plan.touch(tree)
    final = target
    if page_bytes(plan.read(target)) > _cap(plan):
        made = cut(plan, target, why=plan.reason("the collection page passed the cap"))
        final = next((path for path in made if any(entry.id == subject_id for entry in entries_of(plan.read(path)))),
                     target)
    sync_home(plan, tree, category)
    plan.operations.append(f"collections demote {subject_id}")
    plan.settle()
    return final


# ═══════════════════════════════ admit ═══════════════════════════════


def find_entry(plan: Plan, entry_id: str) -> tuple[str, Entry]:
    for path in plan.collection_pages():
        text = plan.read(path)
        if f"`{entry_id}`" not in text:
            continue
        for entry in entries_of(text):
            if entry.id == entry_id:
                return path, entry
    refuse("entry_unknown", "No collection page has this entry.", entry=entry_id)


def admit(plan: Plan, entry_id: str) -> str:
    """Move an entry out into a leaf of its own, with its registry line; returns the new leaf."""
    page_path, entry = find_entry(plan, entry_id)
    if plan.find_subject(entry_id) is not None:
        refuse("entry_is_subject", "The entry's id is already a registered subject.", subject=entry_id)
    _slug_ok = layout.is_slug(entry_id)
    if not _slug_ok:
        refuse("not_a_slug", "The entry's id cannot name a leaf.", entry=entry_id)
    info = layout.classify(page_path)
    tree, category = info.tree or "", info.branch or ""
    leaf = layout.leaf(tree, category, entry_id)
    if plan.exists(leaf):
        refuse("target_exists", "A leaf of this id already exists.", path=leaf)
    parts = read_parts(plan, page_path)
    why = plan.reason(f"`{entry_id}` is due a leaf of its own")
    material = _material(entry)
    standing = kind = None
    body: list[str] = []
    for line in material:
        stripped = line.strip()
        if EntryLine.match(stripped) is not None:
            continue
        if not body and stripped.startswith("- standing:") and standing is None:
            standing = stripped.removeprefix("- standing:").strip()
            continue
        if not body and stripped.startswith("- kind:") and kind is None:
            kind = stripped.removeprefix("- kind:").strip()
            continue
        if not body and not stripped:
            continue
        body.append(line)
    body = shift_headings(body, -1)
    record: list[str] = []
    links: list[str] = []
    free: list[str] = []
    current = free
    for line in body:
        if line.strip() == f"## {RECORD_SECTION}":
            current = record
            continue
        if line.strip() == f"## {LINKS_SECTION}":
            current = links
            continue
        if line.startswith("## "):
            current = free
        current.append(line)
    items = []
    for line in entry.items:
        parsed = TimelineLine.parse(line)
        parsed.text = parsed.text[len(entry.name) + 1:].lstrip()
        items.append(parsed.render())
    dates = [parsed.day for line in items if (parsed := TimelineLine.match(line)) and parsed.day]
    updated = max(dates) if dates else (parts.updated or plan.today)
    leaf_kind = kind if kind in registry.LEAF_KINDS and kind != registry.COLLECTION_KIND else None
    front = ["---", f"entity: {entry_id}", "subtopic: profile"]
    if leaf_kind:
        front.append(f"kind: {leaf_kind}")
    if standing:
        front.append(f"standing: {standing}")
    front += [f"timezone: {parts.timezone}", f"updated_at: {updated}", "---", ""]
    text_lines = [*front, f"# {entry.name}", ""]
    text_lines += [line for line in free]
    while text_lines and not text_lines[-1].strip():
        text_lines.pop()
    text_lines += ["", "## Timeline", "", *items, "", "## Links", "",
                   UpLink(layout.up_link_target(layout.board(tree, category)),
                          up_label(layout.board(tree, category))).render(),
                   *[line for line in links if line.strip()], ""]
    record = [line for line in record if line.strip()]
    if record:
        text_lines += ["## Update record", "", *record, ""]
    plan.write(leaf, "\n".join(text_lines))
    plan.record_change(leaf, "Path changed", f"{page_path}#{entry_id}", leaf, why)
    aliases = list(entry.line.aliases)
    if entry.heading != entry.name:
        other = entry.heading[len(entry.name):].strip()[1:-1].strip()
        if other and other not in aliases:
            aliases.insert(0, other)
    plan.add_subject(tree, RegistryLine(entry_id, entry.name, entry.line.type, "leaf", leaf, aliases))
    day = max(dates) if dates else plan.today
    parts.entries = [other for other in parts.entries if other.id != entry_id]
    parts.references.append(
        ReferenceLine(day, f"{entry.name} admitted as a leaf of its own", leaf).render()
    )
    plan.update(page_path, parts.render(plan))
    plan.record_provenance(page_path, f"Entry `{entry_id}` moved out to its own leaf `{leaf}`", why)
    plan.log("admit", f"{page_path}#{entry_id}", leaf, subject=entry_id, why=why)
    plan.map(f"{page_path}#{entry_id}", leaf)
    plan.hints[leaf] = entry.name
    expected = [line for line in material if EntryLine.match(line.strip()) is None
                and not line.strip().startswith(("- standing:", "- kind:"))]
    plan.expect(f"entry {page_path}#{entry_id}", [*expected, *items], [leaf])
    plan.touch(tree)
    plan.operations.append(f"collections admit {entry_id}")
    plan.settle()
    return leaf


# ═══════════════════════════════ recut and list ═══════════════════════════════


def recut(plan: Plan, path: str) -> list[str]:
    target = layout.normalize(path)
    if layout.page_role(target) is not layout.PageRole.COLLECTION_PAGE:
        refuse("not_a_collection_page", "The page is not a collection page.", path=target)
    made = cut(plan, target, why=plan.reason("the collection page passed the cap"))
    plan.operations.append(f"collections recut {target}")
    plan.settle()
    return made


def pages(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Every collection page with its entries and their distinct days."""
    from . import levels

    plan = Plan.open(root)
    counter = levels.DayCounter(plan)
    found = []
    for path in plan.collection_pages():
        text = plan.read(path)
        found.append({
            "bytes": page_bytes(text),
            "collection": field_of(text, "entity"),
            "entries": [
                {"days": sorted(counter.entry_days(text, entry.name)), "id": entry.id, "name": entry.name,
                 "bytes": material_bytes(entry)}
                for entry in entries_of(text)
            ],
            "path": path,
        })
    return found


def execute(
    root: str | os.PathLike[str],
    action: str,
    target: str,
    *,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
    today: str | None = None,
    why: str | None = None,
) -> dict[str, Any]:
    steps = {"demote": demote, "admit": admit, "recut": recut}
    if action not in steps:
        refuse("operation_unknown", "The collections tool has no such action.", action=action)
    return run(root, lambda plan: steps[action](plan, target), dry_run=dry_run, lock=lock, today=today, why=why)


def _register() -> None:
    from . import move

    move.register("demote", lambda plan, subject: demote(plan, subject))
    move.register("admit", lambda plan, entry: admit(plan, entry))
    move.register("recut", lambda plan, page: recut(plan, page))


_register()



# ═══════════════════════════════ the category limit ═══════════════════════════════


def category_sizes(plan: Plan) -> dict[str, int]:
    """Each category branch's board with its count of leaves, its collection pages counting as one leaf.

    A category is a branch whose board names no subject (no ``entity:``, and no
    subject's registry line has it as home).
    """
    homes = {ref.home for ref in plan.subjects()}
    sizes: dict[str, int] = {}
    for path in plan.pages():
        info = layout.classify(path)
        if info.role is not layout.PageRole.BOARD or not info.tree or not info.branch:
            continue
        if path in homes or field_of(plan.read(path), "entity"):
            continue
        pages = plan.folder_pages(layout.leaf_folder(info.tree, info.branch))
        collected = [page for page in pages if layout.page_role(page) is layout.PageRole.COLLECTION_PAGE]
        sizes[path] = len(pages) - len(collected) + (1 if collected else 0)
    return sizes


def too_large(plan: Plan) -> list[registry.Problem]:
    """``category_too_large``: a category branch over ``limits.category_max_leaves``."""
    limit = plan.config.limits.category_max_leaves
    return [
        registry.Problem("category_too_large", path, None,
                         f"{count} leaves, over {limit}: split the category by kind of subject, never by date")
        for path, count in sorted(category_sizes(plan).items())
        if count > limit
    ]


def _lint_too_large(view: Any) -> list[Any]:
    from . import lint

    return lint.hits_from(too_large(Plan.open(view.root, config=view.config)))


def _register_lint() -> None:
    from . import lint

    lint.register(lint.LintCode("category_too_large", lint.DUE, "a category over `limits.category_max_leaves` splits "
                                "by kind of subject", _lint_too_large))


_register_lint()


# ═══════════════════════════════ entry sources ═══════════════════════════════
# A current-state line that states one entry of a collection page cites `<page>#<id>` and is dated by that
# entry's own lines; a bare collection page stands for the page as a whole, whose date follows every entry.
# `cite_entries` is the one-time upkeep that gives each line citing a bare collection page the entries its
# statement names.

CITE_REASON: Final[str] = "entry-sources"
_LETTER_RE: Final = re.compile(r"[^\W\d_]")


def entry_names(entry: registry.CollectionEntry) -> list[str]:
    """An entry's names in the registry's compared form: its name, the name in another script its heading
    gives, its aliases and its id's words; a starred alias (a person's number), a name with no letter and a
    name shorter than the registry's shortest contained name are left out."""
    found = []
    for name in (entry.name, entry.other_name or "", *entry.aliases, entry.id.replace("-", " ")):
        key = registry.normalize_name(name)
        if len(key) >= registry.MIN_CONTAINED_NAME and "*" not in key and _LETTER_RE.search(key):
            found.append(key)
    return list(dict.fromkeys(found))


def named_entries(statement: str, entries: Iterable[registry.CollectionEntry]) -> list[str]:
    """The ids of the entries a statement names by any of their names, in page order: a name counts where it
    stands as a whole word of the statement (no letter, digit or underscore right before or after it)."""
    text = registry.normalize_name(statement)
    found = []
    for entry in entries:
        if any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) for name in entry_names(entry)):
            found.append(entry.id)
    return found


def cite_line(line: str, view: Any) -> tuple[str, dict[str, Any]]:
    """One current-state line with each bare collection-page source its statement names entries of replaced by
    those entries' addresses, and its `as of` brought back to the latest evidence of what it now cites when it
    is later. Only the tag changes. ``outcome`` is ``one``, ``several``, ``none`` or None (no bare
    collection-page source); ``stale_left`` says the line stays dated before what it cites."""
    info: dict[str, Any] = {"outcome": None, "rewritten": 0, "date_corrected": False, "stale_left": False}
    state = CurrentStateLine.match(line.strip())
    if state is None:
        return line, info
    cited: list[str] = []
    outcomes: list[str] = []
    for source in state.sources:
        page = source.strip()
        if "#" in page or layout.page_role(page) is not layout.PageRole.COLLECTION_PAGE or not view.exists(page):
            cited.append(source)
            continue
        entries, _problems = registry.parse_collection_page(view.text(page))
        ids = named_entries(state.statement, entries)
        outcomes.append("several" if len(ids) > 1 else "one" if ids else "none")
        if not ids:
            cited.append(source)
            continue
        cited.extend(f"{page}#{entry_id}" for entry_id in ids)
        info["rewritten"] += 1
    if outcomes:
        info["outcome"] = "several" if "several" in outcomes else "one" if "one" in outcomes else "none"
    if not info["rewritten"]:
        return line, info
    cited = list(dict.fromkeys(cited))
    as_of = state.date
    dated = [view.source_state(source) for source in cited]
    if all(status == "page" and day is not None for status, day in dated):
        latest = max(day for _status, day in dated).isoformat()
        if as_of > latest:
            as_of, info["date_corrected"] = latest, True
        elif as_of < latest:
            info["stale_left"] = True
    new = replace(state, date=as_of, source=cited[0], more=tuple(cited[1:]))
    start = line.rindex(state.tag())
    return line[:start] + new.tag() + line[start + len(state.tag()):], info


def cite_entries(
    root: str | os.PathLike[str],
    *,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
) -> dict[str, Any]:
    """Run ``cite_line`` over every current-state line of the tree pages and boards.

    Without ``dry_run`` each changed page is archived first as it is (reason ``entry-sources``), the copy is
    read back and must hold the page's bytes, and the page is then written whole. No record line is written:
    a corrected tag changes no statement. A second run changes nothing.
    """
    from . import archive as forest_archive
    from . import lint
    from .locking import atomic_write, writer_lock
    from .view import ForestView

    view = ForestView.open(root)
    report: dict[str, Any] = {"lines": 0, "lines_with_collection_source": 0, "outcomes": {"one": 0, "several": 0,
                              "none": 0}, "references_rewritten": 0, "dates_corrected": 0, "lines_left_stale": 0,
                              "pages": [], "written": False, "archived": []}
    edits: dict[str, dict[int, str]] = {}
    for path, number, line in lint.current_state_lines(view):
        report["lines"] += 1
        new, info = cite_line(line, view)
        if info["outcome"] is None:
            continue
        report["lines_with_collection_source"] += 1
        report["outcomes"][info["outcome"]] += 1
        report["references_rewritten"] += info["rewritten"]
        report["dates_corrected"] += info["date_corrected"]
        report["lines_left_stale"] += info["stale_left"]
        if new != line:
            edits.setdefault(path, {})[number] = new
    report["pages"] = sorted(edits)
    if dry_run or not edits:
        return report
    with writer_lock(view.root, token=lock, label="collections cite-entries") as held:
        for path in sorted(edits):
            target = layout.resolve(view.root, path, must_exist=True)
            data = system.read_bytes(target)
            if data.decode("utf-8", "surrogateescape") != view.text(path):
                refuse("page_changed_meanwhile", "A page changed while the pass read it; nothing of it written.",
                       path=path)
            lines = data.decode("utf-8", "surrogateescape").splitlines(keepends=True)
            for number, new in edits[path].items():
                bare, ending = split_line_ending(lines[number - 1])
                if CurrentStateLine.match(bare.strip()) is None:
                    refuse("cite_line_moved", "A current-state line is not where the pass read it.", path=path)
                lines[number - 1] = new + ending
            entry = forest_archive.put(view.root, path, reason=CITE_REASON, keep=True, lock=held)
            if forest_archive.read_copy(view.root, entry) != data or system.read_bytes(target) != data:
                refuse("archive_copy_mismatch", "The archive copy does not hold the page; not written.", path=path)
            atomic_write(target, "".join(lines).encode("utf-8", "surrogateescape"))
            report["archived"].append(entry.archive_path)
    report["written"] = True
    return report
