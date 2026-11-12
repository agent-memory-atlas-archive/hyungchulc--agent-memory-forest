"""Where the package starts a model for a stage.

A stage's own session is not a conversation to take in. ``prepare`` records the
session id, returns an environment that carries ``MEMORY_FOREST_INTERNAL``, and
opens the prompt with the marker line intake recognises. The caller passes that
environment to the model process. Intake also reads the session file and the
marker line, so a sweep drops the session even when the environment is gone.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from . import layout
from .adapters.common import MARKER, MARKER_LINE, session_token
from .errors import MemoryForestError


SESSIONS: str = f"{layout.STATE_DIR}/intake/internal-sessions"


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


def record_session(root: str | os.PathLike[str], session_id: str) -> None:
    """Append ``session_id`` to the stage's list. The line is the id and nothing else."""
    token = session_token(session_id)
    if token is None:
        raise MemoryForestError(
            "intake_session_invalid",
            "A stage session id cannot be a path.",
            details={"session": ""},
        )
    if session_recorded(root, token):
        return
    from .intake import _append

    layout.ensure_folder(root, f"{layout.STATE_DIR}/intake")
    _append(root, SESSIONS, token + "\n")


def session_recorded(root: str | os.PathLike[str], session_id: str) -> bool:
    """Whether a stage recorded ``session_id``. A missing file is no sessions."""
    path = layout.resolve(root, SESSIONS)
    if not path.is_file() or path.is_symlink():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return session_id in text.splitlines()


def model_environment() -> Mapping[str, str]:
    """The environment a stage sets on the model process it starts."""
    return {MARKER: "1"}
