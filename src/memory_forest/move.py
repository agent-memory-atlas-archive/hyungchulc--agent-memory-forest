"""The operations that move things (contract, sections 3, 6 and 9; design, sections 6 and 7).

Each operation plans into a ``Plan`` (see ``plan``): it checks what it is
given, computes every page it writes, archives or removes, logs one move
line per path it moves, and writes record lines only on pages whose own lines
came from or went to another page (the third kind of record line the contract
allows), naming those pages by path. When it returns, the plan settles: every
reference to the old places follows (or is marked `` (archived)``) and the
derived lists of the touched trees are brought up to the pages. ``execute``
runs one operation and ``run_plan`` many; both check the whole plan in memory
and write it under the forest writer lock, or refuse with a named error and
write nothing.

The operations: ``leaf``, ``rename`` (``rename_subject``, ``rename_branch``),
``promote``, ``merge``, ``archive``, ``move_branch``, ``new_tree``,
``new_agent``, ``retire_tree``, ``fold_tree``, ``set_type``, ``set_fields``
and ``recut_stream``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Final

from . import layout, registry
from .errors import MemoryForestError
from .locking import LockToken
from .model import (
    DOT,
    RECORD_SECTION,
    ListLine,
    Page,
    RegistryLine,
    TimelineLine,
    UpLink,
    parse_page,
    split_line_ending,
)
from .plan import (
    LINKS_SECTION,
    Plan,
    SubjectRef,
    add_items,
    body_lines,
    day_runs,
    edit_items,
    encode,
    field_of,
    first_prose,
    refuse,
    run,
    title_of,
    up_label,
    with_field,
    without_field,
)

TIMELINE: Final[str] = "Timeline"
DAILY_TOTALS: Final[str] = "Daily totals"
POINTERS: Final[str] = "Pointers"
# The sections of a leaf that are the page's frame rather than its free material.
FRAME_SECTIONS: Final[tuple[str, ...]] = (DAILY_TOTALS, TIMELINE, LINKS_SECTION, RECORD_SECTION)
BOARD_SECTIONS: Final[tuple[str, ...]] = ("About", "Current state", "Rules", "Leaves", POINTERS, LINKS_SECTION)
AREA_SECTIONS: Final[tuple[str, ...]] = ("Scope", "Patterns and rules", "Current state", "Branches")
AGENT_SECTIONS: Final[tuple[str, ...]] = ("Role", "Standing instructions", "Lessons", "Current state", "Branches")
# What `## Role` holds while an agent's role is not set (contract, section 5): a marker, never a sentence
# that could be read as the agent's role.
ROLE_UNSET: Final[str] = "(not set)"
_STANDING_RE: Final[re.Pattern[str]] = re.compile(r'(?:said — ".+" \(\d{4}-\d{2}-\d{2}\)|own — ".+")')
_REGISTRY_MARKS: Final[tuple[str, ...]] = ("·", ";", "`")


# ═══════════════════════════════ small helpers ═══════════════════════════════


def _slug(value: str, what: str) -> str:
    if not isinstance(value, str) or not layout.is_slug(value):
        refuse("not_a_slug", f"The {what} must be a slug ([a-z0-9][a-z0-9-]*).", value=value)
    return value


def _name_ok(name: str, what: str) -> str:
    value = " ".join(str(name or "").split())
    if not value:
        refuse("name_missing", f"The {what} must not be empty.")
    if any(mark in value for mark in _REGISTRY_MARKS):
        refuse("name_invalid", f"The {what} cannot stand in a registry line (it holds ·, ; or a backtick).", value=value)
    return value


def _leaf_name(plan: Plan, name: str) -> str:
    problem = layout.name_problem(name, config=plan.config)
    if problem is not None:
        refuse("leaf_name_invalid", problem, name=name)
    return name


def _words(slug: str) -> str:
    return slug.replace("-", " ")


def _section_body(text: str, name: str) -> list[str]:
    section = parse_page(text).section(name)
    if section is None:
        return []
    return [split_line_ending(line)[0] for line in section.lines if line.strip()]


def _lines(*parts: Iterable[str]) -> str:
    out: list[str] = []
    for part in parts:
        out.extend(part)
    return "\n".join(out).rstrip("\n") + "\n"


def _section_block(name: str, body: Iterable[str]) -> list[str]:
    items = [line for line in body if line.strip()]
    return [f"## {name}", "", *items, ""] if items else [f"## {name}", ""]


def _parent_link(path: str) -> list[str]:
    parent = layout.parent_page(path)
    assert parent is not None
    return [UpLink(layout.up_link_target(parent), up_label(parent)).render()]


def board_text(path: str, *, entity: str | None, title: str, about: Iterable[str] = (), state: Iterable[str] = (),
               rules: Iterable[str] = (), pointers: Iterable[str] = ()) -> str:
    """A board in the contract's shape (section 5): About, Current state, Rules when any, Leaves, Pointers, Links."""
    front = ["---", f"entity: {entity}", "---", ""] if entity else []
    rules = list(rules)
    return _lines(
        front,
        [f"# {title}", ""],
        _section_block("About", about),
        _section_block("Current state", state),
        _section_block("Rules", rules) if rules else [],
        ["## Leaves", ""],
        _section_block(POINTERS, pointers),
        _section_block(LINKS_SECTION, _parent_link(path)),
    )


def tree_page_text(tree: str, *, title: str, entity: str | None = None, agent: bool = False,
                   bodies: Mapping[str, Iterable[str]] | None = None, pointers: Iterable[str] = ()) -> str:
    """A tree page: an area's (Scope, Patterns and rules, Current state, Branches) or an agent's (section 5)."""
    bodies = bodies or {}
    front = ["---", f"entity: {entity}", "---", ""] if entity else []
    sections = AGENT_SECTIONS if agent else AREA_SECTIONS
    parts: list[list[str]] = [front, [f"# {title}", ""]]
    for name in sections:
        parts.append(_section_block(name, bodies.get(name, ())))
    pointers = list(pointers)
    if pointers:
        parts.append(_section_block(POINTERS, pointers))
    parts.append(_section_block(LINKS_SECTION, _parent_link(layout.tree_page(tree))))
    return _lines(*parts)


def index_text(tree: str, subjects: Iterable[str] = ()) -> str:
    """A tree's index: its registry in ``## Subjects`` and its board inventory in ``## Branches``."""
    return _lines(
        [f"# {tree} index", ""],
        _section_block("Subjects", subjects),
        ["## Branches", ""],
        _section_block(LINKS_SECTION, _parent_link(layout.tree_index(tree))),
    )


def _merged_about(plan: Plan, values: Iterable[str | None], entity: str) -> str | None:
    """Several ``about:`` values as one: the union of the ids, ``none`` when all were read and name none."""
    ids: list[str] = []
    seen_any = False
    for value in values:
        if value is None or not value.strip():
            continue
        seen_any = True
        for item in registry.about_ids(value):
            if item != entity and item not in plan.config.about_excluded_ids and item not in ids:
                ids.append(item)
    if ids:
        return ", ".join(ids[: plan.config.limits.about_max_ids])
    return registry.ABOUT_NONE if seen_any else None


def _timeline_key(line: str) -> tuple[str, str]:
    parsed = TimelineLine.match(line)
    if parsed is None:
        return ("", "")
    return (parsed.date, (parsed.start or "").zfill(5))


def _insert_timeline(page: Page, lines: list[str]) -> None:
    """Timeline lines added to a page's ``## Timeline`` in date order (each after the lines of its own date or before)."""
    if not lines:
        return
    section = page.section(TIMELINE)
    if section is None:
        anchor = next((name for name in (LINKS_SECTION, RECORD_SECTION) if page.section(name) is not None), None)
        section = page.add_section(TIMELINE, before=anchor)
    ending = section.ending()
    for line in sorted(lines, key=_timeline_key):
        key = _timeline_key(line)
        items = section.items()
        position = None
        for index, existing in items:
            if _timeline_key(existing) > key:
                position = index
                break
        if position is None:
            section.append_item(line)
        else:
            section.lines.insert(position, line + ending)


def _check_known_id(plan: Plan, subject_id: str, what: str) -> None:
    if subject_id not in plan.known_ids():
        refuse("subject_unknown", f"The {what} names no registered subject or collection entry.", subject=subject_id)


def _done(plan: Plan, label: str) -> None:
    plan.operations.append(label)
    plan.settle()


def _collection_ref_in(plan: Plan, tree: str, category: str) -> SubjectRef | None:
    folder = layout.leaf_folder(tree, category) + "/"
    return next(
        (ref for ref in plan.subjects() if ref.type == registry.COLLECTION_TYPE and ref.home.startswith(folder)), None
    )


def _follow_collection(plan: Plan, ref: SubjectRef, tree: str, category: str, why: str) -> None:
    """A collection whose category was renamed or moved takes the id its new place gives it."""
    plain, prefixed = registry.collection_ids(tree, category)
    if ref.id in (plain, prefixed):
        return
    taken = {item.id for item in plan.subjects()}
    new_id = plain if plain not in taken else prefixed
    if new_id in taken:
        refuse("collection_id_taken", "The collection's new place gives it an id another subject holds.",
               subject=new_id)
    words = _words(category).capitalize()
    aliases = list(ref.line.aliases)
    if registry.normalize_name(ref.name) != registry.normalize_name(f"{words} seen once"):
        aliases.append(ref.name)
    new_line = replace(ref.line, id=new_id, name=f"{words} seen once", aliases=aliases)
    plan.replace_subject(ref, new_line)
    for path in plan.pages():
        if layout.page_role(path) is layout.PageRole.COLLECTION_PAGE and field_of(plan.read(path), "entity") == ref.id:
            plan.update(path, with_field(plan.read(path), "entity", new_id))
    plan.replace_about(ref.id, new_id)
    plan.log("rename", f"{ref.index}#{ref.id}", f"{ref.index}#{new_id}", subject=new_id, why=why)


# ═══════════════════════════════ leaf ═══════════════════════════════


def leaf(plan: Plan, source: str, target: str, *, entity: str | None = None) -> str:
    """Move one leaf to another branch or name; with ``entity`` it also takes another subject."""
    src = layout.normalize(source)
    if layout.page_role(src) is not layout.PageRole.LEAF:
        refuse("not_a_leaf", "Only a leaf moves this way (a collection page is cut with `forest collections`).",
               path=src)
    dst = layout.normalize(target)
    if target.rstrip().endswith("/") or (not dst.endswith(".md") and len(dst.split("/")) == 3):
        dst = f"{dst.rstrip('/')}/{PurePosixPath(src).name}"
    info = layout.classify(dst)
    if info.role is not layout.PageRole.LEAF or not info.tree or not info.branch:
        refuse("not_a_leaf_path", "A leaf goes to `04 stm/<tree>/<branch>/<leaf>.md`.", path=dst)
    if not plan.exists(layout.board(info.tree, info.branch)):
        refuse("branch_missing", "The target branch has no board; promote the subject or make the branch first.",
               board=layout.board(info.tree, info.branch))
    if PurePosixPath(dst).name != PurePosixPath(src).name:
        _leaf_name(plan, PurePosixPath(dst).name)
    if entity is not None:
        _check_known_id(plan, entity, "--entity")
    before = plan.read(src)
    why = plan.reason("the leaf belongs in another branch")
    plan.move_page(src, dst, operation="move", why=why, subject=entity or field_of(before, "entity") or None)
    if entity is not None and field_of(before, "entity") != entity:
        plan.update(dst, with_field(plan.read(dst), "entity", entity))
        plan.record_change(dst, "`entity:`", field_of(before, "entity"), entity, why)
    plan.expect(f"leaf {src}", body_lines(before), [dst])
    _done(plan, f"leaf {src} -> {dst}")
    return dst


# ═══════════════════════════════ rename ═══════════════════════════════


def _move_folder(plan: Plan, old: str, new: str, *, operation: str, why: str,
                 change: Callable[[str, str], str] | None = None) -> None:
    """Every page of a leaf folder moves to another folder; references to the folder follow."""
    for path in plan.folder_pages(old):
        target = f"{new}/{PurePosixPath(path).name}"
        plan.move_page(path, target, operation=operation, why=why, subject=field_of(plan.read(path), "entity") or None)
        if change is not None:
            plan.update(target, change(target, plan.read(target)))
    plan.map(old, new)
    plan.make_dir(new)
    plan.remove_dir(old)


def rename_branch(plan: Plan, tree: str, old: str, new: str) -> None:
    """A branch's board and folder take a new name; a subject branch's id follows it."""
    for value, what in ((tree, "tree"), (old, "branch"), (new, "new branch name")):
        _slug(value, what)
    if old == new:
        refuse("rename_to_same", "The branch already has that name.", branch=old)
    owner = next((ref for ref in plan.subjects() if ref.level == "branch" and ref.home == layout.board(tree, old)), None)
    if owner is not None:
        rename_subject(plan, owner.id, new_id=new)
        return
    old_board, new_board = layout.board(tree, old), layout.board(tree, new)
    if not plan.exists(old_board):
        refuse("branch_missing", "There is no such board.", board=old_board)
    if plan.exists(new_board) or plan.under(layout.leaf_folder(tree, new)):
        refuse("branch_exists", "A branch of that name already exists.", board=new_board)
    why = plan.reason(f"branch `{old}` is now named `{new}`")
    description = _branch_line(plan, tree, old)
    collection = _collection_ref_in(plan, tree, old)
    plan.move_page(old_board, new_board, operation="rename", why=why)
    _move_folder(plan, layout.leaf_folder(tree, old), layout.leaf_folder(tree, new), operation="rename", why=why)
    if description:
        plan.hints[f"branch:{tree}/{new}"] = description
    if collection is not None:
        plan.settle()
        _follow_collection(plan, plan.subject(collection.id), tree, new, why)
    _done(plan, f"rename branch {tree}/{old} -> {new}")


def _branch_line(plan: Plan, tree: str, branch: str) -> str | None:
    from . import maps

    return maps.listed_description(plan, layout.board(tree, branch))


def _retitle(plan: Plan, subject_id: str, tree: str, was: str, now: str) -> None:
    """Where a board, tree page or branch line still shows the old name word for word, it shows the new one."""
    heading = re.compile(rf"^# {re.escape(was)}[ \t]*$", re.MULTILINE)
    for path in (layout.board(tree, subject_id), layout.tree_page(subject_id)):
        if plan.exists(path) and field_of(plan.read(path), "entity") == subject_id:
            text = plan.read(path)
            if title_of(text) == was:
                plan.update(path, heading.sub(f"# {now}", text, count=1))
    key = f"branch:{tree}/{subject_id}"
    if (plan.hints.get(key) or "").strip() == was:
        plan.hints[key] = now

    def renamed(line: str) -> str:
        item = ListLine.match(line)
        if item is None or item.description is None or item.description.strip() != was:
            return line
        if item.target.strip().removesuffix(".md").rsplit("/", 1)[-1] != subject_id:
            return line
        return replace(item, description=now).render()

    for path in (layout.tree_page(tree), layout.tree_index(tree)):
        if plan.exists(path):
            text = plan.read(path)
            changed = edit_items(text, "Branches", renamed)
            if changed != text:
                plan.update(path, changed)


def rename_subject(plan: Plan, old: str, *, new_id: str | None = None, new_name: str | None = None) -> None:
    """A subject takes a new id (its pages, folder and every ``entity:``/``about:`` follow) or a new name."""
    ref = plan.subject(old)
    if new_id is None and new_name is None:
        refuse("rename_nothing", "Give a new id, a new name, or both.")
    if ref.type == registry.COLLECTION_TYPE:
        refuse("rename_collection", "A collection's id follows its category; rename the category branch instead.",
               subject=old)
    if ref.level == "tree" and new_id not in (None, old):
        refuse("rename_tree_unsupported", "A tree's id names its pages and folders; fold it or keep its id.",
               subject=old)
    line = ref.line
    why = plan.reason(f"subject `{old}` is renamed")
    if new_name is not None:
        name = _name_ok(new_name, "new name")
        holders = plan.name_holders([name], besides=old)
        if holders:
            refuse("name_taken", "Another subject or entry already holds this name.", holders=holders)
        aliases = list(line.aliases)
        if registry.normalize_name(line.name) != registry.normalize_name(name) and line.name not in aliases:
            aliases.append(line.name)
        aliases = [alias for alias in aliases if registry.normalize_name(alias) != registry.normalize_name(name)]
        plan.record_change(ref.index, f"Name of `{old}`", line.name, name, why)
        line = replace(line, name=name, aliases=aliases)
    if new_id is not None and new_id != old:
        _slug(new_id, "new id")
        if new_id in plan.known_ids():
            refuse("id_taken", "Another subject or entry already has this id.", subject=new_id)
        holders = plan.name_holders([new_id], besides=old)
        if holders:
            refuse("name_taken", "Another subject or entry already holds this name.", holders=holders)
        home = line.home
        if ref.level == "branch" and ref.home == layout.board(ref.tree, old):
            new_board = layout.board(ref.tree, new_id)
            if plan.exists(new_board) or plan.under(layout.leaf_folder(ref.tree, new_id)):
                refuse("branch_exists", "A branch of the new id's name already exists.", board=new_board)
            description = _branch_line(plan, ref.tree, old)
            plan.move_page(ref.home, new_board, operation="rename", why=why, subject=new_id)
            _move_folder(plan, layout.leaf_folder(ref.tree, old), layout.leaf_folder(ref.tree, new_id),
                         operation="rename", why=why)
            if description:
                plan.hints[f"branch:{ref.tree}/{new_id}"] = description
            home = new_board
        elif ref.level == "leaf":
            folder = str(PurePosixPath(ref.home).parent)
            for path in plan.folder_pages(folder):
                stem = PurePosixPath(path).stem
                if field_of(plan.read(path), "entity") != old or not (stem == old or stem.startswith(f"{old}-")):
                    continue
                target = f"{folder}/{new_id}{stem[len(old):]}.md"
                plan.move_page(path, target, operation="rename", why=why, subject=new_id)
                if path == ref.home:
                    home = target
        for path in plan.pages():
            role = layout.page_role(path)
            if role in (layout.PageRole.LEAF, layout.PageRole.BOARD, layout.PageRole.TREE_PAGE):
                text = plan.read(path)
                if field_of(text, "entity") == old:
                    plan.update(path, with_field(text, "entity", new_id))
        plan.replace_about(old, new_id)
        plan.record_change(ref.index, "Subject id", old, new_id, why)
        plan.log("rename", f"{ref.index}#{old}", f"{ref.index}#{new_id}", subject=new_id, why=why)
        line = replace(line, id=new_id, home=home)
    plan.replace_subject(plan.subject(old), line)
    if new_name is not None and line.name != ref.line.name:
        _retitle(plan, line.id, ref.tree, ref.line.name, line.name)
    plan.touch(ref.tree)
    _done(plan, f"rename {old}" + (f" -> {new_id}" if new_id else "") + (f" ({new_name})" if new_name else ""))


def rename(plan: Plan, target: str, new: str | None = None, *, name: str | None = None) -> None:
    """``target`` a subject id (``new`` its new id, ``name`` its new name) or ``<tree>/<branch>`` (``new`` its name)."""
    if "/" in target:
        tree, _, branch = target.partition("/")
        if new is None:
            refuse("rename_nothing", "A branch rename needs the new branch name.")
        rename_branch(plan, tree, branch, new)
        return
    rename_subject(plan, target, new_id=new, new_name=name)


# ═══════════════════════════════ promote ═══════════════════════════════


def _subtopic_name(plan: Plan, path: str, subject_id: str) -> str:
    """The name a leaf of a subject takes in the subject's own folder: its sub-topic."""
    stem = PurePosixPath(path).stem
    if stem.startswith(f"{subject_id}-"):
        return f"{stem[len(subject_id) + 1:]}.md"
    if stem == subject_id:
        subtopic = field_of(plan.read(path), "subtopic")
        if subtopic and subtopic != subject_id and layout.is_slug(subtopic):
            return f"{subtopic}.md"
        return f"{plan.config.levels.default_subtopic}.md"
    return f"{stem}.md"


def _gather_into(plan: Plan, subject_id: str, sources: list[str], folder: str, *, operation: str, why: str) -> None:
    """Move a subject's leaves into its own folder, each named by its sub-topic; clashes are refused before any move."""
    taken = {PurePosixPath(path).name for path in plan.folder_pages(folder) if path not in sources}
    names: dict[str, str] = {}
    for path in sources:
        name = _leaf_name(plan, _subtopic_name(plan, path, subject_id))
        if name in taken:
            refuse("leaf_name_clash", "Two leaves would take one name in the subject's folder; merge or rename one.",
                   name=name, folder=folder)
        taken.add(name)
        names[path] = name
    for path in sources:
        target = f"{folder}/{names[path]}"
        if target == path:
            continue
        before = plan.read(path)
        plan.move_page(path, target, operation=operation, why=why, subject=subject_id)
        if field_of(before, "entity") != subject_id:
            plan.update(target, with_field(plan.read(target), "entity", subject_id))
        plan.expect(f"leaf {path}", body_lines(before), [target])


def _promote_to_branch(plan: Plan, ref: SubjectRef, why: str) -> None:
    tree, subject_id = ref.tree, ref.id
    board, folder = layout.board(tree, subject_id), layout.leaf_folder(tree, subject_id)
    sources = sorted(set(plan.leaves_of(subject_id)) | ({ref.home} if plan.exists(ref.home) else set()))
    if not sources:
        refuse("subject_has_no_leaf", "No leaf of this subject was found.", subject=subject_id)
    if plan.exists(board):
        if field_of(plan.read(board), "entity"):
            refuse("branch_exists", "A subject's board of this name already exists.", board=board)
        _take_over(plan, ref, why)
    else:
        if plan.under(folder):
            refuse("folder_not_empty", "The subject's folder already holds pages but has no board.", folder=folder)
        about = [f"{ref.name} ({ref.type})." + (f" Aliases: {'; '.join(ref.line.aliases)}." if ref.line.aliases else "")]
        plan.write(board, board_text(board, entity=subject_id, title=ref.name, about=about))
        plan.make_dir(folder)
    _gather_into(plan, subject_id, [path for path in sources if not path.startswith(folder + "/")], folder,
                 operation="promote", why=why)
    plan.replace_subject(plan.subject(subject_id), replace(plan.subject(subject_id).line, level="branch", home=board))
    plan.touch(tree)


def _take_over(plan: Plan, ref: SubjectRef, why: str) -> None:
    """A subject whose id names a category of its tree takes over that board; the category's other leaves move to
    the tree's ``shared`` branch (contract, section 3, Levels)."""
    tree, subject_id = ref.tree, ref.id
    board, folder = layout.board(tree, subject_id), layout.leaf_folder(tree, subject_id)
    shared = plan.config.levels.shared_branch
    shared_board, shared_folder = layout.board(tree, shared), layout.leaf_folder(tree, shared)
    others = [path for path in plan.folder_pages(folder) if field_of(plan.read(path), "entity") != subject_id]
    collection = _collection_ref_in(plan, tree, subject_id)
    if others:
        if not plan.exists(shared_board):
            plan.write(shared_board, board_text(shared_board, entity=None, title="Shared",
                                                about=[f"What the {tree} tree holds that no branch of its own holds."]))
            plan.make_dir(shared_folder)
        clashes = [path for path in others if plan.exists(f"{shared_folder}/{PurePosixPath(path).name}")]
        if clashes:
            refuse("leaf_name_clash", "Leaves of the category have names the shared branch already holds.",
                   paths=clashes)
        if collection is not None and _collection_ref_in(plan, tree, shared) is not None:
            refuse("takeover_collection_conflict", "Both the category and the shared branch keep a collection.",
                   subject=collection.id)
        for path in others:
            target = f"{shared_folder}/{PurePosixPath(path).name}"
            plan.move_page(path, target, operation="move", why=why, subject=field_of(plan.read(path), "entity") or None)
        plan.settle()
        if collection is not None:
            _follow_collection(plan, plan.subject(collection.id), tree, shared, why)
    plan.update(board, with_field(plan.read(board), "entity", subject_id))
    plan.record_change(board, "`entity:`", "", subject_id, why)


def _promote_to_tree(plan: Plan, ref: SubjectRef, why: str) -> None:
    old_tree, subject_id = ref.tree, ref.id
    agent = ref.type == registry.AGENT_TYPE
    if not agent and ref.type not in plan.config.levels.tree_types:
        refuse("tree_type_not_allowed", "Only a subject whose type is in levels.tree_types becomes a tree.",
               subject=subject_id, type=ref.type)
    board, folder = layout.board(old_tree, subject_id), layout.leaf_folder(old_tree, subject_id)
    if ref.home != board or not plan.exists(board):
        refuse("branch_missing", "The subject's board is not where its registry line says.", subject=subject_id)
    page, index = layout.tree_page(subject_id), layout.tree_index(subject_id)
    if plan.exists(page) or plan.exists(index) or plan.under(layout.tree_folder(subject_id)) \
            or plan.under(f"{layout.STM}/{subject_id}"):
        refuse("tree_exists", "A tree of this id already exists.", tree=subject_id)
    leaves = sorted(set(plan.folder_pages(folder)) | set(plan.leaves_of(subject_id)))
    strangers = [path for path in leaves if field_of(plan.read(path), "entity") != subject_id
                 or layout.page_role(path) is not layout.PageRole.LEAF]
    if strangers:
        refuse("branch_holds_other_subjects", "The branch holds pages of other subjects; move them first.",
               paths=strangers)
    groups: dict[str, list[str]] = {}
    for path in leaves:
        subtopic = field_of(plan.read(path), "subtopic") or layout.split_period(
            PurePosixPath(path).stem, plan.folder_pages(str(PurePosixPath(path).parent)))[0]
        if not layout.is_slug(subtopic) or layout.name_problem(subtopic, config=plan.config, period_allowed=False):
            refuse("subtopic_invalid", "A leaf's sub-topic cannot name a branch.", path=path, subtopic=subtopic)
        groups.setdefault(subtopic, []).append(path)
    board_before = plan.read(board)
    about = _section_body(board_before, "About")
    rules = _section_body(board_before, "Rules")
    state = _section_body(board_before, "Current state")
    pointers = _section_body(board_before, POINTERS)
    if agent:
        bodies = {"Role": about, "Standing instructions": rules, "Current state": state}
    else:
        bodies = {"Scope": about, "Patterns and rules": rules, "Current state": state}
    plan.write(page, tree_page_text(subject_id, title=ref.name, entity=subject_id if agent else None, agent=agent,
                                    bodies=bodies, pointers=pointers))
    new_line = replace(ref.line, level="tree", home=page)
    plan.replace_subject(ref, None)
    plan.write(index, index_text(subject_id, [new_line.render()]))
    plan.expect(f"board {board}", [*about, *rules, *state, *pointers], [page])
    for subtopic, paths in sorted(groups.items()):
        branch_board = layout.board(subject_id, subtopic)
        branch_folder = layout.leaf_folder(subject_id, subtopic)
        plan.write(branch_board, board_text(branch_board, entity=subject_id, title=f"{ref.name}: {_words(subtopic)}",
                                            about=[f"The {_words(subtopic)} of {ref.name}."]))
        plan.make_dir(branch_folder)
        taken: set[str] = set()
        for path in paths:
            stem = PurePosixPath(path).stem
            name = f"{stem[len(subject_id) + 1:]}.md" if stem.startswith(f"{subject_id}-") else f"{stem}.md"
            _leaf_name(plan, name)
            if name in taken:
                refuse("leaf_name_clash", "Two leaves would take one name in a branch of the new tree.", name=name)
            taken.add(name)
            before = plan.read(path)
            plan.move_page(path, f"{branch_folder}/{name}", operation="promote", why=why, subject=subject_id)
            plan.expect(f"leaf {path}", body_lines(before), [f"{branch_folder}/{name}"])
    plan.fold_page(board, page, reason=why, operation="promote", subject=subject_id)
    plan.map(folder, f"{layout.STM}/{subject_id}")
    plan.remove_dir(folder)
    plan.make_dir(f"{layout.STM}/{subject_id}")
    plan.record_provenance(page, f"Lines came from the board `{board}`", why)
    plan.record_provenance(ref.index, f"Registry line of `{subject_id}` went to `{index}`", why)
    old_page = layout.tree_page(old_tree)
    if plan.exists(old_page):
        plan.update(old_page, add_items(plan.read(old_page), POINTERS, [f"- `{page}` — {ref.name}, now a tree of its own"],
                                        before=(LINKS_SECTION, RECORD_SECTION)))
    if agent:
        role = first_prose(board_before, "About")
        if role:
            plan.hints[f"agent:{subject_id}"] = role
    else:
        plan.hints[f"tree:{subject_id}"] = " ".join(about[:1]) or ref.name
    plan.touch(old_tree, subject_id)
    plan.root_lists = True


def promote(plan: Plan, subject_id: str) -> str:
    """One level up: a leaf subject gets its own branch; a branch subject (of a tree type, or an agent) a tree."""
    ref = plan.subject(subject_id)
    if ref.type == registry.COLLECTION_TYPE:
        refuse("promote_collection", "A collection is not promoted; its entries are admitted one by one.",
               subject=subject_id)
    if ref.level == "leaf":
        why = plan.reason(f"`{subject_id}` is due its own branch")
        _promote_to_branch(plan, ref, why)
        level = "branch"
    elif ref.level == "branch":
        why = plan.reason(f"`{subject_id}` is due its own tree")
        _promote_to_tree(plan, ref, why)
        level = "tree"
    else:
        refuse("already_a_tree", "The subject is a tree already.", subject=subject_id)
    _done(plan, f"promote {subject_id} -> {level}")
    return level


# ═══════════════════════════════ merge ═══════════════════════════════


def _anchor(page: Page, name: str) -> str | None:
    """The section a new section ``name`` goes before: the next frame section the page has (contract, section 4)."""
    frames = [frame.casefold() for frame in FRAME_SECTIONS]
    later = FRAME_SECTIONS[frames.index(name.casefold()) + 1:] if name.casefold() in frames else FRAME_SECTIONS
    return next((frame for frame in later if page.section(frame) is not None), None)


def merge_lines(plan: Plan, source: str, target: str, *, why: str) -> None:
    """The lines of one leaf go into another: free sections, Timeline, Daily totals, Links and record lines.

    The emptied page goes to the archive with ``target`` named as its successor.
    """
    src_text, dst_text = plan.read(source), plan.read(target)
    src, dst = parse_page(src_text), parse_page(dst_text)
    entity = dst.field("entity")
    description = [split_line_ending(line)[0] for line in src.head
                   if line.strip() and not split_line_ending(line)[0].lstrip().startswith("# ")]
    anchor = _anchor(dst, "")
    title = src.title or PurePosixPath(source).stem
    if description:
        target_section = dst.section(title)
        if target_section is None:
            dst.add_section(title, description, before=anchor)
        else:
            for line in description:
                target_section.lines.append(line + dst.ending())
    for section in src.sections:
        name = section.heading
        lines = [split_line_ending(line)[0] for line in section.lines if line.strip()]
        if section.is_named(TIMELINE):
            _insert_timeline(dst, [line for _i, line in section.items()])
            continue
        if section.is_named(LINKS_SECTION):
            lines = [line for line in lines if UpLink.match(line) is None]
        existing = dst.section(name)
        if existing is None:
            if lines:
                dst.add_section(name, lines, before=_anchor(dst, name))
        else:
            for line in lines:
                if line.lstrip().startswith(("-", "*", "+")):
                    existing.append_item(line)
                else:
                    existing.lines.append(line + dst.ending())
    text = dst.render()
    fields = parse_page(text)
    if fields.frontmatter is not None:
        about = _merged_about(plan, [dst.field("about") or None, src.field("about") or None], entity)
        if about is not None:
            text = with_field(text, "about", about)
        dates = [value for value in (dst.field("updated_at"), src.field("updated_at")) if value]
        if dates:
            text = with_field(text, "updated_at", max(dates))
        for key in ("standing", "timezone"):
            if not parse_page(text).field(key) and src.field(key):
                text = with_field(text, key, src.field(key))
    plan.update(target, text)
    plan.expect(f"merged page {source}", body_lines(src_text), [target])
    plan.fold_page(source, target, reason=why, operation="merge")
    plan.record_provenance(target, f"Lines merged in from `{source}`", why)


def merge(plan: Plan, keep: str, drop: str) -> None:
    """Two subjects that are one: the lines of ``drop`` go to ``keep``, its names join ``keep``'s aliases."""
    if keep == drop:
        refuse("merge_same", "A subject is not merged into itself.", subject=keep)
    kept, dropped = plan.subject(keep), plan.subject(drop)
    for ref in (kept, dropped):
        if ref.level == "tree":
            refuse("merge_tree_unsupported", "A tree is folded first (`fold-tree`), then merged.", subject=ref.id)
        if ref.type == registry.COLLECTION_TYPE:
            refuse("merge_collection", "A collection is not merged; its entries are admitted or demoted.",
                   subject=ref.id)
    if kept.level == "leaf" and dropped.level == "branch":
        refuse("merge_into_smaller", "Merge the leaf subject into the branch one, or promote it first.",
               keep=keep, drop=drop)
    why = plan.reason(f"`{drop}` and `{keep}` are one subject")
    sources = sorted(set(plan.leaves_of(drop)) | ({dropped.home} if layout.page_role(dropped.home) is
                                                   layout.PageRole.LEAF and plan.exists(dropped.home) else set()))
    if kept.level == "leaf":
        home_folder = str(PurePosixPath(kept.home).parent)
        for path in sources:
            stem = PurePosixPath(path).stem
            if path == dropped.home or stem == drop:
                merge_lines(plan, path, kept.home, why=why)
                continue
            rest = stem[len(drop) + 1:] if stem.startswith(f"{drop}-") else stem
            target = f"{home_folder}/{keep}-{rest}.md"
            if plan.exists(target):
                merge_lines(plan, path, target, why=why)
                continue
            _leaf_name(plan, PurePosixPath(target).name)
            before = plan.read(path)
            plan.move_page(path, target, operation="merge", why=why, subject=keep)
            plan.update(target, with_field(plan.read(target), "entity", keep))
            plan.expect(f"leaf {path}", body_lines(before), [target])
    else:
        folder = layout.leaf_folder(kept.tree, keep)
        for path in sources:
            name = _subtopic_name(plan, path, drop)
            target = f"{folder}/{name}"
            if plan.exists(target):
                merge_lines(plan, path, target, why=why)
                continue
            _leaf_name(plan, name)
            before = plan.read(path)
            plan.move_page(path, target, operation="merge", why=why, subject=keep)
            plan.update(target, with_field(plan.read(target), "entity", keep))
            plan.expect(f"leaf {path}", body_lines(before), [target])
        if dropped.level == "branch":
            old_board = dropped.home
            keep_board = kept.home
            old_text = plan.read(old_board)
            moved: list[str] = []
            for name in ("About", "Current state", "Rules", POINTERS):
                lines = [line for line in _section_body(old_text, name)
                         if line not in _section_body(plan.read(keep_board), name)]
                if lines:
                    later = BOARD_SECTIONS[BOARD_SECTIONS.index(name) + 1:] + (RECORD_SECTION,)
                    plan.update(keep_board, add_items(plan.read(keep_board), name, lines, before=later))
                    moved.extend(lines)
            plan.expect(f"board {old_board}", moved, [keep_board])
            plan.fold_page(old_board, keep_board, reason=why, operation="merge", subject=keep)
            old_folder = layout.leaf_folder(dropped.tree, drop)
            plan.map(old_folder, folder)
            plan.remove_dir(old_folder)
            plan.record_provenance(keep_board, f"Lines merged in from `{old_board}`", why)
    aliases = list(kept.line.aliases)
    held = {registry.normalize_name(name) for name in (kept.id, kept.name, *aliases)}
    for name in (dropped.name, *dropped.line.aliases):
        if registry.normalize_name(name) not in held:
            aliases.append(name)
            held.add(registry.normalize_name(name))
    plan.replace_subject(plan.subject(drop), None)
    plan.replace_subject(plan.subject(keep), replace(plan.subject(keep).line, aliases=aliases))
    plan.replace_about(drop, keep)
    plan.log("merge", f"{dropped.index}#{drop}", f"{kept.index}#{keep}", subject=keep, why=why)
    plan.record_provenance(dropped.index, f"Registry line of `{drop}` merged into the line of `{keep}` in "
                           f"`{kept.index}`", why)
    plan.touch(kept.tree, dropped.tree)
    _done(plan, f"merge {drop} -> {keep}")


# ═══════════════════════════════ archive ═══════════════════════════════


def archive(plan: Plan, path: str, *, reason: str, successor: str | None = None) -> None:
    """A page goes out of the forest into the archive; references to it stay, marked `` (archived)``."""
    target = layout.normalize(path)
    role = layout.page_role(target)
    if role not in (layout.PageRole.LEAF, layout.PageRole.COLLECTION_PAGE, layout.PageRole.BOARD):
        refuse("archive_unsupported", "Leaves, collection pages and emptied boards are archived this way.", path=target)
    if not plan.exists(target):
        refuse("page_missing", "There is no such page in the forest.", path=target)
    why = " ".join(str(reason or "").split())
    if not why:
        refuse("reason_missing", "An archived page needs a reason.")
    homes = [ref.id for ref in plan.subjects() if ref.home == target]
    if homes:
        refuse("page_is_home", "The page is a subject's registry home; move or merge the subject first.",
               subjects=homes)
    if role is layout.PageRole.BOARD:
        info = layout.classify(target)
        if plan.under(layout.leaf_folder(info.tree or "", info.branch or "")):
            refuse("branch_not_empty", "A board is archived only when its folder is empty.", board=target)
    following = layout.normalize(successor) if successor else None
    if following is not None and not plan.exists(following):
        refuse("successor_missing", "The successor page is not in the forest.", path=following)
    plan.archive_page(target, reason=why, successor=following)
    if role is layout.PageRole.BOARD:
        info = layout.classify(target)
        plan.remove_dir(layout.leaf_folder(info.tree or "", info.branch or ""))
    _done(plan, f"archive {target}")


# ═══════════════════════════════ move-branch ═══════════════════════════════


def move_branch(plan: Plan, tree: str, branch: str, new_tree: str, *, name: str | None = None) -> None:
    """A branch with its board, its folder and the registry lines homed there goes to another tree."""
    new_branch = name or branch
    for value, what in ((tree, "tree"), (branch, "branch"), (new_tree, "new tree"), (new_branch, "branch name")):
        _slug(value, what)
    if tree == new_tree:
        if new_branch == branch:
            refuse("move_to_same_place", "The branch is already there.", branch=branch)
        rename_branch(plan, tree, branch, new_branch)
        return
    old_board, new_board = layout.board(tree, branch), layout.board(new_tree, new_branch)
    old_folder, new_folder = layout.leaf_folder(tree, branch), layout.leaf_folder(new_tree, new_branch)
    if not plan.exists(old_board):
        refuse("branch_missing", "There is no such board.", board=old_board)
    if not plan.exists(layout.tree_index(new_tree)) or not plan.exists(layout.tree_page(new_tree)):
        refuse("tree_unknown", "The tree has no page or index; make it with new-tree first.", tree=new_tree)
    if plan.exists(new_board) or plan.under(new_folder):
        refuse("branch_exists", "The other tree already has a branch of that name; merge the subjects instead.",
               board=new_board)
    homed = [ref for ref in plan.subjects() if ref.tree == tree
             and (ref.home == old_board or ref.home.startswith(old_folder + "/"))]
    owner = next((ref for ref in homed if ref.home == old_board), None)
    collection = next((ref for ref in homed if ref.type == registry.COLLECTION_TYPE), None)
    why = plan.reason(f"branch `{branch}` belongs in tree `{new_tree}`")
    description = _branch_line(plan, tree, branch)
    plan.move_page(old_board, new_board, operation="move-branch", why=why, subject=owner.id if owner else None)
    _move_folder(plan, old_folder, new_folder, operation="move-branch", why=why)
    plan.log("move-branch", old_folder, new_folder, why=why)
    if description:
        plan.hints[f"branch:{new_tree}/{new_branch}"] = description
    for ref in homed:
        current = plan.subject(ref.id)
        plan.replace_subject(current, None)
        home = current.home
        if home == old_board:
            home = new_board
        elif home.startswith(old_folder + "/"):
            home = new_folder + home[len(old_folder):]
        plan.add_subject(new_tree, replace(current.line, home=home))
        plan.log("move-branch", f"{ref.index}#{ref.id}", f"{layout.tree_index(new_tree)}#{ref.id}", subject=ref.id,
                 why=why)
    if homed:
        lines = ", ".join(f"`{ref.id}`" for ref in homed)
        plan.record_provenance(layout.tree_index(tree), f"Registry lines of {lines} went to "
                               f"`{layout.tree_index(new_tree)}`", why)
        plan.record_provenance(layout.tree_index(new_tree), f"Registry lines of {lines} came from "
                               f"`{layout.tree_index(tree)}`", why)
    plan.touch(tree, new_tree)
    plan.settle()
    if owner is not None and new_branch != branch:
        rename_subject(plan, owner.id, new_id=new_branch)
    if collection is not None:
        _follow_collection(plan, plan.subject(collection.id), new_tree, new_branch, why)
    _done(plan, f"move-branch {tree}/{branch} -> {new_tree}/{new_branch}")


# ═══════════════════════════════ trees and agents ═══════════════════════════════


def _tree_free(plan: Plan, tree: str) -> None:
    _slug(tree, "tree id")
    for path in (layout.tree_page(tree), layout.tree_index(tree)):
        if plan.exists(path):
            refuse("tree_exists", "A tree of this id already exists.", path=path)
    if plan.under(layout.tree_folder(tree)) or plan.under(f"{layout.STM}/{tree}"):
        refuse("tree_exists", "Pages of a tree of this id already exist.", tree=tree)


def new_tree(plan: Plan, tree: str, *, name: str | None = None, description: str) -> None:
    """An empty area tree: its page, its index, its leaf folder and its line under ``## Trees``."""
    _tree_free(plan, tree)
    text = " ".join(str(description or "").split())
    if not text:
        refuse("description_missing", "A new tree needs a description of what lives there.")
    if tree in plan.known_ids():
        refuse("id_taken", "A subject or entry already has this id.", subject=tree)
    title = " ".join(str(name).split()) if name else _words(tree).capitalize()
    plan.write(layout.tree_page(tree), tree_page_text(tree, title=title, bodies={"Scope": [text]}))
    plan.write(layout.tree_index(tree), index_text(tree))
    plan.make_dir(f"{layout.STM}/{tree}")
    plan.hints[f"tree:{tree}"] = text
    plan.root_lists = True
    _done(plan, f"new-tree {tree}")


def new_agent(plan: Plan, agent_id: str, *, name: str | None = None, role: str | None = None,
              aliases: Iterable[str] = ()) -> None:
    """An agent's tree (design, section 6): its registry line, tree page, index and line under ``## Agents``.

    A name not given is the id; a role not given leaves ``## Role`` with ``ROLE_UNSET``.
    """
    if not ensure_agent(plan, agent_id, name=name, role=role, aliases=aliases, refuse_existing=True):
        refuse("tree_exists", "The agent has its tree already.", subject=agent_id)


def ensure_agent(plan: Plan, agent_id: str, *, name: str | None = None, role: str | None = None,
                 aliases: Iterable[str] = (), refuse_existing: bool = False) -> bool:
    """The one way an agent's tree is made; True when it made the tree.

    An agent that is registered already is left as it is: what its settings
    entry gives later fills only what the tree left unset (``fill_agent``), a
    value set in both places is changed by ``rename`` or by hand, and a subject
    retyped to an agent below level tree is promoted by ``levels apply``.
    """
    _slug(agent_id, "agent id")
    existing = plan.find_subject(agent_id)
    if existing is not None and not refuse_existing:
        return False
    _tree_free(plan, agent_id)
    title = _name_ok(name if name and name.strip() else agent_id, "agent's name")
    purpose = " ".join(str(role or "").split()) or ROLE_UNSET
    others = [_name_ok(alias, "alias") for alias in aliases]
    holders = plan.name_holders([agent_id, title, *others])
    if holders or agent_id in plan.known_ids():
        refuse("name_taken", "Another subject or entry already holds one of these names.", holders=holders)
    page = layout.tree_page(agent_id)
    line = RegistryLine(agent_id, title, registry.AGENT_TYPE, "tree", page, others)
    plan.write(page, tree_page_text(agent_id, title=title, entity=agent_id, agent=True, bodies={"Role": [purpose]}))
    plan.write(layout.tree_index(agent_id), index_text(agent_id, [line.render()]))
    plan.make_dir(f"{layout.STM}/{agent_id}")
    plan.hints[f"agent:{agent_id}"] = purpose
    plan.root_lists = True
    _done(plan, f"new-agent {agent_id}")
    return True


def fill_agent(plan: Plan, agent_id: str, fills: Mapping[str, str]) -> list[str]:
    """Fill what an agent's tree left unset from its settings entry: a name that is still the id, a ``## Role``
    that is still ``ROLE_UNSET``. Each value filled gets its change line on the tree page; a set value is never
    changed here (``fills`` comes from ``agents.to_fill``)."""
    ref = plan.subject(agent_id)
    page = layout.tree_page(agent_id)
    if ref.type != registry.AGENT_TYPE or ref.level != "tree" or not plan.exists(page):
        refuse("not_an_agent_tree", "Only an agent's tree is filled from its settings entry.", subject=agent_id)
    why = plan.reason(f"the settings entry of `{agent_id}` gives what its tree left unset")
    filled: list[str] = []
    name = fills.get("name")
    if name:
        title = _name_ok(name, "agent's name")
        holders = plan.name_holders([title], besides=agent_id)
        if holders:
            refuse("name_taken", "Another subject or entry already holds this name.", holders=holders)
        was = ref.line.name
        plan.replace_subject(ref, replace(ref.line, name=title))
        _retitle(plan, agent_id, ref.tree, was, title)
        plan.record_change(page, f"Name of `{agent_id}`", was, title, why)
        filled.append("name")
    role = fills.get("role")
    if role:
        text = plan.read(page)
        section = re.compile(r"(^## Role[ \t]*\r?\n)(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
        found = section.search(text)
        was = " ".join(found.group(2).split()) if found else ""
        if found is None or was not in ("", ROLE_UNSET):
            refuse("role_set", "The tree page's role is set already; a set value is changed by hand.",
                   subject=agent_id)
        plan.update(page, text[:found.start(2)] + f"\n{role}\n\n" + text[found.end(2):])
        plan.record_change(page, f"Role of `{agent_id}`", was, role, why)
        plan.hints[f"agent:{agent_id}"] = role
        filled.append("role")
    if filled:
        _fill_root_line(plan, agent_id, ref.line.name, fills)
        plan.root_lists = True
        plan.touch(ref.tree)
        _done(plan, f"fill {agent_id} ({', '.join(filled)})")
    return filled


def _fill_root_line(plan: Plan, agent_id: str, was_name: str, fills: Mapping[str, str]) -> None:
    """The agent's line under the root index's ``## Agents`` shows what was filled where it still shows the
    unset value word for word (a derived list line: no record line, as for every list line a tool rewrites)."""

    def filled(line: str) -> str:
        item = ListLine.match(line)
        if item is None or item.description is None or item.target.strip() != agent_id:
            return line
        parts = item.description.split(DOT)
        if fills.get("name") and parts[0].strip() == was_name:
            parts[0] = fills["name"]
        if fills.get("role") and len(parts) > 1 and parts[1].strip() in ("", ROLE_UNSET):
            parts[1] = fills["role"]
        return replace(item, description=DOT.join(parts)).render()

    if plan.exists(layout.ROOT_INDEX):
        text = plan.read(layout.ROOT_INDEX)
        changed = edit_items(text, "Agents", filled)
        if changed != text:
            plan.update(layout.ROOT_INDEX, changed)


def retire_tree(plan: Plan, tree: str, *, into: str | None = None) -> None:
    """An agent's tree is marked ended and kept; an emptied area tree goes to the archive, folded into ``into``."""
    _slug(tree, "tree")
    page, index = layout.tree_page(tree), layout.tree_index(tree)
    if not plan.exists(page):
        refuse("tree_unknown", "There is no such tree.", tree=tree)
    ref = plan.find_subject(tree)
    why = plan.reason(f"tree `{tree}` is retired")
    if ref is not None and ref.level == "tree" and ref.type == registry.AGENT_TYPE and into is None:
        if ref.line.status == "ended":
            refuse("already_ended", "The agent is retired already.", subject=tree)
        plan.replace_subject(ref, replace(ref.line, status="ended", status_written=True))
        plan.record_change(index, f"Status of `{tree}`", "active", "ended", why)
        plan.root_lists = True
        _done(plan, f"retire-tree {tree} (ended)")
        return
    if into is None:
        refuse("into_missing", "An area tree is retired into the page that took its rules (--into).", tree=tree)
    successor = layout.normalize(into)
    if successor in (page, index) or not plan.exists(successor):
        refuse("successor_missing", "The page that took the tree's rules must exist and be another page.",
               path=successor)
    boards = [path for path in plan.folder_pages(layout.tree_folder(tree)) if path != index]
    leaves = plan.under(f"{layout.STM}/{tree}")
    subjects = [item.id for item in plan.subjects() if item.tree == tree and item.id != tree]
    if boards or leaves or subjects:
        refuse("tree_not_empty", "The tree still holds boards, leaves or subjects; move or merge them first.",
               boards=boards, leaves=leaves, subjects=subjects)
    if ref is not None:
        plan.replace_about(tree, None)
    plan.archive_page(page, reason=why, successor=successor, subject=tree)
    if plan.exists(index):
        plan.archive_page(index, reason=why, successor=successor, subject=tree)
    for folder in (layout.tree_folder(tree), f"{layout.STM}/{tree}"):
        plan.mark_archived(folder)
        plan.remove_dir(folder)
    plan.record_provenance(successor, f"Took the rules of the retired tree `{page}`, now archived", why)
    plan.root_lists = True
    _done(plan, f"retire-tree {tree} -> {successor}")


def fold_tree(plan: Plan, tree: str, *, into: str, name: str | None = None, type_: str | None = None) -> None:
    """A tree becomes a branch of another tree; its old branches become its leaves' ``subtopic:``."""
    branch = name or tree
    for value, what in ((tree, "tree"), (into, "tree to fold into"), (branch, "branch name")):
        _slug(value, what)
    if into == tree:
        refuse("fold_into_itself", "A tree does not fold into itself.", tree=tree)
    old_page, old_index = layout.tree_page(tree), layout.tree_index(tree)
    for path in (old_page, old_index, layout.tree_page(into), layout.tree_index(into)):
        if not plan.exists(path):
            refuse("tree_unknown", "A tree of the fold has no page or index.", path=path)
    ref = plan.find_subject(tree)
    if ref is None or ref.level != "tree":
        refuse("fold_tree_unregistered", "Only a tree with its own registry line (level tree) folds into a branch.",
               tree=tree)
    board, folder = layout.board(into, branch), layout.leaf_folder(into, branch)
    if plan.exists(board) or plan.under(folder):
        refuse("branch_exists", "The other tree already has a branch of that name.", board=board)
    others = [item.id for item in plan.subjects() if item.tree == tree and item.id != tree]
    if others:
        refuse("fold_tree_holds_subjects", "The tree holds other subjects; move their branches first.",
               subjects=others)
    new_type = type_ or ref.type
    if new_type == registry.AGENT_TYPE:
        new_type = type_ or plan.config.agents.folded_type
        if new_type == registry.AGENT_TYPE or new_type not in plan.config.subject_types:
            refuse("fold_tree_type_missing", "An agent folded into a branch is no tree any more and takes another "
                   "type (--type).", subject=tree)
    if new_type not in plan.config.subject_types or new_type == registry.COLLECTION_TYPE:
        refuse("type_unknown", "The type is not one of the forest's subject types.", type=new_type)
    new_id = branch
    if new_id != tree and new_id in plan.known_ids():
        refuse("id_taken", "Another subject already has this id.", subject=new_id)
    boards = [path for path in plan.folder_pages(layout.tree_folder(tree)) if path != old_index]
    branches = {PurePosixPath(path).stem for path in boards}
    pages = plan.under(f"{layout.STM}/{tree}")
    loose = [path for path in pages if layout.page_role(path) is not layout.PageRole.LEAF
             or layout.classify(path).branch not in branches]
    if loose:
        refuse("fold_tree_loose_pages", "The tree holds pages that are no leaf of one of its branches.", paths=loose)
    counts: dict[str, int] = {}
    for path in pages:
        counts[PurePosixPath(path).name] = counts.get(PurePosixPath(path).name, 0) + 1
    names: dict[str, str] = {}
    for path in pages:
        leaf_name, old_branch = PurePosixPath(path).name, layout.classify(path).branch or ""
        names[path] = f"{old_branch}-{leaf_name}" if counts[leaf_name] > 1 else leaf_name
        _leaf_name(plan, names[path])
    if len(set(names.values())) != len(names):
        refuse("leaf_name_clash", "Leaf names of the folded tree would clash in its new folder.")
    why = plan.reason(f"tree `{tree}` is one branch of `{into}`")
    agent = ref.type == registry.AGENT_TYPE
    tree_text = plan.read(old_page)
    about = _section_body(tree_text, "Role" if agent else "Scope")
    rules = [*_section_body(tree_text, "Standing instructions"), *_section_body(tree_text, "Lessons")] if agent \
        else _section_body(tree_text, "Patterns and rules")
    state = _section_body(tree_text, "Current state")
    pointers = _section_body(tree_text, POINTERS)
    for path in boards:
        text = plan.read(path)
        rules += [line for line in _section_body(text, "Rules") if line not in rules]
        state += [line for line in _section_body(text, "Current state") if line not in state]
        pointers += [line for line in _section_body(text, POINTERS) if line not in pointers]
    plan.write(board, board_text(board, entity=new_id, title=ref.name, about=about, state=state, rules=rules,
                                 pointers=pointers))
    plan.make_dir(folder)
    plan.expect(f"tree {tree}", [*about, *rules, *state, *pointers], [board])
    for path in pages:
        old_branch = layout.classify(path).branch or ""
        before = plan.read(path)
        text = with_field(before, "subtopic", old_branch)
        if new_id != tree and field_of(text, "entity") == tree:
            text = with_field(text, "entity", new_id)
        plan.update(path, text)
        plan.move_page(path, f"{folder}/{names[path]}", operation="fold-tree", why=why, subject=new_id)
        plan.expect(f"leaf {path}", body_lines(before), [f"{folder}/{names[path]}"])
    plan.add_subject(into, replace(ref.line, id=new_id, type=new_type, level="branch", home=board))
    for path in (*boards, old_page):
        plan.fold_page(path, board, reason=why, operation="fold-tree", subject=new_id)
    plan.fold_page(old_index, layout.tree_index(into), reason=why, operation="fold-tree", subject=new_id)
    for old_branch in sorted(branches):
        plan.map(layout.leaf_folder(tree, old_branch), folder)
        plan.log("fold-tree", layout.leaf_folder(tree, old_branch), folder, subject=new_id, why=why)
        plan.remove_dir(layout.leaf_folder(tree, old_branch))
    plan.map(f"{layout.STM}/{tree}", folder)
    plan.map(layout.tree_folder(tree), layout.tree_folder(into))
    plan.remove_dir(f"{layout.STM}/{tree}")
    plan.remove_dir(layout.tree_folder(tree))
    if new_id != tree:
        for path in plan.pages():
            if layout.page_role(path) in (layout.PageRole.LEAF, layout.PageRole.BOARD):
                text = plan.read(path)
                if field_of(text, "entity") == tree:
                    plan.update(path, with_field(text, "entity", new_id))
        plan.replace_about(tree, new_id)
    plan.log("fold-tree", f"{old_index}#{tree}", f"{layout.tree_index(into)}#{new_id}", subject=new_id, why=why)
    plan.record_provenance(board, f"Lines came from the folded tree's page `{old_page}`", why)
    plan.record_provenance(layout.tree_index(into), f"Registry line of `{new_id}` came from `{old_index}`", why)
    description = first_prose(tree_text, "Role" if agent else "Scope")
    if description:
        plan.hints[f"branch:{into}/{branch}"] = description
    plan.touch(into)
    plan.root_lists = True
    if ref.type == registry.AGENT_TYPE:
        plan.note(f"`{tree}` was an agent and is now a branch of type `{new_type}`")
    _done(plan, f"fold-tree {tree} -> {into}/{branch}")


# ═══════════════════════════════ set-type and set ═══════════════════════════════


def set_type(plan: Plan, subject_id: str, new_type: str) -> None:
    """Retype one registry line (``set-type <id> agent`` makes a subject an agent; ``levels apply`` promotes it)."""
    ref = plan.subject(subject_id)
    if new_type not in plan.config.subject_types:
        refuse("type_unknown", "The type is not one of the forest's subject types.", type=new_type)
    if ref.type == new_type:
        refuse("type_unchanged", "The subject already has this type.", subject=subject_id)
    if registry.COLLECTION_TYPE in (ref.type, new_type):
        refuse("type_collection", "A collection is a subject of its own kind; use `forest collections`.",
               subject=subject_id)
    why = plan.reason(f"`{subject_id}` is a {new_type}")
    plan.replace_subject(ref, replace(ref.line, type=new_type))
    plan.record_change(ref.index, f"Type of `{subject_id}`", ref.type, new_type, why)
    if new_type == registry.AGENT_TYPE and ref.level != "tree":
        plan.note(f"`{subject_id}` is an agent below level tree: `forest levels apply --subjects {subject_id}` "
                  "promotes it to its tree")
    _done(plan, f"set-type {subject_id} {new_type}")


def set_fields(plan: Plan, path: str, *, kind: str | None = None, about: str | None = None,
               standing: str | None = None) -> None:
    """Set a leaf's ``kind``, ``about`` or ``standing`` (an empty ``standing`` removes it; ``about`` is never
    removed: an empty value writes ``none``)."""
    target = layout.normalize(path)
    if layout.page_role(target) is not layout.PageRole.LEAF:
        refuse("not_a_leaf", "Only a leaf's frontmatter is set this way.", path=target)
    text = plan.read(target)
    before = text
    entity = field_of(text, "entity")
    if kind is not None:
        if kind not in registry.LEAF_KINDS or kind == registry.COLLECTION_KIND:
            refuse("kind_invalid", "The kind is not one of the contract's leaf kinds.", kind=kind)
        text = with_field(text, "kind", kind)
    if about is not None:
        words = [part.strip() for part in about.split(",") if part.strip()]
        if not words or words == [registry.ABOUT_NONE]:
            text = with_field(text, "about", registry.ABOUT_NONE)
        else:
            if registry.ABOUT_NONE in words:
                refuse("about_invalid", "`none` stands alone in `about:`.")
            if len(words) > plan.config.limits.about_max_ids:
                refuse("about_invalid", "`about:` names too many subjects.", limit=plan.config.limits.about_max_ids)
            known = plan.known_ids()
            for word in words:
                if word == entity or word in plan.config.about_excluded_ids:
                    refuse("about_invalid", "`about:` never names the leaf's own subject, the owner or the system.",
                           id=word)
                if word not in known:
                    refuse("about_invalid", "`about:` names a subject the registry does not hold.", id=word)
            text = with_field(text, "about", ", ".join(dict.fromkeys(words)))
    if standing is not None:
        value = " ".join(standing.split())
        if not value:
            text = without_field(text, "standing")
        elif _STANDING_RE.fullmatch(value) is None:
            refuse("standing_invalid", 'A standing line is `said — "<words>" (YYYY-MM-DD)` or `own — "<words>"`.',
                   value=value)
        else:
            text = with_field(text, "standing", value)
    why = plan.reason("set by hand")
    for key in ("kind", "about", "standing"):
        was, now = field_of(before, key), field_of(text, key)
        if was != now:
            plan.record_change(target, f"`{key}:`", was, now, why)
    plan.update(target, text)
    _done(plan, f"set {target}")


# ═══════════════════════════════ recut-stream ═══════════════════════════════


def _period(day: str, grain: str) -> str:
    year, month = day[:4], int(day[5:7])
    if grain == "year":
        return year
    if grain == "quarter":
        return f"{year}-q{(month - 1) // 3 + 1}"
    return f"{year}-{month:02d}"


def _stream_items(path: str, text: str) -> tuple[list[list[str]], list[str], list[str]]:
    """A pure event list's Timeline items (a dated line with the lines under it), other Links lines and record lines."""
    page = parse_page(text)
    for section in page.sections:
        if not any(section.is_named(name) for name in (TIMELINE, LINKS_SECTION, RECORD_SECTION)):
            refuse("stream_leaf_not_a_list", "A stream page holds only its title, a description, Timeline, Links "
                   "and Update record; anything else is cut by sub-topic.", path=path, section=section.heading)
    items: list[list[str]] = []
    timeline = page.section(TIMELINE)
    for raw in timeline.lines if timeline is not None else []:
        bare = split_line_ending(raw)[0]
        if not bare.strip():
            continue
        parsed = TimelineLine.match(bare) if not bare[:1].isspace() else None
        if parsed is not None and parsed.day:
            items.append([bare])
        elif items and bare[:1].isspace():
            items[-1].append(bare)
        else:
            refuse("stream_leaf_not_a_list", "A stream page's Timeline holds only dated lines.", path=path, line=bare)
    links = page.section(LINKS_SECTION)
    other_links = [line for _i, line in links.items() if UpLink.match(line) is None] if links else []
    record = page.section(RECORD_SECTION)
    return items, other_links, (record.item_lines() if record else [])


def recut_stream(plan: Plan, subject_id: str, leaves: list[str]) -> list[str]:
    """A stream of event pages of one subject cut again by period (contract, section 4, Names, size and shape):
    per year the coarsest period whose page stays within the cap (the year, else its quarters, else a passing
    quarter's months), each Timeline line moving whole and in time order."""
    if not leaves:
        refuse("stream_empty", "Name the pages of one stream.")
    ref = plan.subject(subject_id)
    paths = list(dict.fromkeys(layout.normalize(path) for path in leaves))
    folders = {str(PurePosixPath(path).parent) for path in paths}
    if len(folders) != 1:
        refuse("stream_in_several_folders", "A stream's pages share one folder.", folders=sorted(folders))
    folder = folders.pop()
    info = layout.classify(paths[0])
    if info.tree is None or info.branch is None:
        refuse("not_a_leaf_path", "A stream's pages are leaves.", path=paths[0])
    sources: dict[str, str] = {}
    stems, kinds = set(), set()
    for path in paths:
        if layout.page_role(path) is not layout.PageRole.LEAF:
            refuse("not_a_leaf", "A stream's pages are leaves.", path=path)
        text = plan.read(path)
        if field_of(text, "entity") != subject_id:
            refuse("stream_other_subject", "The page is not a leaf of this subject.", path=path)
        stems.add(layout.split_period(PurePosixPath(path).stem, [*paths, *plan.folder_pages(folder)])[0])
        kinds.add(field_of(text, "kind"))
        sources[path] = text
    if len(stems) != 1:
        refuse("stream_names_differ", "Once the period is taken off, the pages of one stream share one name.",
               stems=sorted(stems))
    if len(kinds) != 1:
        refuse("stream_kinds_differ", "The pages of one stream share one kind.", kinds=sorted(kinds))
    stem = stems.pop()
    _leaf_name(plan, f"{stem}.md")
    parsed = {path: _stream_items(path, text) for path, text in sources.items()}
    for path, (items, _links, _records) in parsed.items():
        if not items:
            refuse("stream_leaf_empty", "The page holds no dated line.", path=path)
    first = parse_page(sources[paths[0]])
    for key in ("standing", "date", "status"):
        values = {parse_page(text).field(key) for text in sources.values()}
        if len(values) > 1:
            refuse("stream_fields_differ", "The pages differ in a field a re-cut cannot merge.", field=key)
    ordered: list[tuple[tuple[str, str], int, list[str], str]] = []
    for number, (path, (items, _links, _records)) in enumerate(parsed.items()):
        for position, item in enumerate(items):
            ordered.append((_timeline_key(item[0]), number * 100000 + position, item, path))
    ordered.sort(key=lambda entry: (entry[0], entry[1]))
    about = _merged_about(plan, [parse_page(text).field("about") or None for text in sources.values()], subject_id)
    links = list(dict.fromkeys(line for _items, other, _records in parsed.values() for line in other))
    cap = plan.config.limits.leaf_cap_bytes
    subtopic = first.field("subtopic") or stem
    why = plan.reason("a pure event list over the cap splits by period, the period the last part of its name")
    cut_months: set[str] = set()

    def label(period: str, members: list[tuple[tuple[str, str], int, list[str], str]]) -> str:
        """The period a title names, or on a page of a month cut by days the days it holds."""
        if period[:7] not in cut_months or len(period) < 7 or "-q" in period:
            return period
        days = sorted({member[0][0] for member in members})
        return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"

    def render(period: str, members: list[tuple[tuple[str, str], int, list[str], str]]) -> str:
        owners = list(dict.fromkeys(member[3] for member in members))
        text = sources[owners[0]]
        page = parse_page(text)
        head = ["---"]
        for item in page.frontmatter.fields if page.frontmatter else []:
            if item.key not in ("subtopic", "about", "updated_at"):
                head.extend(split_line_ending(raw)[0] for raw in item.raw)
        head.append(f"subtopic: {subtopic}")
        if about is not None:
            head.append(f"about: {about}")
        dates = [member[0][0] for member in members if len(member[0][0]) == 10]
        head.append(f"updated_at: {max(dates)}")
        head.append("---")
        records = list(dict.fromkeys(line for owner in owners for line in parsed[owner][2]))
        span = label(period, members)
        body = [
            "", f"# {ref.name}: {_words(stem)} ({span})", "",
            f"The {_words(stem)} of {ref.name} {'from' if ' to ' in span else 'on' if len(span) == 10 else 'in'} "
            f"{span}, one line each.", "",
            "## Timeline", "", *(line for member in members for line in member[2]), "",
            "## Links", "", *_parent_link(f"{folder}/{stem}-{period}.md"), *links, "",
        ]
        if records:
            body += ["## Update record", "", *records, ""]
        return "\n".join([*head, *body])

    def fits(period: str, members: list[Any]) -> bool:
        return len(encode(parse_page(render(period, members)).content_text())) <= cap

    pages: dict[str, list[Any]] = {}
    by_year: dict[str, list[Any]] = {}
    for member in ordered:
        by_year.setdefault(_period(member[0][0], "year"), []).append(member)
    for year, members in by_year.items():
        if fits(year, members):
            pages[year] = members
            continue
        quarters: dict[str, list[Any]] = {}
        for member in members:
            quarters.setdefault(_period(member[0][0], "quarter"), []).append(member)
        for quarter, quarter_members in quarters.items():
            if fits(quarter, quarter_members):
                pages[quarter] = quarter_members
                continue
            months: dict[str, list[Any]] = {}
            for member in quarter_members:
                months.setdefault(_period(member[0][0], "month"), []).append(member)
            for month, month_members in months.items():
                if fits(month, month_members):
                    pages[month] = month_members
                    continue
                # A month past the cap is cut into runs of whole days (the first keeping the month's name).
                cut_months.add(month)
                days: dict[str, list[Any]] = {}
                for member in month_members:
                    days.setdefault(member[0][0], []).append(member)
                runs = day_runs(month, list(days.items()),
                                lambda day, number: day if number == 1 else f"{day}-{number}", fits)
                for period, run_members in runs:
                    pages[period] = run_members
    targets = {period: f"{folder}/{stem}-{period}.md" for period in pages}
    for path in targets.values():
        if plan.exists(path) and path not in sources:
            refuse("stream_page_taken", "A page of the new cut exists and is not a page of this stream.", path=path)
    first_page: dict[str, str] = {}
    for period in sorted(pages, key=lambda value: pages[value][0][0]):
        for member in pages[period]:
            first_page.setdefault(member[3], targets[period])
    for path, text in sources.items():
        plan.keep_copy(path, reason=why, successor=first_page[path])
    for path in sources:
        if path not in targets.values():
            plan.delete(path)
    made: list[str] = []
    for period, members in sorted(pages.items()):
        target = targets[period]
        text = render(period, members)
        plan.write(target, text)
        made.append(target)
        owners = list(dict.fromkeys(member[3] for member in members))
        if owners != [target]:
            plan.record_provenance(target, "Timeline lines came from " + ", ".join(f"`{owner}`" for owner in owners
                                                                             if owner != target), why)
        plan.hints[target] = f"{ref.name}: {_words(stem)}, {period}"
        size = len(encode(parse_page(text).content_text()))
        if size > cap:
            plan.note(f"needs_subtopic_split: {target} holds {size} bytes of content in one Timeline item over the cap")
    for path in sources:
        if path not in targets.values():
            plan.map(path, first_page[path])
            plan.log("recut", path, first_page[path], subject=subject_id, why=why)
    timeline = [line for items, _links, _records in parsed.values() for item in items for line in item]
    plan.expect(f"stream {stem}", timeline, made, multiset=True)
    plan.touch(info.tree)
    _done(plan, f"recut-stream {subject_id}")
    return made


# ═══════════════════════════════ running operations ═══════════════════════════════


def _op_leaf(plan: Plan, source: str, target: str, entity: str | None = None) -> Any:
    return leaf(plan, source, target, entity=entity)


def _op_archive(plan: Plan, path: str, reason: str, successor: str | None = None) -> Any:
    return archive(plan, path, reason=reason, successor=successor)


def _op_move_branch(plan: Plan, branch: str, tree: str, name: str | None = None) -> Any:
    old_tree, _, old_branch = branch.partition("/")
    return move_branch(plan, old_tree, old_branch, tree, name=name)


def _op_fold_tree(plan: Plan, tree: str, into: str, name: str | None = None, type: str | None = None) -> Any:
    return fold_tree(plan, tree, into=into, name=name, type_=type)


def _op_plan(plan: Plan, operations: list[Mapping[str, Any]]) -> Any:
    return run_operations(plan, operations)


# Each operation by its command word, taking keyword arguments as a plan file gives them.
OPERATIONS: dict[str, Callable[..., Any]] = {
    "leaf": _op_leaf,
    "rename": lambda plan, target, new=None, name=None: rename(plan, target, new, name=name),
    "promote": lambda plan, subject: promote(plan, subject),
    "merge": lambda plan, keep, drop: merge(plan, keep, drop),
    "archive": _op_archive,
    "move-branch": _op_move_branch,
    "new-tree": lambda plan, tree, description, name=None: new_tree(plan, tree, name=name, description=description),
    "new-agent": lambda plan, id, name=None, role=None, aliases=(): new_agent(plan, id, name=name, role=role,
                                                                              aliases=aliases),
    "retire-tree": lambda plan, tree, into=None: retire_tree(plan, tree, into=into),
    "fold-tree": _op_fold_tree,
    "set-type": lambda plan, subject, type: set_type(plan, subject, type),
    "set": lambda plan, path, kind=None, about=None, standing=None: set_fields(
        plan, path, kind=kind, about=about, standing=standing),
    "recut-stream": lambda plan, subject, leaves: recut_stream(plan, subject, list(leaves)),
}


def register(name: str, operation: Callable[..., Any]) -> None:
    """Make another module's operation (a collections or levels step) usable in a plan file."""
    OPERATIONS[name] = operation


def run_operations(plan: Plan, operations: Iterable[Mapping[str, Any]]) -> list[Any]:
    """Many operations in one plan, each reading what the earlier ones planned."""
    from . import collections as _collections  # noqa: F401  (registers its operations)

    results = []
    for number, item in enumerate(operations, start=1):
        if not isinstance(item, Mapping) or not isinstance(item.get("op"), str):
            refuse("plan_file_invalid", "Each operation is an object with an `op` and its arguments.", number=number)
        name = item["op"]
        if name not in OPERATIONS or name == "plan":
            refuse("operation_unknown", "The plan names an operation the move tool does not know.", op=name)
        arguments = {key: value for key, value in item.items() if key != "op"}
        try:
            results.append(OPERATIONS[name](plan, **arguments))
        except TypeError as exc:
            refuse("plan_file_invalid", "An operation's arguments do not fit it.", number=number, op=name,
                   reason=str(exc))
        except MemoryForestError as exc:
            exc.details.setdefault("operation", number)
            raise
    return results


def read_plan_file(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """A plan file: a JSON list of operations, or an object with ``operations``."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        refuse("plan_file_invalid", "The plan file is not readable JSON.", reason=exc.__class__.__name__)
    if isinstance(data, Mapping):
        data = data.get("operations")
    if not isinstance(data, list):
        refuse("plan_file_invalid", "A plan file is a list of operations (or an object with `operations`).")
    return data


def execute(
    root: str | os.PathLike[str],
    operation: str,
    *,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
    today: str | None = None,
    why: str | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    """Run one operation (or ``plan`` with ``operations=[…]``): check it whole, then write it or refuse."""
    from . import collections as _collections  # noqa: F401

    if operation == "plan":
        steps = arguments.get("operations") or []
        return run(root, lambda plan: run_operations(plan, steps), dry_run=dry_run, lock=lock, today=today, why=why)
    if operation not in OPERATIONS:
        refuse("operation_unknown", "The move tool has no such operation.", op=operation)
    return run(root, lambda plan: OPERATIONS[operation](plan, **arguments), dry_run=dry_run, lock=lock, today=today,
               why=why)
