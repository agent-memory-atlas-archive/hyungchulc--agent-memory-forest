"""The fifth review of search and the MCP server.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

from memory_forest import search
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from v1_support import ForestTestCase


# Each of these is something a regular pointer file can hold, and none of them is an index name.
_DAMAGED = (
    "not utf-8",
    "a nul byte",
    "empty",
    "white space only",
    "more than one line",
    "a name outside the pattern",
    "a parent segment",
    "an absolute path",
    "longer than a name",
)


def _damaged_bytes(kind: str) -> bytes:
    if kind == "not utf-8":
        return b"\xff\xfe"
    if kind == "a nul byte":
        return b"index.sqlite\x00"
    if kind == "empty":
        return b""
    if kind == "white space only":
        return b" \t\n"
    if kind == "more than one line":
        return b"index.sqlite\nextra\n"
    if kind == "a name outside the pattern":
        return b"notes.sqlite\n"
    if kind == "a parent segment":
        return b"../outside.sqlite\n"
    if kind == "an absolute path":
        return b"/tmp/index.sqlite\n"
    if kind == "longer than a name":
        return b"x" * (search._POINTER_BYTES + 8)
    raise AssertionError(kind)


class PointerContentTests(ForestTestCase):
    """A pointer is absent, one index name, or damaged. Damaged never raises out of the reader."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.pointer = self.root / ".forest" / "index.current"

    def test_every_other_content_is_damaged_and_reset_replaces_it(self) -> None:
        calls = (
            ("status", lambda: search.status(self.root)),
            ("query", lambda: search.query(self.root, "trays", reader="till-bot")),
            ("build", lambda: search.build(self.root)),
        )
        for kind in _DAMAGED:
            self.pointer.write_bytes(_damaged_bytes(kind))
            for name, call in calls:
                with self.subTest(kind=kind, call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_unreadable", kind)
                    self.assertEqual(refused.exception.next_step, "run")
                    self.assertEqual(refused.exception.command, "forest index reset")
            with self.subTest(kind=kind, call="reset"):
                result = search.reset(self.root)
                self.assertTrue(result["ok"])
                name = self.pointer.read_text(encoding="utf-8").strip()
                self.assertRegex(name, r"\Aindex-[0-9a-f]{16}\.sqlite\Z")
                self.assertEqual(search.index_relative(self.root), f".forest/{name}")
                status = search.status(self.root)
                self.assertTrue(status["ok"])
                self.assertEqual(status["path"], f".forest/{name}")
