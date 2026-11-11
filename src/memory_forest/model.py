from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Final

from .errors import MemoryForestError


SCHEMA_VERSION: Final[int] = 1
LAYERS: Final[tuple[tuple[int, str], ...]] = (
    (0, "life_archive"),
    (1, "xltm"),
    (2, "ltm"),
    (3, "mtm"),
    (4, "stm"),
    (5, "daily"),
    (6, "istm"),
)
LAYER_DIRECTORY_NAMES: Final[tuple[str, ...]] = tuple(
    f"{number:02d} {name}" for number, name in LAYERS
)
LAYER_BY_NUMBER: Final[dict[int, str]] = dict(LAYERS)

_LAYER_RE = re.compile(
    r"^(?P<number>0[0-6])[ _-]+"
    r"(?P<name>life_archive|xltm|ltm|mtm|stm|daily|istm)$",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class Layer:
    number: int
    name: str
    directory: str

    def as_dict(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "name": self.name,
            "number": self.number,
        }


@dataclass(frozen=True, slots=True)
class Route:
    layer: Layer
    domain: str | None
    branch: str | None
    leaf: str
    path: str

    @property
    def route_key(self) -> str:
        if self.layer.name == "xltm":
            return "xltm"
        values = [self.layer.name]
        if self.domain:
            values.append(self.domain)
        if self.branch:
            values.append(self.branch)
        if not self.branch or self.leaf != self.branch:
            values.append(self.leaf)
        return "/".join(values)

    def as_dict(self) -> dict[str, object]:
        return {
            "branch": self.branch,
            "domain": self.domain,
            "layer": self.layer.as_dict(),
            "leaf": self.leaf,
            "path": self.path,
            "route_key": self.route_key,
        }


def parse_layer(segment: str) -> Layer:
    match = _LAYER_RE.fullmatch(segment)
    if match is None:
        raise MemoryForestError(
            "unknown_layer",
            "The first path segment is not a supported Memory Forest layer.",
            details={"segment": segment},
        )
    number = int(match.group("number"))
    name = match.group("name").lower()
    expected_name = LAYER_BY_NUMBER[number]
    if name != expected_name:
        raise MemoryForestError(
            "layer_number_mismatch",
            "The layer number and name do not match.",
            details={"expected": expected_name, "number": number, "received": name},
        )
    return Layer(number=number, name=name, directory=f"{number:02d} {name}")


def validate_segment(value: str, *, field: str) -> str:
    if not value or value in {".", ".."}:
        raise MemoryForestError(
            "invalid_route_segment",
            "Route segments must be non-empty and may not be dot segments.",
            details={"field": field, "value": value},
        )
    if len(value) > 200 or _CONTROL_RE.search(value) or "/" in value or "\\" in value:
        raise MemoryForestError(
            "invalid_route_segment",
            "A route segment contains a forbidden character or is too long.",
            details={"field": field, "value": value},
        )
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise MemoryForestError(
            "noncanonical_route_segment",
            "Route segments must use NFC Unicode normalization.",
            details={"field": field, "value": value},
        )
    return value


def parse_relative_route(relative: str | PurePosixPath | Path) -> Route:
    raw = relative.as_posix() if isinstance(relative, Path) else str(relative)
    if "\\" in raw:
        raise MemoryForestError(
            "invalid_document_path",
            "Document paths must use forward slashes.",
            details={"path": raw},
        )
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise MemoryForestError(
            "path_escape",
            "A document path must stay inside the selected forest root.",
            details={"path": raw},
        )
    parts = pure.parts
    if len(parts) < 2:
        raise MemoryForestError(
            "invalid_document_path",
            "A document path must include a layer directory and memory file.",
            details={"path": raw},
        )
    layer = parse_layer(parts[0])
    if parts[0] != layer.directory:
        raise MemoryForestError(
            "noncanonical_layer_directory",
            "Layer directories must use the canonical space-separated name.",
            details={"expected": layer.directory, "received": parts[0]},
        )
    suffix = pure.suffix
    if layer.name == "istm":
        if len(parts) != 2 or suffix != ".jsonl":
            raise MemoryForestError(
                "invalid_istm_path",
                "ISTM event streams must be root-level .jsonl files.",
                details={"path": raw},
            )
        leaf = validate_segment(pure.stem, field="leaf")
        return Route(layer, None, None, leaf, pure.as_posix())
    if suffix != ".md":
        raise MemoryForestError(
            "unsupported_file_type",
            "Canonical memory documents must use .md, except ISTM .jsonl streams.",
            details={"path": raw},
        )
    leaf = validate_segment(pure.stem, field="leaf")
    if layer.name == "xltm":
        if parts != (layer.directory, "XLTM.md"):
            raise MemoryForestError(
                "invalid_xltm_path",
                "XLTM must use the canonical root path 01 xltm/XLTM.md.",
                details={"path": raw},
            )
        return Route(layer, None, None, leaf, pure.as_posix())
    if layer.name == "ltm":
        if len(parts) != 2 or not pure.stem.endswith("_LTM"):
            raise MemoryForestError(
                "invalid_ltm_path",
                "LTM domains must use 02 ltm/<domain>_LTM.md.",
                details={"path": raw},
            )
        domain = validate_segment(pure.stem.removesuffix("_LTM"), field="domain")
        return Route(layer, domain, None, leaf, pure.as_posix())
    if layer.name == "mtm":
        if len(parts) != 3:
            raise MemoryForestError(
                "invalid_mtm_path",
                "MTM branches must use 03 mtm/<domain>/<branch>.md.",
                details={"path": raw},
            )
        domain = validate_segment(parts[1], field="domain")
        return Route(layer, domain, leaf, leaf, pure.as_posix())
    if layer.name == "stm":
        if len(parts) != 4:
            raise MemoryForestError(
                "invalid_stm_path",
                "STM leaves must use 04 stm/<domain>/<branch>/<leaf>.md.",
                details={"path": raw},
            )
        domain = validate_segment(parts[1], field="domain")
        branch = validate_segment(parts[2], field="branch")
        return Route(layer, domain, branch, leaf, pure.as_posix())
    if layer.name == "daily":
        if len(parts) != 2:
            raise MemoryForestError(
                "invalid_daily_path",
                "Daily sources must be root-level ISO-date Markdown files.",
                details={"path": raw},
            )
        try:
            parsed = date.fromisoformat(leaf)
        except ValueError as exc:
            raise MemoryForestError(
                "invalid_daily_date",
                "Daily filenames must use a valid YYYY-MM-DD date.",
                details={"path": raw},
            ) from exc
        if parsed.isoformat() != leaf:
            raise MemoryForestError(
                "invalid_daily_date",
                "Daily filenames must use a valid YYYY-MM-DD date.",
                details={"path": raw},
            )
        return Route(layer, None, None, leaf, pure.as_posix())
    if layer.name == "life_archive":
        route_parts = parts[1:-1]
        if len(route_parts) > 2:
            raise MemoryForestError(
                "invalid_life_archive_path",
                "Life Archive records may use at most domain/branch/leaf depth.",
                details={"path": raw},
            )
        domain = validate_segment(route_parts[0], field="domain") if route_parts else None
        branch = (
            validate_segment(route_parts[1], field="branch")
            if len(route_parts) == 2
            else None
        )
        return Route(layer, domain, branch, leaf, pure.as_posix())
    raise MemoryForestError(
        "unsupported_layer",
        "The memory layer is not supported by this schema.",
        details={"layer": layer.name},
    )


def layers_are_adjacent(source: Layer, target: Layer) -> bool:
    return abs(source.number - target.number) == 1


def immediate_parent_path(route: Route) -> str | None:
    if route.layer.name == "ltm":
        return "01 xltm/XLTM.md"
    if route.layer.name == "mtm" and route.domain:
        return f"02 ltm/{route.domain}_LTM.md"
    if route.layer.name == "stm" and route.domain and route.branch:
        return f"03 mtm/{route.domain}/{route.branch}.md"
    return None


# ═══════════════════════════════ version 1 pages ═══════════════════════════════
#
# A page is parsed into the pieces the tools change (frontmatter fields,
# sections, list lines) while every byte the parse does not own is kept as it
# was read, so that ``parse_page(text).render() == text`` for any text and an
# edit changes only the lines it touches. Line parsers take one line without
# its line ending and render it back in the contract's shape; a line already in
# that shape renders to itself.

_FM_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"---[ \t]*")
_FM_KEY_RE: Final[re.Pattern[str]] = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]+(?P<value>.*?))?[ \t]*")
_FM_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+-[ \t]+(?P<item>.*?)[ \t]*")
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]{0,3}(```|~~~)")
_H1_RE: Final[re.Pattern[str]] = re.compile(r"#[ \t]+(?P<text>.*?)[ \t]*")
_H2_RE: Final[re.Pattern[str]] = re.compile(r"##[ \t]+(?P<text>.*?)[ \t]*")
_TOP_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"[-*+][ \t]+\S")
DASH: Final[str] = " — "
DOT: Final[str] = " · "
CODE_SPAN_RE: Final[re.Pattern[str]] = re.compile(r"`(?P<text>[^`\n]+)`")
WIKILINK_RE: Final[re.Pattern[str]] = re.compile(r"\[\[(?P<target>[^\]|\n]+)(?:\|(?P<label>[^\]\n]*))?\]\]")
_PAGE_PATH_PREFIXES: Final[tuple[str, ...]] = ("01 xltm", "02 ltm", "03 mtm", "04 stm", "05 daily", "06 istm")
RECORD_SECTION: Final[str] = "Update record"


class LineShapeError(ValueError):
    """A line is not in the shape a parser expects; the message says why."""


def split_line_ending(line: str) -> tuple[str, str]:
    """A raw line and its ending (``\\r\\n``, ``\\n``, ``\\r`` or nothing)."""
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _bare(line: str) -> str:
    return split_line_ending(line)[0]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


@dataclass(slots=True)
class FrontmatterField:
    """One key of the frontmatter and the raw lines it occupies."""

    key: str
    value: str | list[str]
    raw: list[str]
    style: str = "scalar"  # scalar | flow (`[a, b]`) | block (`- a` lines)

    def as_list(self) -> list[str]:
        """The value as a list: a list as written, or a scalar split at commas."""
        if isinstance(self.value, list):
            return list(self.value)
        return [part.strip() for part in self.value.split(",") if part.strip()]


class Frontmatter:
    """The ``---`` block at the top of a page: ordered keys, raw lines kept."""

    def __init__(self, opening: str, items: list[FrontmatterField | str], closing: str) -> None:
        self.opening = opening
        self.items = items
        self.closing = closing

    @property
    def fields(self) -> list[FrontmatterField]:
        return [item for item in self.items if isinstance(item, FrontmatterField)]

    def keys(self) -> list[str]:
        return [item.key for item in self.fields]

    def field(self, key: str) -> FrontmatterField | None:
        return next((item for item in self.fields if item.key == key), None)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.field(key) is not None

    def get(self, key: str, default: str | list[str] | None = None) -> str | list[str] | None:
        found = self.field(key)
        return default if found is None else found.value

    def text(self, key: str) -> str:
        """A key's value as one string ('' when absent); a list is joined with ', '."""
        value = self.get(key)
        if value is None:
            return ""
        return ", ".join(value) if isinstance(value, list) else value

    def get_list(self, key: str) -> list[str]:
        found = self.field(key)
        return [] if found is None else found.as_list()

    def as_dict(self) -> dict[str, str | list[str]]:
        return {item.key: item.value for item in self.fields}

    def set(self, key: str, value: str | Iterable[str]) -> None:
        """Set a key, rewriting only its own lines; a new key goes last."""
        if not _FM_KEY_RE.fullmatch(f"{key}:"):
            raise LineShapeError(f"`{key}` is not a frontmatter key")
        values: str | list[str] = value if isinstance(value, str) else [str(item) for item in value]
        texts = [values] if isinstance(values, str) else values
        if any("\n" in text or "\r" in text for text in texts):
            raise LineShapeError("a frontmatter value is one line")
        ending = split_line_ending(self.opening)[1] or "\n"
        existing = self.field(key)
        style = existing.style if existing is not None and isinstance(values, list) else "scalar"
        if isinstance(values, list) and style == "flow":
            raw = [f"{key}: [{', '.join(values)}]{ending}"]
        elif isinstance(values, list) and style == "block":
            raw = [f"{key}:{ending}", *(f"  - {item}{ending}" for item in values)]
        elif isinstance(values, list):
            raw = [f"{key}: {', '.join(values)}{ending}"]
        else:
            raw = [f"{key}: {values}{ending}" if values else f"{key}:{ending}"]
        replacement = FrontmatterField(key=key, value=values, raw=raw, style=style)
        if existing is None:
            self.items.append(replacement)
        else:
            self.items[self.items.index(existing)] = replacement

    def remove(self, key: str) -> bool:
        existing = self.field(key)
        if existing is None:
            return False
        self.items.remove(existing)
        return True

    def render(self) -> str:
        parts = [self.opening]
        for item in self.items:
            parts.extend(item.raw if isinstance(item, FrontmatterField) else [item])
        parts.append(self.closing)
        return "".join(parts)


def _parse_frontmatter_lines(lines: list[str]) -> list[FrontmatterField | str]:
    items: list[FrontmatterField | str] = []
    position = 0
    while position < len(lines):
        raw = lines[position]
        bare = _bare(raw)
        match = _FM_KEY_RE.fullmatch(bare)
        if match is None:
            items.append(raw)
            position += 1
            continue
        value = match["value"] or ""
        if value:
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                listed = [_strip_quotes(part.strip()) for part in inner.split(",") if part.strip()]
                items.append(FrontmatterField(match["key"], listed, [raw], "flow"))
            else:
                items.append(FrontmatterField(match["key"], _strip_quotes(value), [raw]))
            position += 1
            continue
        block = [raw]
        listed: list[str] = []
        position += 1
        while position < len(lines):
            item = _FM_ITEM_RE.fullmatch(_bare(lines[position]))
            if item is None:
                break
            listed.append(_strip_quotes(item["item"]))
            block.append(lines[position])
            position += 1
        if listed:
            items.append(FrontmatterField(match["key"], listed, block, "block"))
        else:
            items.append(FrontmatterField(match["key"], "", block))
    return items


@dataclass(slots=True)
class Section:
    """An H2 section: its heading line and the raw lines up to the next H2 or H1."""

    heading_line: str
    lines: list[str]

    @property
    def heading(self) -> str:
        match = _H2_RE.fullmatch(_bare(self.heading_line))
        return match["text"] if match else _bare(self.heading_line)[3:].strip()

    def is_named(self, name: str) -> bool:
        return self.heading.casefold() == name.strip().casefold()

    def items(self) -> list[tuple[int, str]]:
        """(index into ``lines``, bare line) of each top-level list item outside fences."""
        found: list[tuple[int, str]] = []
        fenced = False
        for index, raw in enumerate(self.lines):
            bare = _bare(raw)
            if _FENCE_RE.match(bare):
                fenced = not fenced
                continue
            if not fenced and _TOP_ITEM_RE.match(bare):
                found.append((index, bare))
        return found

    def item_lines(self) -> list[str]:
        return [line for _index, line in self.items()]

    def body(self) -> str:
        return "".join(self.lines)

    def render(self) -> str:
        return self.heading_line + "".join(self.lines)

    def ending(self) -> str:
        return split_line_ending(self.heading_line)[1] or "\n"

    def replace_line(self, index: int, text: str) -> None:
        """Replace one body line's text, keeping its line ending."""
        ending = split_line_ending(self.lines[index])[1]
        self.lines[index] = text + ending

    def append_item(self, text: str) -> None:
        """Add a list line after the section's last list line, or as its first line after one blank line."""
        ending = self.ending()
        items = self.items()
        if items:
            position = items[-1][0] + 1
            if not split_line_ending(self.lines[position - 1])[1]:
                self.lines[position - 1] += ending
            self.lines.insert(position, text + ending)
            return
        if not split_line_ending(self.heading_line)[1]:
            self.heading_line += ending
        if self.lines and not split_line_ending(self.lines[-1])[1]:
            self.lines[-1] += ending
        lead = 0
        while lead < len(self.lines) and not _bare(self.lines[lead]).strip():
            lead += 1
        if lead == 0:
            self.lines.insert(0, ending)
        self.lines.insert(1, text + ending)
        if len(self.lines) > 2:
            if _bare(self.lines[2]).strip():
                self.lines.insert(2, ending)
        elif lead >= 1:
            self.lines.append(ending)


class Page:
    """A forest page: frontmatter, the lines before the first H2, and the H2 sections."""

    def __init__(self, frontmatter: Frontmatter | None, head: list[str], sections: list[Section]) -> None:
        self.frontmatter = frontmatter
        self.head = head
        self.sections = sections

    @property
    def title(self) -> str | None:
        for raw in self.head:
            match = _H1_RE.fullmatch(_bare(raw))
            if match:
                return match["text"]
        return None

    def field(self, key: str) -> str:
        """A frontmatter value as text ('' when the page has none)."""
        return self.frontmatter.text(key) if self.frontmatter is not None else ""

    def section(self, name: str) -> Section | None:
        return next((section for section in self.sections if section.is_named(name)), None)

    def section_names(self) -> list[str]:
        return [section.heading for section in self.sections]

    def add_section(self, name: str, lines: Iterable[str] = (), *, before: str | None = None) -> Section:
        """Add an H2 section, by default before ``## Update record`` (always last) or at the end."""
        ending = self.ending()
        section = Section(f"## {name}{ending}", [ending, *(f"{line}{ending}" for line in lines)])
        anchor = self.section(before) if before else self.section(RECORD_SECTION)
        if anchor is None:
            self._end_with_blank_line()
            self.sections.append(section)
        else:
            position = self.sections.index(anchor)
            previous = self.sections[position - 1].lines if position else self.head
            if previous and _bare(previous[-1]).strip():
                previous.append(ending)
            self.sections.insert(position, section)
            if section.lines[-1].strip():
                section.lines.append(ending)
        return section

    def _end_with_blank_line(self) -> None:
        ending = self.ending()
        if self.sections:
            last = self.sections[-1]
            if not last.lines:
                if not split_line_ending(last.heading_line)[1]:
                    last.heading_line += ending
                last.lines.append(ending)
                return
            tail = last.lines
        else:
            tail = self.head
        if tail and not split_line_ending(tail[-1])[1]:
            tail[-1] += ending
        if tail and _bare(tail[-1]).strip():
            tail.append(ending)

    def ending(self) -> str:
        for raw in (*(self.frontmatter.items if self.frontmatter else ()), *self.head):
            if isinstance(raw, str):
                ending = split_line_ending(raw)[1]
                if ending:
                    return ending
        return "\n"

    def render(self) -> str:
        parts = [self.frontmatter.render()] if self.frontmatter is not None else []
        parts.extend(self.head)
        parts.extend(section.render() for section in self.sections)
        return "".join(parts)

    def content_text(self) -> str:
        """The page without its ``## Update record`` (what the leaf cap counts)."""
        parts = [self.frontmatter.render()] if self.frontmatter is not None else []
        parts.extend(self.head)
        parts.extend(section.render() for section in self.sections if not section.is_named(RECORD_SECTION))
        return "".join(parts)

    def record_text(self) -> str:
        record = self.section(RECORD_SECTION)
        return record.render() if record is not None else ""


def parse_page(text: str) -> Page:
    """Parse a page; ``parse_page(text).render() == text`` for every text."""
    lines = text.splitlines(keepends=True)
    position = 0
    frontmatter: Frontmatter | None = None
    if lines and _FM_FENCE_RE.fullmatch(_bare(lines[0])):
        closing = next(
            (index for index in range(1, len(lines)) if _FM_FENCE_RE.fullmatch(_bare(lines[index]))),
            None,
        )
        if closing is not None:
            frontmatter = Frontmatter(lines[0], _parse_frontmatter_lines(lines[1:closing]), lines[closing])
            position = closing + 1
    head: list[str] = []
    sections: list[Section] = []
    fenced = False
    for raw in lines[position:]:
        bare = _bare(raw)
        if _FENCE_RE.match(bare):
            fenced = not fenced
        elif not fenced and _H2_RE.fullmatch(bare):
            sections.append(Section(raw, []))
            continue
        (sections[-1].lines if sections else head).append(raw)
    return Page(frontmatter, head, sections)


def read_page(path: str | Path) -> Page:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return parse_page(handle.read())


# ─────────────────────────────── spans and links ───────────────────────────────


@dataclass(frozen=True, slots=True)
class CodeSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class Wikilink:
    start: int
    end: int
    target: str
    label: str | None

    @property
    def path(self) -> str:
        """The page the link names, with ``.md``."""
        return link_path(self.target)


def code_spans(line: str) -> list[CodeSpan]:
    return [CodeSpan(match.start(), match.end(), match["text"]) for match in CODE_SPAN_RE.finditer(line)]


def looks_like_forest_path(text: str) -> bool:
    """Whether a code span's text is a path into the forest or its lanes."""
    value = text.strip()
    return any(value == prefix or value.startswith(prefix + "/") for prefix in _PAGE_PATH_PREFIXES)


def code_span_paths(line: str) -> list[CodeSpan]:
    """The code spans of a line that hold forest paths."""
    return [span for span in code_spans(line) if looks_like_forest_path(span.text)]


def wikilinks(line: str) -> list[Wikilink]:
    return [
        Wikilink(match.start(), match.end(), match["target"].strip(), match["label"])
        for match in WIKILINK_RE.finditer(line)
    ]


def link_path(target: str) -> str:
    """A wikilink target as a page path: ``03 mtm/t/b`` → ``03 mtm/t/b.md``."""
    value = target.strip()
    return value if value.endswith(".md") else value + ".md"


def rewrite_code_span_paths(line: str, moves: Mapping[str, str]) -> str:
    """``line`` with each code-span path that ``moves`` names (or that lies under a moved folder) rewritten."""
    if not moves:
        return line
    pieces: list[str] = []
    cursor = 0
    for span in code_span_paths(line):
        replacement = _moved(span.text, moves)
        if replacement is None:
            continue
        pieces.append(line[cursor : span.start])
        pieces.append(f"`{replacement}`")
        cursor = span.end
    pieces.append(line[cursor:])
    return "".join(pieces)


def _moved(path: str, moves: Mapping[str, str]) -> str | None:
    value = path.strip()
    if value in moves:
        return moves[value]
    for old, new in moves.items():
        if not old.endswith(".md") and value.startswith(old.rstrip("/") + "/"):
            return new.rstrip("/") + value[len(old.rstrip("/")) :]
    return None


# ─────────────────────────────── line shapes ───────────────────────────────

_DATE = r"\d{4}-\d{2}-\d{2}"
_TIMELINE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<indent>[ \t]*)- (?:\[superseded (?P<superseded>" + _DATE + r")\] )?(?P<when>.+?) — (?P<text>.*)"
)
_WHEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<qualifier>(?:since |about |since about |until |before |after )?)"
    r"(?P<date>\d{4}(?:-q[1-4]|-\d{2}(?:-\d{2})?)?)"
    r"(?: (?P<start>\d{1,2}:\d{2})(?:[–-](?P<end>\d{1,2}:\d{2}))?)?"
    r"(?: (?P<zone>UTC|Z|[+-]\d{2}:?\d{2}|[A-Za-z][A-Za-z_]*(?:/[A-Za-z0-9_+\-]+)+))?"
    r"(?: (?P<note>\(.*\)))?"
)


@dataclass(slots=True)
class TimelineLine:
    """``- [superseded D] YYYY-MM-DD HH:MM–HH:MM <zone> — what happened`` (contract, section 4)."""

    when: str
    text: str
    superseded: str | None = None
    indent: str = ""

    @classmethod
    def parse(cls, line: str) -> TimelineLine:
        match = _TIMELINE_RE.fullmatch(_bare(line))
        if match is None or _WHEN_RE.fullmatch(match["when"]) is None:
            raise LineShapeError("not a Timeline line (- YYYY-MM-DD HH:MM <zone> — what happened)")
        return cls(match["when"], match["text"], match["superseded"], match["indent"])

    @classmethod
    def match(cls, line: str) -> TimelineLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    @classmethod
    def make(
        cls,
        day: str,
        text: str,
        *,
        start: str | None = None,
        end: str | None = None,
        zone: str | None = None,
    ) -> TimelineLine:
        when = day
        if start:
            when += f" {start}" + (f"–{end}" if end else "")
        if zone:
            when += f" {zone}"
        return cls(when, text)

    def _part(self, name: str) -> str | None:
        match = _WHEN_RE.fullmatch(self.when)
        return (match[name] or None) if match else None

    @property
    def date(self) -> str:
        return self._part("date") or ""

    @property
    def day(self) -> str | None:
        """The full local date, when the line gives one."""
        value = self.date
        return value if re.fullmatch(_DATE, value) else None

    @property
    def start(self) -> str | None:
        return self._part("start")

    @property
    def end(self) -> str | None:
        return self._part("end")

    @property
    def zone(self) -> str | None:
        return self._part("zone")

    @property
    def qualifier(self) -> str | None:
        value = self._part("qualifier")
        return value.strip() if value else None

    @property
    def note(self) -> str | None:
        return self._part("note")

    @property
    def is_superseded(self) -> bool:
        return self.superseded is not None

    def render(self) -> str:
        mark = f"[superseded {self.superseded}] " if self.superseded else ""
        return f"{self.indent}- {mark}{self.when}{DASH}{self.text}"


def _split_names(text: str) -> list[str]:
    names: list[str] = []
    for part in text.split(";"):
        value = part.strip()
        quoted = CODE_SPAN_RE.fullmatch(value)
        value = quoted["text"].strip() if quoted else value
        if value and value not in names:
            names.append(value)
    return names


_REGISTRY_RE: Final[re.Pattern[str]] = re.compile(r"(?P<indent>[ \t]*)[-*+] `(?P<id>[^`]*)` — (?P<rest>\S.*?)[ \t]*")
_ALIASES_RE: Final[re.Pattern[str]] = re.compile(r"aliases\s*:\s*(?P<names>.*)", re.IGNORECASE)
STATUSES: Final[tuple[str, ...]] = ("active", "ended")


@dataclass(slots=True)
class RegistryLine:
    """``- `<id>` — <name> · <type> · <level> · `<home>` · aliases: <a>; <b> · <status>`` (contract, section 2)."""

    id: str
    name: str
    type: str
    level: str
    home: str
    aliases: list[str]
    status: str = "active"
    status_written: bool = False

    @classmethod
    def parse(cls, line: str) -> RegistryLine:
        match = _REGISTRY_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not a registry line (- `<id>` — <name> · <type> · <level> · `<home>`)")
        fields_ = [part.strip() for part in match["rest"].split(DOT)]
        if len(fields_) < 4:
            raise LineShapeError("a registry line needs name · type · level · `home`")
        name, kind, level, home_field, *tail = fields_
        home = CODE_SPAN_RE.fullmatch(home_field)
        if home is None:
            raise LineShapeError("the home of a registry line is one code-span path")
        if not name:
            raise LineShapeError("a registry line names its subject")
        aliases: list[str] | None = None
        status: str | None = None
        for value in tail:
            found = _ALIASES_RE.fullmatch(value)
            if found is not None and aliases is None and status is None:
                aliases = _split_names(found["names"])
            elif value in STATUSES and status is None:
                status = value
            else:
                raise LineShapeError(f"unexpected field `{value}` in a registry line")
        return cls(
            id=match["id"].strip(),
            name=name,
            type=kind,
            level=level,
            home=home["text"].strip(),
            aliases=aliases or [],
            status=status or "active",
            status_written=status is not None,
        )

    @classmethod
    def match(cls, line: str) -> RegistryLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        text = f"- `{self.id}`{DASH}{self.name}{DOT}{self.type}{DOT}{self.level}{DOT}`{self.home}`"
        if self.aliases:
            text += f"{DOT}aliases: {'; '.join(self.aliases)}"
        if self.status != "active" or self.status_written:
            text += f"{DOT}{self.status}"
        return text


_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]*[-*+] entry: `(?P<id>[^`]*)`(?: · (?P<rest>.*?))?[ \t]*")


@dataclass(slots=True)
class EntryLine:
    """``- entry: `<id>` · <type> · aliases: <a>; <b>`` under a collection entry's heading."""

    id: str
    type: str
    aliases: list[str]

    @classmethod
    def parse(cls, line: str) -> EntryLine:
        match = _ENTRY_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not an entry line (- entry: `<id>` · <type> · aliases: …)")
        fields_ = [part.strip() for part in (match["rest"] or "").split(DOT) if part.strip()]
        if not fields_:
            raise LineShapeError(f"the entry `{match['id']}` names no type")
        kind, *tail = fields_
        aliases: list[str] = []
        for value in tail:
            found = _ALIASES_RE.fullmatch(value)
            if found is None or aliases:
                raise LineShapeError(f"unexpected field `{value}` in an entry line")
            aliases = _split_names(found["names"])
        return cls(match["id"].strip(), kind, aliases)

    @classmethod
    def match(cls, line: str) -> EntryLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        text = f"- entry: `{self.id}`{DOT}{self.type}"
        if self.aliases:
            text += f"{DOT}aliases: {'; '.join(self.aliases)}"
        return text


_LIST_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<indent>[ \t]*)- `(?P<target>[^`]+)`(?:(?P<sep> — )(?P<description>.*?))?"
    r"(?: \(updated (?P<updated>" + _DATE + r")\))?[ \t]*"
)


@dataclass(slots=True)
class ListLine:
    """A code-span list line: ``## Leaves``, ``## Branches``, ``## Pointers``, ``## Trees``, ``## Agents``.

    ``- `<target>` — <description> (updated YYYY-MM-DD)``, the description and
    the date each optional.
    """

    target: str
    description: str | None = None
    updated: str | None = None
    indent: str = ""

    @classmethod
    def parse(cls, line: str) -> ListLine:
        match = _LIST_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not a list line (- `<path>` — <what it holds>)")
        description = match["description"] if match["sep"] else None
        return cls(match["target"], description, match["updated"], match["indent"])

    @classmethod
    def match(cls, line: str) -> ListLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        text = f"{self.indent}- `{self.target}`"
        if self.description is not None:
            text += f"{DASH}{self.description}"
        if self.updated:
            text += f" (updated {self.updated})"
        return text


@dataclass(slots=True)
class AgentLine:
    """``- `<id>` — <name> · <role>`` under ``## Agents``, with ``· ended`` for a retired agent."""

    id: str
    name: str
    role: str
    ended: bool = False

    @classmethod
    def parse(cls, line: str) -> AgentLine:
        listed = ListLine.parse(line)
        if listed.description is None or listed.updated is not None:
            raise LineShapeError("not an agent line (- `<id>` — <name> · <role>)")
        parts = listed.description.split(DOT)
        ended = len(parts) > 2 and parts[-1].strip() == "ended"
        if ended:
            parts = parts[:-1]
        if len(parts) < 2 or not parts[0].strip():
            raise LineShapeError("an agent line gives a name and a role")
        return cls(listed.target, parts[0].strip(), DOT.join(parts[1:]).strip(), ended)

    @classmethod
    def match(cls, line: str) -> AgentLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        text = f"- `{self.id}`{DASH}{self.name}{DOT}{self.role}"
        return text + f"{DOT}ended" if self.ended else text


_UP_RE: Final[re.Pattern[str]] = re.compile(r"(?P<indent>[ \t]*)- up: \[\[(?P<target>[^\]|\n]+)(?:\|(?P<label>[^\]\n]*))?\]\][ \t]*")


@dataclass(slots=True)
class UpLink:
    """``- up: [[<path without .md>|<label>]]`` in ``## Links``."""

    target: str
    label: str | None = None

    @classmethod
    def parse(cls, line: str) -> UpLink:
        match = _UP_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not an up link (- up: [[<page>|<label>]])")
        return cls(match["target"].strip(), match["label"])

    @classmethod
    def match(cls, line: str) -> UpLink | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    @property
    def path(self) -> str:
        return link_path(self.target)

    def render(self) -> str:
        label = f"|{self.label}" if self.label is not None else ""
        return f"- up: [[{self.target}{label}]]"


_RECORD_RE: Final[re.Pattern[str]] = re.compile(r"- (?P<date>" + _DATE + r") — (?P<text>.*)")
_WAS_NOW_RE: Final[re.Pattern[str]] = re.compile(r'was "(?P<was>.*?)", now "(?P<now>.*?)"(?=\.?(?: Why:|\s*$))')
_WHY_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\. )Why: (?P<why>.*?)(?=\. Source:|\.?\s*$)")
_SOURCE_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\. )Source: (?P<source>.*?)\.?\s*$")


@dataclass(slots=True)
class RecordLine:
    """An ``## Update record`` line: ``- YYYY-MM-DD — what changed: was "…", now "…". Why: …. Source: `path`.``"""

    date: str
    text: str

    @classmethod
    def parse(cls, line: str) -> RecordLine:
        match = _RECORD_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not a record line (- YYYY-MM-DD — what changed …)")
        return cls(match["date"], match["text"])

    @classmethod
    def match(cls, line: str) -> RecordLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    @classmethod
    def make(
        cls,
        day: str,
        what: str,
        *,
        was: str | None = None,
        now: str | None = None,
        why: str | None = None,
        sources: Iterable[str] = (),
    ) -> RecordLine:
        text = what
        if was is not None or now is not None:
            text += f': was "{was or ""}", now "{now or ""}"'
        if why:
            text += f". Why: {why.rstrip('.')}"
        listed = [f"`{source}`" for source in sources]
        if listed:
            text += f". Source: {', '.join(listed)}"
        return cls(day, text + ".")

    @property
    def what(self) -> str:
        for marker in (": was ", ". Why: ", ". Source: "):
            if marker in self.text:
                return self.text.split(marker, 1)[0]
        return self.text.rstrip(".")

    @property
    def was(self) -> str | None:
        found = _WAS_NOW_RE.search(self.text)
        return found["was"] if found else None

    @property
    def now(self) -> str | None:
        found = _WAS_NOW_RE.search(self.text)
        return found["now"] if found else None

    @property
    def why(self) -> str | None:
        found = _WHY_RE.search(self.text)
        return found["why"] if found else None

    @property
    def sources(self) -> list[str]:
        found = _SOURCE_RE.search(self.text)
        return [span.text for span in code_spans(found["source"])] if found else []

    def render(self) -> str:
        return f"- {self.date}{DASH}{self.text}"


_POINTER_RE: Final[re.Pattern[str]] = re.compile(
    r"- Earlier record lines \((?P<first>\S+) – (?P<last>\S+), (?P<count>\d+) lines?\) "
    r"are kept in `(?P<path>[^`]+)`\."
)


@dataclass(slots=True)
class RecordPointerLine:
    """``- Earlier record lines (<first> – <last>, <n> lines) are kept in `<archive path>`.``"""

    first: str
    last: str
    count: int
    path: str

    @classmethod
    def parse(cls, line: str) -> RecordPointerLine:
        match = _POINTER_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not a record pointer line")
        return cls(match["first"], match["last"], int(match["count"]), match["path"])

    @classmethod
    def match(cls, line: str) -> RecordPointerLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        noun = "line" if self.count == 1 else "lines"
        return (
            f"- Earlier record lines ({self.first} – {self.last}, {self.count} {noun}) "
            f"are kept in `{self.path}`."
        )


_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"- (?P<date>" + _DATE + r") — (?P<event>.+?): recorded in `(?P<path>[^`]+)`(?P<archived> \(archived\))?\."
)


@dataclass(slots=True)
class ReferenceLine:
    """``- YYYY-MM-DD — <the event in a few plain words>: recorded in `<path>`.`` (contract, section 3)."""

    date: str
    event: str
    path: str
    archived: bool = False

    @classmethod
    def parse(cls, line: str) -> ReferenceLine:
        match = _REFERENCE_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not a reference line (- YYYY-MM-DD — <event>: recorded in `<path>`.)")
        return cls(match["date"], match["event"], match["path"], bool(match["archived"]))

    @classmethod
    def match(cls, line: str) -> ReferenceLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        mark = " (archived)" if self.archived else ""
        return f"- {self.date}{DASH}{self.event}: recorded in `{self.path}`{mark}."


_STATE_RE: Final[re.Pattern[str]] = re.compile(
    r"- (?P<statement>.+) \(as of (?P<date>" + _DATE + r"); source `(?P<source>[^`]+)`\)(?P<stop>\.?)"
)


@dataclass(slots=True)
class CurrentStateLine:
    """``- <statement> (as of YYYY-MM-DD; source `<path>`)`` (contract, section 5)."""

    statement: str
    date: str
    source: str
    full_stop: bool = False

    @classmethod
    def parse(cls, line: str) -> CurrentStateLine:
        match = _STATE_RE.fullmatch(_bare(line))
        if match is None:
            raise LineShapeError("not a current-state line (- <statement> (as of YYYY-MM-DD; source `<path>`))")
        return cls(match["statement"], match["date"], match["source"], bool(match["stop"]))

    @classmethod
    def match(cls, line: str) -> CurrentStateLine | None:
        try:
            return cls.parse(line)
        except LineShapeError:
            return None

    def render(self) -> str:
        stop = "." if self.full_stop else ""
        return f"- {self.statement} (as of {self.date}; source `{self.source}`){stop}"
