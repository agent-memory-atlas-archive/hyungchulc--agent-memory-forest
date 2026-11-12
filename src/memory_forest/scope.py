"""Which trees and raw lanes a reader may see (design, section 6).

The trees come from ``agents.readable_trees``. A raw lane is the agent's own,
and an orchestrator reads every lane. The daily record and the root index are
the shared map, so every reader sees them. A tool argument never supplies the
reader: the command or the connection does.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

from . import layout
from .agents import readable_trees
from .config import Config
from .layout import PageRole, PathInfo
from .registry import Registry


ALL_LANES = "*"


@dataclass(frozen=True, slots=True)
class Scope:
    """The trees and raw lanes one reader may see."""

    reader: str | None
    trees: tuple[str, ...]
    lanes: tuple[str, ...]

    def sees_tree(self, tree: str | None) -> bool:
        return not tree or tree in self.trees

    def sees_lane(self, agent: str | None) -> bool:
        if not agent:
            return False
        return ALL_LANES in self.lanes or agent in self.lanes

    def allows(self, info: PathInfo) -> bool:
        """Whether a path of this role is inside the reader's scope."""
        if info.role is PageRole.RAW_FILE:
            return self.sees_lane(info.tree)
        if info.role in (PageRole.DAILY_PAGE, PageRole.ROOT_INDEX):
            return True
        if info.role in (PageRole.UNKNOWN, PageRole.STATE, PageRole.SETTINGS):
            return False
        return self.sees_tree(info.tree)


def for_reader(
    config: Config,
    registry: Registry,
    reader: str | None,
    *,
    named: Iterable[str] = (),
    root: str | os.PathLike[str] | None = None,
) -> Scope:
    """The scope of ``reader``. ``named`` is the agents a task names, not a question.

    An orchestrator reads every raw lane. Any other agent reads its own lane,
    including before its tree exists, when the lane folder or the settings
    already name it. A reader that is no agent reads no raw lane.
    """
    trees = tuple(readable_trees(config, registry, reader, named=named))
    if reader and config.is_orchestrator(reader):
        lanes: tuple[str, ...] = (ALL_LANES,)
    elif reader and _is_agent(config, registry, reader, root):
        lanes = (reader,)
    else:
        lanes = ()
    return Scope(reader, trees, lanes)


def _is_agent(config: Config, registry: Registry, reader: str, root: str | os.PathLike[str] | None) -> bool:
    if reader in registry.agent_trees() or config.agents.declared(reader) is not None:
        return True
    if root is None:
        return False
    folder = layout.resolve(root, f"{layout.ISTM}/{reader}")
    return folder.is_dir() and not folder.is_symlink()
