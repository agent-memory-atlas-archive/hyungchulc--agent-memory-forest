"""The checks of ``forest check``: one table, each check a name, a function and a help line.

``forest check`` runs every check of ``FOREST_CHECKS`` in its order, or the
ones it names, and prints one line per problem with its code and path. A
module that adds a check appends one ``NamedCheck`` here. The lint reports the
same codes for the derived lists, the levels and the categories, so a forest
``forest check`` calls clean has no lint hit of them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import agents as forest_agents
from . import collections as forest_collections
from . import levels as forest_levels
from . import maps as forest_maps
from . import validate as forest_validate
from .config import Config
from .errors import MemoryForestError
from .plan import Plan
from .registry import Problem


@dataclass(frozen=True, slots=True)
class NamedCheck:
    """One part of ``forest check``: ``forest check NAME`` runs it alone; ``order`` sets when it runs."""

    name: str
    run: Callable[[Path, Config], list[Problem]]
    help: str
    order: int = 50


def _structure(root: Path, config: Config) -> list[Problem]:
    return forest_validate.check_structure(root, config=config)


def _lists(root: Path, config: Config) -> list[Problem]:
    return [forest_maps.change_problem(change) for change in forest_maps.check(root, config=config)]


def _pointers(root: Path, config: Config) -> list[Problem]:
    return forest_maps.pointer_problems(Plan.open(root, config=config))


def _levels(root: Path, config: Config) -> list[Problem]:
    return forest_levels.due_problems(Plan.open(root, config=config))


def _categories(root: Path, config: Config) -> list[Problem]:
    return forest_collections.too_large(Plan.open(root, config=config))


def _agents(root: Path, config: Config) -> list[Problem]:
    plan = Plan.open(root, config=config)
    return forest_agents.problems(root, config, forest_levels.agent_subjects(plan))


# The one table of `forest check`: a module that adds a check appends one line here.
FOREST_CHECKS: list[NamedCheck] = [
    NamedCheck("structure", _structure, "page roles, frontmatter, sections, links, homes, trees and agents", 10),
    NamedCheck("lists", _lists, "the derived lists (Leaves, Pointers, Branches, Trees, Agents) against the pages", 20),
    NamedCheck("pointers", _pointers, "pointers that name the board's own leaf or no path", 30),
    NamedCheck("agents", _agents, "raw lane folders and declared agents whose names cannot be an agent's id", 40),
    NamedCheck("levels", _levels, "subjects and agents due a level (`forest levels apply`)", 50),
    NamedCheck("categories", _categories, "category branches over `limits.category_max_leaves`", 60),
]


def run_checks(root: Path, config: Config, names: list[str] | None = None) -> tuple[list[NamedCheck], list[Problem]]:
    """The named checks (every check when none is named) in their order, and every problem they find."""
    by_name = {check.name: check for check in FOREST_CHECKS}
    unknown = [name for name in names or () if name not in by_name]
    if unknown:
        raise MemoryForestError("check_unknown", "No such check.", details={"checks": unknown,
                                                                           "known": sorted(by_name)})
    chosen = sorted((by_name[name] for name in dict.fromkeys(names)) if names else FOREST_CHECKS,
                    key=lambda check: check.order)
    problems: list[Problem] = []
    for check in chosen:
        problems.extend(check.run(root, config))
    return chosen, problems
