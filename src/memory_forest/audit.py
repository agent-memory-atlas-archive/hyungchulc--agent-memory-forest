"""Forest-wide structure checks: what holds the pages together (contract, sections 1 to 5).

- every file and folder of the four layers is where the layout puts it, and
  nothing in the forest or its lanes is a symlink;
- every leaf names a registered subject in ``entity:`` and lies in that
  subject's home;
- every board has its folder of leaves and every folder its board;
- every tree has its page, its index and its line on the root index, and
  every agent its tree and its line under ``## Agents``.

Each check returns ``registry.Problem`` lines: a code, a path, a line and what
is wrong. The leaf checks are shared with the lint, which reports them under
the same codes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from . import layout
from .model import AgentLine, ListLine
from .registry import AGENT_TYPE, COLLECTION_TYPE, Problem
from .view import ForestView, section_start


CODES: Final[tuple[str, ...]] = (
    "symlink",
    "page_outside_layout",
    "leaf_entity_missing",
    "leaf_entity_unknown",
    "leaf_misplaced",
    "board_unpaired",
    "tree_incomplete",
    "agent_tree_incomplete",
)
FIXED_NAMES: Final[frozenset[str]] = frozenset({"XLTM.md"})


# ─────────────────────────────── the layout on disk ───────────────────────────────


def symlinks(root: Path) -> list[Problem]:
    """Every symlink under the four layers, the lanes and ``.forest/`` (none is followed)."""
    found: list[Problem] = []
    for top in (*layout.ALL_LAYERS, layout.STATE_DIR):
        start = root / top
        if start.is_symlink():
            found.append(Problem("symlink", top, None, "a symlink; the forest holds none"))
            continue
        if not start.is_dir():
            continue
        for folder, folders, files in os.walk(start, followlinks=False):
            for name in sorted(folders + files):
                full = Path(folder) / name
                if full.is_symlink():
                    relative = full.relative_to(root).as_posix()
                    found.append(Problem("symlink", relative, None, "a symlink; the forest holds none"))
    return sorted(found, key=lambda item: item.path)


def _layout_problem(relative: str, is_dir: bool) -> str | None:
    """Why a file or folder of the four layers is outside the layout, or None."""
    parts = PurePosixPath(relative).parts
    layer, depth = parts[0], len(parts)
    name = parts[-1]
    if is_dir:
        allowed_depth = {layout.XLTM: 1, layout.LTM: 1, layout.MTM: 2, layout.STM: 3}[layer]
        if depth > allowed_depth:
            return f"a folder where `{layer}` keeps none"
        if not layout.is_slug(name):
            return f"the folder name `{name}` is not a slug"
        return None
    if not name.endswith(".md"):
        return "only `.md` pages live in the four layers"
    info = layout.classify(relative)
    if info.role is layout.PageRole.UNKNOWN:
        return info.problem or "the path has no place in the layout"
    stem = name[:-3]
    if info.role is layout.PageRole.TREE_PAGE and not layout.is_slug(info.tree or ""):
        return f"the tree name `{info.tree}` is not a slug"
    if info.role in (layout.PageRole.BOARD, layout.PageRole.TREE_INDEX) and not (
        layout.is_slug(info.tree or "") and layout.is_slug(info.name or "")
    ):
        return f"`{stem}` is not a slug in a slug-named tree"
    return None


def outside_layout(root: Path) -> list[Problem]:
    """Files and folders of the four layers that the layout has no place for (leaf names are the lint's)."""
    found: list[Problem] = []
    for layer in layout.FOREST_LAYERS:
        start = root / layer
        if not start.is_dir() or start.is_symlink():
            continue
        for folder, folders, files in os.walk(start, followlinks=False):
            for name in sorted(folders):
                full = Path(folder) / name
                if full.is_symlink():
                    continue
                relative = full.relative_to(root).as_posix()
                problem = _layout_problem(relative, True)
                if problem:
                    found.append(Problem("page_outside_layout", relative + "/", None, problem))
            for name in sorted(files):
                full = Path(folder) / name
                if full.is_symlink():
                    continue
                relative = full.relative_to(root).as_posix()
                problem = _layout_problem(relative, False)
                if problem:
                    found.append(Problem("page_outside_layout", relative, None, problem))
    return sorted(found, key=lambda item: item.path)


# ─────────────────────────────── leaves and their subjects ───────────────────────────────


def misplacement(path: str, subject: Mapping[str, Any]) -> str | None:
    """Why a leaf of ``subject`` lies outside the subject's home (contract, section 3), or None."""
    subject_id, level, home = subject["id"], subject["level"], PurePosixPath(subject["home"])
    leaf = PurePosixPath(path)
    if subject["type"] == COLLECTION_TYPE:
        if leaf.parent == home.parent and layout.is_collection_name(leaf.name):
            return None
        return f"the pages of the collection `{subject_id}` are `{home.parent}/seen-once-<period>.md`"
    if level == "tree":
        folder = f"{layout.STM}/{subject_id}/"
        return None if path.startswith(folder) else f"the tree `{subject_id}` keeps its leaves under `{folder}`"
    if level == "branch":
        folder = f"{layout.leaf_folder(subject['tree'], subject_id)}/"
        return None if path.startswith(folder) else f"the branch `{subject_id}` keeps its leaves under `{folder}`"
    if leaf.parent == home.parent and (leaf.stem == subject_id or leaf.stem.startswith(f"{subject_id}-")):
        return None
    return f"the leaf subject `{subject_id}` lives at `{home}`, its other leaves `{subject_id}-<subtopic>.md` beside it"


def entity_line(view: ForestView, path: str) -> int | None:
    page = view.page(path)
    if page.frontmatter is None:
        return None
    number = 2
    for item in page.frontmatter.items:
        if isinstance(item, str):
            number += 1
            continue
        if item.key == "entity":
            return number
        number += len(item.raw)
    return None


def leaf_entity_missing(view: ForestView) -> list[Problem]:
    return [
        Problem("leaf_entity_missing", path, None, "no `entity:` in the frontmatter")
        for path in view.leaves
        if not view.page(path).field("entity").strip()
    ]


def leaf_entity_unknown(view: ForestView) -> list[Problem]:
    found = []
    entities = view.registry.entities
    for path in view.leaves:
        entity = view.page(path).field("entity").strip()
        if entity and entity not in entities:
            known = view.registry.lookup(entity)
            hint = f"; the registry knows that name as `{known['id']}`" if known else ""
            found.append(Problem("leaf_entity_unknown", path, entity_line(view, path),
                                 f"`entity: {entity}` names no registered subject{hint}"))
    return found


def leaf_misplaced(view: ForestView) -> list[Problem]:
    found = []
    entities = view.registry.entities
    for path in view.leaves:
        subject = entities.get(view.page(path).field("entity").strip())
        if subject is None:
            continue
        problem = misplacement(path, subject)
        if problem:
            found.append(Problem("leaf_misplaced", path, entity_line(view, path), problem))
    return found


# ─────────────────────────────── boards, trees and agents ───────────────────────────────


def _folders(root: Path, relative: str) -> list[str]:
    top = root / relative
    if not top.is_dir() or top.is_symlink():
        return []
    return sorted(entry.name for entry in os.scandir(top) if entry.is_dir(follow_symlinks=False))


def board_unpaired(view: ForestView) -> list[Problem]:
    """A board with no folder of leaves, and a folder of leaves with no board."""
    found = []
    boards = set()
    for path in view.boards:
        info = layout.classify(path)
        boards.add((info.tree, info.branch))
        if not (view.root / layout.leaf_folder(info.tree or "", info.branch or "")).is_dir():
            found.append(Problem("board_unpaired", path, None,
                                 f"the board has no folder `{layout.leaf_folder(info.tree or '', info.branch or '')}/`"))
    for tree in _folders(view.root, layout.STM):
        for branch in _folders(view.root, f"{layout.STM}/{tree}"):
            if (tree, branch) not in boards:
                found.append(Problem("board_unpaired", f"{layout.leaf_folder(tree, branch)}/", None,
                                     f"the folder has no board `{layout.board(tree, branch)}`"))
    return sorted(found, key=lambda item: item.path)


def root_lines(view: ForestView) -> tuple[dict[str, int], dict[str, int]]:
    """The trees under ``## Trees`` and the agents under ``## Agents`` of the root index, with their lines."""
    trees: dict[str, int] = {}
    agents: dict[str, int] = {}
    if not view.exists(layout.ROOT_INDEX):
        return trees, agents
    page = view.page(layout.ROOT_INDEX)
    for name, target, parser in (("Trees", trees, ListLine), ("Agents", agents, AgentLine)):
        section = page.section(name)
        start = section_start(page, name)
        if section is None or start is None:
            continue
        for index, line in section.items():
            parsed = parser.match(line)
            if parsed is not None:
                key = parsed.target if isinstance(parsed, ListLine) else parsed.id
                target.setdefault(key, start + index)
    return trees, agents


def tree_incomplete(view: ForestView) -> list[Problem]:
    """Every tree with its page, its index and its line on the root index; every line naming a tree."""
    found = []
    listed_trees, listed_agents = root_lines(view)
    names: set[str] = set(listed_trees)
    for path in view.with_role(layout.PageRole.TREE_PAGE, layout.PageRole.TREE_INDEX):
        names.add(layout.classify(path).tree or "")
    names.update(_folders(view.root, layout.MTM))
    names.update(_folders(view.root, layout.STM))
    agent_trees = {subject["id"] for subject in view.registry.entities.values() if subject["type"] == AGENT_TYPE}
    for tree in sorted(name for name in names if name):
        missing = [path for path in (layout.tree_page(tree), layout.tree_index(tree)) if not view.exists(path)]
        if missing:
            found.append(Problem("tree_incomplete", layout.tree_folder(tree) + "/", None,
                                 "the tree has no " + " and no ".join(f"`{path}`" for path in missing)))
        if tree not in listed_trees and tree not in listed_agents:
            where = "## Agents" if tree in agent_trees else "## Trees"
            found.append(Problem("tree_incomplete", layout.ROOT_INDEX, None,
                                 f"`{tree}` has no line under `{where}`"))
    return found


def agent_tree_incomplete(view: ForestView) -> list[Problem]:
    """Every registered agent with its tree and its line under ``## Agents``, and every such line an agent."""
    found = []
    _trees, listed = root_lines(view)
    agents = {subject_id: subject for subject_id, subject in view.registry.entities.items()
              if subject["type"] == AGENT_TYPE}
    for agent_id, subject in sorted(agents.items()):
        if subject["home"] != layout.tree_page(agent_id) or not view.exists(layout.tree_page(agent_id)):
            found.append(Problem("agent_tree_incomplete", subject["source"], subject["line"],
                                 f"the agent `{agent_id}` has no tree page `{layout.tree_page(agent_id)}`"))
        if agent_id not in listed:
            found.append(Problem("agent_tree_incomplete", layout.ROOT_INDEX, None,
                                 f"the agent `{agent_id}` has no line under `## Agents`"))
    for agent_id, number in sorted(listed.items()):
        if agent_id not in agents:
            found.append(Problem("agent_tree_incomplete", layout.ROOT_INDEX, number,
                                 f"`{agent_id}` is listed under `## Agents` but registered as no agent"))
    return found


def audit(view: ForestView) -> list[Problem]:
    """Every forest-wide structure problem."""
    if (symlinked := symlinks(view.root)):
        return symlinked  # nothing past a symlink is read
    found = [*outside_layout(view.root)]
    for check in (leaf_entity_missing, leaf_entity_unknown, leaf_misplaced, board_unpaired, tree_incomplete,
                  agent_tree_incomplete):
        found.extend(check(view))
    return found
