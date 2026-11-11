"""The subject registry (contract, section 2; design, section 4).

Every subject has one registry line in the ``## Subjects`` section of its home
tree's index. ``build`` reads all of them, the collection entries of every
collection page and the frontmatter of every leaf, checks them, and compiles
``.forest/registry.json``. The pages are the source; the JSON is derived and
the same forest always compiles to the same bytes.

A bad line never stops a build: it is reported as a ``Problem`` with the
contract's code, and the first registration of an id or a name keeps it.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from . import layout
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import atomic_write
from .model import (
    CODE_SPAN_RE,
    EntryLine,
    LineShapeError,
    Page,
    RegistryLine,
    TimelineLine,
    parse_page,
)
from .safety import require_real_root


SCHEMA: Final[str] = "memory-forest-registry/1"
LEVELS: Final[tuple[str, ...]] = ("leaf", "branch", "tree")
ENTRY_LEVEL: Final[str] = "entry"
ALL_LEVELS: Final[tuple[str, ...]] = (ENTRY_LEVEL, *LEVELS)
AGENT_TYPE: Final[str] = "agent"
COLLECTION_TYPE: Final[str] = "collection"
COLLECTION_KIND: Final[str] = "collection"
COLLECTION_SUFFIX: Final[str] = "-seen-once"
ABOUT_NONE: Final[str] = "none"
TREE_KIND_AGENT: Final[str] = "agent"
TREE_KIND_AREA: Final[str] = "area"
SUBJECTS_SECTION: Final[str] = "Subjects"
POINTERS_SECTION: Final[str] = "Pointers"
# The sections of a collection page that are the page's own, not entries.
COLLECTION_OWN_SECTIONS: Final[frozenset[str]] = frozenset({"timeline", "links", "update record", "daily totals"})
LEAF_KINDS: Final[tuple[str, ...]] = (
    "conversation",
    "event",
    "visit",
    "trip",
    "plan",
    "application",
    "purchase",
    "received",
    "notice",
    "brief",
    "watched",
    "read",
    "link",
    "rule",
    "profile",
    "work",
    "collection",
)

CODES: Final[tuple[str, ...]] = (
    "registry_line_invalid",
    "registry_name_masked",
    "registry_duplicate_id",
    "registry_duplicate_alias",
    "registry_home_missing",
    "registry_entry_is_subject",
    "collection_entry_malformed",
)

_LOOSE_RE: Final[re.Pattern[str]] = re.compile(r"[\W_]+")
_STANDING_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]*[-*+] standing:\s*(?P<text>\S.*?)\s*")
_ENTRY_KIND_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]*[-*+] kind:\s*(?P<kind>\S+)\s*")
_SUBHEADING_RE: Final[re.Pattern[str]] = re.compile(r"#{3,6}\s")
# A name found inside a longer query must be at least this long to count.
MIN_CONTAINED_NAME: Final[int] = 2


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    path: str
    line: int | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "line": self.line, "path": self.path}

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{where}: {self.code}: {self.detail}"


# ─────────────────────────────── names ───────────────────────────────


def normalize_name(text: str) -> str:
    """The form names are compared in: NFKC, case-folded, runs of white space made one space."""
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", str(text)).casefold())
    return " ".join(folded.split())


def loose_name(text: str) -> str:
    """``normalize_name`` without punctuation or spaces: two names equal in it are one name."""
    return _LOOSE_RE.sub("", normalize_name(text))


def collection_ids(tree: str, category: str) -> tuple[str, str]:
    """The ids a collection of ``04 stm/<tree>/<category>/`` may carry."""
    return f"{category}{COLLECTION_SUFFIX}", f"{tree}-{category}{COLLECTION_SUFFIX}"


def about_ids(value: str | list[str] | None) -> list[str]:
    """The ids an ``about:`` value names, in order and once each; ``none`` names none."""
    if value is None:
        return []
    parts = value if isinstance(value, list) else str(value).split(",")
    ids: list[str] = []
    for part in parts:
        text = str(part).strip().strip("`").strip()
        if text and text.casefold() != ABOUT_NONE and text not in ids:
            ids.append(text)
    return ids


def about_state(value: str | list[str] | None) -> str:
    """``untagged`` (no ``about:``), ``none`` (read, about no other subject) or ``tagged``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "untagged"
    parts = value if isinstance(value, list) else str(value).split(",")
    words = [str(part).strip().strip("`").strip().casefold() for part in parts if str(part).strip()]
    if words == [ABOUT_NONE]:
        return ABOUT_NONE
    return "tagged" if about_ids(value) else "untagged"


# ─────────────────────────────── reading pages ───────────────────────────────


def _read(root: Path, relative: str) -> str:
    path = layout.resolve(root, relative)
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read()


def _numbered_items(page: Page, section_name: str) -> list[tuple[int, str]]:
    """(1-based line number in the page, line) of each list item of the named H2 sections."""
    number = 1
    if page.frontmatter is not None:
        number += page.frontmatter.render().count("\n")
    number += len(page.head)
    found: list[tuple[int, str]] = []
    for section in page.sections:
        if section.is_named(section_name):
            for index, line in section.items():
                found.append((number + 1 + index, line))
        number += 1 + len(section.lines)
    return found


def subject_lines(root: str | os.PathLike[str]) -> list[tuple[str, str, int, str]]:
    """(tree, index path, line number, line) of every list item under ``## Subjects``."""
    safe_root = require_real_root(root)
    lines: list[tuple[str, str, int, str]] = []
    for tree in layout.trees(safe_root):
        index = layout.tree_index(tree)
        page = parse_page(_read(safe_root, index))
        for number, line in _numbered_items(page, SUBJECTS_SECTION):
            lines.append((tree, index, number, line))
    return lines


@dataclass(frozen=True, slots=True)
class CollectionEntry:
    id: str
    name: str
    other_name: str | None
    type: str
    aliases: tuple[str, ...]
    standing: str
    kind: str | None
    line: int


def parse_collection_page(text: str) -> tuple[list[CollectionEntry], list[tuple[int, str]]]:
    """A collection page's entries and what is malformed in its entry sections.

    Every H2 section but the page's own (Timeline, Links, Update record, Daily
    totals) is one entry: ``## <Name>`` or ``## <Name> (<other script>)``,
    then ``- entry: `<id>` · <type> · aliases: …``, then optional
    ``- standing: …`` and ``- kind: …`` lines before the entry's material.
    """
    page = parse_page(text)
    entries: list[CollectionEntry] = []
    problems: list[tuple[int, str]] = []
    number = 1 + (page.frontmatter.render().count("\n") if page.frontmatter else 0) + len(page.head)
    for section in page.sections:
        heading_number = number
        number += 1 + len(section.lines)
        if section.heading.casefold() in COLLECTION_OWN_SECTIONS:
            continue
        name, _, other = section.heading.partition(" (")
        name = name.strip()
        other_name = other[:-1].strip() if other.endswith(")") else None
        head: list[tuple[int, str]] = []
        for index, raw in enumerate(section.lines):
            bare = raw.rstrip("\r\n")
            if _SUBHEADING_RE.match(bare):
                break
            head.append((heading_number + 1 + index, bare))
        entry_line = next(((n, line) for n, line in head if line.lstrip().startswith(("- entry:", "* entry:"))), None)
        if entry_line is None:
            problems.append((heading_number, f"the entry `{name}` has no `- entry: `<id>` · <type>` line"))
            continue
        try:
            parsed = EntryLine.parse(entry_line[1])
        except LineShapeError as exc:
            problems.append((entry_line[0], str(exc)))
            continue
        standing = next((m["text"] for _n, line in head if (m := _STANDING_RE.fullmatch(line))), "")
        kind = next((m["kind"] for _n, line in head if (m := _ENTRY_KIND_RE.fullmatch(line))), None)
        aliases = list(parsed.aliases)
        if other_name and other_name not in aliases and normalize_name(other_name) != normalize_name(name):
            aliases.insert(0, other_name)
        entries.append(
            CollectionEntry(
                id=parsed.id,
                name=name,
                other_name=other_name,
                type=parsed.type,
                aliases=tuple(aliases),
                standing=standing,
                kind=kind,
                line=heading_number,
            )
        )
    return entries, problems


def collection_timeline_names(text: str) -> list[TimelineLine]:
    """The Timeline lines of a collection page (each opens with its entry's name)."""
    page = parse_page(text)
    section = page.section("Timeline")
    if section is None:
        return []
    return [line for _i, raw in section.items() if (line := TimelineLine.match(raw)) is not None]


# ─────────────────────────────── checks ───────────────────────────────


def home_problem(subject: Mapping[str, Any]) -> str | None:
    """Why a subject's home does not fit its level and the tree its line is in (contract, section 3)."""
    subject_id, tree, level, home = subject["id"], subject["tree"], subject["level"], subject["home"]
    if subject["type"] == COLLECTION_TYPE:
        parts = PurePosixPath(home).parts
        if level != "leaf":
            return f"the collection `{subject_id}` has level `{level}`, not `leaf`"
        if (
            len(parts) == 4
            and parts[:2] == (layout.STM, tree)
            and layout.is_slug(parts[2])
            and layout.is_collection_name(parts[3])
            and subject_id in collection_ids(tree, parts[2])
        ):
            return None
        return (
            f"the collection `{subject_id}` has home `{home}`, not "
            f"`{layout.STM}/{tree}/<category>/seen-once-<period>.md` with id `<category>{COLLECTION_SUFFIX}`"
        )
    if subject["type"] == AGENT_TYPE and level != "tree":
        return f"the agent `{subject_id}` has level `{level}`; an agent is a tree from its first day"
    if level == "tree":
        if tree != subject_id:
            return f"the tree `{subject_id}` is registered in `{tree}`; its line belongs in `{layout.tree_index(subject_id)}`"
        expected = layout.tree_page(subject_id)
    elif level == "branch":
        expected = layout.board(tree, subject_id)
    else:
        parts = PurePosixPath(home).parts
        if (
            len(parts) == 4
            and parts[:2] == (layout.STM, tree)
            and layout.is_slug(parts[2])
            and (parts[3] == f"{subject_id}.md" or parts[3].startswith(f"{subject_id}-"))
        ):
            return None
        expected = f"{layout.STM}/{tree}/<category>/{subject_id}.md"
    return None if home == expected else f"the {level} `{subject_id}` has home `{home}`, not `{expected}`"


def _line_problem(line: RegistryLine, config: Config) -> str | None:
    if not layout.is_slug(line.id):
        return f"the id `{line.id}` is not a slug"
    if line.id == ABOUT_NONE:
        return f"`{ABOUT_NONE}` is the word `about:` uses for a leaf about no other subject, never an id"
    if line.type not in config.subject_types:
        return f"the type `{line.type}` is not one of the forest's subject types"
    if line.level not in LEVELS:
        return f"the level `{line.level}` is not leaf, branch or tree"
    if not layout.is_forest_path(line.home) or not line.home.endswith(".md"):
        return f"the home `{line.home}` is not a forest page"
    return None


def masked_name_problem(names: Iterable[str], config: Config) -> str | None:
    """Why one of a subject's or an entry's names reads as a sign-in value or holds a masked one, or None."""
    from . import credentials  # credentials reads pages through view, which reads the registry

    return credentials.name_problem(names, config=config)


# ─────────────────────────────── build ───────────────────────────────


class _Names:
    """The name claims of a build: exact names and their loose forms, each owned once."""

    def __init__(self) -> None:
        self.exact: dict[str, str] = {}
        self.loose: dict[str, tuple[str, str]] = {}

    def owner(self, name: str) -> str | None:
        key = normalize_name(name)
        if key in self.exact:
            return self.exact[key]
        loose = loose_name(name)
        return self.loose[loose][0] if loose and loose in self.loose else None

    def claim(self, name: str, owner: str) -> str | None:
        """Claim a name for ``owner``; the other owner's id when it is taken."""
        key = normalize_name(name)
        if not key:
            return None
        held = self.exact.get(key)
        if held is not None and held != owner:
            return held
        loose = loose_name(name)
        if loose:
            loose_held = self.loose.get(loose)
            if loose_held is not None and loose_held[0] != owner:
                return loose_held[0]
            self.loose.setdefault(loose, (owner, name))
        self.exact.setdefault(key, owner)
        return None


def _leaf_heads(root: Path) -> dict[str, dict[str, Any]]:
    heads: dict[str, dict[str, Any]] = {}
    for path in layout.iter_pages(root, (layout.STM,)):
        page = parse_page(_read(root, path))
        fields = page.frontmatter.as_dict() if page.frontmatter is not None else {}
        heads[path] = fields
    return heads


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "").strip()


def build(root: str | os.PathLike[str], *, config: Config | None = None) -> Registry:
    """Compile the registry of the forest at ``root`` from its pages."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    problems: list[Problem] = []
    entities: dict[str, dict[str, Any]] = {}
    names = _Names()

    for tree, index, number, raw in subject_lines(safe_root):
        try:
            line = RegistryLine.parse(raw)
        except LineShapeError as exc:
            problems.append(Problem("registry_line_invalid", index, number, f"{exc}: {raw.strip()[:160]}"))
            continue
        reason = _line_problem(line, settings)
        if reason is not None:
            problems.append(Problem("registry_line_invalid", index, number, reason))
            continue
        first = entities.get(line.id)
        if first is not None:
            problems.append(
                Problem(
                    "registry_duplicate_id",
                    index,
                    number,
                    f"`{line.id}` is already registered at {first['source']} line {first['line']}",
                )
            )
            continue
        subject: dict[str, Any] = {
            "id": line.id,
            "name": line.name,
            "type": line.type,
            "level": line.level,
            "home": line.home,
            "aliases": list(line.aliases),
            "status": line.status,
            "tree": tree,
            "source": index,
            "line": number,
        }
        reason = home_problem(subject)
        if reason is not None:
            problems.append(Problem("registry_line_invalid", index, number, reason))
        elif not layout.resolve(safe_root, line.home).is_file():
            problems.append(Problem("registry_home_missing", index, number, f"the home `{line.home}` is not there"))
        entities[line.id] = subject
        reason = masked_name_problem((line.id, line.name, *line.aliases), settings)
        if reason is not None:
            problems.append(Problem("registry_name_masked", index, number, f"`{line.id}`: {reason}"))
        own_names = [line.id, line.name, *line.aliases]
        if line.id == settings.owner.id:
            own_names.extend(settings.owner.names)
        claimed: set[str] = set()
        for name in own_names:
            key = normalize_name(name)
            if not key or key in claimed:
                continue
            claimed.add(key)
            holder = names.claim(name, line.id)
            if holder is not None:
                problems.append(
                    Problem(
                        "registry_duplicate_alias",
                        index,
                        number,
                        f"`{name}` of `{line.id}` already names `{holder}`",
                    )
                )

    heads = _leaf_heads(safe_root)
    leaves: dict[str, list[str]] = {}
    for path, fields in heads.items():
        entity = _text(fields.get("entity"))
        if entity:
            leaves.setdefault(entity, []).append(path)

    entry_index: dict[str, dict[str, str]] = {}
    for subject_id, subject in entities.items():
        subject["leaves"] = sorted(leaves.get(subject_id, []))
        subject["standing"] = next(
            (_text(heads[path].get("standing")) for path in subject["leaves"] if _text(heads[path].get("standing"))),
            "",
        )
        subject["pointers"] = _board_pointers(safe_root, subject)
        if subject["type"] != COLLECTION_TYPE:
            continue
        subject["entries"] = []
        for page_path in subject["leaves"]:
            if _text(heads[page_path].get("kind")) != COLLECTION_KIND:
                continue
            found, malformed = parse_collection_page(_read(safe_root, page_path))
            for number, detail in malformed:
                problems.append(Problem("collection_entry_malformed", page_path, number, detail))
            for entry in found:
                if entry.type not in settings.subject_types or entry.type == COLLECTION_TYPE:
                    problems.append(
                        Problem(
                            "collection_entry_malformed",
                            page_path,
                            entry.line,
                            f"the entry `{entry.id}` has type `{entry.type}`, which no subject can have",
                        )
                    )
                    continue
                if not layout.is_slug(entry.id):
                    problems.append(
                        Problem("collection_entry_malformed", page_path, entry.line, f"the entry id `{entry.id}` is not a slug")
                    )
                    continue
                reason = masked_name_problem((entry.id, entry.name, *entry.aliases), settings)
                if reason is not None:
                    problems.append(Problem("registry_name_masked", page_path, entry.line, f"entry `{entry.id}`: {reason}"))
                claimed: set[str] = set()
                for name in (entry.id, entry.name, *entry.aliases):
                    key = normalize_name(name)
                    if not key or key in claimed:
                        continue
                    claimed.add(key)
                    other = entry_index.get(key)
                    if other is not None:
                        problems.append(
                            Problem(
                                "registry_duplicate_alias",
                                page_path,
                                entry.line,
                                f"`{name}` of entry `{entry.id}` already names entry `{other['entry']}` "
                                f"of `{other['collection']}`",
                            )
                        )
                        continue
                    holder = names.owner(name)
                    if holder is not None:
                        problems.append(
                            Problem(
                                "registry_entry_is_subject",
                                page_path,
                                entry.line,
                                f"`{name}` of entry `{entry.id}` already names the subject `{holder}`",
                            )
                        )
                        continue
                    entry_index[key] = {"collection": subject_id, "entry": entry.id, "page": page_path}
                subject["entries"].append(
                    {
                        "id": entry.id,
                        "name": entry.name,
                        "type": entry.type,
                        "aliases": list(entry.aliases),
                        "page": page_path,
                        "line": entry.line,
                        "standing": entry.standing,
                        "kind": entry.kind,
                    }
                )
        subject["entries"].sort(key=lambda item: (item["page"], item["line"]))

    entry_ids = {item["id"] for subject in entities.values() for item in subject.get("entries", ())}
    known = set(entities) | entry_ids
    excluded = settings.about_excluded_ids
    about: dict[str, set[str]] = {}
    for path, fields in heads.items():
        own = _text(fields.get("entity"))
        for subject_id in about_ids(fields.get("about"))[: settings.limits.about_max_ids]:
            if subject_id != own and subject_id not in excluded and subject_id in known:
                about.setdefault(subject_id, set()).add(path)
    about_index = {subject_id: sorted(paths) for subject_id, paths in sorted(about.items())}
    for subject_id, subject in entities.items():
        subject["about_leaves"] = about_index.get(subject_id, [])
        for item in subject.get("entries", ()):
            item["about_leaves"] = about_index.get(item["id"], [])

    alias_index = dict(sorted(names.exact.items()))
    for key, record in entry_index.items():
        alias_index.setdefault(key, record["collection"])
    agents = sorted(subject_id for subject_id, subject in entities.items() if subject["type"] == AGENT_TYPE)
    agent_pages = {subject["home"] for subject in entities.values() if subject["type"] == AGENT_TYPE}
    tree_kinds = {
        tree: TREE_KIND_AGENT if layout.tree_page(tree) in agent_pages else TREE_KIND_AREA
        for tree in layout.trees(safe_root)
    }
    problems.sort(key=lambda item: (item.path, item.line or 0, item.code, item.detail))
    data = {
        "schema": SCHEMA,
        "entities": {subject_id: entities[subject_id] for subject_id in sorted(entities)},
        "alias_index": dict(sorted(alias_index.items())),
        "about_index": about_index,
        "entry_index": dict(sorted(entry_index.items())),
        "agents": agents,
        "tree_kinds": tree_kinds,
        "problems": [problem.as_dict() for problem in problems],
    }
    return Registry(data)


def _board_pointers(root: Path, subject: Mapping[str, Any]) -> list[str]:
    """The existing pages that the subject's board (or tree pages) list under ``## Pointers``."""
    if subject["level"] == "branch":
        boards = [subject["home"]]
    elif subject["level"] == "tree":
        boards = [subject["home"], layout.tree_index(subject["id"])]
    else:
        return []
    pointers: list[str] = []
    for board in boards:
        path = layout.resolve(root, board)
        if not path.is_file():
            continue
        page = parse_page(_read(root, board))
        for _number, line in _numbered_items(page, POINTERS_SECTION):
            found = CODE_SPAN_RE.search(line)
            if found is None:
                continue
            target = found["text"].strip()
            if target not in pointers and layout.is_forest_path(target) and layout.resolve(root, target).is_file():
                pointers.append(target)
    return pointers


# ─────────────────────────────── the compiled registry ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Match:
    """A subject or a collection entry found by name."""

    id: str
    name: str
    type: str
    level: str
    home: str
    tree: str
    status: str
    match: str  # exact | substring
    matched: str
    collection: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "home": self.home,
            "id": self.id,
            "level": self.level,
            "match": self.match,
            "matched": self.matched,
            "name": self.name,
            "status": self.status,
            "tree": self.tree,
            "type": self.type,
        }


class Registry:
    """The compiled registry, built from the pages or loaded from ``.forest/registry.json``."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        if data.get("schema") != SCHEMA:
            raise MemoryForestError(
                "registry_invalid",
                "The registry file is not a memory-forest registry of this version; rebuild it.",
                details={"schema": data.get("schema")},
            )
        self.data = dict(data)

    # The compiled maps.
    @property
    def entities(self) -> dict[str, dict[str, Any]]:
        return self.data["entities"]

    @property
    def alias_index(self) -> dict[str, str]:
        return self.data["alias_index"]

    @property
    def about_index(self) -> dict[str, list[str]]:
        return self.data["about_index"]

    @property
    def entry_index(self) -> dict[str, dict[str, str]]:
        return self.data["entry_index"]

    @property
    def agents(self) -> list[str]:
        return self.data["agents"]

    @property
    def tree_kinds(self) -> dict[str, str]:
        return self.data["tree_kinds"]

    @property
    def problems(self) -> list[Problem]:
        return [
            Problem(item["code"], item["path"], item["line"], item["detail"]) for item in self.data["problems"]
        ]

    def to_bytes(self) -> bytes:
        """The registry's JSON: sorted keys, two-space indent, UTF-8, one trailing newline."""
        return (json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    # Lookups.
    def subject(self, subject_id: str) -> dict[str, Any] | None:
        return self.entities.get(subject_id)

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        """A collection entry by its id, with its ``collection`` added."""
        for subject_id, subject in self.entities.items():
            for item in subject.get("entries", ()):
                if item["id"] == entry_id:
                    return {**item, "collection": subject_id}
        return None

    def tree_of(self, subject_id: str) -> str | None:
        subject = self.subject(subject_id)
        if subject is not None:
            return subject["tree"]
        entry = self.entry(subject_id)
        return self.entities[entry["collection"]]["tree"] if entry is not None else None

    def tree_kind(self, tree: str) -> str | None:
        return self.tree_kinds.get(tree)

    def is_agent_tree(self, tree: str) -> bool:
        return self.tree_kinds.get(tree) == TREE_KIND_AGENT

    def agent_trees(self) -> list[str]:
        return sorted(tree for tree, kind in self.tree_kinds.items() if kind == TREE_KIND_AGENT)

    def lookup(self, name: str) -> dict[str, Any] | None:
        """The subject or entry one of whose names is exactly ``name`` (after folding), or None."""
        key = normalize_name(name)
        record = self.entry_index.get(key)
        if record is not None:
            return self.entry(record["entry"])
        subject_id = self.alias_index.get(key)
        return self.subject(subject_id) if subject_id is not None else None

    def find(self, name: str) -> list[Match]:
        """Subjects and entries by name: exact names first (subjects before entries), then names
        that contain the query or that the query contains, closest first."""
        query = normalize_name(name)
        if not query:
            return []
        tiers: dict[str, list[tuple[float, str, Match]]] = {
            "exact": [],
            "entry-exact": [],
            "substring": [],
            "entry-substring": [],
        }
        for subject_id, subject in self.entities.items():
            found = _closeness(query, (subject_id, subject["name"], *subject["aliases"]))
            if found is not None:
                kind, matched, closeness = found
                tiers[kind].append(
                    (
                        -closeness,
                        subject_id,
                        Match(
                            subject_id,
                            subject["name"],
                            subject["type"],
                            subject["level"],
                            subject["home"],
                            subject["tree"],
                            subject["status"],
                            kind,
                            matched,
                        ),
                    )
                )
            for item in subject.get("entries", ()):
                found = _closeness(query, (item["id"], item["name"], *item["aliases"]))
                if found is None:
                    continue
                kind, matched, closeness = found
                tiers[f"entry-{kind}"].append(
                    (
                        -closeness,
                        item["id"],
                        Match(
                            item["id"],
                            item["name"],
                            item["type"],
                            ENTRY_LEVEL,
                            item["page"],
                            subject["tree"],
                            subject["status"],
                            kind,
                            matched,
                            collection=subject_id,
                        ),
                    )
                )
        ordered: list[Match] = []
        for tier in ("exact", "entry-exact", "substring", "entry-substring"):
            ordered.extend(match for _c, _i, match in sorted(tiers[tier], key=lambda item: item[:2]))
        return ordered

    def subject_for_path(self, path: str) -> dict[str, Any] | None:
        """The subject a forest path belongs to: its home, one of its leaves, its folder or its index."""
        target = layout.normalize(path)
        page, _, anchor = target.partition("#")
        if anchor:
            entry = self.entry(anchor)
            if entry is not None and entry["page"] == page:
                return entry
        for subject in self.entities.values():
            if page == subject["home"] or page in subject.get("leaves", ()):
                return subject
        info = layout.classify(page)
        if info.tree and info.branch and info.branch in self.entities:
            candidate = self.entities[info.branch]
            if candidate["level"] == "branch" and candidate["tree"] == info.tree:
                return candidate
        if info.tree and info.tree in self.entities and self.entities[info.tree]["level"] == "tree":
            if info.role in (layout.PageRole.TREE_PAGE, layout.PageRole.TREE_INDEX):
                return self.entities[info.tree]
        return None

    def names_of(self, subject_id: str) -> list[str]:
        """Every written name of a subject or entry: id, name and aliases."""
        subject = self.subject(subject_id) or self.entry(subject_id)
        if subject is None:
            return []
        return [subject_id, subject["name"], *subject["aliases"]]


def _closeness(query: str, names: Iterable[str]) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    for name in names:
        key = normalize_name(name)
        if not key:
            continue
        if key == query:
            return "exact", name, 1.0
        if query in key or (len(key) >= MIN_CONTAINED_NAME and key in query):
            closeness = min(len(key), len(query)) / max(len(key), len(query))
            if best is None or closeness > best[2]:
                best = ("substring", name, closeness)
    return best


# ─────────────────────────────── the file ───────────────────────────────


def write(root: str | os.PathLike[str], registry: Registry) -> Path:
    """Write ``.forest/registry.json`` whole."""
    safe_root = require_real_root(root)
    layout.ensure_folder(safe_root, layout.STATE_DIR)
    path = layout.registry_path(safe_root)
    atomic_write(path, registry.to_bytes())
    return path


def build_and_write(root: str | os.PathLike[str], *, config: Config | None = None) -> Registry:
    registry = build(root, config=config)
    write(root, registry)
    return registry


def load(root: str | os.PathLike[str], *, build_if_missing: bool = True) -> Registry:
    """The registry of ``.forest/registry.json``; compiled from the pages when there is none yet."""
    safe_root = require_real_root(root)
    path = layout.registry_path(safe_root)
    if not path.is_file():
        if build_if_missing:
            return build(safe_root)
        raise MemoryForestError("registry_missing", "The forest has no compiled registry; run `forest registry build`.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise MemoryForestError("registry_invalid", "The registry file is not JSON; rebuild it.") from exc
    if not isinstance(data, dict):
        raise MemoryForestError("registry_invalid", "The registry file is not a JSON object; rebuild it.")
    return Registry(data)


def check(root: str | os.PathLike[str], *, config: Config | None = None) -> list[Problem]:
    """Every registry problem of the forest's pages (nothing is written)."""
    return build(root, config=config).problems
