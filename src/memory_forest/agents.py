"""Which agents the forest has, and which trees each reader reads (contract, section 3, Agent trees).

An agent exists for the forest from either of two facts, and nothing has to be
run for it:

- its entry in the settings, ``[agents.known.<id>]`` with an optional ``name``
  and ``role``;
- the first file in its raw lane ``06 istm/<id>/`` (unless
  ``agents.auto_create`` is false).

``due`` names every such agent that has no registry line yet; ``forest levels
check`` reports each as due a tree and ``forest levels apply`` makes it with
``move.ensure_agent``, the operation ``new-agent`` uses. A lane folder or a
declared id that cannot be an agent's id (not a slug, a shared tree's id, a
reserved name) never becomes a tree: ``problems`` reports it as
``agent_id_invalid``.

``readable_trees`` is the reader scope: the trees one reader may read.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from . import layout, registry
from .config import Config, KnownAgent
from .registry import Problem

CONFIG_PATH: Final[str] = "forest.toml"
INVALID: Final[str] = "agent_id_invalid"
WITHOUT_TREE: Final[str] = "agent_lane_without_tree"
SETTINGS_DIFFER: Final[str] = "agent_settings_differ"


@dataclass(frozen=True, slots=True)
class Candidate:
    """An agent the settings or a raw lane name: its id, the name and role the settings give, where it was seen."""

    id: str
    name: str
    role: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "source": self.source}


def lanes(root: str | os.PathLike[str]) -> list[str]:
    """The folders of ``06 istm/`` that hold at least one file (hidden names aside), sorted."""
    top = Path(root) / layout.ISTM
    if not top.is_dir() or top.is_symlink():
        return []
    found = []
    for entry in sorted(os.scandir(top), key=lambda item: item.name):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        if _holds_a_file(Path(entry.path)):
            found.append(entry.name)
    return found


def _holds_a_file(folder: Path) -> bool:
    for current, folders, files in os.walk(folder, followlinks=False):
        folders[:] = [name for name in folders if not name.startswith(".")]
        if any(not name.startswith(".") and (Path(current) / name).is_file() for name in files):
            return True
    return False


def reserved_ids(config: Config) -> frozenset[str]:
    """Ids no agent takes: the owner's, the system's, and ``none``."""
    return frozenset(value for value in (config.owner.id, config.subjects.system_id, registry.ABOUT_NONE) if value)


def why_invalid(agent_id: str, config: Config, area_trees: Iterable[str]) -> str | None:
    """Why a lane folder's or a declared agent's name cannot be an agent's id, or None."""
    if not layout.is_slug(agent_id):
        return "is not a slug ([a-z0-9][a-z0-9-]*)"
    if agent_id in set(area_trees):
        return "is the id of a shared tree"
    if agent_id in reserved_ids(config) or agent_id.endswith(registry.COLLECTION_SUFFIX):
        return "is a reserved name (the owner's or the system's id, `none`, or a collection's id)"
    return None


def candidates(root: str | os.PathLike[str], config: Config) -> list[Candidate]:
    """Every agent the settings declare, then every agent a raw lane names (when ``auto_create``), each once."""
    found: dict[str, Candidate] = {}
    for known in config.agents.known:
        found[known.id] = Candidate(known.id, known.name, known.role, CONFIG_PATH)
    if config.agents.auto_create:
        for lane in lanes(root):
            found.setdefault(lane, Candidate(lane, "", "", f"{layout.raw_lane(lane)}/"))
    return [found[key] for key in sorted(found)]


@dataclass(frozen=True, slots=True)
class Subjects:
    """What ``due`` and ``problems`` need of the registry: each id's type, and which trees are areas."""

    types: Mapping[str, str]
    area_trees: frozenset[str]


def due(root: str | os.PathLike[str], config: Config, subjects: Subjects) -> list[Candidate]:
    """Every declared or laned agent that has no registry line and can have a tree."""
    return [
        item for item in candidates(root, config)
        if item.id not in subjects.types and why_invalid(item.id, config, subjects.area_trees) is None
    ]


def problems(root: str | os.PathLike[str], config: Config, subjects: Subjects) -> list[Problem]:
    """The ``forest check`` problems of the agents' names and lanes.

    ``agent_id_invalid``: a lane folder or a declared id that cannot be an
    agent's id, or a declared agent registered as a subject of another type.
    ``agent_lane_without_tree``: with ``agents.auto_create`` false, a lane
    whose agent is neither declared nor registered.
    """
    found: list[Problem] = []
    declared = {item.id for item in config.agents.known}
    for known in config.agents.known:
        reason = why_invalid(known.id, config, subjects.area_trees)
        kind = subjects.types.get(known.id)
        if reason is None and kind is not None and kind != registry.AGENT_TYPE:
            reason = f"is registered as a `{kind}`, not an agent (`forest move set-type {known.id} agent`)"
        if reason is not None:
            found.append(Problem(INVALID, CONFIG_PATH, None, f"`[agents.known.{known.id}]`: `{known.id}` {reason}"))
    for lane in lanes(root):
        reason = why_invalid(lane, config, subjects.area_trees)
        path = f"{layout.raw_lane(lane)}/"
        if reason is not None:
            found.append(Problem(INVALID, path, None, f"the lane folder `{lane}` {reason}; it never becomes a tree"))
        elif not config.agents.auto_create and lane not in declared and lane not in subjects.types:
            found.append(Problem(WITHOUT_TREE, path, None,
                                 f"the lane of `{lane}` has no agent: `agents.auto_create` is false; declare it "
                                 f"under `[agents.known.{lane}]` or make it with `forest move new-agent {lane}`"))
    return found


def settings_differ(config: Config, names: Mapping[str, str], roles: Mapping[str, str]) -> list[tuple[str, str]]:
    """(agent id, what differs) for each declared agent whose tree shows another name, or no role where the
    settings give one. ``names`` and ``roles`` are what each agent's registry line and tree page hold."""
    from .move import ROLE_UNSET

    found = []
    for known in config.agents.known:
        if known.id not in names:
            continue
        if known.name and registry.normalize_name(known.name) != registry.normalize_name(names[known.id]):
            found.append((known.id, f"the settings name `{known.id}` \"{known.name}\", its registry line "
                                    f"\"{names[known.id]}\" (`forest move rename {known.id} --name …`)"))
        if known.role and roles.get(known.id, "") in ("", ROLE_UNSET):
            found.append((known.id, f"the settings give `{known.id}` a role and its tree page's `## Role` is not set"))
    return found


def known_agent(config: Config, agent_id: str) -> KnownAgent | None:
    return config.agents.declared(agent_id)


# ─────────────────────────────── who reads what ───────────────────────────────


def readable_trees(config: Config, registry_: registry.Registry, reader_id: str | None, *,
                   named: Iterable[str] = ()) -> list[str]:
    """The trees a reader may read (contract, section 3, Agent trees, Who reads what), sorted.

    An orchestrator (``agents.orchestrators``) reads every tree. Any other
    agent reads the shared trees and its own tree, and the other agents' trees
    when ``agents.default_scope`` is ``all`` or its task names them
    (``named``). A reader that is no agent of the forest reads the shared trees
    only. The reader's id comes from its connection, never from a question.
    """
    kinds = registry_.tree_kinds
    every = sorted(kinds)
    shared = [tree for tree in every if kinds[tree] != registry.TREE_KIND_AGENT]
    agent_trees = {tree for tree in every if kinds[tree] == registry.TREE_KIND_AGENT}
    if reader_id and config.is_orchestrator(reader_id):
        return every
    if not reader_id or reader_id not in agent_trees:
        return shared
    if config.agents.default_scope == "all":
        return every
    wanted = {reader_id, *(value for value in named if value in agent_trees)}
    return sorted([*shared, *wanted])


# ─────────────────────────────── the lint codes ───────────────────────────────


def _view_subjects(view: Any) -> Subjects:
    entities = view.registry.entities
    areas = frozenset(tree for tree, kind in view.registry.tree_kinds.items() if kind != registry.TREE_KIND_AGENT)
    return Subjects({subject_id: subject["type"] for subject_id, subject in entities.items()}, areas)


def _lint_without_tree(view: Any) -> list[Any]:
    from . import lint

    return lint.hits_from(problem for problem in problems(view.root, view.config, _view_subjects(view))
                          if problem.code == WITHOUT_TREE)


def tree_roles(view: Any) -> dict[str, str]:
    """Each registered agent's ``## Role`` text as its tree page holds it."""
    from .plan import first_prose

    roles = {}
    for agent_id in view.registry.agents:
        page = layout.tree_page(agent_id)
        if view.exists(page):
            roles[agent_id] = first_prose(view.text(page), "Role") or ""
    return roles


def _lint_settings_differ(view: Any) -> list[Any]:
    from . import lint

    names = {agent_id: view.registry.entities[agent_id]["name"] for agent_id in view.registry.agents}
    return lint.hits_from(Problem(SETTINGS_DIFFER, layout.tree_index(agent_id), None, what)
                          for agent_id, what in settings_differ(view.config, names, tree_roles(view)))


def _register_lint() -> None:
    from . import lint

    lint.register(lint.LintCode(WITHOUT_TREE, lint.UPKEEP, "every agent's lane has its agent: declare it under "
                                "`[agents.known.<id>]` (with `agents.auto_create` false)", _lint_without_tree))
    lint.register(lint.LintCode(SETTINGS_DIFFER, lint.UPKEEP, "an agent's tree shows the name and role its settings "
                                "entry gives (`forest move rename`, or edit `## Role`)", _lint_settings_differ))


_register_lint()
