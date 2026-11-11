"""Values sent to sign in or to pay: the mask, its guards, the write door, the lint and ``forest credentials``.

Every value here is made up for the test and belongs to no account.
"""

from __future__ import annotations

import io
import json
import random
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase  # noqa: E402

from memory_forest import archive, credentials, lint  # noqa: E402
from memory_forest.cli import forest_main  # noqa: E402
from memory_forest.config import DEFAULT_CONFIG, parse_config  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.plan import Plan  # noqa: E402

R = credentials.REDACTED
LEAF = "04 stm/bakery/suppliers/millstone-flour.md"
MADE_UP = "Tulip7-made-up"


def masked(text: str) -> str:
    return credentials.mask(text, config=DEFAULT_CONFIG)


class MaskTests(unittest.TestCase):
    def test_each_default_label_with_and_without_a_colon(self) -> None:
        for label in DEFAULT_CONFIG.credentials.label_words:
            with self.subTest(label=label):
                self.assertEqual(masked(f"the {label}: 4821x9"), f"the {label}: {R}")
                self.assertEqual(masked(f"the {label} 4821x9"), f"the {label} {R}")
                self.assertEqual(masked(f"the {label.upper()} is 4821x9 now"), f"the {label.upper()} is {R} now")

    def test_a_value_glued_to_punctuation_or_in_quotes_keeps_the_punctuation(self) -> None:
        self.assertEqual(masked("Wren sent the door code 7395."), f"Wren sent the door code {R}.")
        self.assertEqual(masked("(PIN: 4821), then left"), f"(PIN: {R}), then left")
        self.assertEqual(masked('password: "my 4 dogs" for the shop'), f'password: "{R}" for the shop')
        self.assertEqual(masked("**PIN:** 4821"), f"**PIN:** {R}")
        self.assertEqual(masked("OTP=ab12cd;"), f"OTP={R};")

    def test_a_colon_takes_any_value_and_no_colon_needs_a_digit(self) -> None:
        self.assertEqual(masked("password: tulip"), f"password: {R}")
        self.assertEqual(masked("password tulip"), "password tulip")

    def test_ordinary_prose_with_a_label_word_and_no_value_is_left_alone(self) -> None:
        for text in (
            "Ilse changed her password on 2051-03-02.",
            "The password reset link came by mail.",
            "a password of 12 characters at least",
            "verification code sent at 10:45",
            "passwords: none kept here",
            "see `04 stm/bakery/password-rules.md` and pinned notes",
            "The PIN pad of the till was replaced.",
            "Password:\nthe next line is prose 2051",
            "pin it to the board",
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_two_labels_in_a_row_find_the_value_after_the_second(self) -> None:
        self.assertEqual(masked("pin pin 1234"), f"pin pin {R}")
        self.assertEqual(masked("the PIN code 4821"), f"the PIN code {R}")

    def test_mask_is_idempotent(self) -> None:
        for text in ("PIN: 4821", "door code 7395.", "password: [REDACTED].", f"pin {R} and OTP 99881",
                     'password "my 4 dogs"', "**PIN**: 4821 and the gate code is 1001#"):
            with self.subTest(text=text):
                once = masked(text)
                self.assertEqual(masked(once), once)

    def test_the_guards_never_change_what_a_step_gives(self) -> None:
        generator = random.Random(20510302)
        pieces = ["PIN", "pin code", "password", "door code", "one-time code", "OTP", "is", "was", ":", "=", " ",
                  "  ", "\n", "4821", "12", "tulip", "x9", ".", ",", '"', "(", ")", "**", "2051-03-02", "10:45",
                  "characters", "digits", "reset", "/", "@", "a", "Ilse", R, "-", "#", "\t", "Ünïcødé"]
        for _ in range(3000):
            text = "".join(generator.choice(pieces) for _ in range(generator.randint(1, 14)))
            guarded = credentials.mask_labelled(text, config=DEFAULT_CONFIG)
            self.assertEqual(guarded, credentials.mask_labelled(text, config=DEFAULT_CONFIG, guarded=False), text)
            self.assertEqual(credentials.mask_labelled(guarded, config=DEFAULT_CONFIG), guarded, text)

    def test_a_forest_adds_the_label_words_of_its_other_languages(self) -> None:
        config = parse_config('[credentials]\nlabel_words = ["password", "mot de passe", "codice porta"]\n'
                              'joining_words = ["is", "est"]\n')
        self.assertEqual(credentials.mask("le mot de passe est 7tulipes", config=config), f"le mot de passe est {R}")
        self.assertEqual(credentials.mask("codice porta: 2468", config=config), f"codice porta: {R}")
        self.assertEqual(credentials.mask("PIN 4821", config=config), "PIN 4821")
        with self.assertRaises(MemoryForestError):
            parse_config('[credentials]\nlabel_words = ["redacted"]\n')

    def test_what_a_tool_shows_is_masked_and_starred(self) -> None:
        shown = credentials.shown({"line": "PIN 4821 to +64 21 555 0199"}, config=DEFAULT_CONFIG)
        self.assertNotIn("4821", shown["line"])
        self.assertNotIn("21 555", shown["line"])
        self.assertIn(R, shown["line"])
        self.assertIn("*", shown["line"])


class DoorAndFixTests(ForestTestCase):
    def test_the_write_door_masks_a_record_line_as_it_stars_a_number(self) -> None:
        root = self.make_forest()
        plan = Plan.open(root)
        plan.record(LEAF, f"- 2051-03-05 — Door code given: was \"none\", now \"door code 7395\". Why: test.")
        self.assertEqual(plan.records[LEAF], [f"- 2051-03-05 — Door code given: was \"none\", now \"door code {R}\". "
                                              "Why: test."])

    def test_lint_reports_the_line_and_fix_masks_it_after_archiving_the_page(self) -> None:
        root = self.make_forest()
        page = root / LEAF
        original = page.read_bytes()
        page.write_text(original.decode("utf-8").replace("Something happened.",
                                                         f"Ilse sent the supplier portal password: {MADE_UP}."),
                        encoding="utf-8")
        before = page.read_bytes()
        report = lint.lint(root, codes=["credential_in_full"])
        hits = [hit for hit in report.hits if hit.code == "credential_in_full"]
        self.assertEqual([(hit.path, hit.class_) for hit in hits], [(LEAF, lint.VIOLATION)])
        self.assertNotIn(MADE_UP, json.dumps([hit.as_dict() for hit in hits]))
        self.assertEqual([item.path for item in credentials.fix(root, dry_run=True)], [LEAF])
        self.assertEqual(page.read_bytes(), before)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(forest_main(["credentials", "fix", "--root", str(root), "--all", "--json"]), 0)
        self.assertNotIn(MADE_UP, output.getvalue())
        text = page.read_text(encoding="utf-8")
        self.assertIn(f"Ilse sent the supplier portal password: {R}.", text)
        self.assertNotIn(MADE_UP, text)
        self.assertEqual(text, before.decode("utf-8").replace(MADE_UP, R))
        entry = archive.latest(root, LEAF)
        self.assertEqual(entry.reason, credentials.FIX_REASON)
        self.assertEqual(archive.read_copy(root, entry), before)
        self.assertEqual(lint.lint(root, codes=["credential_in_full"]).hits, [])
        self.assertEqual(credentials.fix(root), [])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(forest_main(["credentials", "check", "--root", str(root)]), 0)

    def test_archive_show_masks_what_the_archive_keeps_whole(self) -> None:
        root = self.make_forest()
        page = root / LEAF
        page.write_text(page.read_text(encoding="utf-8").replace("Something happened.", f"PIN: {MADE_UP}"),
                        encoding="utf-8")
        credentials.fix(root)
        output = io.BytesIO()

        class Out:
            buffer = output

            @staticmethod
            def flush() -> None:
                return None

        saved = sys.stdout
        sys.stdout = Out()  # type: ignore[assignment]
        try:
            self.assertEqual(forest_main(["archive", "show", "--root", str(root), LEAF]), 0)
        finally:
            sys.stdout = saved
        self.assertNotIn(MADE_UP.encode(), output.getvalue())
        self.assertIn(R.encode(), output.getvalue())


if __name__ == "__main__":
    unittest.main()
