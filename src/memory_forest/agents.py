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
from pathlib import PurePath as Path
from typing import Any, Final

from . import layout, registry, system
from . import names as name_rules
from .config import AGENT_TEXT_MAX, Config, KnownAgent
from .errors import MemoryForestError
from .registry import Problem

CONFIG_PATH: Final[str] = "forest.toml"
INVALID: Final[str] = "agent_id_invalid"
SETTING_INVALID: Final[str] = "agent_setting_invalid"
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
    if system.kind(top) != "folder":
        return []
    return [
        entry.name for entry in system.listing(top)
        if not entry.name.startswith(".") and entry.kind == "folder" and _holds_a_file(entry.path)
    ]


def _holds_a_file(folder: Path) -> bool:
    """Whether ``folder`` or a folder under it holds a file (hidden names aside; nothing is followed)."""
    for entry in system.listing(folder):
        if entry.name.startswith("."):
            continue
        if entry.kind == "file" or (entry.kind == "folder" and _holds_a_file(entry.path)):
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
        if kind == "agent":
            found[slug] = ["agent"]
        bucket = found.setdefault(slug, [])
        if bucket == ["agent"]:
            return
        if kind not in bucket:
            bucket.append(kind)

    add(config.owner.id, "owner-id")
    # The owner's name and aliases hold the id a new agent of that name would be offered (``names.make_id``).
    add(name_rules.make_id(config.owner.name), "owner-name")
    for alias in config.owner.aliases:
        add(name_rules.make_id(alias), "owner-alias")
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
        for tree, kind in compiled.tree_kinds.items():
            if kind != registry.TREE_KIND_AGENT:
                add(tree, "area-tree")
    return found


def kinds_of(slug: str, taken_map: Mapping[str, list[str]]) -> list[str]:
    """The kinds that hold ``slug`` (:func:`taken`). A collection suffix counts even when nothing is registered
    under it."""
    found = list(taken_map.get(slug, []))
    if slug.endswith(registry.COLLECTION_SUFFIX):
        found.append("collection-suffix")
    return found


def new_id(name: str, taken_map: Mapping[str, list[str]]) -> str:
    """The id of a new agent named ``name``, made once (``names.new_id``): never an id something holds."""
    return name_rules.new_id(name, lambda candidate: kinds_of(candidate, taken_map))


def _registry(root: str | os.PathLike[str], config: Config) -> registry.Registry | None:
    try:
        return registry.build(root, config=config)
    except MemoryForestError:
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
    ``agent_setting_invalid``: a value of the agents' tables wrong for one
    entry; that entry was left out and the rest of the settings loaded.
    """
    from . import groups

    rows = [(agent_id, name or agent_id) for agent_id, name in groups._agent_rows(root, config)]
    found: list[Problem] = []
    declared = {item.id for item in config.agents.known}
    for where, agent_id in config.agents.unplaceable:
        if (where.startswith("agents.groups.") and where.endswith(".members")
                and len(name_rules.find(name_rules.check(agent_id), rows)) == 1):
            continue
        found.append(Problem(
            INVALID, where, None,
            f"`{agent_id}` cannot be placed; it is not an id a file can be named, and the rest of the pass goes on",
        ))
    for where, why in config.agents.left_out:
        found.append(Problem(SETTING_INVALID, CONFIG_PATH, None, f"`{where}` {why}; everything else loads"))
    for known in config.agents.known:
        reason = why_invalid(known.id, config, subjects.area_trees)
        if name_rules.reserved_id(known.id):
            replacement = new_id(known.name or known.id, taken(root, config))
            found.append(Problem(INVALID, CONFIG_PATH, None,
                                 f"`{known.id}` is a reserved device basename on Windows. Rename the table "
                                 f"[agents.known.{known.id}] to [agents.known.{replacement}] and replace "
                                 f"its id in members, orchestrators and intake agent settings with `{replacement}`; "
                                 "move its lane and tree folders to that id on a system that can hold them."))
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
    for group in config.agents.groups:
        seen_members: set[str] = set()
        for reference in group.member_refs:
            hits = name_rules.find(name_rules.check(reference), rows)
            if len(hits) != 1:
                continue
            agent_id = hits[0]
            where = f"agents.groups.{group.id}.members"
            if agent_id in seen_members and not any(path == where and repr(reference) in why
                                                   for path, why in config.agents.left_out):
                found.append(Problem(SETTING_INVALID, CONFIG_PATH, None,
                                     f"`{where}` lists `{agent_id}` twice ({reference!r}); remove the duplicate reference"))
            seen_members.add(agent_id)
    for index, (left_id, left_name) in enumerate(rows):
        if name_rules.has_invisible(left_name):
            found.append(Problem("agent_name_invisible", CONFIG_PATH, None,
                                 f"notice: `{left_id}` ({left_name}) holds an invisible format character; "
                                 "it remains a distinct name."))
        for right_id, right_name in rows[index + 1:]:
            if (name_rules.invisible(left_name) == name_rules.invisible(right_name)
                    and name_rules.key(left_name) != name_rules.key(right_name)):
                found.append(Problem("agent_name_invisible", CONFIG_PATH, None,
                                     f"notice: `{left_id}` ({left_name}) and `{right_id}` ({right_name}) "
                                     "differ only by invisible format characters; they remain distinct agents."))
            if name_rules.key(left_name) == name_rules.key(right_name):
                found.append(Problem(SETTING_INVALID, CONFIG_PATH, None,
                                     f"`{left_id}` ({left_name}) and `{right_id}` ({right_name}) have one name key. "
                                     f"Change name in [agents.known.{right_id}] to a distinct name, or merge "
                                     "their declarations and membership into one id."))
            if left_name == right_id or right_name == left_id:
                shadow = left_id if left_name == right_id else right_id
                found.append(Problem(SETTING_INVALID, CONFIG_PATH, None,
                                     f"`{left_id}` ({left_name}) and `{right_id}` ({right_name}): a declared name "
                                     f"is another agent's exact id. Change name in [agents.known.{shadow}]; "
                                     "the exact id selects its holder on every group."))
    for group_id, detail in groups.source_problems(root, config):
        found.append(Problem("member_source_failed", CONFIG_PATH, None, f"`{group_id}`: {detail}"))
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
    # A name not given is the id itself (the literal id, not another name of the same key).
    if (known.name and name == agent_id
            and not name_rules.same(known.name, name)):
        fills["name"] = known.name
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
        if (known.name and name != known.id
                and not name_rules.same(known.name, name)):
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
