"""Caller keys are keyed digests in lanes; this small per-lane lookup is disposable.

The credential ledger owns the digest mechanism and its private forest key.
Holding a lane alone reveals equality of keys, not a way to test guesses without
that key. Holding the forest key too permits an offline dictionary attack.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from . import credential_ledger, layout, sources, system
from .errors import MemoryForestError


def digest(root, value: str) -> str:
    """Use the existing HMAC-SHA256 mechanism and key, creating that key before any lane commit."""
    ledger = credential_ledger.load(root)
    if ledger is None:
        folder = layout.ensure_folder(root, credential_ledger.FOLDER)
        system.set_mode(folder, 0o700)
        credential_ledger._make_key(layout.resolve(root, credential_ledger.KEY_FILE))
        system.sync_folder(folder)
        ledger = credential_ledger.load(root)
    return ledger.digest(value)


def of_record(root, record: Mapping[str, Any]) -> str | None:
    """The persisted digest, or the digest of a legacy raw key (never return the raw key)."""
    value = record.get("turn_digest")
    if isinstance(value, str):
        return value
    legacy = record.get("turn")
    return digest(root, legacy) if isinstance(legacy, str) else None


def path(root, agent: str):
    return layout.resolve(root, f"{layout.STATE_DIR}/intake/turns/{agent}.json")


def scan(root, agent: str) -> dict[str, Any]:
    """Reconstruct exact addresses from every complete line of every lane day, with no age limit."""
    keys: dict[str, Any] = {}
    for name in sorted(sources.lanes_before(root, agent)):
        data = system.read_bytes(layout.resolve(root, f"{layout.ISTM}/{agent}/{name}"))
        for number, raw in enumerate(data.split(b"\n")[:-1]):
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict):
                continue
            token = of_record(root, record)
            if token is not None:
                session = str(record.get("session") or "")
                keys.setdefault(session, {}).setdefault(token, [name[:-6], number, record.get("id")])
    return keys


def load(root, agent: str) -> dict[str, Any]:
    """A matching whole record, otherwise the lanes. Never trust a cache for a conflicting turn's words."""
    stamps = sources.lanes_before(root, agent)
    try:
        record = json.loads(system.read_text(path(root, agent)))
    except (MemoryForestError, system.Outcome, UnicodeError, ValueError):
        record = None
    if isinstance(record, dict) and record.get("lanes") == stamps and _valid(record.get("keys")):
        keys = record["keys"]
        if record.get("seal") == sources._seal(stamps, keys):
            return keys
    return scan(root, agent)


def _valid(keys: Any) -> bool:
    """Validate every session, digest and address; never silently keep the well-formed subset."""
    if not isinstance(keys, dict):
        return False
    for session, turns in keys.items():
        if not isinstance(session, str) or not isinstance(turns, dict):
            return False
        for token, address in turns.items():
            if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
                return False
            if not isinstance(address, list) or len(address) != 3:
                return False
            day, line, identifier = address
            if (not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day)
                    or type(line) is not int or line < 0 or not isinstance(identifier, str)):
                return False
    return True


def save(root, agent: str) -> None:
    """Derived bookkeeping, called only after the lane commit."""
    keys = scan(root, agent)
    stamps = sources.lanes_before(root, agent)
    layout.ensure_folder(root, f"{layout.STATE_DIR}/intake/turns")
    system.write_whole(path(root, agent), json.dumps({"lanes": stamps, "keys": keys,
                                                   "seal": sources._seal(stamps, keys)}, sort_keys=True) + "\n")
