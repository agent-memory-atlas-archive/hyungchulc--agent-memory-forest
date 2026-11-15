"""A memory-work session marks itself, so intake leaves it out.

The package starts no model: the owner's own agent carries out the routine. A
session that agent opens for the routine is not a conversation to take in. A
keeper that starts such a session from Python calls ``prepare``: it records the
session id (one small file named by its hash), returns an environment that
carries ``MEMORY_FOREST_INTERNAL``, and opens the prompt with the marker line
intake recognises. The caller passes that environment to the model process.
Intake looks for the session's file, with one ``stat``, while the session's
decision is open, and reads the marker line on its first owner turn, so a sweep
drops the session even when the environment is gone.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping

from . import layout
from .adapters.common import MARKER, MARKER_LINE, session_token
from .errors import MemoryForestError


SESSIONS: str = f"{layout.STATE_DIR}/intake/stage-sessions"
# A sweep removes the session files older than this. A stage session is decided on its first owner turn,
# which a sweep or a hook reads long before then.
SESSION_DAYS: int = 30


def prepare(
    root: str | os.PathLike[str],
    session_id: str,
    prompt: str,
) -> tuple[dict[str, str], str]:
    """Record ``session_id`` and return the environment and prompt a stage sends."""
    record_session(root, session_id)
    return {MARKER: "1"}, mark_prompt(prompt)


def mark_prompt(prompt: str) -> str:
    """``prompt`` with the marker line first. A prompt that already opens with it is unchanged."""
    if prompt == MARKER_LINE or prompt.startswith(MARKER_LINE + "\n"):
        return prompt
    if not prompt:
        return MARKER_LINE + "\n"
    return MARKER_LINE + "\n" + prompt


def _session_name(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def record_session(root: str | os.PathLike[str], session_id: str) -> None:
    """One small file for the stage session, named by a hash of its id. The file holds nothing."""
    token = session_token(session_id)
    if token is None:
        raise MemoryForestError(
            "intake_session_invalid",
            "A stage session id cannot be a path.",
            details={"session": ""},
        )
    layout.ensure_folder(root, SESSIONS)
    path = layout.resolve(root, f"{SESSIONS}/{_session_name(token)}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    os.utime(path)


def session_recorded(root: str | os.PathLike[str], session_id: str) -> bool:
    """Whether a stage recorded ``session_id``: one ``stat``, whatever the number of stage sessions."""
    token = session_token(session_id)
    if token is None:
        return False
    try:
        return os.path.isfile(layout.resolve(root, f"{SESSIONS}/{_session_name(token)}"))
    except (OSError, MemoryForestError):
        return False


def prune_sessions(root: str | os.PathLike[str], *, days: int = SESSION_DAYS) -> int:
    """Remove the session files older than ``days``. A sweep calls it; a hook never lists the folder."""
    try:
        folder = layout.resolve(root, SESSIONS)
    except MemoryForestError:
        return 0
    if not folder.is_dir() or folder.is_symlink():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                os.unlink(entry.path)
                removed += 1
        except OSError:
            continue
    return removed


def model_environment() -> Mapping[str, str]:
    """The environment a memory-work session's process carries."""
    return {MARKER: "1"}
