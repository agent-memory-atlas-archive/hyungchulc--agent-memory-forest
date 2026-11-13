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
from .config import AGENT_TEXT_MAX, Config, KnownAgent
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
    if len(agent_id) > AGENT_TEXT_MAX:
        return f"is longer than {AGENT_TEXT_MAX} characters, so it cannot be a file name"
    if agent_id in set(area_trees):
        return "is the id of a shared tree"
    if agent_id in reserved_ids(config) or agent_id.endswith(registry.COLLECTION_SUFFIX):
        return "is a reserved name (the owner's or the system's id, `none`, or a collection's id)"
    return None


def file_id_ok(agent_id: str) -> bool:
    """Whether ``agent_id`` can be a directory or file name."""
    return layout.is_slug(agent_id) and len(agent_id) <= AGENT_TEXT_MAX


def letters(text: str) -> str:
    """The letters of ``text``, ignoring case, spaces, hyphens and underscores."""
    return "".join(character for character in text.casefold() if character not in " -_")


def find_agent(given: str, rows: Iterable[tuple[str, str]]) -> list[str]:
    """The ids ``given`` names, in the order of ``rows``.

    A match is an id, a name exactly, or the same letters apart from case, spaces,
    hyphens and underscores. Nothing looser. The settings parser, a group
    connection, a command's ``--agent`` and a reader of an app's member list all
    use this. An empty list is no agent. More than one id is ambiguous.
    """
    if not isinstance(given, str):
        return []
    slug = layout.slugify(given)
    folded = letters(given)
    exact: list[str] = []
    folded_hits: list[str] = []
    for agent_id, name in rows:
        if given == agent_id or (name and given == name) or (slug and slug == agent_id):
            if agent_id not in exact:
                exact.append(agent_id)
            continue
        if folded and (folded == letters(agent_id) or (name and folded == letters(name))):
            if agent_id not in folded_hits:
                folded_hits.append(agent_id)
    return exact or folded_hits


# Kinds of taken id that do not come from a table. Subject types come from
# ``subjects.types``; each non-agent type is ``subject:<type>``.
UNTABLED_ID_KINDS: Final[tuple[str, ...]] = (
    "owner-id",
    "owner-name",
    "owner-alias",
    "group",
    "system",
    "reserved",
    "collection-suffix",
    "area-tree",
)


def id_kind_names(config: Config) -> list[str]:
    """Every kind :func:`taken` can report. Subject types are read from the settings table."""
    names = ["agent" if item == registry.AGENT_TYPE else f"subject:{item}" for item in config.subjects.types]
    names.extend(UNTABLED_ID_KINDS)
    return names


def taken(root: str | os.PathLike[str], config: Config) -> dict[str, list[str]]:
    """Each id that is already something, and the kinds of thing it is.

    An id held by an agent is the kind ``agent``. Anything else blocks a new
    agent from taking that id. A kind with no table is one of
    :data:`UNTABLED_ID_KINDS`.
    """
    found: dict[str, list[str]] = {}

    def add(slug: str, kind: str) -> None:
        if not slug or not layout.is_slug(slug):
            return
        bucket = found.setdefault(slug, [])
        if kind not in bucket:
            bucket.append(kind)

    add(config.owner.id, "owner-id")
    add(layout.slugify(config.owner.name), "owner-name")
    for alias in config.owner.aliases:
        add(layout.slugify(alias), "owner-alias")
    if config.subjects.system_id:
        add(config.subjects.system_id, "system")
    add(registry.ABOUT_NONE, "reserved")
    for group in config.agents.groups:
        add(group.id, "group")
        for member in group.members:
            add(member, "agent")
    for known in config.agents.known:
        add(known.id, "agent")
    for agent_id in config.agents.orchestrators:
        add(agent_id, "agent")
    for tool in config.intake.tools:
        if tool.agent:
            add(tool.agent, "agent")
    try:
        for lane in lanes(root):
            add(lane, "agent")
    except OSError:
        pass
    compiled = _registry(root, config)
    if compiled is not None:
        for subject_id, subject in compiled.entities.items():
            kind = str(subject.get("type") or "")
            if kind == registry.AGENT_TYPE:
                add(subject_id, "agent")
            elif kind:
                add(subject_id, f"subject:{kind}")
            if str(subject_id).endswith(registry.COLLECTION_SUFFIX):
                add(subject_id, "collection-suffix")
        for tree, kind in compiled.tree_kinds.items():
            if kind != registry.TREE_KIND_AGENT:
                add(tree, "area-tree")
    return found


def kinds_of(
    slug: str, taken_map: Mapping[str, list[str]] | None = None, *, root: str | os.PathLike[str] | None = None,
    config: Config | None = None,
) -> list[str]:
    """The kinds that hold ``slug``. A collection suffix counts even when nothing is registered under it."""
    if taken_map is None:
        if root is None or config is None:
            return []
        taken_map = taken(root, config)
    found = list(taken_map.get(slug, []))
    if slug.endswith(registry.COLLECTION_SUFFIX) and "collection-suffix" not in found:
        found.append("collection-suffix")
    return found


def free_id(slug: str, taken_map: Mapping[str, list[str]]) -> str:
    """An id for a new agent whose slug is already something that is not an agent.

    The slug, then ``<slug>-agent``, then ``<slug>-agent-<n>``. Empty when none
    of those fit in a file name.
    """
    number = 1
    while number < 1000:
        candidate = f"{slug}-agent" if number == 1 else f"{slug}-agent-{number}"
        if len(candidate) > AGENT_TEXT_MAX:
            return ""
        if not kinds_of(candidate, taken_map):
            return candidate
        number += 1
    return ""


def _registry(root: str | os.PathLike[str], config: Config) -> registry.Registry | None:
    try:
        return registry.build(root, config=config)
    except (MemoryForestError, OSError):
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
    for where, agent_id in config.agents.unplaceable:
        found.append(Problem(
            INVALID, where, None,
            f"`{agent_id}` cannot be placed; it is not an id a file can be named, and the rest of the pass goes on",
        ))
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


def role_text(text: str) -> str:
    """The first paragraph of a tree page's ``## Role``, its whitespace collapsed; empty when there is none."""
    from .model import parse_page

    section = parse_page(text).section("Role")
    words: list[str] = []
    for raw in section.lines if section is not None else []:
        bare = raw.strip()
        if not bare:
            if words:
                break
            continue
        if bare.startswith("#"):
            break
        words.append(bare)
    return " ".join(" ".join(words).split())


def _role_unset(role: str) -> bool:
    from .move import ROLE_UNSET

    return role in ("", ROLE_UNSET)


def _same_role(one: str, other: str) -> bool:
    return " ".join(one.split()).rstrip(".").casefold() == " ".join(other.split()).rstrip(".").casefold()


def to_fill(known: KnownAgent | None, agent_id: str, name: str, role: str) -> dict[str, str]:
    """What the settings entry gives that the agent's tree still leaves unset: ``name`` while the registry
    line's name is the id, ``role`` while ``## Role`` is not set. A value set in the tree is never in it."""
    if known is None:
        return {}
    fills = {}
    if (known.name and registry.normalize_name(name) == registry.normalize_name(agent_id)
            and registry.normalize_name(known.name) != registry.normalize_name(agent_id)):
        fills["name"] = " ".join(known.name.split())
    if known.role and _role_unset(role):
        fills["role"] = " ".join(known.role.split())
    return fills


def settings_differ(config: Config, names: Mapping[str, str], roles: Mapping[str, str]) -> list[tuple[str, str]]:
    """(agent id, what differs) for each declared agent whose tree and settings entry both set a name or a role
    and set it differently. An unset value is not a difference: ``forest levels apply`` fills it. ``names`` and
    ``roles`` are what each agent's registry line and tree page hold."""
    found = []
    for known in config.agents.known:
        if known.id not in names:
            continue
        name = names[known.id]
        if (known.name and registry.normalize_name(name) != registry.normalize_name(known.id)
                and registry.normalize_name(known.name) != registry.normalize_name(name)):
            found.append((known.id, f"the settings name `{known.id}` \"{known.name}\", its registry line "
                                    f"\"{name}\" (`forest move rename {known.id} --name …`, or change the settings)"))
        role = roles.get(known.id, "")
        if known.role and not _role_unset(role) and not _same_role(known.role, role):
            found.append((known.id, f"the settings give `{known.id}` the role \"{' '.join(known.role.split())}\", "
                                    f"its tree page's `## Role` \"{role}\" (edit one of them)"))
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
    roles = {}
    for agent_id in view.registry.agents:
        page = layout.tree_page(agent_id)
        if view.exists(page):
            roles[agent_id] = role_text(view.text(page))
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
    lint.register(lint.LintCode(SETTINGS_DIFFER, lint.UPKEEP, "an agent's tree and its settings entry do not set its "
                                "name or its role to two different values (`forest move rename`, or edit `## Role`)", _lint_settings_differ))


_register_lint()
