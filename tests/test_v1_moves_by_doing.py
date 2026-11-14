"""Every ``fix`` and ``run`` next move, proved by doing what it says.

``errors.REFUSALS`` gives each refusal code its next move. This file proves two of them by doing:

- ``fix``: the call itself is wrong, and a corrected call succeeds on the forest as it is. For every such
  code a row holds the wrong call and the corrected one. The wrong call is refused with exactly that code
  and ``next: fix``, and no entry under the forest root changes (path, bytes and mode). The corrected call
  then succeeds on the same forest, which nobody changed in between.
- ``run``: a command this caller may run now, which changes the state. For every such code a row makes
  the state and gets the refusal. Its ``command`` is parsed by the command line's own parser and holds no
  placeholder and no prose; it is run, and the same call is answered afterwards.

A call is a ``forest`` command, a 0.3 ``memory-forest`` command, an MCP tool call, or a public function of
the library. A row uses the command or the tool that reaches its code with a wrong call, and a public
function only where none does.

A walk proves the closed set: the ``fix`` codes of the table are the codes with a fix row, and the ``run``
codes the codes with a run row. A code with neither is ``retry`` or ``report``; the codes named below say why
no row can hold them (``NOT_FIX``, ``UNREACHABLE``), and the rows a bug in the package keeps from holding
(``BUGS``) are expected to fail until the package is changed.

The bakery, Ilse Varrow, the potter Juno Hale, the observatory crew, the bots and every page are invented.
Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import io
import json
import os
import re
import select
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import bounds  # installs a deadline for every unittest invocation
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

from memory_forest import cli, cli_intake, core, errors, intake, lint, locking, mcp, movelog
from memory_forest import parse_relative_route, repeats, retrieval, search, sources, stage, writer
from memory_forest import plan as forest_plan
from memory_forest import record as forest_record
from memory_forest import registry as forest_registry
from memory_forest.config import load_config, parse_config
from memory_forest.errors import MemoryForestError
from memory_forest.model import parse_page

from test_v1_find import FILES as FIND_FILES
import codes_walk
from v1_support import BAKERY_FILES, SYNTHETIC_FOREST


# ─────────────────────────── forests ───────────────────────────


def _write(root: Path, files: Mapping[str, str | bytes | None]) -> None:
    """Pages written (text or bytes) or taken away (None) under ``root``, private as the package writes them:
    a file 0600 and a new folder 0700 (the 0.3 writer refuses a plan file others can read)."""
    for relative, data in files.items():
        target = root / relative
        if data is None:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif os.path.lexists(target):
                target.unlink()
            continue
        for folder in reversed(target.relative_to(root).parents):
            if not (root / folder).is_dir():
                (root / folder).mkdir(mode=0o700)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data, encoding="utf-8")
        os.chmod(target, 0o600)


def _settle(root: Path) -> None:
    """A version 1 forest as its first command leaves it: the registry compiled, the writer lock's file made."""
    for words in (["registry", "build"], ["recover"]):
        code, out, err = _forest_cli([*words, "--root", str(root), "--json"])
        if code != 0:
            raise AssertionError(f"the forest did not settle: forest {' '.join(words)}: {out}{err}")


def _synthetic(root: Path) -> None:
    shutil.copytree(SYNTHETIC_FOREST, root)
    _settle(root)


def _bakery(root: Path) -> None:
    _write(root, BAKERY_FILES)
    _settle(root)


def _find(root: Path) -> None:
    _write(root, FIND_FILES)
    _settle(root)


def _legacy(root: Path) -> None:
    """A 0.3 forest: ``memory-forest init <folder> --example``."""
    code, out, err = _legacy_cli(["init", str(root), "--example", "--json"])
    if code != 0:
        raise AssertionError(f"memory-forest init failed: {out}{err}")


def _find_indexed(root: Path) -> None:
    _find(root)
    code, out, err = _forest_cli(["index", "build", "--root", str(root), "--json"])
    if code != 0:
        raise AssertionError(f"forest index build failed: {out}{err}")


def _legacy_indexed(root: Path) -> None:
    _legacy(root)
    code, out, err = _legacy_cli(["index", str(root), "--json"])
    if code != 0:
        raise AssertionError(f"memory-forest index failed: {out}{err}")


def _folder(root: Path) -> None:
    """An empty folder, no forest in it."""
    root.mkdir(mode=0o700)


# Each forest a row names, by the name the row gives. Every one is made fresh for the row.
FORESTS: dict[str, Callable[[Path], None]] = {
    "synthetic": _synthetic,  # Juno Hale's pottery studio (examples/synthetic-forest): check and lint are clean
    "bakery": _bakery,  # Ilse Varrow's bakery (tests/v1_support.py)
    "find": _find,  # the bakery with its bots' lanes and a daily page (tests/test_v1_find.py)
    "find-indexed": _find_indexed,  # the same, its search index built
    "legacy": _legacy,  # a 0.3 forest made by `memory-forest init --example`
    "legacy-indexed": _legacy_indexed,  # the same, its 0.3 index built
    "folder": _folder,  # an empty folder
}


# ─────────────────────────── calls ───────────────────────────


def _filled(text: str, root: Path, base: Path) -> str:
    """``{root}``, ``{base}`` and ``{forest_id}`` filled in: the forest, the folder that holds it, its 0.3 id."""
    text = text.replace("{root}", str(root)).replace("{base}", str(base))
    if "{forest_id}" in text:
        identity = json.loads((root / ".memory-forest" / "forest.json").read_text(encoding="utf-8"))
        text = text.replace("{forest_id}", identity["forest_id"])
    return text


class _Streams:
    """Stdin from bytes (None: a process started with stdin closed), and a stdout and stderr that also take
    bytes (``archive show`` writes them)."""

    def __init__(self, stdin: bytes | None) -> None:
        self.stdin = None if stdin is None else io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
        self.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        self.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")

    def __enter__(self) -> _Streams:
        self._patches = [
            mock.patch.object(sys, "stdin", self.stdin),
            redirect_stdout(self.stdout),
            redirect_stderr(self.stderr),
        ]
        for patch in self._patches:
            patch.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        for patch in reversed(self._patches):
            patch.__exit__(*exc)

    def text(self, stream: io.TextIOWrapper) -> str:
        stream.flush()
        return stream.buffer.getvalue().decode("utf-8", "replace")  # type: ignore[attr-defined]


def _forest_cli(argv: list[str], stdin: bytes | None = b"") -> tuple[int, str, str]:
    with _Streams(stdin) as streams:
        try:
            code = cli.forest_main(argv)
        except SystemExit as exc:  # the parser's own refusal of words it cannot read
            code = exc.code if isinstance(exc.code, int) else 2
    return code, streams.text(streams.stdout), streams.text(streams.stderr)


def _legacy_cli(argv: list[str], stdin: bytes | None = b"") -> tuple[int, str, str]:
    with _Streams(stdin) as streams:
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # --version or --help
            code = exc.code if isinstance(exc.code, int) else 2
    return code, streams.text(streams.stdout), streams.text(streams.stderr)


@dataclass(frozen=True)
class Answer:
    """What a call gave: a refusal's body, or what it answered."""

    refused: dict[str, Any] | None
    answer: Any
    said: str  # what was printed, for a failure's message


@dataclass(frozen=True)
class Forest:
    """``forest <words> --root <forest> --json``; ``--root`` is added unless the words name one."""

    words: tuple[str, ...]
    stdin: bytes | None = b""
    exits: tuple[int, ...] = (0,)  # the exit codes of an answer (a check that finds something exits 1)

    def run(self, root: Path, base: Path) -> Answer:
        words = [_filled(word, root, base) for word in self.words]
        if "--root" not in words and words[:1] != ["init"]:
            words += ["--root", str(root)]
        code, out, err = _forest_cli([*words, "--json"], self.stdin)
        said = f"forest {shlex.join(words)} -> exit {code}: {out}{err}"
        body = json.loads(out) if out.strip().startswith("{") else None
        if code == 2 and isinstance(body, dict) and "error" in body:
            return Answer(body["error"], None, said)
        if code not in self.exits:
            raise AssertionError(f"neither an answer nor a refusal: {said}")
        return Answer(None, body, said)


@dataclass(frozen=True)
class Legacy:
    """``memory-forest <words> --json``, the 0.3 command line."""

    words: tuple[str, ...]
    stdin: bytes | None = b""

    def run(self, root: Path, base: Path) -> Answer:
        words = [_filled(word, root, base) for word in self.words]
        code, out, err = _legacy_cli([*words, "--json"], self.stdin)
        said = f"memory-forest {shlex.join(words)} -> exit {code}: {out}{err}"
        body = json.loads(out) if out.strip().startswith("{") else None
        if code == 2 and isinstance(body, dict) and "error" in body:
            return Answer(body["error"], None, said)
        if code != 0 or not isinstance(body, dict) or body.get("ok") is not True:
            raise AssertionError(f"neither an answer nor a refusal: {said}")
        return Answer(None, body, said)


@dataclass(frozen=True)
class Tool:
    """An MCP ``tools/call`` on stdio, as ``reader`` (or, with ``group``, as the member ``agent`` names)."""

    name: str
    arguments: Mapping[str, Any]
    reader: str = "till-bot"
    group: str | None = None
    version: str = "2025-11-25"

    def run(self, root: Path, base: Path) -> Answer:
        arguments = json.loads(_filled(json.dumps(self.arguments), root, base))
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": self.name, "arguments": arguments}}
        reply, _version = mcp.handle_message(root, self.reader, message, "stdio", self.version, group=self.group)
        said = f"tool {self.name} {arguments} as {self.group or self.reader} -> {reply}"
        if "result" not in reply:
            raise AssertionError(f"a protocol error, neither an answer nor a refusal: {said}")
        body = json.loads(reply["result"]["content"][0]["text"])
        if reply["result"]["isError"]:
            return Answer(body, None, said)
        return Answer(None, body, said)


@dataclass(frozen=True)
class Api:
    """A public function of the package, called with the forest root and the folder that holds it."""

    what: str
    function: Callable[[Path, Path], Any]

    def run(self, root: Path, base: Path) -> Answer:
        try:
            answer = self.function(root, base)
        except MemoryForestError as exc:
            return Answer(exc.as_dict(), None, f"{self.what} -> {exc.as_dict()}")
        return Answer(None, answer, f"{self.what} -> {answer!r}"[:2000])


@dataclass(frozen=True)
class Served:
    """``forest <words> --root <forest>`` in a child process that serves until it is stopped: answered when
    its handshake line comes (read against a deadline), then stopped."""

    words: tuple[str, ...]
    handshake: str
    seconds: float = 120

    def run(self, root: Path, base: Path) -> Answer:
        words = [*self.words, "--root", str(root)]
        source = str(Path(cli.__file__).resolve().parents[1])
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; from memory_forest.cli import forest_main; sys.exit(forest_main())",
             *words],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=base,
            env={**os.environ, "PYTHONPATH": source})
        assert child.stderr is not None
        line = b""
        try:
            deadline = time.monotonic() + self.seconds
            while not line.endswith(b"\n"):
                left = deadline - time.monotonic()
                ready, _, _ = select.select([child.stderr], [], [], max(left, 0))
                if not ready:
                    break
                piece = os.read(child.stderr.fileno(), 1)
                if not piece:
                    break
                line += piece
        finally:
            child.kill()
            child.wait(timeout=30)
            child.stderr.close()
        said = f"forest {shlex.join(words)} -> {line.decode('utf-8', 'replace')!r}"
        if not line.decode("utf-8", "replace").startswith(self.handshake):
            raise AssertionError(f"no handshake: {said}")
        return Answer(None, line.decode("utf-8"), said)


Call = Forest | Legacy | Tool | Api | Served


def forest(*words: str, stdin: bytes | None = b"", exits: tuple[int, ...] = (0,)) -> Forest:
    return Forest(tuple(words), stdin, exits)


def legacy(*words: str, stdin: bytes | None = b"") -> Legacy:
    return Legacy(tuple(words), stdin)


def tool(name: str, arguments: Mapping[str, Any], *, reader: str = "till-bot", group: str | None = None) -> Tool:
    return Tool(name, arguments, reader, group)


def api(what: str, function: Callable[[Path, Path], Any]) -> Api:
    return Api(what, function)


# ─────────────────────────── rows ───────────────────────────


@dataclass(frozen=True)
class Fix:
    """A wrong call and the corrected one, on one forest.

    ``files`` are written on the forest (or taken away, as None) before anything is run: they are the forest
    as it is. ``beside`` are files in the folder that holds the forest (a plan file, a decisions file); one
    given as a function is written from the forest once it is made (a plan that names the bytes it changes).
    """

    forest: str
    wrong: Call
    right: Call
    files: Mapping[str, str | bytes | None] = field(default_factory=dict)
    beside: Mapping[str, str | bytes | Callable[[Path, Path], str]] = field(default_factory=dict)
    make: Callable[[Path, Path], None] | None = None  # the forest as it is, where files cannot write it


@dataclass(frozen=True)
class Run:
    """A state, made on a forest, and a call refused with a ``run`` move while it stands.

    ``make`` makes what ``files`` cannot write (a damaged index, a journal a killed writer left).
    """

    forest: str
    call: Call
    files: Mapping[str, str | bytes | None] = field(default_factory=dict)
    make: Callable[[Path, Path], None] | None = None


def _add(rows: dict[str, Any], more: Mapping[str, Any]) -> None:
    """Rows added to a table: a code has one row."""
    twice = sorted(set(rows) & set(more))
    if twice:
        raise AssertionError(f"a code with two rows: {twice}")
    rows.update(more)


# ═══════════════════════════════ the fix rows ═══════════════════════════════

FIX_ROWS: dict[str, Fix] = {}

# ─── the structure tools: move.py, collections.py, plan.py and repeats.py ───
#
# On the synthetic forest: the studio tree with the branch subject `kiln-two`, the people tree with the leaf
# `odile-brask` and the collection `acquaintances-seen-once`, the admin tree with `cinderwell-supply` and
# `juno-hale`, and the agent trees `studio-hub`, `kiln-bot` and `ledger-bot`.
#
# A wrong argument is corrected in place. Where the forest holds what blocks the call (a page of another
# subject in the branch, a board with leaves), the corrected call is one `forest move plan` that first does
# what the message names and then the call: all of it is written, or none.


def _shipped(relative: str) -> str:
    """A page of the synthetic forest as it ships."""
    return (SYNTHETIC_FOREST / relative).read_text(encoding="utf-8")


def _leaf(entity: str, title: str, *, board: str, subtopic: str, kind: str = "event", extra: str = "",
          sections: str = "", timeline: str = "- 2042-05-10 09:00 America/Halifax — Juno noted it.") -> str:
    """A leaf in the synthetic forest's shape, under the board ``board`` (``03 mtm/<tree>/<branch>``)."""
    return (
        f"---\nentity: {entity}\nsubtopic: {subtopic}\nkind: {kind}\nabout: none\ntimezone: America/Halifax\n"
        f"updated_at: 2042-05-10\n{extra}---\n\n# {title}\n\nWhat Juno wrote down about it.\n\n{sections}"
        f"## Timeline\n\n{timeline}\n\n## Links\n\n- up: [[{board}|{board.rsplit('/', 1)[-1]} MTM]]\n\n"
        "## Update record\n"
    )


def _board(title: str, about: str, *, tree: str) -> str:
    return (f"# {title}\n\n## About\n\n{about}\n\n## Current state\n\n## Leaves\n\n## Pointers\n\n## Links\n\n"
            f"- up: [[02 ltm/{tree}_LTM|{tree} LTM]]\n\n## Update record\n")


def _with_subjects(index: str, *lines: str) -> dict[str, str]:
    """A tree index of the synthetic forest with registry lines added under ``## Subjects``."""
    return {index: _shipped(index).replace("\n\n## Branches", "\n" + "\n".join(lines) + "\n\n## Branches", 1)}


def _plan(*operations: Mapping[str, Any]) -> str:
    """A `forest move plan` file."""
    return json.dumps(list(operations))


def _plan_files(wrong: list[Mapping[str, Any]] | None, right: list[Mapping[str, Any]]) -> dict[str, str]:
    files = {"right.json": _plan(*right)}
    if wrong is not None:
        files["wrong.json"] = _plan(*wrong)
    return files


WRONG_PLAN = forest("move", "plan", "{base}/wrong.json")
RIGHT_PLAN = forest("move", "plan", "{base}/right.json")

KILN = "04 stm/studio/kiln-two"
REPAIRS = f"{KILN}/repairs.md"
Q1, Q2 = f"{KILN}/firings-2042-q1.md", f"{KILN}/firings-2042-q2.md"
FIRINGS_2041 = f"{KILN}/firings-2041.md"
PEOPLE = "04 stm/people/acquaintances"
ODILE = f"{PEOPLE}/odile-brask.md"
SEEN_ONCE = f"{PEOPLE}/seen-once-2042-q2.md"
SHOPS = "04 stm/admin/shops"
GLAZES = "04 stm/studio/glazes"


def _firings_2041(**options: str) -> dict[str, str]:
    """One more page of Kiln Two's stream of firings, shaped by ``options``."""
    options.setdefault("timeline", "- 2041-11-20 08:00–19:30 America/Halifax — glaze firing to cone 6, 30 pieces.")
    return {FIRINGS_2041: _leaf("kiln-two", "Kiln Two firings (2041)", board="03 mtm/studio/kiln-two",
                                subtopic="firings", **options)}


# The shop filed twice: a second leaf subject for Cinderwell Supply.
TWICE = {
    **_with_subjects("03 mtm/admin/admin_MTM.md", f"- `cinderwell-clay` — Cinderwell Clay · organization · leaf · "
                     f"`{SHOPS}/cinderwell-clay.md` · aliases: the clay depot"),
    f"{SHOPS}/cinderwell-clay.md": _leaf("cinderwell-clay", "Cinderwell Clay", board="03 mtm/admin/shops",
                                         subtopic="orders", kind="purchase"),
}
# An area tree with nothing in it yet.
GARDEN = {
    "02 ltm/garden_LTM.md": "# garden\n\n## Scope\n\nThe herb beds behind the studio.\n\n## Patterns and rules\n\n"
    "## Current state\n\n## Branches\n\n## Links\n\n- up: [[01 xltm/XLTM|XLTM]]\n\n## Update record\n",
    "03 mtm/garden/garden_MTM.md": "# garden index\n\n## Subjects\n\n## Branches\n\n## Links\n\n"
    "- up: [[02 ltm/garden_LTM|garden LTM]]\n\n## Update record\n",
}
# Ledger Bot, retired.
ENDED = {"03 mtm/ledger-bot/ledger-bot_MTM.md": _shipped("03 mtm/ledger-bot/ledger-bot_MTM.md").replace(
    "`02 ltm/ledger-bot_LTM.md`", "`02 ltm/ledger-bot_LTM.md` · ended")}
# A category board `glazes` with no subject, the leaf subject `glazes` that takes it over when promoted, and a
# page of the tree's shared branch with the name a leaf of the category brings along.
TAKEOVER = {
    **_with_subjects("03 mtm/studio/studio_MTM.md",
                     f"- `glazes` — Glazes · topic · leaf · `{GLAZES}/glazes.md`",
                     f"- `celadon-glaze` — Celadon glaze · product · leaf · `{GLAZES}/celadon-glaze.md`"),
    "03 mtm/studio/glazes.md": _board("Glazes", "The glazes the studio mixes.", tree="studio"),
    "03 mtm/studio/shared.md": _board("Shared", "What the studio tree holds that no branch holds.", tree="studio"),
    f"{GLAZES}/glazes.md": _leaf("glazes", "Glaze notes", board="03 mtm/studio/glazes", subtopic="notes"),
    f"{GLAZES}/celadon-glaze.md": _leaf("celadon-glaze", "Celadon glaze", board="03 mtm/studio/glazes",
                                         subtopic="profile", kind="profile"),
    "04 stm/studio/shared/celadon-glaze.md": _leaf("kiln-two", "Celadon test tiles in Kiln Two",
                                                    board="03 mtm/studio/shared", subtopic="tests"),
}
# A second board named `shops`, in the studio tree, and a subject holding the id a new admin/shops collection
# would take there.
SHOPS_ELSEWHERE = {
    "03 mtm/studio/shops.md": _board("Shops", "The shops that deliver to the studio.", tree="studio"),
    **_with_subjects("03 mtm/admin/admin_MTM.md", "- `admin-shops-seen-once` — Shop notes · topic · leaf · "
                     "`04 stm/admin/self/shop-notes.md`"),
    "04 stm/admin/self/shop-notes.md": _leaf("admin-shops-seen-once", "Shop notes", board="03 mtm/admin/self",
                                             subtopic="notes"),
}
# Another Odile Brask, seen once, already an entry of the people collection.
SECOND_ODILE = {SEEN_ONCE: _shipped(SEEN_ONCE).replace(
    "## Timeline",
    "## Odile Brask\n\n- entry: `odile-brask-fair` · person\n- kind: event\n\n"
    "Another Odile Brask, who sold Juno her summer fair ticket.\n\n## Timeline", 1).replace(
    "- 2042-05-14 11:20", "- 2042-05-12 09:00 America/Halifax — Odile Brask: sold the fair ticket.\n- 2042-05-14 11:20", 1)}
# Three fact lines Juno wrote on two leaves of Kiln Two, and what `forest repeats` reads them by.
FACTS = [
    "- Kiln Two takes nine full shelves when the posts are set at the middle height.",
    "- Its lid hinge is greased with the graphite paste kept in the glaze room.",
    "- A cone pack sits on the third shelf from the bottom on every glaze load.",
]
REPEATED = {
    f"{KILN}/{name}.md": _leaf("kiln-two", f"Kiln Two {name}", board="03 mtm/studio/kiln-two", subtopic=name,
                               kind="rule", sections="## Facts\n\n" + "\n".join(FACTS) + "\n\n")
    for name in ("shelves", "loading")
}


def _decisions(home: str) -> str:
    return json.dumps([{"subject": "kiln-two", "digest": repeats.digest(line), "home": home,
                        "reason": "the shelves page keeps the kiln's fixed facts"} for line in FACTS])


_add(FIX_ROWS, {
    # move.py: a wrong argument, corrected in place.
    "about_invalid": Fix("synthetic", forest("move", "set", REPAIRS, "--about", "none, cinderwell-supply"),
                         forest("move", "set", REPAIRS, "--about", "cinderwell-supply")),
    "already_a_tree": Fix("synthetic", forest("move", "promote", "kiln-bot"), forest("move", "promote", "odile-brask")),
    "already_ended": Fix("synthetic", forest("move", "retire-tree", "ledger-bot"),
                         forest("move", "retire-tree", "kiln-bot"), files=ENDED),
    "archive_unsupported": Fix("synthetic", forest("move", "archive", "02 ltm/studio_LTM.md", "--reason", "summed up"),
                               forest("move", "archive", Q1, "--reason", "summed up on the board")),
    "branch_exists": Fix("synthetic", forest("move", "rename", "studio-hub/routing", "hand-offs"),
                         forest("move", "rename", "studio-hub/routing", "dispatch")),
    "branch_missing": Fix("synthetic", forest("move", "leaf", REPAIRS, "04 stm/studio/kilns/repairs.md"),
                          forest("move", "leaf", REPAIRS, f"{KILN}/fixes.md")),
    "description_missing": Fix("synthetic", forest("move", "new-tree", "garden", "--description", "  "),
                               forest("move", "new-tree", "garden", "--description", "the herb beds")),
    "fold_into_itself": Fix("synthetic", forest("move", "fold-tree", "ledger-bot", "--into", "ledger-bot"),
                            forest("move", "fold-tree", "ledger-bot", "--into", "studio", "--type", "project")),
    "fold_tree_type_missing": Fix("synthetic",
                                  forest("move", "fold-tree", "ledger-bot", "--into", "studio", "--type", "agent"),
                                  forest("move", "fold-tree", "ledger-bot", "--into", "studio", "--type", "project")),
    "fold_tree_unregistered": Fix("synthetic", forest("move", "fold-tree", "admin", "--into", "people"),
                                  forest("move", "fold-tree", "ledger-bot", "--into", "studio", "--type", "project")),
    "id_taken": Fix("synthetic", forest("move", "new-tree", "odile-brask", "--description", "the weaver's matters"),
                    forest("move", "new-tree", "garden", "--description", "the herb beds")),
    "into_missing": Fix("synthetic", forest("move", "retire-tree", "garden"),
                        forest("move", "retire-tree", "garden", "--into", "02 ltm/studio_LTM.md"), files=GARDEN),
    "kind_invalid": Fix("synthetic", forest("move", "set", REPAIRS, "--kind", "anecdote"),
                        forest("move", "set", REPAIRS, "--kind", "event")),
    "leaf_name_invalid": Fix("synthetic", forest("move", "leaf", REPAIRS, f"{KILN}/repairs-may.md"),
                             forest("move", "leaf", REPAIRS, f"{KILN}/fixes.md")),
    "merge_collection": Fix("synthetic", forest("move", "merge", "cinderwell-supply", "acquaintances-seen-once"),
                            forest("move", "merge", "cinderwell-supply", "cinderwell-clay"), files=TWICE),
    "merge_into_smaller": Fix("synthetic", forest("move", "merge", "odile-brask", "kiln-two"),
                              forest("move", "merge", "kiln-two", "odile-brask")),
    "merge_same": Fix("synthetic", forest("move", "merge", "cinderwell-supply", "cinderwell-supply"),
                      forest("move", "merge", "cinderwell-supply", "cinderwell-clay"), files=TWICE),
    "move_to_same_place": Fix("synthetic", forest("move", "move-branch", "studio-hub/routing", "studio-hub"),
                              forest("move", "move-branch", "studio-hub/routing", "kiln-bot")),
    "name_invalid": Fix("synthetic", forest("move", "rename", "odile-brask", "--name", "Odile · the weaver"),
                        forest("move", "rename", "odile-brask", "--name", "Odile the weaver")),
    "name_missing": Fix("synthetic", forest("move", "rename", "odile-brask", "--name", "  "),
                        forest("move", "rename", "odile-brask", "--name", "Odile the weaver")),
    "name_taken": Fix("synthetic", forest("move", "rename", "odile-brask", "--name", "Juno Hale"),
                      forest("move", "rename", "odile-brask", "--name", "Odile the weaver")),
    "not_a_leaf": Fix("synthetic", forest("move", "set", "03 mtm/studio/kiln-two.md", "--kind", "event"),
                      forest("move", "set", REPAIRS, "--kind", "event")),
    "not_a_leaf_path": Fix("synthetic", forest("move", "leaf", REPAIRS, "03 mtm/studio/repairs.md"),
                           forest("move", "leaf", REPAIRS, f"{KILN}/fixes.md")),
    "not_a_slug": Fix("synthetic", forest("move", "new-tree", "Herb Garden", "--description", "the herb beds"),
                      forest("move", "new-tree", "herb-garden", "--description", "the herb beds")),
    "page_is_home": Fix("synthetic", forest("move", "archive", f"{SHOPS}/cinderwell-supply.md", "--reason", "closed"),
                        forest("move", "archive", Q1, "--reason", "summed up on the board")),
    "page_missing": Fix("synthetic", forest("move", "archive", f"{KILN}/firings-2041-q4.md", "--reason", "old"),
                        forest("move", "archive", Q1, "--reason", "summed up on the board")),
    "promote_collection": Fix("synthetic", forest("move", "promote", "acquaintances-seen-once"),
                              forest("collections", "admit", "pell-arrowsmith")),
    "reason_missing": Fix("synthetic", forest("move", "archive", Q1, "--reason", " "),
                          forest("move", "archive", Q1, "--reason", "summed up on the board")),
    "rename_collection": Fix("synthetic", forest("move", "rename", "acquaintances-seen-once", "--name", "Met once"),
                             forest("move", "rename", "people/acquaintances", "met-once")),
    "rename_nothing": Fix("synthetic", forest("move", "rename", "odile-brask"),
                          forest("move", "rename", "odile-brask", "--name", "Odile the weaver")),
    "rename_to_same": Fix("synthetic", forest("move", "rename", "studio-hub/routing", "routing"),
                          forest("move", "rename", "studio-hub/routing", "dispatch")),
    "rename_tree_unsupported": Fix("synthetic", forest("move", "rename", "kiln-bot", "glaze-bot"),
                                   forest("move", "rename", "kiln-bot", "--name", "Kiln Robot")),
    "standing_invalid": Fix("synthetic", forest("move", "set", REPAIRS, "--standing", "check the elements"),
                            forest("move", "set", REPAIRS, "--standing", 'own — "check the elements after a repair"')),
    "subject_unknown": Fix("synthetic", forest("move", "promote", "kiln-three"), forest("move", "promote", "odile-brask")),
    "successor_missing": Fix("synthetic", forest("move", "archive", Q1, "--reason", "summed up", "--successor",
                                                 f"{KILN}/firings-2042.md"),
                             forest("move", "archive", Q1, "--reason", "summed up", "--successor", Q2)),
    "target_exists": Fix("synthetic", forest("move", "leaf", REPAIRS, f"{KILN}/profile.md"),
                         forest("move", "leaf", REPAIRS, f"{KILN}/fixes.md")),
    "tree_exists": Fix("synthetic", forest("move", "new-tree", "studio", "--description", "a second studio"),
                       forest("move", "new-tree", "garden", "--description", "the herb beds")),
    "tree_unknown": Fix("synthetic", forest("move", "move-branch", "studio-hub/routing", "garden"),
                        forest("move", "move-branch", "studio-hub/routing", "kiln-bot")),
    "type_collection": Fix("synthetic", forest("move", "set-type", "odile-brask", "collection"),
                           forest("move", "set-type", "odile-brask", "organization")),
    "type_unchanged": Fix("synthetic", forest("move", "set-type", "odile-brask", "person"),
                          forest("move", "set-type", "odile-brask", "organization")),
    "type_unknown": Fix("synthetic", forest("move", "set-type", "odile-brask", "weaver"),
                        forest("move", "set-type", "odile-brask", "organization")),
    # move.py: a page the call names as the successor is a page in no branch.
    "path_not_in_forest": Fix("synthetic", forest("move", "retire-tree", "garden", "--into", "04 stm/studio/notes.md"),
                              forest("move", "retire-tree", "garden", "--into", "02 ltm/studio_LTM.md"),
                              files={**GARDEN, "04 stm/studio/notes.md": "# Studio notes\n\nA page in no branch.\n"}),
    # move.py, recut-stream: the pages named are not one stream of the subject.
    "stream_empty": Fix("synthetic", WRONG_PLAN, RIGHT_PLAN, beside=_plan_files(
        [{"op": "recut-stream", "subject": "kiln-two", "leaves": []}],
        [{"op": "recut-stream", "subject": "kiln-two", "leaves": [Q1, Q2]}])),
    "stream_fields_differ": Fix("synthetic", forest("move", "recut-stream", "kiln-two", FIRINGS_2041, Q1),
                                forest("move", "recut-stream", "kiln-two", Q1, Q2),
                                files=_firings_2041(extra='standing: own — "fire glaze loads to cone 6"\n')),
    "stream_in_several_folders": Fix("synthetic", forest("move", "recut-stream", "kiln-two", Q1,
                                                         f"{SHOPS}/cinderwell-supply.md"),
                                     forest("move", "recut-stream", "kiln-two", Q1, Q2)),
    "stream_kinds_differ": Fix("synthetic", forest("move", "recut-stream", "kiln-two", FIRINGS_2041, Q1),
                               forest("move", "recut-stream", "kiln-two", Q1, Q2), files=_firings_2041(kind="work")),
    "stream_leaf_empty": Fix("synthetic", forest("move", "recut-stream", "kiln-two", FIRINGS_2041, Q1),
                             forest("move", "recut-stream", "kiln-two", Q1, Q2), files=_firings_2041(timeline="")),
    "stream_leaf_not_a_list": Fix("synthetic", forest("move", "recut-stream", "kiln-two", FIRINGS_2041, Q1),
                                  forest("move", "recut-stream", "kiln-two", Q1, Q2),
                                  files=_firings_2041(sections="## Kiln log\n\nThe controller was replaced.\n\n")),
    "stream_names_differ": Fix("synthetic", forest("move", "recut-stream", "kiln-two", Q1, REPAIRS),
                               forest("move", "recut-stream", "kiln-two", Q1, Q2)),
    "stream_other_subject": Fix("synthetic", forest("move", "recut-stream", "odile-brask", Q1, Q2),
                                forest("move", "recut-stream", "kiln-two", Q1, Q2)),
    "stream_page_taken": Fix("synthetic", forest("move", "recut-stream", "kiln-two", Q1, Q2),
                             forest("move", "recut-stream", "kiln-two", f"{KILN}/firings-2042.md", Q1, Q2),
                             files={f"{KILN}/firings-2042.md": _leaf(
                                 "kiln-two", "Kiln Two firings (2042)", board="03 mtm/studio/kiln-two",
                                 subtopic="firings", timeline="- 2042-01-08 08:00 America/Halifax — bisque firing.")}),
    # move.py and plan.py: a plan file and its operations.
    "operation_unknown": Fix("synthetic", WRONG_PLAN, RIGHT_PLAN, beside=_plan_files(
        [{"op": "polish", "subject": "kiln-two"}], [{"op": "set-type", "subject": "odile-brask", "type": "organization"}])),
    "page_removed": Fix("synthetic", WRONG_PLAN, RIGHT_PLAN, beside=_plan_files(
        [{"op": "archive", "path": Q1, "reason": "summed up"}, {"op": "set", "path": Q1, "about": "cinderwell-supply"}],
        [{"op": "set", "path": Q1, "about": "cinderwell-supply"}, {"op": "archive", "path": Q1, "reason": "summed up"}])),
    "plan_file_invalid": Fix("synthetic", WRONG_PLAN, RIGHT_PLAN, beside={
        "wrong.json": '[{"op": "set-type", "subject": "odile-brask", "type": "organization"',
        "right.json": _plan({"op": "set-type", "subject": "odile-brask", "type": "organization"})}),
    "tree_type_not_allowed": Fix("synthetic", WRONG_PLAN, RIGHT_PLAN, beside=_plan_files(
        [{"op": "promote", "subject": "odile-brask"}, {"op": "promote", "subject": "odile-brask"}],
        [{"op": "promote", "subject": "odile-brask"}])),
    # plan.py up_line: Plan.move_page asks it only for a page that has a parent, so only a direct call reaches
    # the refusal.
    "path_has_no_parent": Fix("synthetic", api("plan.up_line('01 xltm/XLTM.md')",
                                               lambda root, base: forest_plan.up_line("01 xltm/XLTM.md")),
                              api("plan.up_line('02 ltm/studio_LTM.md')",
                                  lambda root, base: forest_plan.up_line("02 ltm/studio_LTM.md"))),
    # move.py: the forest holds what blocks the call; one plan does what the message names, then the call.
    "branch_holds_other_subjects": Fix(
        "synthetic", forest("move", "promote", "kiln-two"), RIGHT_PLAN,
        files={f"{KILN}/kiln-shelves.md": _leaf("cinderwell-supply", "Kiln shelves from Cinderwell",
                                                board="03 mtm/studio/kiln-two", subtopic="orders", kind="purchase")},
        beside=_plan_files(None, [{"op": "leaf", "source": f"{KILN}/kiln-shelves.md", "target": f"{SHOPS}/kiln-shelves.md"},
                                  {"op": "promote", "subject": "kiln-two"}])),
    "branch_not_empty": Fix(
        "synthetic", forest("move", "archive", "03 mtm/studio-hub/hand-offs.md", "--reason", "hand-offs go by routing"),
        RIGHT_PLAN, beside=_plan_files(None, [
            {"op": "archive", "path": "04 stm/studio-hub/hand-offs/open-hand-offs.md", "reason": "hand-offs go by routing"},
            {"op": "archive", "path": "03 mtm/studio-hub/hand-offs.md", "reason": "hand-offs go by routing"}])),
    "fold_tree_holds_subjects": Fix(
        "synthetic", forest("move", "fold-tree", "kiln-bot", "--into", "studio", "--type", "project"), RIGHT_PLAN,
        files={**_with_subjects("03 mtm/kiln-bot/kiln-bot_MTM.md", "- `glaze-tests` — Glaze tests · topic · leaf · "
                                "`04 stm/kiln-bot/methods/glaze-tests.md`"),
               "04 stm/kiln-bot/methods/glaze-tests.md": _leaf("glaze-tests", "Glaze tests",
                                                               board="03 mtm/kiln-bot/methods", subtopic="methods")},
        beside=_plan_files(None, [{"op": "move-branch", "branch": "kiln-bot/methods", "tree": "studio", "name": "kiln-methods"},
                                  {"op": "fold-tree", "tree": "kiln-bot", "into": "studio", "type": "project"}])),
    "fold_tree_loose_pages": Fix(
        "synthetic", forest("move", "fold-tree", "ledger-bot", "--into", "studio", "--type", "project"), RIGHT_PLAN,
        files={"04 stm/ledger-bot/notes/cinderwell-invoices.md": _leaf(
            "cinderwell-supply", "Cinderwell invoices", board="03 mtm/ledger-bot/notes", subtopic="invoices")},
        beside=_plan_files(None, [{"op": "leaf", "source": "04 stm/ledger-bot/notes/cinderwell-invoices.md",
                                   "target": f"{SHOPS}/cinderwell-invoices.md"},
                                  {"op": "fold-tree", "tree": "ledger-bot", "into": "studio", "type": "project"}])),
    "folder_not_empty": Fix(
        "synthetic", forest("move", "promote", "odile-brask"), RIGHT_PLAN,
        files={"04 stm/people/odile-brask/dye-orders.md": _leaf(
            "cinderwell-supply", "Onion-skin dye orders", board="03 mtm/people/odile-brask", subtopic="orders")},
        beside=_plan_files(None, [{"op": "leaf", "source": "04 stm/people/odile-brask/dye-orders.md",
                                   "target": f"{SHOPS}/dye-orders.md"},
                                  {"op": "promote", "subject": "odile-brask"}])),
    "leaf_name_clash": Fix(
        "synthetic", forest("move", "promote", "glazes"), RIGHT_PLAN, files=TAKEOVER,
        beside=_plan_files(None, [{"op": "leaf", "source": "04 stm/studio/shared/celadon-glaze.md",
                                   "target": "04 stm/studio/shared/celadon-tiles.md"},
                                  {"op": "promote", "subject": "glazes"}])),
    "tree_not_empty": Fix(
        "synthetic", forest("move", "retire-tree", "admin", "--into", "02 ltm/people_LTM.md"), RIGHT_PLAN,
        beside=_plan_files(None, [{"op": "move-branch", "branch": "admin/self", "tree": "people"},
                                  {"op": "move-branch", "branch": "admin/shops", "tree": "people"},
                                  {"op": "retire-tree", "tree": "admin", "into": "02 ltm/people_LTM.md"}])),
    # The message says to fold the tree first, then merge: one plan of the two.
    "merge_tree_unsupported": Fix(
        "synthetic", forest("move", "merge", "kiln-two", "kiln-bot"), RIGHT_PLAN,
        beside=_plan_files(None, [{"op": "fold-tree", "tree": "kiln-bot", "into": "studio", "type": "project"},
                                  {"op": "merge", "keep": "kiln-two", "drop": "kiln-bot"}])),
    # collections.py
    "collection_id_taken": Fix(
        "synthetic", forest("collections", "demote", "cinderwell-supply"), RIGHT_PLAN, files=SHOPS_ELSEWHERE,
        beside=_plan_files(None, [{"op": "rename", "target": "admin-shops-seen-once", "new": "shop-notes"},
                                  {"op": "demote", "subject": "cinderwell-supply"}])),
    "demote_standing": Fix(
        "synthetic", forest("collections", "demote", "odile-brask"), RIGHT_PLAN,
        files={ODILE: _shipped(ODILE).replace("about: none\n", 'about: none\nstanding: own — "keep Odile a leaf"\n')},
        beside=_plan_files(None, [{"op": "set", "path": ODILE, "standing": ""},
                                  {"op": "demote", "subject": "odile-brask"}])),
    "demote_unsupported": Fix("synthetic", forest("collections", "demote", "kiln-two"),
                              forest("collections", "demote", "odile-brask")),
    "entry_name_taken": Fix(
        "synthetic", forest("collections", "demote", "odile-brask"), RIGHT_PLAN, files=SECOND_ODILE,
        beside=_plan_files(None, [{"op": "rename", "target": "odile-brask", "name": "Odile the weaver"},
                                  {"op": "demote", "subject": "odile-brask"}])),
    "entry_unknown": Fix("synthetic", forest("collections", "admit", "tamsin-orrow"),
                         forest("collections", "admit", "pell-arrowsmith")),
    "not_a_collection_page": Fix("synthetic", forest("collections", "recut", REPAIRS),
                                 forest("collections", "recut", SEEN_ONCE)),
    # repeats.py
    "decision_home_invalid": Fix("synthetic", forest("repeats", "apply", "--decisions", "{base}/wrong.json"),
                                 forest("repeats", "apply", "--decisions", "{base}/right.json"), files=REPEATED,
                                 beside={"wrong.json": _decisions(REPAIRS), "right.json": _decisions(f"{KILN}/shelves.md")}),
    "decisions_invalid": Fix("synthetic", forest("repeats", "apply", "--decisions", "{base}/wrong.json"),
                             forest("repeats", "apply", "--decisions", "{base}/right.json"),
                             beside={"wrong.json": '[{"subject": "kiln-two"', "right.json": "[]"}),
})


# ─── the 0.3 command line and its library: cli.py, core.py, model.py, index.py, retrieval.py, writer.py ───

# On a 0.3 forest made by `memory-forest init --example`: the research-notes tree with the leaf
# `instrument-calibration`, the mission-operations tree with `telemetry-replay`, and the Daily page of
# 2042-04-12. A plan names the forest by `{forest_id}`, filled in from the forest's own identity.

CALIBRATION = "04 stm/research-notes/observatory-trial/instrument-calibration.md"


def _hex(digit: str) -> str:
    return digit * 64


# model.py: a page's path read against the seven layers by the package's own reader. One row for each code,
# generated from the wrong path and the right one.
ROUTE_PATHS: dict[str, tuple[str, str]] = {
    "invalid_document_path": ("01 xltm\\XLTM.md", "01 xltm/XLTM.md"),
    "unknown_layer": ("07 archive/XLTM.md", "01 xltm/XLTM.md"),
    "layer_number_mismatch": ("02 xltm/XLTM.md", "01 xltm/XLTM.md"),
    "noncanonical_layer_directory": ("01_xltm/XLTM.md", "01 xltm/XLTM.md"),
    "invalid_istm_path": ("06 istm/events.json", "06 istm/events.jsonl"),
    "unsupported_file_type": (CALIBRATION.removesuffix(".md") + ".txt", CALIBRATION),
    "invalid_route_segment": ("04 stm/research-notes/observatory-trial/" + "lamp-" * 41 + "check.md",
                              "04 stm/research-notes/observatory-trial/lamp-check.md"),
    "noncanonical_route_segment": ("04 stm/research-notes/observatory-trial/e\u0301talonnage.md",  # NFD
                                   "04 stm/research-notes/observatory-trial/\u00e9talonnage.md"),  # NFC
    "invalid_xltm_path": ("01 xltm/Root.md", "01 xltm/XLTM.md"),
    "invalid_ltm_path": ("02 ltm/research-notes.md", "02 ltm/research-notes_LTM.md"),
    "invalid_mtm_path": ("03 mtm/observatory-trial.md", "03 mtm/research-notes/observatory-trial.md"),
    "invalid_stm_path": ("04 stm/research-notes/instrument-calibration.md", CALIBRATION),
    "invalid_daily_path": ("05 daily/2042/2042-04-12.md", "05 daily/2042-04-12.md"),
    "invalid_daily_date": ("05 daily/2042-04-31.md", "05 daily/2042-04-30.md"),
    "invalid_life_archive_path": ("00 life_archive/observatory/trials/seasons/field-season-2042.md",
                                  "00 life_archive/observatory/trials/field-season-2042.md"),
}


def _route(path: str) -> Api:
    return api(f"memory_forest.parse_relative_route({path!r})", lambda root, base: parse_relative_route(path))


_add(FIX_ROWS, {code: Fix("folder", _route(wrong), _route(right)) for code, (wrong, right) in ROUTE_PATHS.items()})

# cli.py and core.py: the words of the 0.3 command line, and `init`, which makes a new folder. The command
# line's parser refuses a word it does not know first (invalid_arguments); only a direct call of
# `cli.run_command` with parsed words reaches unknown_command.
_add(FIX_ROWS, {
    "invalid_arguments": Fix("legacy", legacy("check", "{root}"), legacy("audit", "{root}")),
    "unknown_command": Fix("legacy", api("cli.run_command(command=prune)", lambda root, base: cli.run_command(
                               argparse.Namespace(command="prune", root=str(root)))),
                           api("cli.run_command(command=audit)", lambda root, base: cli.run_command(
                               argparse.Namespace(command="audit", root=str(root))))),
    "root_exists": Fix("folder", legacy("init", "{root}"), legacy("init", "{base}/second-forest")),
    "missing_parent": Fix("folder", legacy("init", "{base}/seasons/second-forest"),
                          legacy("init", "{base}/second-forest")),
})

# index.py: what a route or a search asks for.
_add(FIX_ROWS, {
    "invalid_limit": Fix("legacy-indexed", legacy("route", "{root}", "calibration", "--limit", "0"),
                         legacy("route", "{root}", "calibration", "--limit", "5")),
    "empty_query": Fix("legacy-indexed", legacy("route", "{root}", "?!"), legacy("route", "{root}", "calibration")),
    "query_too_long": Fix("legacy-indexed", legacy("route", "{root}", "calibration " * 90),
                          legacy("route", "{root}", "calibration")),
})


# retrieval.py: the query-only expansion plan of `retrieve --query-plan`.
def _query_plan(*queries: str, schema: int = 1) -> str:
    return json.dumps({"schema_version": schema, "probes": [{"query": query} for query in queries]})


PROBES = {"probes.json": _query_plan("reference lamp")}


def _retrieve(plan: str, stdin: bytes | None = b"") -> Legacy:
    return legacy("retrieve", "{root}", "calibration", "--query-plan", plan, stdin=stdin)


def _wrong_probes(text: str | bytes) -> dict[str, str | bytes]:
    return {"wrong.json": text, **PROBES}


_add(FIX_ROWS, {
    "query_plan_not_found": Fix("legacy-indexed", _retrieve("{base}/probe.json"), _retrieve("{base}/probes.json"),
                                beside=PROBES),
    "query_plan_too_large": Fix("legacy-indexed", _retrieve("{base}/wrong.json"), _retrieve("{base}/probes.json"),
                                beside=_wrong_probes(b" " * (retrieval.MAX_QUERY_PLAN_BYTES + 1))),
    "invalid_query_plan": Fix("legacy-indexed", _retrieve("{base}/wrong.json"), _retrieve("{base}/probes.json"),
                              beside=_wrong_probes(_query_plan())),
    "unsupported_query_plan_schema": Fix("legacy-indexed", _retrieve("{base}/wrong.json"),
                                         _retrieve("{base}/probes.json"),
                                         beside=_wrong_probes(_query_plan("reference lamp", schema=2))),
    "invalid_query_plan_probe": Fix("legacy-indexed", _retrieve("{base}/wrong.json"), _retrieve("{base}/probes.json"),
                                    beside=_wrong_probes(_query_plan(" reference lamp"))),
    "duplicate_query_plan_probe": Fix("legacy-indexed", _retrieve("{base}/wrong.json"),
                                      _retrieve("{base}/right.json"),
                                      beside={"wrong.json": _query_plan("reference lamp", "Reference Lamp"),
                                              "right.json": _query_plan("reference lamp", "observation window")}),
    # A process started with its standard input closed (`0<&-`): `-` names no plan; a file does.
    "query_plan_input_unavailable": Fix("legacy-indexed", _retrieve("-", stdin=None),
                                        _retrieve("{base}/probes.json", stdin=None), beside=PROBES),
})

# writer.py: Daily batches, promotions and structured sweeps. `legacy-daily` holds one admitted batch, the
# entry `lamp-check` of 2042-04-12, written by `memory-forest apply-daily` itself.
LAMP_BATCH, LAMP_COMMIT = _hex("1"), _hex("3")


def _daily_plan(*, transaction: str = _hex("4"), entry: str = "window-log", day: str = "2042-04-13",
                result: str = _hex("6"), schema: str = writer.DAILY_PLAN_SCHEMA, forest_id: str = "{forest_id}",
                summary: str = "The crew logged a clear observation window.") -> str:
    return json.dumps({
        "schema_version": schema, "forest_id": forest_id, "transaction_id": transaction, "date": day,
        "entries": [{"entry_id": entry, "source_record_ids": ["sensor-log-8"], "summary": summary}],
        "provenance": {"packet_sha256": _hex("5"), "result_sha256": result, "batch_id": transaction},
    })


def _legacy_daily(root: Path) -> None:
    _legacy(root)
    lamp = _filled(_daily_plan(transaction=LAMP_BATCH, entry="lamp-check", day="2042-04-12", result=LAMP_COMMIT,
                               summary="The crew checked the reference lamp before the window."), root, root.parent)
    code, out, err = _legacy_cli(["apply-daily", str(root), "-", "--json"], lamp.encode("utf-8"))
    if code != 0:
        raise AssertionError(f"memory-forest apply-daily failed: {out}{err}")


FORESTS["legacy-daily"] = _legacy_daily


def _promotion_plan(*, source: str = "lamp-check", commit: str = LAMP_COMMIT, confidence: str = "medium") -> str:
    return json.dumps({
        "schema_version": writer.PROMOTION_PLAN_SCHEMA, "forest_id": "{forest_id}", "transaction_id": _hex("7"),
        "date": "2042-04-12",
        "promotions": [{
            "source_daily_entry_ids": [source],
            "route": {"domain": "research-notes", "domain_title": "Research notes", "branch": "observatory-trial",
                      "branch_title": "Observatory trial", "leaf": "instrument-calibration"},
            "title": "Instrument calibration",
            "content": "The crew checks the reference lamp twice before a window.",
            "confidence": confidence,
        }],
        "provenance": {"packet_sha256": _hex("8"), "result_sha256": _hex("7"), "daily_commit_sha256s": [commit]},
    })


def _sweep_plan(root: Path, *, action: str = "replace", expected: str | None = None,
                snapshot: str | None = None) -> dict[str, Any]:
    """A structured sweep that adds a line to the calibration leaf, read from the forest as it is."""
    text = (root / CALIBRATION).read_text(encoding="utf-8")
    return {
        "schema_version": writer.STRUCTURED_SWEEP_PLAN_SCHEMA, "forest_id": core.load_forest_identity(root),
        "transaction_id": _hex("a"), "date": "2042-04-12",
        "changes": [{
            "action": action,
            "target": {"layer": "stm", "tree": "research-notes", "branch": "observatory-trial",
                       "leaf": "instrument-calibration"},
            "expected_sha256": expected or hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "body": text + "\nThe crew now checks the reference lamp again after each window.\n",
            "source_daily_entry_ids": [], "reason": "The calibration routine gained a second lamp check.",
            "confidence": "medium",
        }],
        "dispositions": [],
        "provenance": {"packet_sha256": _hex("b"), "result_sha256": _hex("a"),
                       "forest_snapshot_sha256": snapshot or core.structured_forest_snapshot_sha256(root),
                       "daily_commit_sha256s": []},
    }


def _sweep_file(**options: str) -> Callable[[Path, Path], str]:
    """A structured sweep plan file, written from the forest as it is when the row is made."""
    return lambda root, base: json.dumps(_sweep_plan(root, **options))


DAILY = {"daily.json": _daily_plan()}
WRONG_DAILY = legacy("apply-daily", "{root}", "{base}/wrong.json")
RIGHT_DAILY = legacy("apply-daily", "{root}", "{base}/daily.json")
WRONG_PROMOTION = legacy("promote", "{root}", "{base}/wrong.json")
RIGHT_PROMOTION = legacy("promote", "{root}", "{base}/right.json")
WRONG_SWEEP = legacy("apply-structured", "{root}", "{base}/wrong.json")
RIGHT_SWEEP = legacy("apply-structured", "{root}", "{base}/right.json")

_add(FIX_ROWS, {
    "plan_not_found": Fix("legacy", legacy("apply-daily", "{root}", "{base}/daily-plan.json"), RIGHT_DAILY,
                          beside=DAILY),
    "plan_too_large": Fix("legacy", legacy("apply-daily", "{root}", "-", stdin=b" " * (writer.MAX_PLAN_BYTES + 1)),
                          RIGHT_DAILY, beside=DAILY),
    # A process started with its standard input closed (`0<&-`): `-` names no plan; a file does.
    "missing_plan_input": Fix("legacy", legacy("apply-daily", "{root}", "-", stdin=None),
                              legacy("apply-daily", "{root}", "{base}/daily.json", stdin=None), beside=DAILY),
    "invalid_write_plan": Fix("legacy", WRONG_DAILY, RIGHT_DAILY, beside={"wrong.json": _daily_plan()[:-1], **DAILY}),
    "invalid_daily_plan": Fix("legacy", WRONG_DAILY, RIGHT_DAILY,
                              beside={"wrong.json": _daily_plan(schema="memory-forest-daily-plan-v2"), **DAILY}),
    "forest_identity_mismatch": Fix("legacy", WRONG_DAILY, RIGHT_DAILY,
                                    beside={"wrong.json": _daily_plan(forest_id="0123456789abcdef" * 2), **DAILY}),
    "duplicate_daily_entry_id": Fix("legacy-daily", WRONG_DAILY, RIGHT_DAILY,
                                    beside={"wrong.json": _daily_plan(entry="lamp-check"), **DAILY}),
    # The lamp batch's receipt archived, as the writer asks when its receipt store is full; the Daily page keeps
    # the batch.
    "transaction_conflict": Fix("legacy-daily", WRONG_DAILY, RIGHT_DAILY,
                                files={f".memory-forest/receipts/{LAMP_BATCH}.json": None},
                                beside={"wrong.json": _daily_plan(transaction=LAMP_BATCH, day="2042-04-12"),
                                        **DAILY}),
    "invalid_promotion_plan": Fix("legacy-daily", WRONG_PROMOTION, RIGHT_PROMOTION,
                                  beside={"wrong.json": _promotion_plan(confidence="certain"),
                                          "right.json": _promotion_plan()}),
    "missing_daily_source": Fix("legacy-daily", WRONG_PROMOTION, RIGHT_PROMOTION,
                                beside={"wrong.json": _promotion_plan(source="lamp-chek"),
                                        "right.json": _promotion_plan()}),
    "daily_provenance_mismatch": Fix("legacy-daily", WRONG_PROMOTION, RIGHT_PROMOTION,
                                     beside={"wrong.json": _promotion_plan(commit=_hex("9")),
                                             "right.json": _promotion_plan()}),
    "invalid_structured_sweep_plan": Fix("legacy", WRONG_SWEEP, RIGHT_SWEEP,
                                         beside={"wrong.json": _sweep_file(action="delete"),
                                                 "right.json": _sweep_file()}),
    "structured_preimage_mismatch": Fix("legacy", WRONG_SWEEP, RIGHT_SWEEP,
                                        beside={"wrong.json": _sweep_file(expected=_hex("0")),
                                                "right.json": _sweep_file()}),
    # A sweep bound to a snapshot of the forest that is not the forest as it is.
    "structured_snapshot_mismatch": Fix("legacy", WRONG_SWEEP, RIGHT_SWEEP,
                                        beside={"wrong.json": _sweep_file(snapshot=_hex("c")),
                                                "right.json": _sweep_file()}),
})


# ─── version 1 reads, logs, intake and checks: search.py, cli_find.py, groups.py, intake.py, mcp.py, ───
# ─── archive.py, record.py, numbers.py, credentials.py, checks.py, lint.py, timeline.py, layout.py ───

# On the find forest: till-bot reads the shared bakery tree and its own lane, never oven-bot's tree. The
# floor page has 16 lines: frontmatter 1-8, `#head` 9-13, `#links` 14-16.
#
# A row calls a public function where no command or tool reaches its code, and where the command keeps the
# refusal to itself: `forest intake --hook` records it in .forest/intake/failures.jsonl and exits 0, as a
# client's hook needs.

FLOOR_PAGE = "04 stm/bakery/ovens/floor.md"
TILL_LANE = "06 istm/till-bot/2051-03-02.jsonl"
DAILY_PAGE = "05 daily/2051-03-01.md"  # the fifth layer: not a page of the four forest layers
TURN = b"Till Bot counted the rye trays at the counter.\n"


def _find_settings(agents: str = "", tail: str = "") -> str:
    """The find forest's forest.toml with ``agents`` added to its [agents] table and ``tail`` after the rest."""
    table = '[agents]\norchestrators = ["front-desk"]\n'
    assert table in FIND_FILES["forest.toml"]
    return FIND_FILES["forest.toml"].replace(table, table + agents) + tail


_BAKERY_BOTS_GROUP = '\n[agents.groups.bakery-bots]\nmembers = ["till-bot", "oven-bot"]\nopen = false\n'
BAKERY_BOTS = _find_settings(
    '\n[agents.known.till-bot]\nname = "Till Bot"\n\n[agents.known.oven-bot]\nname = "Oven Bot"\n' + _BAKERY_BOTS_GROUP)
SHOP_BOTS = _find_settings(  # two members under one name
    '\n[agents.known.till-bot]\nname = "Shop Bot"\n\n[agents.known.oven-bot]\nname = "Shop Bot"\n' + _BAKERY_BOTS_GROUP)

DAWN = json.dumps({"at": "2051-03-02T06:10:00+00:00", "id": "dawn-1", "origin": "interactive", "role": "agent",
                   "session": "dawn-shift", "text": "Till Bot opened the counter and set out the rye trays."},
                  sort_keys=True) + "\n"
JSONL_FOLDER = _find_settings(tail='\n[intake.tools.jsonl]\npath = "{base}/shift-records"\n')
JSONL_HOOKED = _find_settings(
    tail='\n[intake.tools.jsonl]\nenabled = true\nagent = "till-bot"\npath = "{base}/shift-records"\n')
INTAKE_ON = '{"placed": true}\n'  # .forest/intake/tools/jsonl/state.json: a first sweep turned jsonl intake on

RACK = b"# Rack\n\nThe cooling rack stood by the back door until the floor was swept.\n"
RACK_COPY = "2051-03-01/04 stm/bakery/ovens/rack.md.gz"
ARCHIVED = {  # the archive holds one page: the rack notes, folded into the floor page
    ".forest/archive/manifest.jsonl": json.dumps(
        {"archive_file": RACK_COPY, "bytes": len(RACK), "path": "04 stm/bakery/ovens/rack.md",
         "reason": "the rack notes went into the floor page", "sha256": hashlib.sha256(RACK).hexdigest(),
         "successor": FLOOR_PAGE, "time": "2051-03-01T10:00:00Z"}, sort_keys=True) + "\n",
    f".forest/archive/{RACK_COPY}": gzip.compress(RACK, compresslevel=9, mtime=0),
}
BACK_GATE = "04 stm/bakery/ovens/back-gate.md"  # line 3 quotes a turn holding one code-shaped token: place 1
BACK_GATE_TEXT = "# Back gate\n\n> 4821, see you at the gate\n"
LINT_BASELINE = json.dumps({"codes": [], "hits": [], "schema": "memory-forest-lint-baseline/1",
                            "taken_at": "2051-03-01T00:00:00Z"}) + "\n"
RACK_TEXT = "# Rack\n\nThe cooling rack stood by the back door.\n"


def _nested_hold(with_token: bool) -> Callable[[Path, Path], Any]:
    """``locking.hold_lock`` of the writer lock inside a hold of it, given the token or not."""
    def call(root: Path, base: Path) -> bool:
        with locking.hold_lock(root) as held:
            with locking.hold_lock(root, token=held if with_token else None):
                return True
    return call


def _trimmed(text: str) -> Callable[[Path, Path], Any]:
    """``record.trimmed_text`` on a page's text, with the plan ``record.plan_trim`` makes for it."""
    def call(root: Path, base: Path) -> str:
        page = parse_page(text)
        trim = forest_record.plan_trim(page, "04 stm/bakery/ovens/rack.md", config=load_config(root))
        return forest_record.trimmed_text(page, trim, None)
    return call


def _hook(**payload: str) -> Callable[[Path, Path], Any]:
    """``intake.run`` of the jsonl adapter with a hook's payload: what `forest intake --from jsonl --hook` runs."""
    def call(root: Path, base: Path) -> Any:
        filled = {key: _filled(value, root, base) for key, value in payload.items()}
        return intake.run(root, "jsonl", hook={"hook_event_name": "Stop", "session_id": "dawn-shift", **filled})
    return call


def _hook_payload(data: bytes) -> Callable[[Path, Path], Any]:
    """``cli_intake.read_hook_payload``: what `forest intake --hook` reads from its standard input."""
    return lambda root, base: cli_intake.read_hook_payload(io.BytesIO(data))


def _first_keyed_turn(root: Path) -> None:
    """till-bot's turn with its own turn id ``tray-count-1``, written as `forest log --turn-id` writes it."""
    mcp.record_turn(root, "till-bot", TURN.decode(), origin="interactive", session="cli", door="cli",
                    turn_id="tray-count-1")


def _owned(**options: str) -> Callable[[Path, Path], Any]:
    """``intake.append_owned``: one turn of till-bot's lane, with the role and origin the caller gives."""
    return lambda root, base: intake.append_owned(root, "till-bot", TURN.decode(), **options)


_add(FIX_ROWS, {
    # search.py and safety.py: a path, an address or the lines a reader names (as till-bot)
    "address_unknown": Fix("find", forest("read", f"{FLOOR_PAGE}#prices", "--as", "till-bot"),
                           forest("read", f"{FLOOR_PAGE}#links", "--as", "till-bot")),
    "out_of_scope": Fix("find", tool("memory_read", {"path": "04 stm/oven-bot/methods/cooling.md"}),
                        tool("memory_read", {"path": FLOOR_PAGE})),
    "path_escape": Fix("find", forest("read", "../front-door.md", "--as", "till-bot"),
                       forest("read", FLOOR_PAGE, "--as", "till-bot")),
    "path_not_found": Fix("find", forest("read", "04 stm/bakery/ovens/rack.md", "--as", "till-bot"),
                          forest("read", FLOOR_PAGE, "--as", "till-bot")),
    "invalid_path": Fix("find", forest("read", f"{TILL_LANE}/turns", "--as", "till-bot"),
                        forest("read", TILL_LANE, "--as", "till-bot")),
    "read_lines": Fix("find", forest("read", FLOOR_PAGE, "--lines", "12-10", "--as", "till-bot"),
                      forest("read", FLOOR_PAGE, "--lines", "10-12", "--as", "till-bot")),
    "read_lines_empty": Fix("find", forest("read", FLOOR_PAGE, "--lines", "40-50", "--as", "till-bot"),
                            forest("read", FLOOR_PAGE, "--lines", "10-12", "--as", "till-bot")),
    "search_empty": Fix("find", forest("search", "   ", "--as", "till-bot"),
                        forest("search", "trays", "--as", "till-bot")),
    "search_limit": Fix("find", forest("search", "trays", "--as", "till-bot", "--limit", "0"),
                        forest("search", "trays", "--as", "till-bot", "--limit", "3")),
    # mcp.py: under 2025-11-25 a schema refusal is a tool result
    "arguments_invalid": Fix("find", tool("memory_read", {"path": FLOOR_PAGE, "lines": "twelve"}),
                             tool("memory_read", {"path": FLOOR_PAGE, "lines": "10-12"})),
    # cli_find.py and groups.py: who reads
    "scope_widened": Fix("find", forest("read", FLOOR_PAGE, "--as", "till-bot", "--named", "oven-bot"),
                         forest("read", FLOOR_PAGE, "--as", "till-bot")),
    "reader_conflict": Fix("find", forest("read", FLOOR_PAGE, "--as", "till-bot", "--as-group", "bakery-bots"),
                           forest("read", FLOOR_PAGE, "--as", "till-bot")),
    "agent_needs_group": Fix("find", forest("read", FLOOR_PAGE, "--as", "till-bot", "--agent", "till-bot"),
                             forest("read", FLOOR_PAGE, "--as", "till-bot")),
    "agent_unset": Fix("find", forest("read", FLOOR_PAGE, "--as-group", "bakery-bots"),
                       forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "till-bot"),
                       files={"forest.toml": BAKERY_BOTS}),
    "agent_ambiguous": Fix("find", forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "Shop Bot"),
                           forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "till-bot"),
                           files={"forest.toml": SHOP_BOTS}),
    # A closed group: a name that is no member, and a name that cannot be an agent at all.
    "agent_not_in_group": Fix("find", forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "kiln-bot"),
                              forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "till-bot"),
                              files={"forest.toml": BAKERY_BOTS}),
    "agent_name_invalid": Fix("find", forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "rye" * 70),
                              forest("read", FLOOR_PAGE, "--as-group", "bakery-bots", "--agent", "till-bot"),
                              files={"forest.toml": BAKERY_BOTS}),
    # A source is a kind and a name, and one of the reader's own: till-bot's lane came through the jsonl adapter.
    "source_unknown": Fix("find", forest("search", "trays", "--as", "till-bot", "--source", "night-shift"),
                          forest("search", "trays", "--as", "till-bot", "--source", "adapter:jsonl"),
                          make=lambda root, base: sources.listed(root, ["till-bot"])),
    # The caller's own turn id, already a turn of the session with other words.
    "turn_id_reused": Fix("find", forest("log", "--as", "till-bot", "--turn-id", "tray-count-1",
                                         stdin=b"Till Bot counted the oat trays.\n"),
                          forest("log", "--as", "till-bot", "--turn-id", "tray-count-2", stdin=TURN),
                          make=lambda root, base: _first_keyed_turn(root)),
    # `forest log` (cli_find.py, intake.py)
    "log_text_from_stdin": Fix("find", forest("log", "--as", "till-bot", "Till Bot counted the rye trays."),
                               forest("log", "--as", "till-bot", stdin=TURN)),
    "log_caller_fields": Fix("find", forest("log", "--as", "till-bot", "--origin", "headless", stdin=TURN),
                             forest("log", "--as", "till-bot", stdin=TURN)),
    "log_agent_unset": Fix("find", forest("log", stdin=TURN), forest("log", "--as", "till-bot", stdin=TURN)),
    "log_too_large": Fix("find", forest("log", "--as", "till-bot", stdin=b"rye " * 250_001),
                         forest("log", "--as", "till-bot", stdin=TURN)),
    "invalid_utf8": Fix("find", forest("log", "--as", "till-bot", stdin=b"\xff\xfe Till Bot counted the trays.\n"),
                        forest("log", "--as", "till-bot", stdin=TURN)),
    "intake_text_empty": Fix("find", forest("log", "--as", "till-bot", stdin=b"   \n"),
                             forest("log", "--as", "till-bot", stdin=TURN)),
    # `forest mcp` (mcp.py): a stdio server ends at the end of its (empty) input; one on HTTP says it listens
    "mcp_agent_unset": Fix("find", forest("mcp"), forest("mcp", "--as", "till-bot")),
    "mcp_reader_conflict": Fix("find", forest("mcp", "--as", "till-bot", "--as-group", "bakery-bots"),
                               forest("mcp", "--as", "till-bot")),
    "mcp_group_invalid": Fix("find", forest("mcp", "--as-group", "Night Shift"),
                             forest("mcp", "--as-group", "bakery-bots"), files={"forest.toml": BAKERY_BOTS}),
    "mcp_bind": Fix("find", forest("mcp", "--as", "till-bot", "--http", "the loopback"),
                    Served(("mcp", "--as", "till-bot", "--http", "127.0.0.1:0"), "forest mcp: listening on ")),
    # `forest intake` (cli_intake.py, intake.py and its adapters)
    "intake_arguments": Fix("find", forest("intake", "rewind"), forest("intake", "status")),
    "intake_adapter_unset": Fix("find", forest("intake"), forest("intake", "--from", "jsonl")),
    "intake_adapter_unknown": Fix("find", forest("intake", "--from", "oven-reader"),
                                  forest("intake", "--from", "jsonl")),
    "intake_agent_unset": Fix("find", forest("intake", "--from", "jsonl", "--once", "--transcript",
                                             "{base}/dawn.jsonl"),
                              forest("intake", "--from", "jsonl", "--once", "--transcript", "{base}/dawn.jsonl",
                                     "--as", "till-bot"),
                              beside={"dawn.jsonl": DAWN}),
    "intake_path_unset": Fix("find", forest("intake", "--from", "jsonl", "--once", "--as", "till-bot"),
                             forest("intake", "--from", "jsonl", "--once", "--as", "till-bot",
                                    "--transcript", "{base}/dawn.jsonl"),
                             beside={"dawn.jsonl": DAWN}),
    "intake_agent_invalid": Fix("find", forest("intake", "rename-agent", "Till Bot", "counter-bot"),
                                forest("intake", "rename-agent", "till-bot", "counter-bot")),
    # The intake lock's file is there, as the first intake of a forest leaves it.
    "intake_rename_exists": Fix("find", forest("intake", "rename-agent", "oven-bot", "till-bot"),
                                forest("intake", "rename-agent", "oven-bot", "hearth-bot"),
                                files={".forest/locks/intake.lock": ""}),
    "unsafe_transcript": Fix("find",
                             forest("intake", "--from", "jsonl", "--once", "--as", "till-bot",
                                    "--transcript", "{base}/stray.jsonl"),
                             forest("intake", "--from", "jsonl", "--once", "--as", "till-bot",
                                    "--transcript", "{base}/shift-records/dawn.jsonl"),
                             files={"forest.toml": JSONL_FOLDER},
                             beside={"shift-records/dawn.jsonl": DAWN, "stray.jsonl": DAWN}),
    "hook_incomplete": Fix("find", api("intake.run(jsonl, a hook naming no transcript)", _hook()),
                           api("intake.run(jsonl, a hook naming its transcript)",
                               _hook(transcript_path="{base}/shift-records/dawn.jsonl")),
                           files={"forest.toml": JSONL_HOOKED, ".forest/intake/tools/jsonl/state.json": INTAKE_ON},
                           beside={"shift-records/dawn.jsonl": DAWN}),
    "hook_invalid": Fix("find", api("read_hook_payload(text, no JSON)", _hook_payload(b"the oven hook said nothing")),
                        api("read_hook_payload(a JSON object)", _hook_payload(b'{"session_id": "dawn-shift"}'))),
    "hook_too_large": Fix("find", api("read_hook_payload(an object over 8 MiB)",
                                      _hook_payload(b'{"note": "' + b"r" * (8 * 1024 * 1024) + b'"}')),
                          api("read_hook_payload(a small object)", _hook_payload(b'{"session_id": "dawn-shift"}'))),
    # No command or tool gives append_owned another role or origin: `forest log` and memory_log pass their own.
    "intake_role_invalid": Fix("find", api("intake.append_owned(role baker)",
                                           _owned(role="baker", origin="interactive")),
                               api("intake.append_owned(role agent)", _owned(role="agent", origin="interactive"))),
    "intake_origin_invalid": Fix("find", api("intake.append_owned(origin by phone)",
                                             _owned(role="agent", origin="by phone")),
                                 api("intake.append_owned(origin interactive)",
                                     _owned(role="agent", origin="interactive"))),
    "intake_session_invalid": Fix("find", api("stage.prepare(root, '../dawn-shift', ...)",
                                              lambda root, base: stage.prepare(root, "../dawn-shift",
                                                                               "Count the rye trays.")),
                                  api("stage.prepare(root, 'dawn-shift', ...)",
                                      lambda root, base: stage.prepare(root, "dawn-shift", "Count the rye trays."))),
    # archive.py and record.py
    "archive_not_found": Fix("find", forest("archive", "show", FLOOR_PAGE),
                             forest("archive", "show", "04 stm/bakery/ovens/rack.md"), files=ARCHIVED),
    "archive_path_invalid": Fix("find", forest("archive", "put", DAILY_PAGE, "--reason", "kept before a rewrite",
                                               "--keep"),
                                forest("archive", "put", FLOOR_PAGE, "--reason", "kept before a rewrite", "--keep")),
    "archive_reason_missing": Fix("find", forest("archive", "put", FLOOR_PAGE, "--reason", "  ", "--keep"),
                                  forest("archive", "put", FLOOR_PAGE, "--reason", "kept before a rewrite",
                                         "--keep")),
    "record_path_invalid": Fix("find", forest("record", "trim", DAILY_PAGE, "--dry-run"),
                               forest("record", "trim", FLOOR_PAGE, "--dry-run")),
    # `forest record trim` returns before trimmed_text when nothing moves, so only a direct call reaches it.
    "record_missing": Fix("find", api("record.trimmed_text(a page with no update record)", _trimmed(RACK_TEXT)),
                          api("record.trimmed_text(a page with its update record)",
                              _trimmed(RACK_TEXT + "\n## Update record\n"))),
    # numbers, credentials, check and lint
    "numbers_path_invalid": Fix("find", forest("numbers", "check", DAILY_PAGE),
                                forest("numbers", "check", FLOOR_PAGE, exits=(0, 1))),
    "credentials_path_invalid": Fix("find", forest("credentials", "check", DAILY_PAGE),
                                    forest("credentials", "check", FLOOR_PAGE, exits=(0, 1))),
    "credential_candidate_invalid": Fix("find", forest("credentials", "judge", f"{BACK_GATE}:3", "--not-sign-in"),
                                        forest("credentials", "judge", f"{BACK_GATE}:3:1", "--not-sign-in"),
                                        files={BACK_GATE: BACK_GATE_TEXT}),
    "check_unknown": Fix("synthetic", forest("check", "ovens"), forest("check", "journal", exits=(0, 1))),
    "lint_code_unknown": Fix("synthetic", forest("lint", "--codes", "oven_door_open"),
                             forest("lint", "--codes", "leaf_entity_missing", exits=(0, 1))),
    "baseline_invalid": Fix("synthetic",
                            forest("lint", "--codes", "leaf_entity_missing", "--against", "{base}/recipe-card.json"),
                            forest("lint", "--codes", "leaf_entity_missing", "--against", "{base}/lint-baseline.json",
                                   exits=(0, 1)),
                            beside={"recipe-card.json": '{"schema": "a recipe card", "hits": []}\n',
                                    "lint-baseline.json": LINT_BASELINE}),
    # Only the package registers lint codes, each once, as it is imported.
    "lint_code_invalid": Fix("synthetic",
                             api("lint.register(level_due as a violation)", lambda root, base: lint.register(
                                 dataclasses.replace(lint.CODES["level_due"], class_=lint.VIOLATION))),
                             api("lint.register(level_due as it is)",
                                 lambda root, base: lint.register(lint.CODES["level_due"]))),
    # Public functions no command calls with these arguments.
    "move_record_invalid": Fix("find", api("movelog.record(move, floor -> floor)",
                                           lambda root, base: movelog.record("move", FLOOR_PAGE, FLOOR_PAGE)),
                               api("movelog.record(move, floor -> sweeping)",
                                   lambda root, base: movelog.record("move", FLOOR_PAGE,
                                                                     "04 stm/bakery/ovens/sweeping.md"))),
    "invalid_lock_name": Fix("find", api("locking.lock_status(root, 'Oven Lock')",
                                         lambda root, base: locking.lock_status(root, "Oven Lock")),
                             api("locking.lock_status(root, 'writer')",
                                 lambda root, base: locking.lock_status(root, "writer"))),
    # timeline.py, on a forest whose index is built: timeline.query brings the index up to date before it reads
    # the phrase, so on a forest with no index the refused call would write .forest/index.sqlite first.
    "time_phrase_empty": Fix("find-indexed", forest("timeline", "query", "   ", "--as", "till-bot"),
                             forest("timeline", "query", "yesterday evening", "--as", "till-bot")),
    "time_phrase_unknown": Fix("find-indexed", forest("timeline", "query", "the day the oven sang", "--as", "till-bot"),
                               forest("timeline", "query", "the last 3 hours", "--as", "till-bot")),
    # safety.py and layout.py: the root a command names
    "root_not_found": Fix("find", forest("agents", "--root", "{base}/closed-shop"), forest("agents")),
    "root_not_directory": Fix("find", forest("agents", "--root", "{root}/forest.toml"), forest("agents")),
    "root_not_empty": Fix("bakery", forest("init", "{root}"), forest("init", "{base}/second-shop")),
    # A folder with no forest.toml: the corrected call names the forest.
    "no_forest": Fix("find", forest("agents", "--root", "{base}"), forest("agents")),
    "root_above_file": Fix(
        "find",
        forest("index", "status", "--root", "{base}/blocker/forest"),
        forest("index", "status"),
        make=lambda _root, base: (base / "blocker").write_text("not a folder\n", encoding="utf-8"),
    ),
    # The holder of a lock enters it again with its token.
    "lock_already_held": Fix("find", api("hold_lock inside its own hold, without the token", _nested_hold(False)),
                             api("hold_lock inside its own hold, with the token", _nested_hold(True))),
})


# ═══════════════════════════════ the run rows ═══════════════════════════════

RUN_ROWS: dict[str, Run] = {}

# ─── the 0.3 index (index.py, retrieval.py, core.py), on a 0.3 forest whose index was built ───

LEGACY_INDEX = ".memory-forest/index.sqlite3"
TELEMETRY = "04 stm/mission-operations/recovery-drill/telemetry-replay.md"


def _legacy_sql(statement: str) -> Callable[[Path, Path], None]:
    def make(root: Path, base: Path) -> None:
        database = sqlite3.connect(root / LEGACY_INDEX)
        try:
            database.execute(statement)
            database.commit()
        finally:
            database.close()

    return make


def _legacy_page_changed(root: Path, base: Path) -> None:
    page = root / TELEMETRY
    page.write_text(page.read_text(encoding="utf-8") + "\nA line written after the index was built.\n",
                    encoding="utf-8")


_add(RUN_ROWS, {
    "index_corrupt": Run("legacy-indexed", legacy("retrieve", "{root}", "telemetry"),
                         make=_legacy_sql("UPDATE documents SET route_key = 'xltm/elsewhere' "
                                          "WHERE path = '01 xltm/XLTM.md'")),
    "index_not_found": Run("legacy", legacy("search", "{root}", "telemetry")),
    "index_schema_mismatch": Run("legacy-indexed", legacy("search", "{root}", "telemetry"),
                                 make=_legacy_sql("UPDATE metadata SET value = '1' "
                                                  "WHERE key = 'index_schema_version'")),
    "index_stale": Run("legacy-indexed", legacy("search", "{root}", "telemetry", "--include-body"),
                       make=_legacy_page_changed),
})

# ─── the forest command line and the registry (cli.py, registry.py) ───

_add(RUN_ROWS, {
    # The compiled registry is gone. A command compiles it again from the pages; a caller of registry.load
    # that asks it not to is refused.
    "registry_missing": Run("bakery", api("registry.load(root, build_if_missing=False)",
                                          lambda root, base: forest_registry.load(root, build_if_missing=False)),
                            files={".forest/registry.json": None}),
    # The compiled registry is not JSON.
    "registry_invalid": Run("synthetic", forest("registry", "show", "kiln-two"),
                            files={".forest/registry.json": '{"schema": '}),
})

# ─── the version 1 index (search.py): the index file is damaged; reset, then build, cures it ───


def _damaged_index(root: Path, base: Path) -> None:
    (root / ".forest" / "index.sqlite").write_bytes(b"not a database, and longer than one header of a page " * 4)


def _require_index(root: Path, base: Path) -> bool:
    """``search.rows``: one statement on the published index. No index is ``index_missing``."""
    search.rows(root, "SELECT 1")
    return True


_add(RUN_ROWS, {
    "index_missing": Run("find", api('search.rows(root, "SELECT 1")', _require_index)),
    "index_unreadable": Run("find-indexed", forest("search", "trays", "--as", "till-bot"), make=_damaged_index),
})


# ═══════════════════════════════ no row, and why ═══════════════════════════════

# A code no corrected call cures on the forest as it is: only the owner (a file, a setting, an access right)
# or another account can change what is wrong. Its move is `report`, and this says why it has no fix row.
NOT_FIX: dict[str, str] = {
    # The structure tools.
    "collection_page_taken": "collections.py cut: a cut of a collection page past the cap would make a month or day "
    "page that already exists (choose_page itself files new entries on month pages beside a full quarter page); no "
    "tool joins two collection pages, so only the owner can merge them before the cut",
    "demote_several_leaves": "collections.py demote: the subject has more than one leaf and must be one leaf first; no "
    "tool joins two leaves of one subject (merge joins two subjects, recut-stream only re-cuts period pages of one "
    "stream), so the owner merges them by hand",
    "entry_is_subject": "collections.py admit: a collection entry carries the id of a registered subject (the lint's "
    "registry_entry_is_subject); no call admits that entry, only an edit of the collection page",
    "role_set": "move.py fill_agent, reached by `forest levels apply --dry-run` (listed under `refused` without "
    "--dry-run): the settings give an agent a role while its tree page's `## Role` reads as unset to "
    "agents.role_text (first paragraph `(not set)`, or no such heading) and as set to fill_agent (a further "
    "paragraph); only an edit of the tree page or the settings ends it",
    "subject_has_no_leaf": "move.py promote: the subject's registry line names a home page that is not there and no "
    "leaf names the subject (registry_home_missing); only the owner can write the page or correct the line",
    "subtopic_invalid": "move.py promote to a tree: a leaf's `subtopic:` cannot name a branch; no tool sets a leaf's "
    "subtopic (`forest move set` takes kind, about and standing), so the owner edits the field",
    "takeover_collection_conflict": "move.py take-over: the category and the tree's shared branch each keep a "
    "collection, which would have to become one; no tool joins two collections (`forest move merge` refuses a "
    "collection), so only the owner can",
    # The 0.3 forest's own files.
    "invalid_config": "core.py:414,445,646: .memory-forest/forest.json is not strict JSON or holds a field of the "
    "wrong shape; every 0.3 command reads it first, and no command writes it again (init refuses an existing root)",
    "invalid_retrieval_config": "core.py:701-718: the `retrieval` block of .memory-forest/forest.json holds a bound "
    "out of range; only the owner's edit of the file changes it",
    "invalid_daily_machine_block": "writer.py:2134-2246: a Daily page holds a damaged admitted-batch block; every "
    "writer reads every Daily page first (writer.py:1503,1551,1692) and renders only whole blocks, so no plan "
    "gets past it until the owner mends the page",
    "invalid_receipt": "writer.py:2295-2430: a receipt under .memory-forest/receipts/ is damaged; a plan that names "
    "its transaction reads it, and a plan with another transaction id is another write, not a corrected call",
}

# A code no caller can reach: the raise site is a guard no input gets past. A caller is a command, an MCP tool,
# or a public function of the library (no leading underscore) called with plain values. A private function or
# method is not one, nor a public function that needs what only a running command holds (a `Plan` taken under
# the writer lock). No row can make it, so its move is `report`: if it ever leaves, the owner hears of it.
UNREACHABLE: dict[str, str] = {
    # The version 1 tools.
    "not_an_agent_tree": "move.py fill_agent takes a Plan held under the writer lock; its only caller is "
    "levels.carry_out for a `fill` item, and levels.due makes a `fill` item only when levels.agent_fills finds an "
    "agent at level tree whose tree page exists, the very conditions fill_agent refuses without",
    # The 0.3 command line, model and writer.
    "write_journal_exists": "writer.py _MutationSession.prepare (a private class): apply_daily, promote and "
    "apply_structured_sweep each run _recover_write_journal under the maintenance lock first, which rolls back and "
    "removes `<root>.write-journal` or refuses (unsafe_write_journal, write_recovery_failed); only a process "
    "ignoring the lock could make one in between. Its command `forest recover` would not cure it either: it rolls "
    "back a version 1 forest's .forest/journal, and on a 0.3 forest (no forest.toml) it is refused no_forest",
    "unsupported_layer": "model.py:250: parse_relative_route reaches it only for a layer it has no branch for, and "
    "the layer pattern admits only the seven names, each handled above it",
    "noncanonical_write_path": "writer.py:1643,1834, in the private _structured_sweep_changes and _execute_write: "
    "the writer builds its target paths from checked slugs and dates, which parse back to themselves",
    "write_target_escape": "writer.py:264, in the private _MutationSession: a target is root / a canonical "
    "relative path; the sort just before (writer.py:241) calls relative_to first, so an escaping path would fail "
    "there with ValueError, not this code",
    "write_transaction_too_large": "writer.py:244, in the private _MutationSession: prepare gets the index, the "
    "receipt and the pages of a plan, never none, and at most the root page and 3 pages for each of 128 promotions "
    "(MAX_PROMOTIONS): 387 of 512",
    "invalid_write_operation": "writer.py:1814: the private _execute_write is called only with the three receipt "
    "operations (writer.py:1520,1601,1749)",
    "empty_jsonl": "core.py:786: _validate_jsonl runs only inside inspect_forest's try (core.py:248), which turns the "
    "refusal into an issue of `validate` or `audit`",
    "invalid_jsonl": "core.py:774: as empty_jsonl, an issue of `validate` or `audit`, never a refusal",
    "invalid_jsonl_event": "core.py:780: as empty_jsonl, an issue of `validate` or `audit`, never a refusal",
}

# A row a bug in the package keeps from holding: the input, and what happens. Each row is in FIX_ROWS or
# RUN_ROWS and is expected to fail until the package is changed.
BUGS: dict[str, str] = {}



# ═══════════════════════════════ the proofs ═══════════════════════════════


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Every entry under the root: its mode, and a file's bytes."""
    if not root.exists():
        return {}
    found: dict[str, tuple[int, bytes | None]] = {".": (stat.S_IMODE(root.lstat().st_mode), None)}
    for folder, names, files in os.walk(root):
        for name in names + files:
            path = Path(folder) / name
            info = path.lstat()
            data = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
            found[str(path.relative_to(root))] = (stat.S_IMODE(info.st_mode), data)
    return found


def _changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return sorted(
        [f"+ {path}" for path in set(after) - set(before)]
        + [f"- {path}" for path in set(before) - set(after)]
        + [f"~ {path}" for path in set(before) & set(after) if before[path] != after[path]]
    )


class _Scratch:
    """A private folder for one row: the forest is ``<base>/forest``."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.base = Path(self._temporary.name).resolve()
        self.root = self.base / "forest space ' $"

    def make(self, name: str, files: Mapping[str, str | bytes | None],
             beside: Mapping[str, str | bytes | Callable[[Path, Path], str]] = {}) -> None:
        FORESTS[name](self.root)
        _write(self.root, {key: self._value(value) for key, value in files.items()})
        _write(self.base, {key: self._value(value) for key, value in beside.items()})

    def _value(self, value: Any) -> Any:
        if callable(value):
            return value(self.root, self.base)
        return _filled(value, self.root, self.base) if isinstance(value, str) else value

    def close(self) -> None:
        for folder, names, _files in os.walk(self.base):
            for name in names:
                path = Path(folder) / name
                if not path.is_symlink():
                    os.chmod(path, 0o700)
        self._temporary.cleanup()


class FixMoveTests(unittest.TestCase):
    """A wrong call is refused with its code and changes nothing; the corrected call then succeeds."""

    def test_every_fix_row(self) -> None:
        for code, row in sorted(FIX_ROWS.items()):
            with self.subTest(code=code):
                if code in BUGS:
                    with self.assertRaises(AssertionError, msg=f"{code} holds now: take it out of BUGS"):
                        self._prove(code, row)
                else:
                    self._prove(code, row)

    def _prove(self, code: str, row: Fix) -> None:
        scratch = _Scratch()
        try:
            scratch.make(row.forest, row.files, row.beside)
            root, base = scratch.root, scratch.base
            if row.make is not None:
                row.make(root, base)
            before = _snapshot(root)
            wrong = row.wrong.run(root, base)
            self.assertIsNotNone(wrong.refused, f"the wrong call was answered: {wrong.said}")
            assert wrong.refused is not None
            self.assertEqual(wrong.refused["code"], code, wrong.said)
            self.assertEqual(wrong.refused.get("next"), "fix", wrong.said)
            self.assertNotIn("command", wrong.refused, wrong.said)
            self.assertEqual(_changes(before, _snapshot(root)), [], f"the refused call changed the forest: {wrong.said}")
            right = row.right.run(root, base)
            self.assertIsNone(right.refused, f"the corrected call was refused: {right.said}")
        finally:
            scratch.close()


class RunMoveTests(unittest.TestCase):
    """A refusal whose move is ``run``: its command parses, holds no placeholder, and cures the state."""

    def test_every_run_row(self) -> None:
        for code, row in sorted(RUN_ROWS.items()):
            with self.subTest(code=code):
                scratch = _Scratch()
                try:
                    scratch.make(row.forest, row.files)
                    if row.make is not None:
                        row.make(scratch.root, scratch.base)
                    refused = row.call.run(scratch.root, scratch.base)
                    self.assertIsNotNone(refused.refused, f"the call was answered: {refused.said}")
                    assert refused.refused is not None
                    self.assertEqual(refused.refused["code"], code, refused.said)
                    self.assertEqual(refused.refused.get("next"), "run", refused.said)
                    command = refused.refused.get("command") or ""
                    if code in BUGS:
                        with self.assertRaises(AssertionError, msg=f"{code} holds now: take it out of BUGS"):
                            self._cure(code, command, row, scratch)
                    else:
                        self._cure(code, command, row, scratch)
                finally:
                    scratch.close()

    def _cure(self, code: str, command: str, row: Run, scratch: _Scratch) -> None:
        """Parse the command with its own parser, run it (and each ``run`` move its answer gives), call again."""
        queue = [command]
        for _ in range(4):
            if not queue:
                break
            text = queue.pop(0)
            answer = _carry_out(self, text, scratch)
            if isinstance(answer, dict) and answer.get("next") == "run" and answer.get("command"):
                queue.append(answer["command"])
        self.assertEqual(queue, [], f"{code}: the moves did not end")
        again = row.call.run(scratch.root, scratch.base)
        self.assertIsNone(again.refused, f"{code}: after `{command}` the same call is still refused: {again.said}")


_PLACEHOLDER = re.compile(r"<[^<>]*>")


def _carry_out(case: unittest.TestCase, command: str, scratch: _Scratch) -> Any:
    """One ``run`` command: parsed by its program's own parser, then run on the forest the refusal is about."""
    case.assertTrue(command, "a run move with no command")
    case.assertIsNone(_PLACEHOLDER.search(command), f"a placeholder in the command: {command}")
    try:
        words = shlex.split(command)
    except ValueError as error:
        case.fail(f"the run command is not shell-quoted correctly: {error}")
    case.assertIn(words[0], ("forest", "memory-forest"), f"not a command of the package: {command}")
    parser = cli.build_forest_parser() if words[0] == "forest" else cli.build_parser()
    with _Streams(b""):
        try:
            parser.parse_args(words[1:])
        except (SystemExit, MemoryForestError) as exc:
            raise AssertionError(f"the command line's parser does not take it: {command}: {exc}") from None
    if words[0] == "forest":
        argv = words[1:]
        if "--root" not in argv and argv[:1] != ["init"]:
            argv += ["--root", str(scratch.root)]
        with _cwd(scratch.base):
            code, out, err = _forest_cli([*argv, "--json"])
    else:
        with _cwd(scratch.base):
            code, out, err = _legacy_cli([*words[1:], "--json"])
    case.assertEqual(code, 0, f"`{command}` did not run: {out}{err}")
    return json.loads(out) if out.strip().startswith("{") else None


class _cwd:
    """Run in ``folder``: a command with a relative path means a path under it."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def __enter__(self) -> None:
        self.before = os.getcwd()
        os.chdir(self.folder)

    def __exit__(self, *exc: Any) -> None:
        os.chdir(self.before)


# ═══════════════════════════════ the closed set ═══════════════════════════════


class ClosedSetTests(unittest.TestCase):
    """The rows and the table are one closed set, and every code without a row says why."""

    def test_every_fix_and_run_code_has_its_row_or_its_reason(self) -> None:
        moves = {code: nxt for code, (nxt, _command) in errors.REFUSALS.items()}
        reasons = {**NOT_FIX, **UNREACHABLE}
        self.assertEqual(sorted(set(FIX_ROWS) & set(reasons)), [], "a code has a row and a reason for none")
        self.assertEqual(sorted(set(NOT_FIX) & set(UNREACHABLE)), [])
        for code in FIX_ROWS:
            self.assertEqual(moves.get(code), "fix", f"{code}: a fix row for a code whose move is not fix")
        for code in RUN_ROWS:
            self.assertEqual(moves.get(code), "run", f"{code}: a run row for a code whose move is not run")
        for code in reasons:
            self.assertEqual(moves.get(code), "report", f"{code}: no row can hold it, so its move is report")
        for code in BUGS:
            self.assertIn(code, set(FIX_ROWS) | set(RUN_ROWS), f"{code}: a bug is marked on a row")
        fix = {code for code, nxt in moves.items() if nxt == "fix"}
        run = {code for code, nxt in moves.items() if nxt == "run"}
        self.assertEqual(sorted(fix - set(FIX_ROWS) - set(reasons)), [], "a fix code with no row and no reason")
        self.assertEqual(sorted(run - set(RUN_ROWS)), [], "a run code with no row")
        self.assertEqual(sorted((fix | run) - set(FIX_ROWS) - set(RUN_ROWS) - set(reasons)), [])

    def test_a_code_with_a_reason_is_still_made_somewhere(self) -> None:
        """A code named here has a site the package keeps; when the site goes, so does the entry."""
        codes = codes_walk.walk().codes()
        for code in [*NOT_FIX, *UNREACHABLE]:
            self.assertIn(code, codes, f"{code}: no site makes it any more; take it out")


if __name__ == "__main__":
    unittest.main()
