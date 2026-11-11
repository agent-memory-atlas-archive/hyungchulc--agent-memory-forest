"""Where everything lives on disk (design, section 3) and what each path is.

All forest paths are forward-slash text relative to the forest root, the way
pages write them in code spans: ``04 stm/<tree>/<branch>/<leaf>.md``. The
helpers here build such paths, say which role a path plays, check names
against the contract's slug and date rules, and turn a relative path into a
real one without ever leaving the root or following a symlink.
"""

from __future__ import annotations

import enum
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Final

from .config import CONFIG_FILENAME, DEFAULT_CONFIG, Config, render_config
from .errors import MemoryForestError
from .safety import prepare_new_root, safe_join, secure_create_file, secure_mkdir


XLTM: Final[str] = "01 xltm"
LTM: Final[str] = "02 ltm"
MTM: Final[str] = "03 mtm"
STM: Final[str] = "04 stm"
DAILY: Final[str] = "05 daily"
ISTM: Final[str] = "06 istm"
FOREST_LAYERS: Final[tuple[str, ...]] = (XLTM, LTM, MTM, STM)
LANES: Final[tuple[str, ...]] = (DAILY, ISTM)
ALL_LAYERS: Final[tuple[str, ...]] = (*FOREST_LAYERS, *LANES)

ROOT_INDEX: Final[str] = f"{XLTM}/XLTM.md"
ROOT_INDEX_SECTIONS: Final[tuple[str, ...]] = ("Identity", "Trees", "Agents")

STATE_DIR: Final[str] = ".forest"
REGISTRY_FILE: Final[str] = f"{STATE_DIR}/registry.json"
MOVES_FILE: Final[str] = f"{STATE_DIR}/moves.jsonl"
ARCHIVE_DIR: Final[str] = f"{STATE_DIR}/archive"
LOCKS_DIR: Final[str] = f"{STATE_DIR}/locks"
INDEX_DIR: Final[str] = f"{STATE_DIR}/index"
BASELINES_DIR: Final[str] = f"{STATE_DIR}/baselines"
# What in `.forest/` is kept, never rebuilt (design, section 3).
KEPT_STATE: Final[tuple[str, ...]] = (MOVES_FILE, ARCHIVE_DIR)

SLUG_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9-]*")
COLLECTION_PREFIX: Final[str] = "seen-once-"

# A period at the end of a name (contract, section 4): a year, a quarter or a month, or, on a page of a
# month cut by days, the first day of its run, with `-2`, `-3`, … for the further pages of one day. This
# is the one definition of a period suffix; collection pages, streams, the lint and the level rule read it.
PERIOD_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<year>\d{4})(?:-(?:q(?P<quarter>[1-4])|(?P<month>0[1-9]|1[0-2])"
    r"(?:-(?P<day>0[1-9]|[12]\d|3[01])(?:-(?P<page>[2-9]|[1-9]\d+))?)?))?"
)
_PERIOD_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<stem>[a-z0-9][a-z0-9-]*?)-(?P<period>" + PERIOD_RE.pattern + r")$"
)
_NUMBERED_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|-)(\d+)$")
_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|-)(?:19|20|21)\d{2}(?=-|$)")
_DAILY_RE: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}")


class PageRole(enum.Enum):
    ROOT_INDEX = "root-index"
    TREE_PAGE = "tree-page"
    TREE_INDEX = "tree-index"
    BOARD = "board"
    LEAF = "leaf"
    COLLECTION_PAGE = "collection-page"
    DAILY_PAGE = "daily-page"
    RAW_FILE = "raw-file"
    SETTINGS = "settings"
    STATE = "state"
    UNKNOWN = "unknown"

    @property
    def in_forest(self) -> bool:
        return self in _FOREST_ROLES


_FOREST_ROLES: Final[frozenset[PageRole]] = frozenset(
    {
        PageRole.ROOT_INDEX,
        PageRole.TREE_PAGE,
        PageRole.TREE_INDEX,
        PageRole.BOARD,
        PageRole.LEAF,
        PageRole.COLLECTION_PAGE,
    }
)


@dataclass(frozen=True, slots=True)
class PathInfo:
    """What a relative path is: its role, and the tree, branch and name it names."""

    path: str
    role: PageRole
    tree: str | None = None
    branch: str | None = None
    name: str | None = None
    problem: str | None = None

    @property
    def in_forest(self) -> bool:
        return self.role.in_forest


# ─────────────────────────────── paths ───────────────────────────────


def tree_page(tree: str) -> str:
    return f"{LTM}/{tree}_LTM.md"


def tree_index(tree: str) -> str:
    return f"{MTM}/{tree}/{tree}_MTM.md"


def tree_folder(tree: str) -> str:
    return f"{MTM}/{tree}"


def board(tree: str, branch: str) -> str:
    return f"{MTM}/{tree}/{branch}.md"


def leaf_folder(tree: str, branch: str) -> str:
    return f"{STM}/{tree}/{branch}"


def leaf(tree: str, branch: str, name: str) -> str:
    stem = name.removesuffix(".md")
    return f"{STM}/{tree}/{branch}/{stem}.md"


def collection_page(tree: str, category: str, period: str) -> str:
    return leaf(tree, category, f"{COLLECTION_PREFIX}{period}")


def daily_page(day: date | str) -> str:
    text = day.isoformat() if isinstance(day, date) else str(day)
    date.fromisoformat(text)
    return f"{DAILY}/{text}.md"


def raw_lane(agent: str) -> str:
    return f"{ISTM}/{agent}"


def up_link_target(path: str) -> str:
    """The wikilink target of a page (its path without ``.md``)."""
    return path.removesuffix(".md")


def parent_page(path: str) -> str | None:
    """The page a page's single ``up:`` link names (contract, section 6)."""
    info = classify(path)
    if info.role in (PageRole.LEAF, PageRole.COLLECTION_PAGE) and info.tree and info.branch:
        return board(info.tree, info.branch)
    if info.role in (PageRole.BOARD, PageRole.TREE_INDEX) and info.tree:
        return tree_page(info.tree)
    if info.role is PageRole.TREE_PAGE:
        return ROOT_INDEX
    return None


def state_path(root: str | os.PathLike[str], relative: str) -> Path:
    """A path under ``.forest/`` resolved safely inside the root."""
    if relative != STATE_DIR and not relative.startswith(STATE_DIR + "/"):
        raise MemoryForestError(
            "invalid_path",
            "A state path must lie under .forest/.",
            details={"path": relative},
        )
    return safe_join(root, relative)


def registry_path(root: str | os.PathLike[str]) -> Path:
    return state_path(root, REGISTRY_FILE)


def moves_path(root: str | os.PathLike[str]) -> Path:
    return state_path(root, MOVES_FILE)


def archive_path(root: str | os.PathLike[str]) -> Path:
    return state_path(root, ARCHIVE_DIR)


def locks_path(root: str | os.PathLike[str]) -> Path:
    return state_path(root, LOCKS_DIR)


def resolve(root: str | os.PathLike[str], relative: str, *, must_exist: bool = False) -> Path:
    """The real path of a forest-relative path; refuses escapes and symlinks."""
    return safe_join(root, normalize(relative), must_exist=must_exist)


def ensure_folder(root: str | os.PathLike[str], relative: str) -> Path:
    """Create ``relative`` and its missing parents inside the root (private folders)."""
    target = resolve(root, relative)
    parts = normalize(relative).split("/")
    current = resolve(root, parts[0])
    if not current.exists():
        secure_mkdir(current)
    for part in parts[1:]:
        current = current / part
        if not current.exists():
            current.mkdir(mode=0o700)
        elif current.is_symlink() or not current.is_dir():
            raise MemoryForestError(
                "unsafe_existing_path",
                "An expected directory exists as a symlink or non-directory.",
                details={"path": relative},
            )
    return target


def normalize(path: str) -> str:
    """A path as a page writes it: trimmed, forward slashes, no leading ``./``."""
    text = str(path).strip()
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/") if text not in ("", "/") else text


def relative_to_root(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> str:
    """``path`` (absolute under the root, or already relative) as forest-relative text."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return normalize(PurePosixPath(*candidate.parts).as_posix())
    base = Path(os.path.abspath(root))
    try:
        return candidate.relative_to(base).as_posix()
    except ValueError:
        raise MemoryForestError(
            "path_escape",
            "The requested path escapes the selected forest root.",
            details={"path": str(path)},
        ) from None


# ─────────────────────────────── names ───────────────────────────────


def is_slug(text: str) -> bool:
    return SLUG_RE.fullmatch(text) is not None


def slugify(name: str) -> str:
    """The slug of a name written in Latin letters: accents dropped, lower case, hyphens.

    A name in another script needs its romanization first (contract,
    section 2, Ids from names); for it this returns the empty string rather
    than a guess.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    ascii_text = ascii_text.casefold().replace("ß", "ss").replace("'", "").replace("’", "")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug


def period_grain(period: str) -> str | None:
    """``year``, ``quarter``, ``month`` or ``day`` (a day's further pages included), or None for no period."""
    match = PERIOD_RE.fullmatch(period)
    if match is None:
        return None
    if match["day"]:
        return "day"
    if match["month"]:
        return "month"
    return "quarter" if match["quarter"] else "year"


def split_period(stem: str, siblings: Iterable[str] = ()) -> tuple[str, str | None]:
    """A name's stem and its period suffix (``firings-2042-q2`` → ``firings``, ``2042-q2``).

    A year, a quarter or a month is a period wherever it stands. A day
    (``-2042-05-14``, ``-2042-05-14-2``) is a period only on a page of a month
    cut by days: where ``siblings`` (the other page stems of the folder) hold a
    page of the same stream with a year, quarter or month period. A lone
    day-named page keeps its date in its stem, which the name rules refuse.
    """
    match = _PERIOD_SUFFIX_RE.fullmatch(stem)
    if match is None:
        return stem, None
    base, period = match["stem"], match["period"]
    if period_grain(period) != "day":
        return base, period
    for other in siblings:
        other_match = _PERIOD_SUFFIX_RE.fullmatch(PurePosixPath(other).name.removesuffix(".md"))
        if (other_match is not None and other_match["stem"] == base
                and period_grain(other_match["period"]) in ("year", "quarter", "month")):
            return base, period
    return stem, None


def collection_period(stem: str) -> str | None:
    """The period a collection page's name carries, or None when it is no collection page name.

    A collection page carries a quarter, a month or a day of a month cut by
    days (contract, section 3, Collections), never a whole year.
    """
    if not stem.startswith(COLLECTION_PREFIX):
        return None
    period = stem[len(COLLECTION_PREFIX):]
    return period if period_grain(period) in ("quarter", "month", "day") else None


def is_collection_name(name: str) -> bool:
    return collection_period(PurePosixPath(name).name.removesuffix(".md")) is not None


def folder_stems(paths: Iterable[str]) -> dict[str, list[str]]:
    """The page stems of each folder of ``paths`` (the ``siblings`` a period suffix is read against)."""
    found: dict[str, list[str]] = {}
    for path in paths:
        pure = PurePosixPath(path)
        found.setdefault(str(pure.parent), []).append(pure.name.removesuffix(".md"))
    return found


def name_problem(name: str, *, config: Config = DEFAULT_CONFIG, period_allowed: bool = True,
                 siblings: Iterable[str] = ()) -> str | None:
    """Why a file or folder name breaks the name rules (contract, sections 1 and 4), or None.

    A name is a slug with no date, year, month or season word and no number,
    apart from a period suffix (allowed unless ``period_allowed`` is false;
    a day only beside its stream's other period pages, ``siblings``) and the
    period of a collection page.
    """
    stem = name.removesuffix(".md")
    if not is_slug(stem):
        return f"`{name}` is not a slug ([a-z0-9][a-z0-9-]*)"
    if is_collection_name(stem):
        return None
    base, period = split_period(stem, siblings)
    if period is not None and not period_allowed:
        return f"`{name}` carries the period `{period}` where none is allowed"
    if _YEAR_RE.search(base) or PERIOD_RE.fullmatch(base):
        return f"`{name}` carries a date or a year outside a period suffix"
    words = frozenset(word.casefold() for word in config.names.date_words)
    for part in base.split("-"):
        if part in words:
            return f"`{name}` carries the date word `{part}`"
    numbered = _NUMBERED_RE.search(base)
    if numbered:
        return f"`{name}` is a numbered name (`{numbered.group(1)}`)"
    return None


# ─────────────────────────────── roles ───────────────────────────────


def classify(path: str) -> PathInfo:
    """The role of a forest-relative path, read from its shape alone."""
    text = normalize(path)
    parts = tuple(text.split("/")) if text else ()
    if not parts:
        return PathInfo(text, PageRole.UNKNOWN, problem="empty path")
    head = parts[0]
    if parts == (CONFIG_FILENAME,):
        return PathInfo(text, PageRole.SETTINGS)
    if head == STATE_DIR:
        return PathInfo(text, PageRole.STATE)
    if head == ISTM:
        agent = parts[1] if len(parts) > 2 else None
        return PathInfo(text, PageRole.RAW_FILE, tree=agent, name=parts[-1] if len(parts) > 1 else None)
    if head == DAILY:
        if len(parts) == 2 and parts[1].endswith(".md") and _DAILY_RE.fullmatch(parts[1][:-3]):
            return PathInfo(text, PageRole.DAILY_PAGE, name=parts[1][:-3])
        return PathInfo(text, PageRole.UNKNOWN, problem="a daily page is `05 daily/YYYY-MM-DD.md`")
    if not text.endswith(".md"):
        return PathInfo(text, PageRole.UNKNOWN, problem="forest pages are `.md` files")
    if head == XLTM:
        if parts == (XLTM, "XLTM.md"):
            return PathInfo(text, PageRole.ROOT_INDEX, name="XLTM")
        return PathInfo(text, PageRole.UNKNOWN, problem=f"the only page of `{XLTM}` is `{ROOT_INDEX}`")
    if head == LTM:
        if len(parts) == 2 and parts[1].endswith("_LTM.md"):
            tree = parts[1][: -len("_LTM.md")]
            return PathInfo(text, PageRole.TREE_PAGE, tree=tree, name=tree)
        return PathInfo(text, PageRole.UNKNOWN, problem=f"a tree page is `{LTM}/<tree>_LTM.md`")
    if head == MTM:
        if len(parts) == 3:
            tree, page = parts[1], parts[2][:-3]
            if page == f"{tree}_MTM":
                return PathInfo(text, PageRole.TREE_INDEX, tree=tree, name=tree)
            return PathInfo(text, PageRole.BOARD, tree=tree, branch=page, name=page)
        return PathInfo(text, PageRole.UNKNOWN, problem=f"a board is `{MTM}/<tree>/<branch>.md`")
    if head == STM:
        if len(parts) == 4:
            tree, branch, page = parts[1], parts[2], parts[3][:-3]
            role = PageRole.COLLECTION_PAGE if is_collection_name(page) else PageRole.LEAF
            return PathInfo(text, role, tree=tree, branch=branch, name=page)
        return PathInfo(text, PageRole.UNKNOWN, problem=f"a leaf is `{STM}/<tree>/<branch>/<leaf>.md`")
    return PathInfo(text, PageRole.UNKNOWN, problem="the path is in no layer")


def page_role(path: str) -> PageRole:
    return classify(path).role


def is_forest_path(path: str) -> bool:
    """Whether a path text names something in the four forest layers (a page or a folder)."""
    text = normalize(path)
    return any(text == layer or text.startswith(layer + "/") for layer in FOREST_LAYERS)


# ─────────────────────────────── walking ───────────────────────────────


def iter_pages(root: str | os.PathLike[str], layers: Iterable[str] = FOREST_LAYERS) -> list[str]:
    """Every ``.md`` page under the given layers, sorted, as forest-relative paths.

    A symlink anywhere is refused rather than followed.
    """
    pages: list[str] = []
    for layer in layers:
        top = resolve(root, layer)
        if not top.exists():
            continue
        stack = [top]
        while stack:
            folder = stack.pop()
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        raise MemoryForestError(
                            "symlink_forbidden",
                            "Symlinks are not allowed inside a Memory Forest.",
                            details={"path": relative_to_root(root, entry.path)},
                        )
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".md"):
                        pages.append(relative_to_root(root, entry.path))
    return sorted(pages)


def trees(root: str | os.PathLike[str]) -> list[str]:
    """The trees that have an index on disk, sorted."""
    folder = resolve(root, MTM)
    if not folder.is_dir():
        return []
    found = []
    for entry in sorted(os.scandir(folder), key=lambda item: item.name):
        if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
            if (Path(entry.path) / f"{entry.name}_MTM.md").is_file():
                found.append(entry.name)
    return found


# ─────────────────────────────── init ───────────────────────────────


def root_index_text() -> str:
    return (
        "# XLTM\n\n"
        "The root index of this forest: who the owner is, the trees, the agents.\n\n"
        "## Identity\n\n"
        "## Trees\n\n"
        "## Agents\n"
    )


def initialize(target: str | os.PathLike[str], *, config: Config = DEFAULT_CONFIG) -> dict[str, object]:
    """Create a new forest at ``target``: settings, layer folders, an empty root index, ``.forest/``.

    The folder may exist only if it is empty; nothing that is there is overwritten.
    """
    root, existed = prepare_new_root(target)
    if existed:
        with os.scandir(root) as entries:
            if any(True for _ in entries):
                raise MemoryForestError(
                    "root_not_empty",
                    "forest init needs a new or empty folder.",
                    details={"path": str(root)},
                )
    else:
        secure_mkdir(root)
    created: list[str] = []
    for layer in (*ALL_LAYERS, STATE_DIR, LOCKS_DIR, ARCHIVE_DIR):
        ensure_folder(root, layer)
        created.append(layer + "/")
    secure_create_file(safe_join(root, CONFIG_FILENAME), render_config(config).encode("utf-8"))
    created.append(CONFIG_FILENAME)
    secure_create_file(safe_join(root, ROOT_INDEX), root_index_text().encode("utf-8"))
    created.append(ROOT_INDEX)
    return {"root": str(root), "created": created}
