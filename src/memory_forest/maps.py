"""The derived lists, brought up to the pages on disk (contract, sections 3 and 5; design, section 5).

Five lists are computed from the forest rather than written by hand:

- a board's ``## Leaves``: every page in its folder,
  ``- `<leaf>.md` — <what it holds> (updated YYYY-MM-DD)``, the date being the
  leaf's ``updated_at``;
- a subject board's ``## Pointers``: every leaf outside the board's folder
  whose ``about:`` names the board's subject, ``- `<path>` — <what it holds>``,
  the description being that leaf's line on its own board;
- a tree page's ``## Branches`` (``- `<branch>` — …``) and a tree index's
  ``## Branches`` (``- `<branch>.md` — …``): every board of the tree;
- the root index's ``## Trees`` (every tree that is not an agent's) and
  ``## Agents`` (every agent, ``- `<id>` — <name> · <role>``, with ``· ended``
  for a retired one).

``sync`` compares each list with what the pages make of it and rewrites only
the lines that differ: a line that already names a page keeps its hand-written
description after the dash, a missing page gets a line at the end of the list,
a line naming a page that is not there goes. Lines that are no list lines (a
placement rule under ``## Trees``) and pointers to boards or tree pages, which
are written by hand, stay as they are. A sync writes no record line: bringing a
derived list up to the pages changes no statement of the page.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

from . import layout, names, registry
from .locking import LockToken
from .config import Config
from .model import DOT, RECORD_SECTION, ListLine, code_spans, parse_page
from .plan import Plan, first_prose, title_of
from .registry import Problem

LEAVES: Final[str] = "Leaves"
POINTERS: Final[str] = "Pointers"
BRANCHES: Final[str] = "Branches"
TREES: Final[str] = "Trees"
AGENTS: Final[str] = "Agents"
ENDED: Final[str] = "ended"
# Where a list section goes when a page has none: before the first of these that it has.
_BOARD_AFTER_LEAVES: Final[tuple[str, ...]] = (POINTERS, "Links", RECORD_SECTION)
_BOARD_AFTER_POINTERS: Final[tuple[str, ...]] = ("Links", RECORD_SECTION)
_AFTER_BRANCHES: Final[tuple[str, ...]] = ("Pointers", "Links", RECORD_SECTION)


@dataclass(frozen=True, slots=True)
class Wanted:
    """One line a list should hold: its target as written, a description for a new line, its date."""

    target: str
    description: str
    updated: str | None = None
    adjust: Callable[[str], str] | None = None


@dataclass(slots=True)
class ListChange:
    path: str
    section: str
    added: list[str]
    removed: list[str]
    updated: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "path": self.path,
            "removed": self.removed,
            "section": self.section,
            "updated": self.updated,
        }

    @property
    def code(self) -> str:
        return {LEAVES: "board_leaves_stale", POINTERS: "board_pointers_stale"}.get(self.section, "branch_list_stale")


def _one_line(text: str | None, fallback: str) -> str:
    value = " ".join((text or "").split())
    return value or fallback


def sync_list(
    text: str,
    section_name: str,
    wanted: dict[str, Wanted],
    *,
    key: Callable[[str], str],
    removable: Callable[[str], bool],
    before: Iterable[str] = (),
    path: str = "",
) -> tuple[str, ListChange | None]:
    """One list section brought to ``wanted`` (by ``key`` of each line's target); the new text and what changed."""
    page = parse_page(text)
    section = page.section(section_name)
    change = ListChange(path, section_name, [], [], [])
    if section is None:
        if not wanted:
            return text, None
        anchor = next((other for other in before if page.section(other) is not None), None)
        section = page.add_section(section_name, before=anchor)
    items = [(index, bare, ListLine.match(bare)) for index, bare in section.items()]
    first: dict[str, int] = {}
    for index, _bare, line in items:
        if line is not None:
            first.setdefault(key(line.target), index)
    seen: set[str] = set()
    for index, bare, line in reversed(items):
        if line is None:
            continue
        name = key(line.target)
        if name not in wanted:
            if removable(line.target):
                del section.lines[index]
                change.removed.append(bare)
            continue
        if first[name] != index:
            del section.lines[index]
            change.removed.append(bare)
            continue
        seen.add(name)
        item = wanted[name]
        description = line.description if line.description is not None else item.description
        if item.adjust is not None:
            description = item.adjust(description)
        new = ListLine(line.target, description, item.updated or line.updated, line.indent)
        if new.render() != bare:
            section.replace_line(index, new.render())
            change.updated.append(new.render())
    for name in sorted(set(wanted) - seen):
        item = wanted[name]
        new = ListLine(item.target, item.description, item.updated).render()
        section.append_item(new)
        change.added.append(new)
    change.removed.reverse()
    change.updated.reverse()
    if not (change.added or change.removed or change.updated):
        return text, None
    return page.render(), change


# ─────────────────────────────── what each list should hold ───────────────────────────────


def listed_description(plan: Plan, path: str) -> str | None:
    """The description a page has on its parent's list now (a leaf on its board, a board on its tree page)."""
    info = layout.classify(path)
    if info.role in (layout.PageRole.LEAF, layout.PageRole.COLLECTION_PAGE) and info.tree and info.branch:
        board, name, section = layout.board(info.tree, info.branch), PurePosixPath(path).name, LEAVES
    elif info.role is layout.PageRole.BOARD and info.tree and info.branch:
        board, name, section = layout.tree_page(info.tree), info.branch, BRANCHES
    elif info.role is layout.PageRole.TREE_PAGE and info.tree:
        board, name, section = layout.ROOT_INDEX, info.tree, TREES
    else:
        return None
    if not plan.exists(board):
        return None
    page = parse_page(plan.read(board))
    for candidate in (section, AGENTS) if section == TREES else (section,):
        found = page.section(candidate)
        if found is None:
            continue
        for bare in found.item_lines():
            line = ListLine.match(bare)
            if line is not None and PurePosixPath(line.target).name == name and line.description:
                return line.description
    return None


def _leaf_wanted(plan: Plan, tree: str, branch: str) -> dict[str, Wanted]:
    wanted: dict[str, Wanted] = {}
    for path in plan.folder_pages(layout.leaf_folder(tree, branch)):
        name = PurePosixPath(path).name
        text = plan.read(path)
        updated = parse_page(text).field("updated_at").strip() or None
        description = _one_line(plan.hints.get(path) or title_of(text), PurePosixPath(path).stem)
        wanted[name] = Wanted(name, description, updated)
    return wanted


def _pointer_wanted(plan: Plan, subject_id: str, folder: str) -> dict[str, Wanted]:
    wanted: dict[str, Wanted] = {}
    for path in plan.pages():
        if layout.page_role(path) is not layout.PageRole.LEAF or path.startswith(folder + "/"):
            continue
        text = plan.read(path)
        page = parse_page(text)
        if subject_id not in registry.about_ids(page.frontmatter.get("about") if page.frontmatter else None):
            continue
        description = listed_description(plan, path) or title_of(text)
        wanted[path] = Wanted(path, _one_line(description, PurePosixPath(path).stem))
    return wanted


def _is_pointer_leaf(target: str) -> bool:
    return layout.page_role(target) is layout.PageRole.LEAF


def _board_description(plan: Plan, tree: str, branch: str) -> str:
    hint = plan.hints.get(f"branch:{tree}/{branch}")
    if hint:
        return _one_line(hint, branch)
    text = plan.read(layout.board(tree, branch))
    return _one_line(first_prose(text, "About") or title_of(text), branch)


def _branch_targets(plan: Plan, tree: str) -> list[str]:
    index = layout.tree_index(tree)
    return [
        PurePosixPath(path).stem
        for path in plan.folder_pages(layout.tree_folder(tree))
        if path != index and layout.page_role(path) is layout.PageRole.BOARD
    ]


def _branch_key(tree: str) -> Callable[[str], str]:
    def key(target: str) -> str:
        value = target.strip().rstrip("/")
        if "/" in value:
            info = layout.classify(value if value.endswith(".md") else value + ".md")
            return (info.branch or value) if info.tree == tree else value
        return value.removesuffix(".md")

    return key


def sync_tree(plan: Plan, tree: str, changes: list[ListChange]) -> None:
    """A tree's own lists: its page's and its index's ``## Branches``, and each board's Leaves and Pointers."""
    branches = _branch_targets(plan, tree)
    key = _branch_key(tree)
    for path, shown in ((layout.tree_page(tree), "{}"), (layout.tree_index(tree), "{}.md")):
        if not plan.exists(path):
            continue
        wanted = {
            branch: Wanted(shown.format(branch), _board_description(plan, tree, branch)) for branch in branches
        }
        text, change = sync_list(
            plan.read(path), BRANCHES, wanted, key=key, removable=lambda _target: True,
            before=_AFTER_BRANCHES, path=path,
        )
        if change is not None:
            plan.update(path, text)
            changes.append(change)
    homes = {ref.home: ref.id for ref in plan.subjects() if ref.level == "branch"}
    for branch in branches:
        board = layout.board(tree, branch)
        folder = layout.leaf_folder(tree, branch)
        text, change = sync_list(
            plan.read(board), LEAVES, _leaf_wanted(plan, tree, branch),
            key=lambda target: PurePosixPath(target).name, removable=lambda _target: True,
            before=_BOARD_AFTER_LEAVES, path=board,
        )
        if change is not None:
            plan.update(board, text)
            changes.append(change)
        subject_id = homes.get(board)
        if subject_id is None:
            continue
        text, change = sync_list(
            plan.read(board), POINTERS, _pointer_wanted(plan, subject_id, folder),
            key=lambda target: target.strip(), removable=_is_pointer_leaf,
            before=_BOARD_AFTER_POINTERS, path=board,
        )
        if change is not None:
            plan.update(board, text)
            changes.append(change)


def _with_ended(ended: bool) -> Callable[[str], str]:
    def adjust(description: str) -> str:
        parts = description.split(DOT)
        has = len(parts) > 2 and parts[-1].strip() == ENDED
        if ended and not has:
            return description + DOT + ENDED
        if not ended and has:
            return DOT.join(parts[:-1])
        return description

    return adjust


def sync_root(plan: Plan, changes: list[ListChange]) -> None:
    """The root index's ``## Trees`` and ``## Agents``."""
    if not plan.exists(layout.ROOT_INDEX):
        return
    agents = {
        ref.id: ref
        for ref in plan.subjects()
        if ref.type == registry.AGENT_TYPE and ref.level == "tree" and plan.exists(layout.tree_page(ref.id))
    }
    area: dict[str, Wanted] = {}
    for tree in plan.trees():
        page = layout.tree_page(tree)
        if tree in agents or not plan.exists(page):
            continue
        text = plan.read(page)
        hint = plan.hints.get(f"tree:{tree}")
        area[tree] = Wanted(tree, _one_line(hint or first_prose(text, "Scope") or title_of(text), tree))
    listed: dict[str, Wanted] = {}
    for agent_id, ref in agents.items():
        text = plan.read(layout.tree_page(agent_id))
        role = _one_line(plan.hints.get(f"agent:{agent_id}") or first_prose(text, "Role"), ref.type)
        ended = ref.line.status == "ended"
        description = f"{names.encode(ref.name)}{DOT}{role}" + (f"{DOT}{ENDED}" if ended else "")
        listed[agent_id] = Wanted(agent_id, description, adjust=_with_ended(ended))
    text = plan.read(layout.ROOT_INDEX)
    for section, wanted in ((TREES, area), (AGENTS, listed)):
        text, change = sync_list(
            text, section, wanted, key=lambda target: target.strip(),
            removable=lambda target: layout.is_slug(target.strip()), before=(AGENTS, RECORD_SECTION) if section == TREES
            else (RECORD_SECTION,), path=layout.ROOT_INDEX,
        )
        if change is not None:
            changes.append(change)
    plan.update(layout.ROOT_INDEX, text)


def sync(plan: Plan, *, trees: Iterable[str] | None = None, root_index: bool = True) -> list[ListChange]:
    """Bring the derived lists of ``trees`` (every tree when None) and of the root index up to the planned pages."""
    changes: list[ListChange] = []
    known = set(plan.trees())
    for tree in sorted(known if trees is None else set(trees) & known):
        sync_tree(plan, tree, changes)
    if root_index:
        sync_root(plan, changes)
    return changes


def check(root: str | os.PathLike[str], *, trees: Iterable[str] | None = None,
          config: Config | None = None) -> list[ListChange]:
    """Every derived list that differs from what the pages make of it (nothing is written)."""
    plan = Plan.open(root, config=config)
    return sync(plan, trees=trees)


def _targets(lines: list[str]) -> str:
    return ", ".join(f"`{target}`" for line in lines if (target := _target_of(line)))


def _target_of(line: str) -> str | None:
    parsed = ListLine.match(line)
    return parsed.target if parsed is not None else None


def change_problem(change: ListChange) -> Problem:
    """One list that differs, as one problem line: its code, its page, and what it misses, names or has stale."""
    parts = []
    if change.added:
        parts.append(f"misses {_targets(change.added)}")
    if change.removed:
        parts.append(f"names {_targets(change.removed)}, no page of the list")
    if change.updated:
        parts.append(f"has a stale line for {_targets(change.updated)}")
    return Problem(change.code, change.path, None, f"`## {change.section}` " + "; ".join(parts))


def pointer_problems(plan: Plan) -> list[Problem]:
    """``board_pointer_stale``: a board's pointer to a leaf of its own folder, or a bare name instead of a path."""
    found = []
    for path in plan.pages():
        info = layout.classify(path)
        if info.role is not layout.PageRole.BOARD or not info.tree or not info.branch:
            continue
        section = parse_page(plan.read(path)).section(POINTERS)
        if section is None:
            continue
        own = layout.leaf_folder(info.tree, info.branch) + "/"
        for _index, bare in section.items():
            spans = [span.text.strip() for span in code_spans(bare)]
            target = next((span for span in spans if layout.is_forest_path(span)), None)
            if target is None:
                found.append(Problem("board_pointer_stale", path, None,
                                     f"a pointer names no page by its path: {bare.strip()[:80]!r}"))
            elif target.startswith(own):
                found.append(Problem("board_pointer_stale", path, None,
                                     f"a pointer to `{target}`, a leaf of the board's own folder (`## Leaves` lists it)"))
    return found


def apply(
    root: str | os.PathLike[str],
    *,
    trees: Iterable[str] | None = None,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """``forest check --sync``: rewrite the derived lists that differ, under the writer lock."""
    from .plan import run

    found: list[ListChange] = []

    def build(plan: Plan) -> list[dict[str, Any]]:
        plan.operations.append("check --sync")
        found.extend(sync(plan, trees=trees))
        return [change.as_dict() for change in found]

    return run(root, build, dry_run=dry_run, lock=lock, config=config)


def render_change(change: ListChange) -> list[str]:
    lines = [f"{change.path}: {change.code}: ## {change.section}"]
    lines += [f"  + {line}" for line in change.added]
    lines += [f"  - {line}" for line in change.removed]
    lines += [f"  ~ {line}" for line in change.updated]
    return lines



# ─────────────────────────────── the lint codes of the derived lists ───────────────────────────────


def _lint_changes(view: Any) -> list[ListChange]:
    cached = view.__dict__.get("_maps_changes")
    if cached is None:
        cached = check(view.root, config=view.config)
        view.__dict__["_maps_changes"] = cached
    return cached


def _lint_list(code: str) -> Callable[[Any], list[Any]]:
    def found(view: Any) -> list[Any]:
        from . import lint

        return lint.hits_from(change_problem(change) for change in _lint_changes(view) if change.code == code)

    found.__name__ = code
    return found


def _lint_pointer(view: Any) -> list[Any]:
    from . import lint

    return lint.hits_from(pointer_problems(Plan.open(view.root, config=view.config)))


def _register() -> None:
    from . import lint

    rule = "`forest check --sync` brings it up to the pages"
    lint.register(lint.LintCode("board_leaves_stale", lint.VIOLATION, "`## Leaves` lists the board's folder; " + rule,
                                _lint_list("board_leaves_stale"), False))
    lint.register(lint.LintCode("branch_list_stale", lint.VIOLATION, "`## Branches`, `## Trees` and `## Agents` "
                                "list the pages; " + rule, _lint_list("branch_list_stale"), False))
    lint.register(lint.LintCode("board_pointers_stale", lint.UPKEEP, "`## Pointers` is what the `about:` lines "
                                "elsewhere make of it; " + rule, _lint_list("board_pointers_stale")))
    lint.register(lint.LintCode("board_pointer_stale", lint.UPKEEP, "a pointer names another branch's page by its "
                                "path", _lint_pointer))


_register()
