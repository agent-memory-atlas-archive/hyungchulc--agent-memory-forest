"""Number starring. Every number here is made up: the 555 exchange, the published test card range, an IBAN
whose country code no country has, a birth date in the future."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_checks_support import STAND, CheckForestTestCase, leaf  # noqa: E402
from v1_support import REPOSITORY_ROOT, SYNTHETIC_FOREST  # noqa: E402

from memory_forest import archive, numbers, record  # noqa: E402
from memory_forest.config import DEFAULT_CONFIG, parse_config, render_config  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402

STAND_UP = "03 mtm/workshop/truing-stand"
IBAN = "XQ47 TOOL 0001 2345 6789 01"
CARD = "4111 1111 1111 1111"
PHONE = "+1 555 0100 2233"


def forest(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())", *arguments],
        capture_output=True,
        env=environment,
        cwd=REPOSITORY_ROOT,
        check=False,
    )


def starred(text: str, config=DEFAULT_CONFIG) -> tuple[str, dict[str, int]]:
    new, counts = numbers.star_numbers(text, config=config)
    return new, dict(counts)


class StarTests(unittest.TestCase):
    def test_the_middle_is_starred_and_the_ends_stay(self) -> None:
        self.assertEqual(numbers.star_middle("1234 5678 9012"), "123* **** 9012")
        self.assertEqual(numbers.star_middle(PHONE), "+1 5** **** 2233")

    def test_iban_by_its_check_digits_whatever_the_country_letters(self) -> None:
        self.assertTrue(numbers.iban_holds(IBAN))
        self.assertFalse(numbers.iban_holds(IBAN.replace("47", "48")))
        new, counts = starred(f"Paid to {IBAN} on 2051-03-02.")
        self.assertEqual(counts, {"iban": 1})
        self.assertIn("XQ4* **** **** **** **89 01", new)
        self.assertNotIn("2345", new)

    def test_card_by_its_check_digit_and_order_numbers_stay_whole(self) -> None:
        new, counts = starred(f"The card {CARD} paid order {CARD.replace(' ', '')}.")
        self.assertEqual(counts, {"card": 1})
        self.assertIn("411* **** **** 1111", new)
        self.assertIn("order 4111111111111111", new)
        self.assertEqual(starred("Card 4111 1111 1111 1112.")[1], {})

    def test_account_by_the_word_before_it(self) -> None:
        new, counts = starred("Account 1234 5678 9012 at the bank; parcel 1234 5678 9012 came.")
        self.assertEqual(counts, {"account": 1})
        self.assertIn("Account 123* **** 9012", new)
        self.assertIn("parcel 1234 5678 9012", new)
        self.assertEqual(starred("Shelf 1234 5678 9012.")[1], {})

    def test_a_bank_name_is_an_account_word_by_setting(self) -> None:
        config = parse_config('[numbers]\naccount_words = ["Harbour Mutual"]\n')
        self.assertEqual(starred("Harbour Mutual 1234-5678-9012", config)[1], {"account": 1})
        self.assertEqual(starred("Account 1234-5678-9012", config)[1], {})

    def test_international_phone_numbers_and_lines_kept_whole(self) -> None:
        self.assertEqual(starred(f"Marit called from {PHONE}.")[1], {"phone": 1})
        config = parse_config('[numbers]\nkeep_whole = ["^\\\\+1 ?555 ?0100"]\n')
        new, counts = starred(f"The shop line is {PHONE}.", config)
        self.assertEqual(counts, {})
        self.assertIn(PHONE, new)

    def test_national_phone_forms_only_by_setting(self) -> None:
        text = "Call 555-0100-2233 after six."
        self.assertEqual(starred(text)[1], {})
        config = parse_config('[numbers]\nphone_patterns = ["\\\\b555-\\\\d{4}-\\\\d{4}\\\\b"]\n')
        new, counts = starred(text, config)
        self.assertEqual(counts, {"phone": 1})
        self.assertIn("555-****-2233", new)

    def test_identity_numbers_by_the_settings_formats(self) -> None:
        config = parse_config('[numbers]\nidentity_formats = ["NNNNNN-NNNN:last-4"]\n')
        new, counts = starred("Born 510302-9988 per the form; a part number 991399-1234 stays.", config)
        self.assertEqual(counts, {"identity": 1})
        self.assertIn("510302-****", new)
        self.assertIn("991399-1234", new)
        by_word = starred("personal number 991399-1234", config)
        self.assertEqual(by_word[1], {"identity": 1})
        self.assertEqual(starred("Born 510302-9988.")[1], {})

    def test_dates_amounts_urls_and_starred_numbers_stay(self) -> None:
        text = (
            "On 2051-03-02 10:15 it cost 1,234.50; see https://example.test/item/4111111111111111 "
            "and the card 411* **** **** 1111, account 123* **** 9012."
        )
        self.assertEqual(starred(text), (text, {}))


class SettingsTests(unittest.TestCase):
    def test_the_new_settings_check_their_values_and_round_trip(self) -> None:
        for text, key in (
            ('[numbers]\nphone_patterns = ["(unclosed"]\n', "numbers.phone_patterns"),
            ('[numbers]\nkeep_whole = ["[x"]\n', "numbers.keep_whole"),
            ('[record]\nquote_marks = ["<<>>"]\n', "record.quote_marks"),
        ):
            with self.assertRaises(MemoryForestError) as raised:
                parse_config(text)
            self.assertEqual(raised.exception.details["key"], key)
        config = parse_config('[numbers]\naccount_words = ["ledger no"]\nkeep_whole = ["^\\\\+1 ?800"]\n'
                              '[record]\nowner_word_names = ["the boss"]\n')
        self.assertEqual(parse_config(render_config(config)), config)


class StarCommandTests(CheckForestTestCase):
    def test_check_names_lines_and_kinds_never_digits(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, body=f"Paid with {CARD}; called {PHONE}.")})
        found = numbers.check(root)
        self.assertEqual([(item.path, item.kinds) for item in found], [(STAND, {"card": 1, "phone": 1})])
        result = forest("numbers", "check", "--root", str(root))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b"1111", result.stdout)
        self.assertNotIn(b"2233", result.stdout)

    def test_star_archives_first_and_writes_no_record_line(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, body=f"Paid with {CARD}.")})
        before = (root / STAND).read_text(encoding="utf-8")
        planned = numbers.star(root, [STAND], dry_run=True)
        self.assertFalse(planned[0].written)
        self.assertEqual((root / STAND).read_text(encoding="utf-8"), before)
        done = numbers.star(root, [STAND])
        self.assertTrue(done[0].written)
        after = (root / STAND).read_text(encoding="utf-8")
        self.assertEqual(after, before.replace(CARD, "411* **** **** 1111"))
        entry = archive.latest(root, STAND)
        self.assertEqual(entry.reason, numbers.STAR_REASON)
        self.assertEqual(archive.read_copy(root, entry).decode("utf-8"), before)
        self.assertEqual(numbers.check(root), [])

    def test_star_command_needs_pages_or_all(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, body=f"Paid with {CARD}.")})
        refused = forest("numbers", "star", "--root", str(root))
        self.assertEqual(refused.returncode, 2)
        done = forest("numbers", "star", "--all", "--root", str(root), "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([page["path"] for page in json.loads(done.stdout)["pages"]], [STAND])
        self.assertEqual(forest("numbers", "check", "--root", str(root)).returncode, 0)

    def test_only_forest_pages_are_starred(self) -> None:
        root = self.workshop()
        with self.assertRaises(MemoryForestError):
            numbers.check(root, ["05 daily/2051-03-02.md"])

    def test_the_synthetic_forest_is_clean(self) -> None:
        result = forest("numbers", "check", "--root", str(SYNTHETIC_FOREST))
        self.assertEqual(result.returncode, 0, result.stdout)



class ReadPathTests(CheckForestTestCase):
    """The tools star what they show, as the write path stars what it writes."""

    def test_an_archived_copy_is_shown_starred(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, body=f"Paid with {CARD}.")})
        numbers.star(root, [STAND])
        self.assertIn(CARD, archive.read_copy(root, archive.latest(root, STAND)).decode("utf-8"))  # kept whole
        shown = forest("archive", "show", STAND, "--root", str(root))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn(b"411* **** **** 1111", shown.stdout)
        self.assertNotIn(CARD.encode(), shown.stdout)

    def test_record_lines_read_back_from_a_history_file_are_starred(self) -> None:
        lines = [f'- 2051-01-{day:02d} — Contact line: was "the old desk", now "{PHONE}, ask for the desk". '
                 "Why: the workshop moved." for day in range(1, 29)]
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, record="\n".join(lines))})
        result = record.trim_page(root, STAND)
        self.assertTrue(result.written)
        self.assertIn(PHONE, (root / result.archive_path).read_text(encoding="utf-8"))  # the history keeps bytes
        read_back = [line.render() for line in record.archived_record_lines(root, STAND)]
        self.assertTrue(read_back)
        self.assertTrue(all(numbers.star_middle(PHONE) in line and PHONE not in line for line in read_back))
        shown = forest("record", "history", STAND, "--root", str(root))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn(PHONE.encode(), shown.stdout)


if __name__ == "__main__":
    unittest.main()
