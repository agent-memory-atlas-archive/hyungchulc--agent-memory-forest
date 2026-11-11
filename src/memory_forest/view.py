"""A read-only view of one forest for the checks: its settings, its registry and its pages.

The lint, the structure checks and the record and number tools read the same
pages; a ``ForestView`` reads each page once, parses it on first use and keeps
the result, so that one run of many checks reads the disk once. Nothing here
writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from pathlib import Path

from . import layout
from . import registry as forest_registry
from .config import Config, load_config
from .errors import MemoryForestError
from .model import Page, parse_page, split_line_ending
from .safety import require_real_root


def page_line_numbers(page: Page) -> dict[str, int]:
    """The 1-based line number of each H2 heading of a parsed page, by its heading text (first wins)."""
    number = 1
    if page.frontmatter is not None:
        number += page.frontmatter.render().count("\n")
    number += len(page.head)
    found: dict[str, int] = {}
    for section in page.sections:
        found.setdefault(section.heading.casefold(), number)
        number += 1 + len(section.lines)
    return found


def body_line_numbers(page: Page, skip: tuple[str, ...] = ()) -> list[tuple[int, str]]:
    """(1-based line number, bare line) of a page's head and sections, outside fenced blocks, the headings
    of its sections and the sections named in ``skip``."""
    skipped = {name.casefold() for name in skip}
    number = 1 + (page.frontmatter.render().count("\n") if page.frontmatter else 0)
    found: list[tuple[int, str]] = []
    fenced = False
    for raw in page.head:
        line = split_line_ending(raw)[0]
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            found.append((number, line))
        number += 1
    for section in page.sections:
        number += 1
        inside = section.heading.casefold() not in skipped
        for raw in section.lines:
            line = split_line_ending(raw)[0]
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            elif inside and not fenced:
                found.append((number, line))
            number += 1
    return found


def section_start(page: Page, name: str) -> int | None:
    """The 1-based line number of the first body line of the named H2 section, or None."""
    heading = page_line_numbers(page).get(name.casefold())
    return None if heading is None else heading + 1


def read_text(path: Path) -> str:
    """A page's text; a byte that is not UTF-8 is kept as a surrogate, so it is written back unchanged."""
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "surrogateescape")


def updated_date(page: Page) -> date | None:
    """A page's ``updated_at`` as a date, or None when it has none that reads as one."""
    text = page.field("updated_at").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class ForestView:
    """One forest as the checks see it."""

    root: Path
    config: Config
    _texts: dict[str, str] = field(default_factory=dict)
    _pages: dict[str, Page] = field(default_factory=dict)

    @classmethod
    def open(cls, root: str | os.PathLike[str], *, config: Config | None = None) -> ForestView:
        safe_root = require_real_root(root)
        return cls(safe_root, config if config is not None else load_config(safe_root))

    @cached_property
    def paths(self) -> list[str]:
        """Every page of the four forest layers, sorted (a symlink raises ``symlink_forbidden``)."""
        return layout.iter_pages(self.root)

    @cached_property
    def registry(self) -> forest_registry.Registry:
        """The registry compiled from the pages now (never the file, which may be older)."""
        return forest_registry.build(self.root, config=self.config)

    def exists(self, path: str) -> bool:
        try:
            return layout.resolve(self.root, path).exists()
        except MemoryForestError:  # an escape or a symlink names nothing in the forest
            return False

    def text(self, path: str) -> str:
        if path not in self._texts:
            self._texts[path] = read_text(layout.resolve(self.root, path, must_exist=True))
        return self._texts[path]

    def page(self, path: str) -> Page:
        if path not in self._pages:
            self._pages[path] = parse_page(self.text(path))
        return self._pages[path]

    def role(self, path: str) -> layout.PageRole:
        return layout.classify(path).role

    def with_role(self, *roles: layout.PageRole) -> list[str]:
        return [path for path in self.paths if self.role(path) in roles]

    @property
    def leaves(self) -> list[str]:
        """The pages of ``04 stm``: leaves and collection pages."""
        return self.with_role(layout.PageRole.LEAF, layout.PageRole.COLLECTION_PAGE)

    @property
    def boards(self) -> list[str]:
        return self.with_role(layout.PageRole.BOARD)

    def page_bytes(self, path: str) -> int:
        return len(self.text(path).encode("utf-8", "surrogateescape"))

    def content_bytes(self, path: str) -> int:
        return len(self.page(path).content_text().encode("utf-8", "surrogateescape"))
