"""Nearest reproducible attempts for outcomes a healthy disk did not give."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest import mock

from memory_forest import derived, search, system


def attempt(step, wanted, nearest):
    def probe(case, root):
        near_step, near_value = nearest if isinstance(nearest, tuple) else (step, nearest)
        make = next(make for at, value, make in case._cases() if at == near_step and value == near_value)
        made = make(root)
        options = made if isinstance(made, dict) else {}
        target = made if isinstance(made, Path) else root
        with mock.patch.object(system, "OPEN_DEADLINE_SECONDS", options.get("deadline", 10)):
            observed = case._facts(target)
        case.assertNotEqual(getattr(observed, step), wanted)
        case.assertEqual(getattr(observed, near_step), near_value)
        return observed
    return probe


def sidecar(case, root, suffix, kind):
    path = root / ".forest/index.sqlite"
    side = Path(str(path) + suffix)
    raw = []
    def swap(*, path):
        if kind == "fifo":
            os.mkfifo(side)
        else:
            side.write_bytes(b"wal")
            os.chmod(side, 0)
    def prepare(db):
        try:
            return search._prepare(db)
        except sqlite3.Error as error:
            raw.append(error.sqlite_errorcode & 255)
            raise
    try:
        with system.at(system.POINT_INDEX_OPEN, swap):
            found = derived.read_facts(root, prepare=prepare, fingerprint=search._fingerprint)
        case.assertEqual(found.facts.beside, "not_a_file" if kind == "fifo" else "unreadable")
        case.assertEqual(found.facts.metadata, derived.NR)
        case.assertIn(side.name, found.facts.part)
        case.assertEqual(raw, [sqlite3.SQLITE_IOERR if kind == "fifo" else sqlite3.SQLITE_CANTOPEN])
        found.close()
    finally:
        side.unlink(missing_ok=True)


def metadata_read_only(case, root):
    path = root / ".forest/index.sqlite"
    os.chmod(path, 0o444)
    try:
        db = sqlite3.connect(path)
        try:
            case.assertTrue(db.execute("SELECT * FROM metadata").fetchall())
            db.execute("PRAGMA query_only=ON")
            with case.assertRaises(sqlite3.OperationalError) as refused:
                db.execute("UPDATE metadata SET value='other'")
            case.assertEqual(refused.exception.sqlite_errorcode & 255, sqlite3.SQLITE_READONLY)
        finally:
            db.close()
        case.assertEqual(case._facts(root).metadata, "equal")
    finally:
        os.chmod(path, 0o600)


RACE_PROBES = {
    ("metadata", "cannot_open"): lambda case, root: sidecar(case, root, "-wal", "unreadable"),
    ("metadata", "io"): lambda case, root: sidecar(case, root, "-shm", "fifo"),
    ("metadata", "read_only"): metadata_read_only,
}


def metadata_locked(case, root):
    uri = "file:synthetic-lock-probe?mode=memory&cache=shared"
    left = sqlite3.connect(uri, uri=True, timeout=0.01)
    right = sqlite3.connect(uri, uri=True, timeout=0.01)
    try:
        left.execute("CREATE TABLE probe (n)")
        left.commit()
        left.execute("INSERT INTO probe VALUES (1)")
        with case.assertRaises(sqlite3.OperationalError) as refused:
            right.execute("SELECT * FROM probe").fetchall()
        case.assertEqual(refused.exception.sqlite_errorcode & 255, sqlite3.SQLITE_LOCKED)
    finally:
        left.close()
        right.close()
    case.assertEqual(case._facts(root).metadata, "equal")


def metadata_full(case, root):
    path = root / ".forest/quota-probe.sqlite"
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA page_size=512")
        db.execute("CREATE TABLE probe (data)")
        count = db.execute("PRAGMA page_count").fetchone()[0]
        db.execute(f"PRAGMA max_page_count={count}")
        with case.assertRaises(sqlite3.OperationalError) as refused:
            db.execute("INSERT INTO probe VALUES (zeroblob(65536))")
        case.assertEqual(refused.exception.sqlite_errorcode & 255, sqlite3.SQLITE_FULL)
    finally:
        db.close()
        path.unlink(missing_ok=True)
    case.assertEqual(case._facts(root).metadata, "equal")
