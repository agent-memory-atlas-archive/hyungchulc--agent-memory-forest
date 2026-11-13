"""The one function that makes what leaves a command or a tool.

The command line and the MCP tools add only the form (lines, JSON, a tool result). The code, the move,
the message and which details an agent sees are made here, so two doors cannot disagree. Everything that
can fail is done before a stream is written: the caller has already read the masking ledger through the
door. When that ledger cannot be had, a refusal leaves in fixed words and nothing the masking would have
read is printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .errors import REFUSALS, MemoryForestError

# Details that are words of a fixed vocabulary. A refusal keeps them when its values cannot be masked.
KEPT_DETAILS = ("access", "kind", "state")

_INTERNAL = (
    "The package met a fault in its own code. Tell the owner the class the details name."
)


@dataclass(frozen=True, slots=True)
class Masking:
    """The ledger and the settings, already read through the door."""

    config: Config
    ledger: Any


def body(
    outcome: Any,
    *,
    reader: str,
    masking: Masking | None,
    root: str | None = None,
    plain: bool = False,
) -> Any:
    """The body of an answer or a refusal, for ``reader`` (``owner``, ``agent`` or ``group``).

    ``masking`` None and ``plain`` means a command with no forest: a refusal is folded for an agent and
    nothing is read from the disk. ``masking`` None without ``plain`` means the ledger could not be had:
    a refusal leaves in fixed words, and a success is returned as it is only when the caller has no
    forest (``plain`` is then set by the caller for lines it already trusts).
    """
    if isinstance(outcome, MemoryForestError):
        return _refusal(outcome, reader=reader, masking=masking, root=root, plain=plain)
    if masking is None:
        return outcome
    from .search import mask_result

    return mask_result(outcome, config=masking.config, ledger=masking.ledger)


def _refusal(
    refusal: MemoryForestError,
    *,
    reader: str,
    masking: Masking | None,
    root: str | None,
    plain: bool,
) -> dict[str, Any]:
    checked = _checked(refusal)
    if masking is None and not plain:
        return _fixed(checked)
    if masking is None:
        payload = checked.as_dict()
        return _for_reader(payload, reader=reader, root=root)
    from .credentials import shown

    masked = shown(checked.as_dict(), config=masking.config, ledger=masking.ledger)
    if not isinstance(masked, dict):
        return _fixed(_checked(MemoryForestError("internal_error", _INTERNAL, next_step="report")))
    return _checked_dict(_for_reader(masked, reader=reader, root=root))


def _for_reader(payload: dict[str, Any], *, reader: str, root: str | None) -> dict[str, Any]:
    """An agent or a group sees no lock token, no host and no absolute forest path. The owner does."""
    if reader == "owner":
        return payload
    details = payload.get("details")
    if isinstance(details, dict):
        payload = {**payload, "details": {key: value for key, value in details.items() if key != "token"}}
    from .mcp import _fold_refusal

    folded = _fold_refusal(payload, root or "")
    return folded if isinstance(folded, dict) else payload


def _checked(refusal: MemoryForestError) -> MemoryForestError:
    """A refusal whose code is in the table and whose move is the table's. Anything else is internal."""
    listed = REFUSALS.get(refusal.code)
    if listed is None or refusal.next_step != listed[0]:
        return MemoryForestError("internal_error", _INTERNAL, details={"reason": "refusal"}, next_step="report")
    return refusal


def _checked_dict(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("code")
    listed = REFUSALS.get(code) if isinstance(code, str) else None
    move = payload.get("next")
    if listed is None or move != listed[0]:
        return _fixed(MemoryForestError("internal_error", _INTERNAL, details={"reason": "refusal"}, next_step="report"))
    return payload


def _fixed(refusal: MemoryForestError) -> dict[str, Any]:
    """The code, the move and the message, and only the details that are fixed words."""
    raw = refusal.as_dict()
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    raw["details"] = {key: details[key] for key in KEPT_DETAILS if key in details}
    return raw
