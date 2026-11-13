"""One state for the derived folder, and every command tells it the same way.

The inputs are a product, and the test walks all of it: the forest root (writable or not), ``.forest``
(absent, writable, not writable), the pointer (absent, a name, damaged content, a mode that cannot be
read), the generation the pointer names (sound, damaged, gone) and the metadata against the settings
(equal, masking changed, pages changed since the build). A cell that cannot be made is the same as
another cell, and the test says which and why.

For every cell: every command gives the same state; every sentence of a refusal's text is checked by
doing what it says; a next move this account can run is run and leaves the index sound; a next move
for a folder that cannot be written names what an account that can write would run, and that is run
by such an account; and nothing on disk changes in a cell whose folder cannot be written.

The second part walks the index's own catalogue: every table, index and shadow table, dropped and
emptied, and status, build and query agree, and the next move cures it.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable

from memory_forest import mcp, search, timeline
from memory_forest.cli import forest_main
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES


ROOTS = ("writable", "not writable")
FORESTS = ("absent", "writable", "not writable")
POINTERS = ("absent", "a name", "damaged", "unreadable mode")
GENERATIONS = ("sound", "damaged", "gone")
METADATA = ("equal", "masking changed", "pages changed")

PAGE = "04 stm/bakery/ovens/floor.md"
LANE = "06 istm/till-bot/2051-03-02.jsonl"


def collapsed(cell: tuple[str, ...]) -> str | None:
    """Why this cell is the same as another one and is not made on its own, or None for a cell that is made."""
    _root, forest, pointer, generation, metadata = cell
    if forest == "absent" and (pointer, generation, metadata) != ("absent", "gone", "equal"):
        return "with no .forest there is no pointer, no generation and no metadata"
    if pointer in ("damaged", "unreadable mode") and (generation, metadata) != ("sound", "equal"):
        return "a pointer that cannot be read names no generation"
    if generation != "sound" and metadata != "equal":
        return "metadata is read only from a sound generation"
    return None


def expected(cell: tuple[str, ...]) -> tuple[str, bool]:
    """The state and whether the derived folder can be written, worked out from the cell, not from the code."""
    root, forest, pointer, generation, metadata = cell
    writable = (root == "writable") if forest == "absent" else (forest == "writable")
    if forest == "absent" or generation == "gone":
        kind = "missing"
    elif pointer in ("damaged", "unreadable mode"):
        kind = "pointer_damaged"
    elif generation == "damaged":
        kind = "damaged"
    elif metadata == "masking changed":
        kind = "masking_changed"
    else:
        kind = "sound"
    if pointer in ("damaged", "unreadable mode") and forest != "absent":
        kind = "pointer_damaged"
    return kind, writable


def _cli(arguments: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = forest_main(arguments)
    return code, out.getvalue(), err.getvalue()


def _tool(root: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    reply, _version = mcp.handle_message(
        root,
        "till-bot",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        "stdio",
        "2025-11-25",
    )
    result = reply["result"]
    body = json.loads(result["content"][0]["text"])
    return {"error": body} if result["isError"] else {"ok": body}


def _api(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": call()}
    except MemoryForestError as exc:
        return {"error": exc.as_dict()}


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Every entry under the root: its mode, and a file's bytes. A file this account cannot read is its mode."""
    found: dict[str, tuple[int, bytes | None]] = {".": (stat.S_IMODE(root.lstat().st_mode), None)}
    for folder, names, files in os.walk(root):
        for name in names + files:
            path = Path(folder) / name
            info = path.lstat()
            data = None
            if stat.S_ISREG(info.st_mode):
                try:
                    data = path.read_bytes()
                except PermissionError:
                    data = None
            found[str(path.relative_to(root))] = (stat.S_IMODE(info.st_mode), data)
    return found


def _release(root: Path) -> None:
    """Give the owner its modes back so the folder can be read and removed."""
    if not root.exists():
        return
    os.chmod(root, 0o700)
    for folder, names, files in os.walk(root):
        for name in names:
            os.chmod(Path(folder) / name, 0o700)
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                os.chmod(path, 0o600)


class _Cells:
    """Two built forests, copied for each cell: one with no pointer, one whose pointer names a generation."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.legacy = base / "legacy"
        _write(self.legacy, FILES)
        search.build(self.legacy)
        self.generation = base / "generation"
        shutil.copytree(self.legacy, self.generation)
        search.reset(self.generation)
        search.build(self.generation)
        self.count = 0

    def make(self, cell: tuple[str, ...]) -> Path:
        root_mode, forest, pointer, generation, metadata = cell
        self.count += 1
        root = self.base / f"cell-{self.count}"
        source = self.legacy if pointer == "absent" else self.generation
        shutil.copytree(source, root)
        folder = root / ".forest"
        if forest == "absent":
            shutil.rmtree(folder)
        else:
            current = folder / _pointed(folder)
            if generation == "damaged":
                current.write_bytes(b"not a database, and longer than one header of a page " * 4)
            elif generation == "gone":
                current.unlink()
            if metadata == "masking changed":
                database = sqlite3.connect(current)
                database.execute("UPDATE metadata SET value = 'an older masking' WHERE key = 'masking'")
                database.commit()
                database.close()
            elif metadata == "pages changed":
                for relative in (PAGE, LANE):
                    target = root / relative
                    target.write_bytes(target.read_bytes() + b"\nThe floor gained a line.\n")
            if pointer == "damaged":
                (folder / "index.current").write_bytes(b"\xff\xfe")
            elif pointer == "unreadable mode":
                os.chmod(folder / "index.current", 0)
            for leftover in folder.glob("*-wal"):
                raise AssertionError(f"a write-ahead log was left in the fixture: {leftover.name}")
            if forest == "not writable":
                os.chmod(folder, 0o555)
        if root_mode == "not writable":
            os.chmod(root, 0o555)
        return root


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _pointed(folder: Path) -> str:
    pointer = folder / "index.current"
    if not pointer.exists():
        return "index.sqlite"
    return pointer.read_text(encoding="utf-8").strip()


# ─────────────────────────── the sentences of a message ───────────────────────────

_PARAMETERS = (
    ("cure_here", re.compile(r"Run `(?P<commands>[^`]+)`\.")),
    ("cure_by_writer", re.compile(r"An account that can write the (?:\.forest|forest) folder runs (?P<commands>`[^`]+`(?:, then `[^`]+`)*)\.")),
    ("pages_changed", re.compile(r"(?P<count>\d+) files? changed since the build\.")),
)


def claims_in(message: str) -> list[tuple[str, str]]:
    """The claims a message is made of, in order. Every character of it belongs to one claim."""
    found: list[tuple[str, str]] = []
    rest = message.strip()
    sentences = sorted(search.CLAIMS.items(), key=lambda item: -len(item[1]))
    while rest:
        for claim, sentence in sentences:
            if rest.startswith(sentence):
                found.append((claim, ""))
                rest = rest[len(sentence):].lstrip()
                break
        else:
            for claim, pattern in _PARAMETERS:
                matched = pattern.match(rest)
                if matched:
                    value = matched.groupdict().get("commands") or matched.groupdict().get("count") or ""
                    found.append((claim, value))
                    rest = rest[matched.end():].lstrip()
                    break
            else:
                raise AssertionError(f"a sentence that is no claim: {rest!r} (in {message!r})")
    return found


def _commands(value: str) -> list[str]:
    return re.findall(r"`([^`]+)`", value) or [value]


class StateProductTests(unittest.TestCase):
    """Every cell of the product, through every command."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.cells = _Cells(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        _release(cls.base)
        cls._temporary.cleanup()

    def _fresh(self, cell: tuple[str, ...]) -> Path:
        root = self.cells.make(cell)
        self.addCleanup(_release, root)
        return root

    def test_every_cell_of_the_product(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("the superuser writes a folder that is not writable")
        product = list(itertools.product(ROOTS, FORESTS, POINTERS, GENERATIONS, METADATA))
        made = [cell for cell in product if collapsed(cell) is None]
        self.assertEqual(len(product), 216)
        self.assertEqual(len(made), 50)
        self.checked: dict[str, int] = {}
        for cell in made:
            with self.subTest(cell=cell):
                self._walk(cell)
        # Every sentence a state can say was said in some cell and checked there, except those this
        # product has no cell for.
        not_in_product = {
            "wal": "a write-ahead log is not an input of this product (test_v1_index_review5 walks it)",
            "masking_missing": "an index without a fingerprint is not an input of this product",
        }
        said = set(self.checked)
        self.assertEqual(
            said | set(not_in_product),
            set(search.CLAIMS) | {"cure_here", "cure_by_writer", "pages_changed"},
            self.checked,
        )

    # One cell ──────────────────────────────────────────────────────────────

    def _walk(self, cell: tuple[str, ...]) -> None:
        kind, writable = expected(cell)
        probe = self._fresh(cell)
        state = search.index_state(probe)
        self.assertEqual((state.kind, state.writable), (kind, writable), "the state is what is on disk")
        if not writable and (os.access(probe / ".forest", os.W_OK) if state.made else os.access(probe, os.W_OK)):
            self.skipTest("this user can write a folder whose mode says it cannot")
        reads = kind == "sound" or (writable and kind in ("missing", "masking_changed"))
        answers = self._run_all(cell, writable)
        messages: list[tuple[str, dict[str, Any]]] = []
        for name, answer in answers.items():
            body = answer.get("error")
            if name in ("status", "search", "timeline", "memory_search", "memory_timeline", "cli search"):
                self.assertEqual(body is None, reads, f"{name}: {answer}")
                if body is not None:
                    self.assertEqual(body["code"], "index_unreadable" if writable else "index_read_only", name)
            elif name == "build":
                runs = writable and kind not in ("damaged", "pointer_damaged")
                self.assertEqual(body is None, runs, f"{name}: {answer}")
                if body is not None:
                    self.assertEqual(body["code"], "index_unreadable" if writable else "index_read_only", name)
            elif name == "reset":
                self.assertEqual(body is None, writable, f"{name}: {answer}")
                if body is not None:
                    self.assertEqual(body["code"], "index_read_only")
            elif name in ("memory_log", "cli log"):
                self.assertEqual(body is None, writable, f"{name}: {answer}")
                if body is not None:
                    self.assertEqual(body["code"], "log_read_only")
                    self.assertEqual(body["next"], "report")
                    self.assertIn("forest log --as till-bot", body["message"])
                    continue
                payload = answer["ok"]
                self.assertTrue(payload["wrote"])
                if kind in ("damaged", "pointer_damaged"):
                    self.assertEqual(payload["reason"], "index_unreadable")
                continue
            if body is not None:
                # (1) one state, whichever command met it.
                self.assertEqual(body["details"].get("state"), kind, f"{name}: {body}")
                messages.append((name, body))
            elif name == "status":
                self.assertEqual(answer["ok"]["state"], kind)
                if answer["ok"].get("message"):
                    messages.append((name, answer["ok"]))
        self._claims_hold(cell, messages, writable)

    def _run_all(self, cell: tuple[str, ...], writable: bool) -> dict[str, dict[str, Any]]:
        """Each command on the cell as made. A writable cell gets a fresh copy per command, because a
        command may cure it. A cell that cannot be written gets one copy, and nothing in it may change."""
        calls: list[tuple[str, Callable[[Path], dict[str, Any]]]] = [
            ("status", lambda root: _api(lambda: search.status(root))),
            ("build", lambda root: _api(lambda: search.build(root))),
            ("reset", lambda root: _api(lambda: search.reset(root))),
            ("search", lambda root: _api(lambda: search.query(root, "trays", reader="till-bot"))),
            ("timeline", lambda root: _api(lambda: timeline.query(root, "2051-03-02", reader="till-bot"))),
            ("memory_search", lambda root: _tool(root, "memory_search", {"words": "trays"})),
            ("memory_timeline", lambda root: _tool(root, "memory_timeline", {"phrase": "2051-03-02"})),
            ("memory_log", lambda root: _tool(root, "memory_log", {"text": "the till counted the trays"})),
            ("cli search", lambda root: self._cli_json(["search", "trays", "--root", str(root), "--as", "till-bot"])),
        ]
        answers: dict[str, dict[str, Any]] = {}
        if writable:
            for name, call in calls:
                answers[name] = call(self._fresh(cell))
            return answers
        root = self._fresh(cell)
        before = _snapshot(root)
        for name, call in calls:
            answers[name] = call(root)
        # (4) a folder that cannot be written: nothing on disk changed, bytes and modes.
        self.assertEqual(_snapshot(root), before, "a cell that cannot be written was changed")
        return answers

    def _cli_json(self, arguments: list[str]) -> dict[str, Any]:
        code, out, err = _cli([*arguments, "--json"])
        body = json.loads(out) if out.strip() else {}
        if code != 0:
            self.assertIn("error", body, out + err)
            return {"error": body["error"]}
        return {"ok": body}

    # The sentences ─────────────────────────────────────────────────────────

    def _claims_hold(self, cell: tuple[str, ...], messages: list[tuple[str, dict[str, Any]]], writable: bool) -> None:
        checked: set[str] = set()
        for name, body in messages:
            message = body["message"]
            if message in checked:
                continue
            checked.add(message)
            claims = claims_in(message)
            nxt = body.get("next")
            if nxt == "run":
                self.assertEqual(claims[-1][0], "cure_here", message)
                self.assertEqual(_commands(claims[-1][1]), [body["command"]], message)
            if not writable:
                # No command of this account cures it: the next move says so and names the writer's.
                self.assertEqual(nxt, "report", f"{name}: {body}")
                self.assertNotIn("command", body)
                self.assertEqual(claims[-1][0], "cure_by_writer", message)
            for claim, value in claims:
                with self.subTest(claim=claim, message=message):
                    self._check(cell, claim, value, body)
                    self.checked[claim] = self.checked.get(claim, 0) + 1

    def _check(self, cell: tuple[str, ...], claim: str, value: str, body: dict[str, Any]) -> None:
        root = self._fresh(cell)
        folder = root / ".forest"
        if claim == "pointer_damaged":
            pointer = folder / "index.current"
            try:
                data = pointer.read_bytes()
            except PermissionError:
                return
            self.assertIsNone(re.fullmatch(rb"index(-[0-9a-f]{16})?\.sqlite\n?", data), data)
        elif claim == "damaged":
            self.assertTrue(_damaged(folder / _pointed(folder)))
        elif claim == "no_index":
            current = folder / _pointed(folder) if folder.exists() else folder / "index.sqlite"
            self.assertFalse(current.exists())
        elif claim == "wal":
            self.assertTrue(Path(str(folder / _pointed(folder)) + "-wal").exists())
        elif claim in ("masking_changed", "masking_missing"):
            meta = _read_meta(folder / _pointed(folder))
            if claim == "masking_missing":
                self.assertNotIn("masking", meta)
            else:
                self.assertNotEqual(meta.get("masking"), search.masking_fingerprint(root))
        elif claim in ("folder_unwritable", "root_unwritable"):
            where = folder if claim == "folder_unwritable" else root
            self.assertEqual(folder.exists(), claim == "folder_unwritable")
            with self.assertRaises(PermissionError):
                (where / "a-probe-the-test-writes").write_bytes(b"x")
        elif claim == "reads_answer":
            found = search.query(root, "trays", reader="till-bot")
            self.assertIn("trays", json.dumps(found))
        elif claim == "reads_refused":
            with self.assertRaises(MemoryForestError):
                search.query(root, "trays", reader="till-bot")
            with self.assertRaises(MemoryForestError):
                timeline.query(root, "2051-03-02", reader="till-bot")
        elif claim == "pages_changed":
            listed = search.status(root)["stale"]
            self.assertEqual(len(listed), int(value))
            stored = _stored_hashes(folder / _pointed(folder))
            for relative in listed:
                digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
                self.assertNotEqual(stored.get(relative), digest, relative)
        elif claim == "cure_here":
            self._cure(root, _commands(value), as_writer=False)
        elif claim == "cure_by_writer":
            self._cure(root, _commands(value), as_writer=True)
        else:
            raise AssertionError(f"no check for the claim {claim!r}")

    def _cure(self, root: Path, commands: list[str], *, as_writer: bool) -> None:
        """Run the commands, then every ``run`` move their answers give, and see the index sound."""
        modes: dict[Path, int] = {}
        if as_writer:
            for path in (root, root / ".forest"):
                if path.exists():
                    modes[path] = stat.S_IMODE(path.stat().st_mode)
                    os.chmod(path, 0o700)
        try:
            queue = list(commands)
            for _ in range(4):
                if not queue:
                    break
                command = queue.pop(0)
                words = command.split()
                self.assertEqual(words[0], "forest", command)
                code, out, err = _cli([*words[1:], "--root", str(root), "--json"])
                self.assertEqual(code, 0, f"{command}: {out}{err}")
                answer = json.loads(out)
                if answer.get("next") == "run" and not queue:
                    queue.append(answer["command"])
            self.assertEqual(queue, [], "the moves did not end")
            pointer = root / ".forest" / "index.current"
            if pointer.exists():
                self.assertEqual(stat.S_IMODE(pointer.stat().st_mode), 0o600)
        finally:
            for path, mode in modes.items():
                os.chmod(path, mode)
        state = search.status(root)
        self.assertEqual(state["state"], "sound", state)
        self.assertNotIn("next", state)
        self.assertIn("trays", json.dumps(search.query(root, "trays", reader="till-bot")))


def _damaged(path: Path) -> bool:
    """Read with SQLite directly: not a database, an object of the schema missing, or a filled table empty."""
    try:
        database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            names = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
            if not {name for _kind, name in search.expected_objects("trigram" in search.tokenizer_name())} <= names:
                return True
            for table in sorted(names & set(_reference_filled())):
                if database.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is None:
                    return True
            return False
        finally:
            database.close()
    except sqlite3.Error:
        return True


_FILLED: list[str] = []


def _reference_filled() -> list[str]:
    """The tables a build of the fixture fills, from a build in a folder of its own."""
    if not _FILLED:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch).resolve() / "reference"
            _write(root, FILES)
            search.build(root)
            database = sqlite3.connect(root / ".forest" / "index.sqlite")
            try:
                names = [row[0] for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )]
                _FILLED.extend(name for name in names if database.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone())
            finally:
                database.close()
    return _FILLED


def _read_meta(path: Path) -> dict[str, str]:
    database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        return dict(database.execute("SELECT key, value FROM metadata").fetchall())
    finally:
        database.close()


def _stored_hashes(path: Path) -> dict[str, str]:
    database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        return dict(database.execute("SELECT path, sha256 FROM files").fetchall())
    finally:
        database.close()


# ─────────────────────────── the index's own catalogue ───────────────────────────

# A table that a build of the fixture leaves empty: emptying it changes nothing, and the index is sound.
EMPTY_AT_BUILD = {
    "vectors": "no embedding provider is registered, so a build stores no vector and a query reads none",
}
# What SQLite itself will not drop.
UNDROPPABLE = re.compile(r"sqlite_autoindex_")


class CatalogueTests(unittest.TestCase):
    """Every object the index holds, dropped and emptied: status, build and query agree, and the move cures."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.built = cls.base / "built"
        _write(cls.built, FILES)
        search.build(cls.built)
        database = sqlite3.connect(cls.built / ".forest" / "index.sqlite")
        try:
            cls.catalogue = sorted(
                (str(kind), str(name)) for kind, name in database.execute("SELECT type, name FROM sqlite_master")
            )
            cls.empty = sorted(
                name
                for kind, name in cls.catalogue
                if kind == "table" and database.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone() is None
            )
        finally:
            database.close()
        cls.count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _copy(self) -> Path:
        type(self).count += 1
        root = self.base / f"copy-{self.count}"
        shutil.copytree(self.built, root)
        return root

    def _changed(self, statement: str) -> Path:
        root = self._copy()
        database = sqlite3.connect(root / ".forest" / "index.sqlite")
        try:
            database.execute(statement)
            database.commit()
        finally:
            database.close()
        return root

    def test_the_catalogue_is_the_schema(self) -> None:
        names = {name for _kind, name in self.catalogue if not UNDROPPABLE.match(name)}
        expected = {name for _kind, name in search.expected_objects("trigram" in search.tokenizer_name())}
        self.assertEqual(names, expected)
        self.assertEqual(self.empty, sorted(EMPTY_AT_BUILD), "a table a build leaves empty is named with its reason")

    def test_every_object_dropped(self) -> None:
        for kind, name in self.catalogue:
            with self.subTest(kind=kind, name=name):
                if UNDROPPABLE.match(name):
                    with self.assertRaises(sqlite3.Error):
                        self._changed(f'DROP INDEX "{name}"')
                    continue
                verb = "INDEX" if kind == "index" else "TABLE"
                self._agree_and_cure(f'DROP {verb} IF EXISTS "{name}"', sound=False)

    def test_every_table_emptied(self) -> None:
        for kind, name in self.catalogue:
            if kind != "table":
                continue
            with self.subTest(name=name):
                self._agree_and_cure(f'DELETE FROM "{name}"', sound=name in EMPTY_AT_BUILD)

    def _agree_and_cure(self, statement: str, *, sound: bool) -> None:
        state = search.status(self._changed(statement)) if sound else None
        status = _api(lambda: search.status(self._changed(statement)))
        query = _api(lambda: search.query(self._changed(statement), "trays", reader="till-bot"))
        build = _api(lambda: search.build(self._changed(statement)))
        if sound:
            assert state is not None
            self.assertEqual(state["state"], "sound")
            self.assertNotIn("next", state)
            self.assertIn("ok", query)
            self.assertIn("ok", build)
            return
        if "error" in status:
            body = status["error"]
            self.assertEqual(body["code"], "index_unreadable", body)
            self.assertEqual(body["details"]["state"], "damaged")
            self.assertEqual(query.get("error", {}).get("code"), "index_unreadable", query)
            self.assertEqual(build.get("error", {}).get("code"), "index_unreadable", build)
            first = body["command"]
        else:
            # An emptied metadata table is no index yet: status says so, and a query or a build makes one.
            payload = status["ok"]
            self.assertEqual(payload["state"], "missing", payload)
            self.assertIn("ok", query, query)
            self.assertIn("ok", build, build)
            first = payload["command"]
        root = self._changed(statement)
        queue = [first]
        while queue:
            words = queue.pop(0).split()
            code, out, err = _cli([*words[1:], "--root", str(root), "--json"])
            self.assertEqual(code, 0, out + err)
            answer = json.loads(out)
            if answer.get("next") == "run":
                queue.append(answer["command"])
        cured = search.status(root)
        self.assertEqual(cured["state"], "sound", cured)
        self.assertNotIn("next", cured)
        self.assertIn("trays", json.dumps(search.query(root, "trays", reader="till-bot")))


if __name__ == "__main__":
    unittest.main()


# ─────────────────────────── what leaves a public function ───────────────────────────


class BoundaryTests(unittest.TestCase):
    """An internal code never leaves: it is turned into a public one at the edge of every public function."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name).resolve() / "forest"
        _write(cls.root, FILES)
        search.build(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _public_calls(self) -> list[tuple[str, Callable[[], Any]]]:
        root = self.root
        return [
            ("status", lambda: search.status(root)),
            ("query", lambda: search.query(root, "trays", reader="till-bot")),
            ("connect", lambda: search.connect(root)),
            ("require_index", lambda: search.require_index(root)),
            ("index_state", lambda: search.index_state(root)),
            ("refresh_lanes_and_daily", lambda: search.refresh_lanes_and_daily(root)),
            ("timeline.query", lambda: timeline.query(root, "2051-03-02", reader="till-bot")),
            ("timeline.subject", lambda: timeline.subject(root, "deck-oven", reader="till-bot")),
        ]

    def test_every_public_read_turns_the_internal_code_into_a_public_one(self) -> None:
        from unittest import mock

        internal = MemoryForestError("index_protocol", "inside", details={"path": ".forest/index.sqlite"})

        def raise_internal(*_args: Any, **_kwargs: Any) -> Any:
            raise internal

        with mock.patch.object(search, "_open_state", raise_internal), \
                mock.patch.object(search, "_retry_pause", lambda seconds: None):
            for name, call in self._public_calls():
                with self.subTest(call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertNotIn(refused.exception.code, search.INTERNAL_CODES)
                    self.assertEqual(refused.exception.code, "index_io_error")
                    self.assertEqual(refused.exception.next_step, "report")

    def test_every_public_write_turns_the_internal_code_into_a_public_one(self) -> None:
        from unittest import mock

        def raise_internal(*_args: Any, **_kwargs: Any) -> Any:
            raise MemoryForestError("index_protocol", "inside", details={"path": ".forest/index.sqlite"})

        with mock.patch.object(search, "_write_held", raise_internal), \
                mock.patch.object(search, "_reset_held", raise_internal), \
                mock.patch.object(search, "_retry_pause", lambda seconds: None):
            for name, call in (
                ("build", lambda: search.build(self.root)),
                ("build waiting", lambda: search.build(self.root, wait_seconds=0.3)),
                ("update_files", lambda: search.update_files(self.root, [LANE])),
                ("reset", lambda: search.reset(self.root)),
            ):
                with self.subTest(call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_io_error")

    def test_a_raw_sqlite_error_is_classified_at_the_edge(self) -> None:
        from unittest import mock

        def raise_raw(*_args: Any, **_kwargs: Any) -> Any:
            raise sqlite3.DatabaseError("database disk image is malformed")

        with mock.patch.object(search, "_open_state", raise_raw):
            for name, call in self._public_calls():
                with self.subTest(call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_unreadable")

    def test_every_clock_sequence_of_a_protocol_retry_leaves_as_io(self) -> None:
        """The loop alone reads the clock. Each row is when the deadline passes: before the first retry,
        after one, after two, after three. The review's sequence (inside the read the deadline had not
        passed, in the loop it had) is the first row, because the read no longer looks at the clock."""
        from unittest import mock

        rows = [(0.0,) + (0.0,) * before + (search.READER_TIMEOUT_SECONDS + 1.0,) for before in range(4)]
        for row in rows:
            with self.subTest(clock=row):
                ticks = iter(row + (search.READER_TIMEOUT_SECONDS + 1.0,) * 8)
                attempts = {"n": 0}

                def checked(_root: Any, _fn: Any, describe: bool = False) -> Any:
                    attempts["n"] += 1
                    raise MemoryForestError("index_protocol", "inside", details={"path": ".forest/index.sqlite"})

                with mock.patch.object(search, "refresh_lanes_and_daily", lambda root: 0), \
                        mock.patch.object(search, "_checked_read", checked), \
                        mock.patch.object(search, "_retry_pause", lambda seconds: None), \
                        mock.patch.object(search.time, "monotonic", lambda: next(ticks)):
                    with self.assertRaises(MemoryForestError) as refused:
                        search.query(self.root, "trays", reader="till-bot")
                self.assertEqual(refused.exception.code, "index_io_error")
                self.assertEqual(attempts["n"], len(row) - 1)


# ─────────────────────────── open first, then look ───────────────────────────


class LookThenOpenTests(unittest.TestCase):
    """What is opened is checked after it is opened. Nothing blocks on a fifo, and nothing that went away
    between two steps leaves as a raw error."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve() / "forest"
        _write(self.root, FILES)
        search.build(self.root)
        self.folder = self.root / ".forest"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _within(self, call: Callable[[], Any], seconds: float = 20) -> dict[str, Any]:
        """Run ``call`` in a thread and wait for it on an event with a deadline."""
        import threading

        outcome: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            outcome.update(_api(call))
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(seconds), "the call blocked on what was put in the file's place")
        return outcome

    def test_a_fifo_in_the_place_of_the_pointer_or_the_index_does_not_block(self) -> None:
        pointer = self.folder / "index.current"
        os.mkfifo(pointer)
        try:
            for name, call in (
                ("status", lambda: search.status(self.root)),
                ("query", lambda: search.query(self.root, "trays", reader="till-bot")),
                ("reset", lambda: search.reset(self.root)),
            ):
                with self.subTest(place="pointer", call=name):
                    self.assertEqual(self._within(call)["error"]["code"], "index_reset_refused")
        finally:
            pointer.unlink()
        index = self.folder / "index.sqlite"
        index.unlink()
        os.mkfifo(index)
        for name, call in (
            ("status", lambda: search.status(self.root)),
            ("query", lambda: search.query(self.root, "trays", reader="till-bot")),
            ("build", lambda: search.build(self.root)),
        ):
            with self.subTest(place="index", call=name):
                self.assertEqual(self._within(call)["error"]["code"], "index_cannot_open")

    def test_an_index_file_that_goes_away_before_sqlite_opens_it_is_busy(self) -> None:
        from unittest import mock

        real = sqlite3.connect
        index = self.folder / "index.sqlite"

        def gone_first(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            if index.exists():
                index.unlink()
            return real(*args, **kwargs)

        with mock.patch.object(search.sqlite3, "connect", gone_first):
            refused = _api(lambda: search.status(self.root))
        self.assertEqual(refused["error"]["code"], "index_busy", refused)
        self.assertEqual(refused["error"]["next"], "retry")
        self.assertFalse(index.exists(), "no empty file was made where the index went away")

    def test_a_page_that_goes_away_during_a_build_is_busy(self) -> None:
        from unittest import mock

        real = search._open_page
        page = self.root / PAGE
        (self.root / PAGE).write_text(page.read_text(encoding="utf-8") + "\nA new line.\n", encoding="utf-8")

        def gone(path: Path) -> Any:
            if path.name == page.name:
                raise FileNotFoundError(str(path))
            return real(path)

        with mock.patch.object(search, "_open_page", gone):
            answer = search.build(self.root)
            code, out, err = _cli(["index", "build", "--root", str(self.root), "--json"])
        self.assertTrue(answer["busy"], answer)
        self.assertIn("A page went away", answer["message"])
        refused = json.loads(out)["error"]
        self.assertEqual((code, refused["code"], refused["next"]), (2, "index_busy", "retry"))
        self.assertIn("A page went away", refused["message"])
        self.assertEqual(search.status(self.root)["state"], "sound")
