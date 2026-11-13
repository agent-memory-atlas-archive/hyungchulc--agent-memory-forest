"""One reader of a page path a caller gives (``layout.caller_page_path``), for the scope check and the read alike.

The test walks spellings x (a page in the reader's scope, a page outside it, a page that does not exist), on
every door that takes a page path: the public functions, the commands and the MCP tools. A page in scope is
never called out of scope under any spelling the reader accepts, and no spelling reaches a page outside it.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from memory_forest import cli, layout, mcp, search
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES

READER = "till-bot"
# For each door: a page in till-bot's scope, one outside it (oven-bot's own tree), one that is not there.
PAGES = {
    "read": {
        "in scope": "04 stm/bakery/ovens/floor.md",
        "outside": "04 stm/oven-bot/methods/cooling.md",
        "absent": "04 stm/bakery/ovens/rack.md",
    },
    "navigate": {
        "in scope": "03 mtm/bakery/ovens.md",
        "outside": "03 mtm/oven-bot/methods.md",
        "absent": "03 mtm/bakery/racks.md",
    },
}


def accepted(page: str) -> dict[str, str]:
    """Spellings the reader takes as ``page`` itself."""
    return {
        "as written": page,
        "a leading ./": f"./{page}",
        "two leading ./": f"././{page}",
        "doubled slashes": page.replace("/", "//"),
        "a ./ inside": page.replace("/", "/./", 1),
        "a trailing slash": f"{page}/",
        "blanks around": f"  {page} ",
        "all of them": f" ./{page.replace('/', '//./')}/ ",
    }


def refused(page: str) -> dict[str, str]:
    """Spellings the reader refuses, with the code it refuses them with."""
    return {
        f"/{page}": "path_escape",
        f"../{page}": "path_escape",
        page.replace("/", "/../", 1): "path_escape",
        page.replace("/", "\\"): "invalid_path",
        f"{page}\x00": "invalid_path",
        f"{page}\n": "invalid_path",
        "a/" * 600 + page: "invalid_path",
        " / ": "path_escape",
        " ./ ": "invalid_path",
    }


class PagePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name).resolve() / "forest"
        for relative, text in FILES.items():
            target = cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _doors(self, kind: str, spelling: str) -> dict[str, tuple[str | None, str | None]]:
        """``{door: (code or None, the path answered)}`` for the spelling on every door of that kind."""
        found: dict[str, tuple[str | None, str | None]] = {}
        try:
            if kind == "read":
                answer = search.read(self.root, spelling, reader=READER)
            else:
                answer = search.navigate(self.root, reader=READER, path=spelling)
            found["public function"] = (None, answer["results"][0]["path"])
        except MemoryForestError as exc:
            found["public function"] = (exc.code, None)
        words = ["read", spelling] if kind == "read" else ["navigate", spelling]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.forest_main([*words, "--as", READER, "--root", str(self.root), "--json"])
        body = json.loads(out.getvalue())
        found["command"] = (body["error"]["code"], None) if code else (None, body["results"][0]["path"])
        tool = "memory_read" if kind == "read" else "memory_navigate"
        reply = mcp._call(self.root, READER, {"name": tool, "arguments": {"path": spelling}}, "stdio")
        body = json.loads(reply["content"][0]["text"])
        found["tool"] = (body["code"], None) if reply["isError"] else (None, body["results"][0]["path"])
        return found

    def test_every_spelling_on_every_door(self) -> None:
        for kind, pages in PAGES.items():
            for which, page in pages.items():
                expected = {"in scope": None, "outside": "out_of_scope", "absent": "path_not_found"}[which]
                for label, spelling in accepted(page).items():
                    for door, (code, answered) in self._doors(kind, spelling).items():
                        with self.subTest(kind=kind, page=which, spelling=label, door=door):
                            self.assertEqual(code, expected)
                            if expected is None:
                                self.assertEqual(answered, page)
                for spelling, wanted in refused(page).items():
                    for door, (code, _answered) in self._doors(kind, spelling).items():
                        with self.subTest(kind=kind, page=which, spelling=spelling[:40], door=door):
                            self.assertEqual(code, wanted)

    def test_the_reader_is_one_function_with_a_stated_length(self) -> None:
        self.assertEqual(layout.caller_page_path(" ./04 stm//bakery/./ovens/floor.md/ "), "04 stm/bakery/ovens/floor.md")
        self.assertEqual(layout.caller_page_path("a" * 255), "a" * 255)
        with self.assertRaises(MemoryForestError) as caught:
            layout.caller_page_path("a" * 256)
        self.assertEqual((caught.exception.code, caught.exception.next_step), ("invalid_path", "fix"))
        long = "/".join(["ab"] * 200)
        self.assertEqual(layout.caller_page_path(long), long)
        with self.assertRaises(MemoryForestError) as caught:
            layout.caller_page_path("a" * (layout.MAX_CALLER_PATH + 1))
        self.assertEqual((caught.exception.code, caught.exception.next_step), ("invalid_path", "fix"))
        self.assertEqual(layout.caller_page_path("04 stm/bakery/café.md"), "04 stm/bakery/café.md")


if __name__ == "__main__":
    unittest.main()
