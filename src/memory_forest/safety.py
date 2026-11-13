from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from . import system
from .errors import MemoryForestError


STATE_DIRECTORY: Final[str] = ".memory-forest"
RECEIPTS_DIRECTORY: Final[str] = "receipts"
RECEIPT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\.json")
MAX_RECEIPTS: Final[int] = 10_000
MAX_RECEIPT_BYTES: Final[int] = 256 * 1024
_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ForestLimits:
    max_files: int = 10_000
    max_directories: int = 20_000
    max_depth: int = 32
    max_total_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 4 * 1024 * 1024
    max_links: int = 200_000
    max_results: int = 100

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_directories",
            "max_depth",
            "max_total_bytes",
            "max_file_bytes",
            "max_links",
            "max_results",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


DEFAULT_LIMITS: Final[ForestLimits] = ForestLimits()


@dataclass(frozen=True, slots=True)
class ScannedDirectory:
    path: Path
    relative: str
    mode: int


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: Path
    relative: str
    mode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ScanResult:
    root: Path
    directories: tuple[ScannedDirectory, ...]
    files: tuple[ScannedFile, ...]
    total_bytes: int


def _absolute_lexical(path: str | os.PathLike[str]) -> Path:
    return system.absolute(path)


def _assert_real(path: Path, *, code: str = "symlink_forbidden") -> None:
    real = system.real_path(path)
    if real != path:
        raise MemoryForestError(
            code,
            "The selected path must be a real path with no symlink components.",
            details={"path": str(path), "real_path": str(real)},
        )


def prepare_new_root(path: str | os.PathLike[str]) -> tuple[Path, bool]:
    candidate = _absolute_lexical(path)
    info = system.look_or_none(candidate)
    if info is not None:
        if info.kind == "link":
            raise MemoryForestError(
                "symlink_forbidden",
                "The selected forest root may not be a symlink.",
                details={"path": str(candidate)},
            )
        return require_real_root(candidate), True
    parent = candidate.parent
    parent_info = system.look_or_none(parent)
    if parent_info is not None and parent_info.kind == "link":
        parent_info = system.look_or_none(system.real_path(parent))
    if parent_info is None or parent_info.kind != "folder":
        raise MemoryForestError(
            "missing_parent",
            "The forest root requires an existing real parent directory.",
            details={"parent": str(parent)},
        )
    _assert_real(parent)
    return candidate, False


def require_real_root(path: str | os.PathLike[str]) -> Path:
    root = _absolute_lexical(path)
    try:
        info = system.look(root)
    except system.Absent as exc:
        raise MemoryForestError(
            "root_not_found",
            "The selected forest root does not exist.",
            details={"path": str(root)},
        ) from exc
    if info.kind == "link":
        raise MemoryForestError(
            "symlink_forbidden",
            "The selected forest root may not be a symlink.",
            details={"path": str(root)},
        )
    if info.kind != "folder":
        raise MemoryForestError(
            "root_not_directory",
            "The selected forest root is not a directory.",
            details={"path": str(root)},
        )
    _assert_real(root)
    account = system.account_id()
    if account is not None and info.uid != account:
        raise MemoryForestError(
            "root_not_owned",
            "The selected forest root must be owned by the current user.",
            details={"path": str(root)},
        )
    return root


def ensure_inside(root: Path, path: Path, *, must_exist: bool = True) -> Path:
    root = require_real_root(root)
    candidate = _absolute_lexical(path if path.is_absolute() else root / path)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MemoryForestError(
            "path_escape",
            "The requested path escapes the selected forest root.",
            details={"path": str(candidate), "root": str(root)},
        ) from exc
    if must_exist:
        try:
            info = system.look(candidate)
        except system.Absent as exc:
            raise MemoryForestError(
                "path_not_found",
                "The requested path does not exist.",
                details={"path": str(candidate)},
            ) from exc
        if info.kind == "link":
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are not allowed inside a Memory Forest.",
                details={"path": str(candidate)},
            )
        _assert_real(candidate)
    return candidate


def safe_join(
    root: str | os.PathLike[str],
    relative: str,
    *,
    must_exist: bool = False,
) -> Path:
    """``root / relative`` after refusing every way the result could leave the root.

    ``relative`` is a forward-slash path inside the root: not absolute, no
    ``.`` or ``..`` segment, no backslash or control character, NFC. Each
    component that exists on disk must be a real directory or, for the last
    one, a regular file or directory; a symlink anywhere is refused. With
    ``must_exist`` the whole path must exist.
    """
    safe_root = require_real_root(root)
    text = str(relative)
    if not text or "\\" in text or _CONTROL_RE.search(text):
        raise MemoryForestError(
            "invalid_path",
            "A forest path must be non-empty forward-slash text without control characters.",
            details={"path": text},
        )
    if unicodedata.normalize("NFC", text) != text:
        raise MemoryForestError(
            "invalid_path",
            "A forest path must use NFC Unicode normalization.",
            details={"path": text},
        )
    parts = text.split("/")
    if text.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise MemoryForestError(
            "path_escape",
            "The requested path escapes the selected forest root.",
            details={"path": text},
        )
    current = safe_root
    for position, part in enumerate(parts):
        current = current / part
        info = system.look_or_none(current)
        if info is None:
            if must_exist:
                raise MemoryForestError(
                    "path_not_found",
                    "The requested path does not exist.",
                    details={"path": text},
                ) from None
            return safe_root.joinpath(*parts)
        if info.kind == "link":
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are not allowed inside a Memory Forest.",
                details={"path": "/".join(parts[: position + 1])},
            )
        last = position == len(parts) - 1
        if not last and info.kind != "folder":
            raise MemoryForestError(
                "invalid_path",
                "A forest path runs through something that is not a directory.",
                details={"path": "/".join(parts[: position + 1])},
            )
        if last and info.kind not in ("folder", "file"):
            raise MemoryForestError(
                "non_regular_file",
                "Only regular files and directories are allowed in a forest.",
                details={"path": text},
            )
    return current


def secure_mkdir(path: Path, *, mode: int = 0o700) -> bool:
    """Make ``path`` private. Another process having made it already is the same outcome.

    An existing directory is made private, which is what init asks for. A caller
    that must leave an existing mode alone uses ``layout.ensure_folder``.
    """
    try:
        made = system.make_folder(path, mode=mode)
    except (system.Link, system.NotAFolder) as exc:
        found = "a link" if isinstance(exc, system.Link) else "not a folder"
        raise MemoryForestError(
            "unsafe_existing_path",
            f"An expected folder is {found}.",
            details={"path": str(path)},
        ) from exc
    if not made:
        system.set_mode(path, mode)
    return made


def secure_create_file(path: Path, data: bytes, *, mode: int = 0o600) -> bool:
    if system.create_new(path, data, mode=mode):
        return True
    info = system.look(path)
    if info.kind != "file":
        raise MemoryForestError(
            "unsafe_existing_path",
            "An expected file is a link." if info.kind == "link" else f"An expected file is a {info.kind}.",
            details={"path": str(path)},
        )
    return False


def scan_forest(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> ScanResult:
    safe_root = require_real_root(root)
    directories: list[ScannedDirectory] = []
    files: list[ScannedFile] = []
    total_bytes = 0

    def visit(directory: Path, *, depth: int) -> None:
        nonlocal total_bytes
        entry_limit = (
            limits.max_files
            - len(files)
            + limits.max_directories
            - len(directories)
            + (1 if directory == safe_root else 0)
        )
        try:
            entries = system.listing(directory, looks=True)
        except system.Outcome as exc:
            raise MemoryForestError(
                "scan_failed",
                "A forest directory could not be scanned.",
                details={"path": str(directory), "reason": exc.kind},
            ) from exc
        if len(entries) > entry_limit:
            raise MemoryForestError(
                "directory_entry_count_exceeded",
                "A forest directory exceeds the remaining traversal budget.",
                details={"limit": entry_limit},
            )
        for entry in entries:
            path = entry.path
            relative = path.relative_to(safe_root).as_posix()
            info = entry.look
            assert info is not None
            if info.kind == "link":
                raise MemoryForestError(
                    "symlink_forbidden",
                    "Symlinks are not allowed inside a Memory Forest.",
                    details={"path": relative},
                )
            if info.kind == "folder":
                if path.parent == safe_root and entry.name == STATE_DIRECTORY:
                    _validate_state_directory(path)
                    continue
                child_depth = depth + 1
                if child_depth > limits.max_depth:
                    raise MemoryForestError(
                        "directory_depth_exceeded",
                        "The forest exceeds the configured directory-depth limit.",
                        details={
                            "depth": child_depth,
                            "limit": limits.max_depth,
                            "path": relative,
                        },
                    )
                directories.append(
                    ScannedDirectory(
                        path=path,
                        relative=relative,
                        mode=info.mode,
                    )
                )
                if len(directories) > limits.max_directories:
                    raise MemoryForestError(
                        "directory_count_exceeded",
                        "The forest exceeds the configured directory-count limit.",
                        details={
                            "count": len(directories),
                            "limit": limits.max_directories,
                        },
                    )
                visit(path, depth=child_depth)
                continue
            if info.kind != "file":
                raise MemoryForestError(
                    "non_regular_file",
                    "Only regular files and directories are allowed in a forest.",
                    details={"path": relative},
                )
            if info.size > limits.max_file_bytes:
                raise MemoryForestError(
                    "file_too_large",
                    "A forest file exceeds the configured per-file byte limit.",
                    details={
                        "limit": limits.max_file_bytes,
                        "path": relative,
                        "size": info.size,
                    },
                )
            files.append(
                ScannedFile(
                    path=path,
                    relative=relative,
                    mode=info.mode,
                    size=info.size,
                    mtime_ns=info.mtime_ns,
                )
            )
            total_bytes += info.size
            if len(files) > limits.max_files:
                raise MemoryForestError(
                    "file_count_exceeded",
                    "The forest exceeds the configured file-count limit.",
                    details={"count": len(files), "limit": limits.max_files},
                )
            if total_bytes > limits.max_total_bytes:
                raise MemoryForestError(
                    "byte_limit_exceeded",
                    "The forest exceeds the configured total-byte limit.",
                    details={"bytes": total_bytes, "limit": limits.max_total_bytes},
                )

    visit(safe_root, depth=0)
    return ScanResult(
        root=safe_root,
        directories=tuple(directories),
        files=tuple(files),
        total_bytes=total_bytes,
    )


def _validate_state_directory(state: Path) -> None:
    info = system.look(state)
    if info.kind != "folder":
        raise MemoryForestError(
            "unsafe_state_directory",
            "The derived-state path must be a real directory.",
            details={"path": str(state)},
        )
    _assert_real(state)
    allowed = {"forest.json", "index.sqlite3", RECEIPTS_DIRECTORY}
    for entry in system.listing(state):
        child = entry.path
        if entry.name == RECEIPTS_DIRECTORY:
            _validate_receipts_directory(child)
            continue
        if entry.name not in allowed or entry.kind != "file":
            raise MemoryForestError(
                "unsafe_state_entry",
                "Derived state may contain only known files and receipts.",
                details={"path": str(child)},
            )


def _validate_receipts_directory(receipts: Path) -> None:
    info = system.look(receipts)
    if info.kind != "folder" or info.mode != 0o700:
        raise MemoryForestError(
            "unsafe_receipts_directory",
            "The receipts directory must be a real private directory.",
            details={"path": str(receipts)},
        )
    _assert_real(receipts)
    count = 0
    for entry in system.listing(receipts, looks=True):
        count += 1
        if count > MAX_RECEIPTS:
            raise MemoryForestError(
                "receipt_count_exceeded",
                "The receipts directory exceeds the supported entry limit.",
                details={"limit": MAX_RECEIPTS},
            )
        child = entry.path
        child_info = entry.look
        assert child_info is not None
        if (
            RECEIPT_NAME_RE.fullmatch(entry.name) is None
            or child_info.kind != "file"
            or child_info.mode != 0o600
            or child_info.size > MAX_RECEIPT_BYTES
        ):
            raise MemoryForestError(
                "unsafe_receipt_entry",
                "Receipt entries must be bounded private transaction JSON files.",
                details={"path": str(child)},
            )


def secure_state_directory(root: str | os.PathLike[str]) -> Path:
    safe_root = require_real_root(root)
    state = safe_root / STATE_DIRECTORY
    try:
        made = system.make_folder(state, mode=0o700)
    except (system.Link, system.NotAFolder):
        made = False
    if not made:
        _validate_state_directory(state)
    system.set_mode(state, 0o700)
    return state
