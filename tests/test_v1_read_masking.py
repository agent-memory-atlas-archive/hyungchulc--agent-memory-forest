"""Every read that prints page text shows a person's number starred and a sign-in value masked.

One made-up page per forest holds a phone number and a labelled sign-in value; each read of the package
that prints page text (the version 1 ``forest`` commands and the 0.3 ``memory-forest`` reads) is fed it,
and neither value may come out whole. The values are made up and belong to no one.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase  # noqa: E402

from memory_forest import cli, index_forest, initialize_forest  # noqa: E402

PHONE = "+64 21 555 0199"
PHONE_MIDDLE = "21 555"
SECRET = "4821x9"
LINE = f"Bram Eskell rang from {PHONE}; the portal PIN: {SECRET}."
LEAF = "04 stm/bakery/suppliers/millstone-flour.md"
OVEN_NOTES = "04 stm/bakery/deck-oven/notes.md"
OLD_LEAF = "04 stm/mission-operations/recovery-drill/telemetry-replay.md"


class _Capture(io.StringIO):
    """A text stdout with a byte buffer beside it, for reads that write bytes (``archive show``)."""

    def __init__(self) -> None:
        super().__init__()
        self.buffer = io.BytesIO()


def run(entry, argv: list[str]) -> str:
    out, err = _Capture(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        entry(argv)
    return out.getvalue() + out.buffer.getvalue().decode("utf-8", "replace") + err.getvalue()


class ReadMaskingTests(ForestTestCase):
    def forest(self) -> Path:
        root = self.make_forest()
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8") + "\n[limits]\nrecord_keep_bytes = 200\n",
                            encoding="utf-8")
        leaf = root / LEAF
        record = "".join(f"- 2051-03-0{day} — Line changed: was \"{LINE}\", now \"rang again\". Why: test.\n"
                         for day in range(1, 6))
        leaf.write_text(leaf.read_text(encoding="utf-8").replace("Something happened.", LINE)
                        + f"\n## Update record\n\n{record}", encoding="utf-8")
        repeated = "".join(f"{LINE} Said on visit {number} at the front counter.\n\n" for number in range(3))
        oven = root / "04 stm/bakery/deck-oven/repairs.md"
        oven.write_text(oven.read_text(encoding="utf-8").replace("Something happened.", repeated), encoding="utf-8")
        (root / OVEN_NOTES).write_text(oven.read_text(encoding="utf-8").replace("subtopic: notes", "subtopic: calls"),
                                       encoding="utf-8")
        index = root / "03 mtm/bakery/bakery_MTM.md"
        index.write_text(index.read_text(encoding="utf-8").replace(
            " · aliases: Millstone", f" · aliases: Millstone; Millstone line {PHONE}; PIN: {SECRET}").replace(
            "\n## Branches", f"- broken line {LINE}\n\n## Branches"), encoding="utf-8")
        page = root / "04 stm/people/regulars/seen-once-2051-q1.md"
        page.write_text(page.read_text(encoding="utf-8").replace("## Bram Eskell", f"## Caller {PHONE}").replace(
            "Delivered a crate of apples.", LINE), encoding="utf-8")
        return root

    def old_forest(self) -> Path:
        root = self.base / "old"
        initialize_forest(root, example=True)
        leaf = root / OLD_LEAF
        leaf.write_text(leaf.read_text(encoding="utf-8") + f"\nTelemetry crew: {LINE}\n", encoding="utf-8")
        index_forest(root)
        return root

    def assert_hidden(self, name: str, output: str, *, seen: bool = True) -> None:
        self.assertNotIn(SECRET, output, name)
        self.assertNotIn(PHONE_MIDDLE, output, name)
        if seen:
            self.assertTrue("0199" in output or "[REDACTED]" in output, f"{name} did not show the page: {output[:300]}")

    def test_every_read_that_prints_page_text_masks_and_stars_it(self) -> None:
        root = self.forest()
        forest = ["--root", str(root)]
        run(cli.forest_main, ["archive", "put", LEAF, "--keep", "--reason", "a test copy", *forest])
        run(cli.forest_main, ["record", "trim", LEAF, *forest])
        reads = [
            ("archive show", ["archive", "show", LEAF], True),
            ("archive list", ["archive", "list", "--path", LEAF], False),
            ("record history", ["record", "history", LEAF], True),
            ("record kinds --examples", ["record", "kinds", "--examples", "9"], True),
            ("record size", ["record", "size", LEAF], False),
            ("registry show", ["registry", "show", "millstone-flour"], True),
            ("registry check", ["registry", "check"], True),
            ("check", ["check"], False),
            ("lint", ["lint"], True),
            ("lint --json", ["lint", "--json"], True),
            ("collections list", ["collections", "list"], True),
            ("collections cite-entries --dry-run", ["collections", "cite-entries", "--dry-run"], False),
            ("levels check", ["levels", "check"], False),
            ("repeats plan", ["repeats", "plan"], False),
            ("repeats check --json", ["repeats", "check", "--json"], False),
            ("numbers check", ["numbers", "check", "--all"], False),
            ("credentials check", ["credentials", "check", "--all"], False),
            ("credentials candidates", ["credentials", "candidates"], False),
            ("move resolve", ["move", "resolve", LEAF], False),
            ("move set --dry-run", ["move", "set", LEAF, "--kind", "event", "--dry-run"], False),
        ]
        for name, argv, seen in reads:
            with self.subTest(read=name):
                self.assert_hidden(name, run(cli.forest_main, [*argv, *forest]), seen=seen)

    def test_every_version_0_3_read_masks_and_stars_page_text(self) -> None:
        root = str(self.old_forest())
        reads = [
            ("search --include-body", ["search", root, "telemetry", "--include-body"], True),
            ("search", ["search", root, "telemetry"], False),
            ("route", ["route", root, "telemetry"], False),
            ("retrieve", ["retrieve", root, "telemetry"], False),
            ("structured-context", ["structured-context", root, "telemetry"], True),
        ]
        for name, argv, seen in reads:
            with self.subTest(read=name):
                self.assert_hidden(name, run(cli.main, argv), seen=seen)
