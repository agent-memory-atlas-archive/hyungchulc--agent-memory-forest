"""Page structure checks: each page's role, frontmatter, sections and links (contract, sections 4 to 6).

A page's role comes from its path (``layout.classify``). For each role this
module knows which frontmatter keys a page may carry, which ``##`` sections it
holds and in which order, and which page its single ``up:`` link names.
``check_structure`` runs these page checks and the forest-wide ones of
``audit`` together: it is the structure half of ``forest check``.

What a check does not judge: the order of frontmatter keys, the words of a
section, and anything the lint reports (a missing ``kind:``, a stale list).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from . import audit as forest_audit
from . import layout
from .config import Config
from .model import RECORD_SECTION, UpLink, split_line_ending, wikilinks
from .registry import Problem
from .view import ForestView, page_line_numbers


CODES: Final[tuple[str, ...]] = (
    "frontmatter_key_unknown",
    "frontmatter_value_invalid",
    "section_missing",
    "section_unknown",
    "section_order",
    "up_link_missing",
    "up_link_wrong",
    "wikilink_not_up",
)

LEAF_KEYS: Final[tuple[str, ...]] = (
    "entity",
    "subtopic",
    "kind",
    "about",
    "standing",
    "timezone",
    "updated_at",
    "date",
    "status",
    "superseded_by",
)
COLLECTION_KEYS: Final[tuple[str, ...]] = ("entity", "subtopic", "kind", "timezone", "updated_at")
COLLECTION_SUBTOPIC: Final[str] = "seen-once"
COLLECTION_KIND: Final[str] = "collection"
SUPERSEDED: Final[str] = "superseded"


@dataclass(frozen=True, slots=True)
class Shape:
    """The sections of a page of one role: in order, each required or not; ``free`` pages put theirs first."""

    sections: tuple[tuple[str, bool], ...]
    keys: tuple[str, ...]
    free: bool = False


ROOT_SHAPE: Final[Shape] = Shape((("Identity", True), ("Trees", True), ("Agents", True), (RECORD_SECTION, False)), ())
AREA_TREE_SHAPE: Final[Shape] = Shape(
    (
        ("Scope", True),
        ("Patterns and rules", True),
        ("Current state", True),
        ("Branches", True),
        ("Links", True),
        (RECORD_SECTION, False),
    ),
    ("entity",),
)
AGENT_TREE_SHAPE: Final[Shape] = Shape(
    (
        ("Role", True),
        ("Standing instructions", True),
        ("Lessons", True),
        ("Current state", True),
        ("Branches", True),
        ("Links", True),
        (RECORD_SECTION, False),
    ),
    ("entity",),
)
INDEX_SHAPE: Final[Shape] = Shape(
    (("Subjects", True), ("Branches", True), ("Links", True), (RECORD_SECTION, False)), ()
)
BOARD_SHAPE: Final[Shape] = Shape(
    (
        ("About", True),
        ("Current state", True),
        ("Rules", False),
        ("Leaves", True),
        ("Pointers", True),
        ("Links", True),
        (RECORD_SECTION, False),
    ),
    ("entity",),
)
LEAF_SHAPE: Final[Shape] = Shape(
    (("Daily totals", False), ("Timeline", False), ("Links", True), (RECORD_SECTION, False)), LEAF_KEYS, free=True
)
COLLECTION_SHAPE: Final[Shape] = Shape(
    (("Timeline", False), ("Links", True), (RECORD_SECTION, False)), COLLECTION_KEYS, free=True
)


def shape_of(view: ForestView, path: str) -> Shape | None:
    role = view.role(path)
    if role is layout.PageRole.ROOT_INDEX:
        return ROOT_SHAPE
    if role is layout.PageRole.TREE_PAGE:
        tree = layout.classify(path).tree or ""
        return AGENT_TREE_SHAPE if view.registry.is_agent_tree(tree) else AREA_TREE_SHAPE
    if role is layout.PageRole.TREE_INDEX:
        return INDEX_SHAPE
    if role is layout.PageRole.BOARD:
        return BOARD_SHAPE
    if role is layout.PageRole.LEAF:
        return LEAF_SHAPE
    if role is layout.PageRole.COLLECTION_PAGE:
        return COLLECTION_SHAPE
    return None


def frontmatter_problems(view: ForestView, path: str, shape: Shape) -> list[Problem]:
    page = view.page(path)
    if page.frontmatter is None:
        return []
    found = []
    for key in page.frontmatter.keys():
        if key not in shape.keys:
            found.append(Problem("frontmatter_key_unknown", path, None, f"`{key}:` is no key of this kind of page"))
    role = view.role(path)
    if role is layout.PageRole.LEAF:
        status, successor = page.field("status").strip(), page.field("superseded_by").strip()
        if status and status != SUPERSEDED:
            found.append(Problem("frontmatter_value_invalid", path, None, f"`status: {status}` is not `{SUPERSEDED}`"))
        if bool(status) != bool(successor):
            found.append(Problem("frontmatter_value_invalid", path, None,
                                 "`status: superseded` and `superseded_by:` go together"))
        if page.field("date").strip() and page.section("Timeline") is not None:
            found.append(Problem("frontmatter_value_invalid", path, None,
                                 "`date:` is only for a leaf with no `## Timeline`"))
    if role is layout.PageRole.COLLECTION_PAGE:
        if page.field("subtopic").strip() != COLLECTION_SUBTOPIC:
            found.append(Problem("frontmatter_value_invalid", path, None,
                                 f"a collection page has `subtopic: {COLLECTION_SUBTOPIC}`"))
        if page.field("kind").strip() != COLLECTION_KIND:
            found.append(Problem("frontmatter_value_invalid", path, None,
                                 f"a collection page has `kind: {COLLECTION_KIND}`"))
    if role is layout.PageRole.TREE_PAGE:
        tree = layout.classify(path).tree or ""
        entity = page.field("entity").strip()
        if view.registry.is_agent_tree(tree) and entity != tree:
            found.append(Problem("frontmatter_value_invalid", path, None,
                                 f"an agent's tree page has `entity: {tree}`"))
    if role is layout.PageRole.BOARD:
        entity = page.field("entity").strip()
        if entity and entity not in view.registry.entities:
            found.append(Problem("frontmatter_value_invalid", path, None,
                                 f"`entity: {entity}` names no registered subject"))
    return found


def section_problems(view: ForestView, path: str, shape: Shape) -> list[Problem]:
    page = view.page(path)
    lines = page_line_numbers(page)
    names = page.section_names()
    order = [name.casefold() for name, _required in shape.sections]
    found = []
    for name, required in shape.sections:
        if required and name.casefold() not in (item.casefold() for item in names):
            found.append(Problem("section_missing", path, None, f"no `## {name}`"))
    seen_fixed = -1
    for name in names:
        key = name.casefold()
        line = lines.get(key)
        if key in order:
            position = order.index(key)
            if position < seen_fixed:
                found.append(Problem("section_order", path, line,
                                     f"`## {name}` stands after `## {shape.sections[seen_fixed][0]}`"))
            seen_fixed = max(seen_fixed, position)
        elif shape.free:
            if seen_fixed >= 0:
                found.append(Problem("section_order", path, line,
                                     f"`## {name}` stands after `## {shape.sections[seen_fixed][0]}`, "
                                     "which close the page"))
        else:
            found.append(Problem("section_unknown", path, line, f"`## {name}` is no section of this kind of page"))
    return found


def link_problems(view: ForestView, path: str) -> list[Problem]:
    """A page's single ``up:`` link names its parent; every other wikilink is a code span instead."""
    page = view.page(path)
    parent = layout.parent_page(path)
    ups: list[tuple[int | None, UpLink]] = []
    others: list[tuple[int | None, str]] = []
    lines = page_line_numbers(page)
    number = 1 + (page.frontmatter.render().count("\n") if page.frontmatter else 0)
    for raw in page.head:
        for link in wikilinks(split_line_ending(raw)[0]):
            others.append((number, link.target))
        number += 1
    for section in page.sections:
        number = lines.get(section.heading.casefold(), number) + 1
        for offset, raw in enumerate(section.lines):
            line = split_line_ending(raw)[0]
            up = UpLink.match(line) if section.is_named("Links") else None
            if up is not None:
                ups.append((number + offset, up))
                continue
            for link in wikilinks(line):
                others.append((number + offset, link.target))
    found = []
    if parent is None:
        for line, target in [*((n, u.target) for n, u in ups), *others]:
            found.append(Problem("wikilink_not_up", path, line, f"`[[{target}]]`: the root index links to nothing"))
        return found
    if not ups:
        found.append(Problem("up_link_missing", path, None,
                             f"no `- up: [[{layout.up_link_target(parent)}|…]]` under `## Links`"))
    for line, up in ups:
        if up.path != parent:
            found.append(Problem("up_link_wrong", path, line, f"`up:` names `{up.path}`, not the parent `{parent}`"))
    if len(ups) > 1:
        found.append(Problem("up_link_wrong", path, ups[1][0], f"{len(ups)} `up:` links; a page has one"))
    if parent is not None and not view.exists(parent):
        found.append(Problem("up_link_wrong", path, None, f"the parent `{parent}` is not there"))
    for line, target in others:
        found.append(Problem("wikilink_not_up", path, line,
                             f"`[[{target}]]`: wikilinks join a page to its parent only; write a code-span path"))
    return found


def validate_page(view: ForestView, path: str) -> list[Problem]:
    shape = shape_of(view, path)
    if shape is None:
        return []
    return [*frontmatter_problems(view, path, shape), *section_problems(view, path, shape), *link_problems(view, path)]


def validate_pages(view: ForestView, paths: Iterable[str] | None = None) -> list[Problem]:
    found: list[Problem] = []
    for path in view.paths if paths is None else paths:
        found.extend(validate_page(view, path))
    return found


def check_structure(root: str | os.PathLike[str], *, config: Config | None = None) -> list[Problem]:
    """The structure half of ``forest check``: every page's shape and the forest-wide audit."""
    view = ForestView.open(root, config=config)
    symlinked = forest_audit.symlinks(view.root)
    if symlinked:
        return symlinked
    found = [*validate_pages(view), *forest_audit.audit(view)]
    return sorted(found, key=lambda item: (item.path, item.line or 0, item.code, item.detail))
