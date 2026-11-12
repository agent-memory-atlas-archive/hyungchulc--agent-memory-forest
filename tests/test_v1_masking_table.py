"""Masking as one table of cases and three properties over all of it.

Each row is a text, the secret part of it (or None when the text must stay as written), and a note. The
rows hold every input of the three intake reviews, the leaks and the damaged ordinary text alike, each row
of the table of what is a secret (``credentials``, above ``secret_word``), each header form and each part
tag with and without a colon. Every value is made up and belongs to no account.

Property 1: the masked row is the row with its secret part replaced by the mark, so the secret is absent
and nothing around it changed; a row with no secret is unchanged.
Property 2: masking twice gives what masking once gives.
Property 3: a row masks the same secret wherever it stands: alone, after other lines, inside each part tag,
as a JSON string value, as a tool argument, and between two other rows.
"""

from __future__ import annotations

import json
import random
import re
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_forest import credentials  # noqa: E402
from memory_forest.config import DEFAULT_CONFIG  # noqa: E402

R = credentials.REDACTED
TOKEN = "Ab3" * 12
BASIC = "QWxhZGRpbjpvcGVu"
GIT_HASH = "0123456789abcdef" * 2 + "01234567"
HEX64 = "0123456789abcdef" * 4
UUID = "550e8400-e29b-01d4-c716-446655440000"
DIGEST = 'username="kiln", realm="glaze", nonce="b1e2", uri="/shelf", response="6629fae49393a05397450978507c4ef1"'
TAGS = ("pasted_contents", "pasted_text", "attached_files", "file_contents", "ide_opened_file", "quoted_message",
        "quoted_selection", "ide_selection")
PRIVATE_KEY = (
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n"
    "bWFkZS11cC1rZXk=\n"
    "-----END " + "OPENSSH PRIVATE KEY-----"
)

ROWS: list[tuple[str, str | None, str]] = [
    # The third review.
    ("note <pasted_text>pin 4821</pasted_text>", "4821", "a value before a closing tag ends at the tag"),
    ("<quoted_message>the door code is 7395</quoted_message>", "7395", "the same in a quote"),
    ("<pasted_text>PIN: 4821</pasted_text>", "4821", "with a colon the tag stays whole"),
    (f"Authorization: Bearer {TOKEN}", TOKEN, "the mark is one token: no [[REDACTED]]"),
    ("notes\nKEY=hunter22", "hunter22", "a name on a later line"),
    ('cfg {"key": "hunter22"}', "hunter22", "a JSON key after other words"),
    ("set key=hunter22", "hunter22", "a name after a word"),
    ("notes\nCREDENTIAL=hunter22", "hunter22", "CREDENTIAL on a second line"),
    ("PASSWORD_MIN_LENGTH=12", None, "the secret word is not the last part"),
    ("TOKEN_LIMIT=4096", None, "the secret word is not the last part"),
    ("max_token: 1024", None, "a bare number after a token name needs 6 digits"),
    ("sort_key = lambda x: x", None, "lambda is code"),
    ("primary_key: id", None, "after key, a value that is no literal secret"),
    ("foreign_key=user_id", None, "after key, 7 characters"),
    ("key_count: 7", None, "the secret word is not the last part"),
    ("secret_santa: Mira", None, "the secret word is not the last part"),
    ("cache_key=build_key(x)", None, "an identifier followed by ( is code"),
    ("token = os.environ['KILN_TOKEN']", None, "the label step leaves code too"),
    ("basic internationalization-support", None, "basic is a word, not a scheme, outside a header"),
    ("a basic understanding_of_the_problem_space", None, "the same"),
    ("the Digest https://example.com/kiln/notes", None, "Digest outside a header: the address stays"),
    (f"{{'Authorization': 'Bearer {TOKEN}'}}", TOKEN, "the closing quote and brace stay"),
    (f"Authorization: Digest {DIGEST}", DIGEST, "the whole parameter list, the response hash too"),
    # The second review.
    ("password: <pasted_text>hunter22</pasted_text>", "hunter22", "a tag is not the value"),
    (f"api key: {GIT_HASH}", GIT_HASH, "a look-alike after a label is the value"),
    (f"password: {HEX64}", HEX64, "the same"),
    (f"API key: {UUID}", UUID, "the same"),
    ("export OPENAI_API_KEY=sk-" + "Ab9" * 12, "sk-" + "Ab9" * 12, "a key shape after export"),
    ("here: sk-" + "Ab9" * 12, "sk-" + "Ab9" * 12, "a key shape after a word"),
    ("sk-admin-" + "Ef7" * 12, "sk-admin-" + "Ef7" * 12, "an admin key"),
    ("sk-svcacct-" + "Cd8" * 12, "sk-svcacct-" + "Cd8" * 12, "a service account key"),
    ("db_password: hunter22", "hunter22", "a named assignment"),
    ('"client_secret": "hunter22"', "hunter22", "a quoted named assignment"),
    (f"authorization=Bearer {TOKEN}", TOKEN, "an equals sign"),
    (f"Authorization: Basic {BASIC}", BASIC, "basic after authorization"),
    (f"Basic {BASIC}", None, "Basic alone is no header: only Bearer alone is a scheme (item A5)"),
    (f"Authorization: {R} {TOKEN}", TOKEN, "a lane line masked under an earlier rule: the token after the mark"),
    (f"Authorization: {R} {BASIC}", BASIC, "the same with a shorter value"),
    # The first review and the label step.
    ("PIN: 4821", "4821", "a label and a colon"),
    ("Wren sent the door code 7395.", "7395", "the full stop stays"),
    ("(PIN: 4821), then left", "4821", "a bracket and a comma stay"),
    ('password: "my 4 dogs" for the shop', "my 4 dogs", "a quoted value is whole"),
    ("**PIN:** 4821", "4821", "a label in bold"),
    ("OTP=ab12cd;", "ab12cd", "a semicolon ends the value"),
    ("password: tulip", "tulip", "after a colon any value"),
    ("password tulip", None, "without a colon a word is prose"),
    ("PIN: -", None, "a dash is no value"),
    ("password: ...", None, "dots are no value"),
    ("the shed's door code: [4821]", "4821", "the brackets stay"),
    ("Ilse changed her password on 2051-03-02.", None, "a date"),
    ("verification code sent at 10:45", None, "a time"),
    ("a password of 12 characters at least", None, "a count"),
    ("see `04 stm/bakery/password-rules.md` and pinned notes", None, "a path"),
    ("pin pin 1234", "1234", "two labels"),
    ("the PIN code 4821", "4821", "a two-word label"),
    ("the gate code is 1001#", "1001#", "a symbol in the value"),
    # The table of what is a secret.
    ("key=hunter22", "hunter22", "after key: 8 characters with a digit"),
    ('"key": "hunter22"', "hunter22", "after key: quoted"),
    ("key: 12345678", "12345678", "after key: 8 digits"),
    ("key: 1234", None, "after key: a short number"),
    ("token: 123456", "123456", "after a token label: 6 digits"),
    ("token: 1024", None, "after a token label: 4 digits"),
    ("pin: 12", "12", "after pin any number"),
    ("otp 48213", "48213", "a code after otp"),
    ("passcode=7395", "7395", "a passcode"),
    ("auth-token: Ab3xYz9Qk", "Ab3xYz9Qk", "a name with a hyphen"),
    ('apiKey: "hunter22"', "hunter22", "a camel-case name"),
    ("DB_PASSWORD=hunter22", "hunter22", "an environment line"),
    ("client_secret=hunter22", "hunter22", "a secret name"),
    ("self.api_token = 'hunter22'", "hunter22", "an attribute"),
    ("connect(api_key='hunter22x')", "hunter22x", "a keyword argument"),
    ("monkey=banana", None, "monkey is not a key"),
    ('api_key = os.getenv("KILN_KEY")', None, "a call"),
    ("password=password", None, "the name passed on"),
    ("password: str", None, "a type"),
    ("API_TOKEN=${KILN_TOKEN}", None, "the environment"),
    ('"password": "${DB_PASS}"', None, "the environment, quoted"),
    ("secret = process.env.KILN_SECRET", None, "the environment in a script"),
    ("token: undefined", None, "a code word"),
    ("api_key = None", None, "a code word"),
    ("password: settings.db_password", None, "an attribute is code"),
    ("token: ya29.A0kilnGlaze0123456789", "ya29.A0kilnGlaze0123456789", "a dotted token: its head holds a digit"),
    ("access token ya29.A0kilnGlaze0123456789", "ya29.A0kilnGlaze0123456789", "the same beside an access word"),
    # Header forms.
    (f'"Authorization": "Basic {BASIC}"', BASIC, "a JSON header"),
    (f"authorization Bearer {TOKEN}", TOKEN, "white space only, a mixed token"),
    (f"Bearer {TOKEN}", TOKEN, "Bearer alone before a mixed token"),
    ("Authorization: Token Ab3xYz9Qk", "Ab3xYz9Qk", "another scheme word through the label"),
    ("the bearer of good news", None, "prose"),
    # Shapes.
    ("the kiln log held sk-ant-" + "Ab9" * 12 + " overnight", "sk-ant-" + "Ab9" * 12, "a model key"),
    ("ghp_" + "Gh6" * 12, "ghp_" + "Gh6" * 12, "a code host token"),
    ("AKIA" + "Z9Y8X7W6V5U4T3S2", "AKIA" + "Z9Y8X7W6V5U4T3S2", "a cloud key"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJraWxuIn0.Ab9Cd8Ef7Gh6Ij5Kl4",
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJraWxuIn0.Ab9Cd8Ef7Gh6Ij5Kl4", "a JWT"),
    ("api key " + "Ab9Cd8" * 8, "Ab9Cd8" * 8, "a long token beside an access word"),
    (PRIVATE_KEY, PRIVATE_KEY, "a private-key block"),
    ("the sk-test kiln", None, "a short word with a key's head"),
    (f"commit {GIT_HASH} at the kiln", None, "a git hash"),
    (f"commit {UUID} at the kiln", None, "a UUID"),
    ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ", None, "an image head"),
]
# Each part tag, with and without a colon.
for _tag in TAGS:
    ROWS.append((f"<{_tag}>pin 4821</{_tag}>", "4821", f"{_tag} without a colon"))
    ROWS.append((f"<{_tag}>PIN: 4821</{_tag}>", "4821", f"{_tag} with a colon"))
    ROWS.append((f'<{_tag} name="kiln.txt">password: hunter22</{_tag}>', "hunter22", f"{_tag} with an attribute"))

# A name of any length, a name joined by two hyphens, a command-line flag, and a value longer than MAX_VALUE.
ROWS += [
    ("Q" * 200 + "_API_KEY=Zq81xWv0", "Zq81xWv0", "a name of 200 characters"),
    ("kiln-key--api-key=Zq81xWv0", "Zq81xWv0", "a name joined by two hyphens"),
    ("glaze --api-key=Zq81xWv0 --fire", "Zq81xWv0", "a command-line flag"),
    ("glaze -key=Zq81xWv0 -fire", "Zq81xWv0", "a flag with one hyphen"),
    ("glaze\\n-api_token=Zq81xWv0", "Zq81xWv0", "a flag after a JSON escape"),
    ("api_token=" + "os.x(" * 300, "os.x(" * 300, "a value longer than MAX_VALUE, masked whole"),
]

NEIGHBOURS = ("The kiln cooled overnight.", "PIN: 9036", "TOKEN_LIMIT=2048")


def masked(text: str) -> str:
    return credentials.mask(text, config=DEFAULT_CONFIG, ledger=None)


def contexts(text: str) -> list[tuple[str, str]]:
    """Where a row can stand, each with how to build it from a text."""
    found = [
        ("alone", text),
        ("after other lines", "The kiln cooled overnight.\nThe shelf is clean.\n" + text),
        ("between two rows", f"{NEIGHBOURS[1]}\n{text}\n{NEIGHBOURS[2]}"),
        ("as a JSON string value", json.dumps({"text": text}, ensure_ascii=False)),
        ("as a tool argument", json.dumps({"input": {"command": text}}, ensure_ascii=False)),
    ]
    for tag in TAGS:
        found.append((f"inside {tag}", f"<{tag}>{text}</{tag}>"))
    return found


def build(where: str, text: str) -> str:
    return dict(contexts(text))[where]


class MaskingTableTests(unittest.TestCase):
    def test_property_1_the_secret_part_is_replaced_and_nothing_else(self) -> None:
        for text, secret, note in ROWS:
            with self.subTest(note=note, text=text[:40]):
                result = masked(text)
                if secret is None:
                    self.assertEqual(result, text)
                    continue
                self.assertIn(secret, text)
                self.assertNotIn(secret, result)
                self.assertEqual(result, text.replace(secret, R))

    def test_property_2_masking_twice_gives_what_masking_once_gives(self) -> None:
        for text, _secret, note in ROWS:
            for where, built in contexts(text):
                with self.subTest(note=note, where=where):
                    once = masked(built)
                    self.assertEqual(masked(once), once)
        joined = "\n".join(text for text, _secret, _note in ROWS)
        self.assertEqual(masked(masked(joined)), masked(joined))

    def test_property_3_a_row_masks_the_same_secret_wherever_it_stands(self) -> None:
        for text, secret, note in ROWS:
            for where, built in contexts(text):
                with self.subTest(note=note, where=where):
                    result = masked(built)
                    if secret is not None:
                        self.assertNotIn(secret, result)
                    # The row inside its context is masked as it is masked alone.
                    self.assertEqual(result, build(where, masked(text)) if where != "between two rows"
                                     else f"{masked(NEIGHBOURS[1])}\n{masked(text)}\n{NEIGHBOURS[2]}")

    def test_every_part_tag_is_intact_after_masking(self) -> None:
        for text, _secret, note in ROWS:
            for where, built in contexts(text):
                with self.subTest(note=note, where=where):
                    result = masked(built)
                    for tag in TAGS:
                        self.assertEqual(result.count(f"<{tag}"), built.count(f"<{tag}"))
                        self.assertEqual(result.count(f"</{tag}>"), built.count(f"</{tag}>"))

    def test_the_guards_never_change_what_a_step_gives(self) -> None:
        generator = random.Random(20510302)
        pieces = ["PIN", "pin code", "password", "door code", "OTP", "is", "was", ":", "=", " ", "  ", "\n", "4821",
                  "12", "tulip", "x9", ".", ",", '"', "'", "(", ")", "[", "]", "{", "}", "**", "2051-03-02", "10:45",
                  "characters", "reset", "/", "@", "a", R, "-", "#", "\t", "Ünïcødé", "KEY", "api_key", "_token",
                  "db_password", "TOKEN_LIMIT", "Authorization", "Bearer", "basic", "Digest", "realm=", TOKEN, BASIC,
                  "sk-" + "Ab9" * 8, "lambda", "os.environ", "${X}", "<pasted_text>", "</pasted_text>", "\\n", "\\\"",
                  "secret", "credential", "hunter22", ";", "=>", "::", "PRIVATE KEY"]
        for _ in range(4000):
            text = "".join(generator.choice(pieces) for _ in range(generator.randint(1, 16)))
            labelled = credentials.mask_labelled(text, config=DEFAULT_CONFIG)
            self.assertEqual(labelled, credentials.mask_labelled(text, config=DEFAULT_CONFIG, guarded=False), text)
            shapes = credentials.mask_shapes(text)
            self.assertEqual(shapes, credentials.mask_shapes(text, guarded=False), text)
            once = masked(text)
            self.assertEqual(masked(once), once, text)


# The assignment as a pattern: what ``_named_assignments`` finds, defined the way it was before the scan. Tried at
# each start, it reads a run of hyphenated words once for each hyphen in it; the scan reads it once.
NAMED_ASSIGNMENT = re.compile(
    r"""(?x)
    (?:(?<![A-Za-z0-9_.\\])|(?<=\\[nrt]))
    (?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)
    (?:\\?["'])?
    [ \t]*
    (?::(?![:=])|=>|=(?![=>]))
    [ \t]*
    """
)


class _ReadCount(str):
    """A text that counts the characters read from it, one for an index and the length for a slice."""

    reads = 0

    def __getitem__(self, key):
        found = str.__getitem__(self, key)
        self.reads += max(len(found), 1)
        return found


class _Counted:
    """A stand-in for ``_BARE_VALUE_RE`` that adds up the characters each read consumes."""

    def __init__(self, pattern, tally: list[int]) -> None:
        self._pattern = pattern
        self._tally = tally

    def match(self, text: str, start: int):
        found = self._pattern.match(text, start)
        self._tally[0] += found.end() - start
        return found


# A run with no stop in it and a label every few characters: before the bounds, each label read the run to its
# end again, so the work grew with the square of the length. Each run is also tried with a mark at its end, so
# every value in it is left (it holds the mark) and no masking moves past the run.
RUNS = ("token:os.x(", "api_token=os.x(", "Authorization: Bearer aB3", "Bearer aB3x", "key-", "--key", "\\npassword:a")


class WorkTests(unittest.TestCase):
    def test_an_assignment_is_found_where_the_pattern_finds_it(self) -> None:
        generator = random.Random(20510303)
        pieces = ["key", "KEY", "api_key", "db-key", "-", "--", ".", "_", "4821", "x9", ":", "::", "=", "==", "=>",
                  ":=", " ", "\t", '"', "'", "\\", "\\n", "\\t", "\n", "a", "Q"]
        texts = [text for text, _secret, _note in ROWS]
        texts += ["".join(generator.choice(pieces) for _ in range(generator.randint(1, 18))) for _ in range(6000)]
        for text in texts:
            expected = [(found.start(), found.group("name"), found.end()) for found in NAMED_ASSIGNMENT.finditer(text)]
            self.assertEqual(list(credentials._named_assignments(text)), expected, text)

    def test_an_assignment_search_reads_each_character_a_bounded_number_of_times(self) -> None:
        for unit in ("key-", "--key", "a.b-c_d", "\\nkey-", " -k", "key ="):
            text = _ReadCount(unit * (20000 // len(unit)) + "=Zq81xWv0")
            found = list(credentials._named_assignments(text))
            with self.subTest(unit=unit):
                self.assertTrue(found)
                self.assertLessEqual(text.reads, 4 * len(text), "a run of a name was read again for each start in it")

    def test_a_masking_reads_each_character_a_bounded_number_of_times(self) -> None:
        for unit, tail in [(unit, tail) for unit in RUNS for tail in ("", R)]:
            text = unit * (20000 // len(unit)) + tail
            scanned = [0]
            checked = [0]

            def counted(function, size):
                def wrapper(*args, **kwargs):
                    checked[0] += size(*args)
                    return function(*args, **kwargs)
                return wrapper

            with self.subTest(unit=unit, tail=tail), \
                    mock.patch.object(credentials, "_BARE_VALUE_RE", _Counted(credentials._BARE_VALUE_RE, scanned)), \
                    mock.patch.object(credentials, "_code_value", counted(credentials._code_value, lambda t, s, e: e - s)), \
                    mock.patch.object(credentials, "_mixed_token", counted(credentials._mixed_token, len)), \
                    mock.patch.object(credentials, "_assigned", counted(credentials._assigned, len)), \
                    mock.patch.object(credentials, "_bare", counted(credentials._bare, len)):
                masked(text)
                # Linear: a bounded number of reads of each character, whatever the length.
                self.assertLessEqual(scanned[0], 4 * len(text), "a value was read again for each label in it")
                self.assertLessEqual(checked[0], 4 * len(text), "a value was checked again for each label in it")


if __name__ == "__main__":
    unittest.main()
