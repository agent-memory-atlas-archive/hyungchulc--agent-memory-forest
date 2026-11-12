"""Values sent to sign in or to pay (contract, section 4, Sign-in values).

A memory that records conversations is handed passwords, PINs, one-time
codes and door codes in passing. Such a value is never starred like a number
(``numbers``): it is removed. The page keeps that a value was sent (who, to
whom, for which account) and ``[REDACTED]`` in the value's place.

A value is recognised by the word before it, one of ``credentials.label_words``
(English by default; a forest adds the words of its other languages):

- after a colon or an equals sign (``PIN: 4821``, ``password = tulip``), any
  value that holds a letter or a digit (``PIN: -`` says there is none);
- without one (``door code 4821``, ``the password is tulip7``), a run of 3 to 64
  characters that holds a digit and is no date, time, path, address or mail
  address, and is not followed by one of ``credentials.count_words``
  (``a password of 12 characters``): a word alone after a label is prose.

A value with no label word (a bare code in a quoted turn) is masked once a
reader has judged it, through the forest's ledger of keyed digests
(``credential_ledger``).

A value is also recognised by its shape, before any label is applied, in any
string: the published key prefixes below, an ``Authorization`` header
(``Basic``, ``Bearer`` or ``Digest`` after ``authorization``; ``Bearer`` alone
before a mixed token), a private-key block, a JWT, and a long token beside a
word that names access (``token``, ``api key``, ``secret``, ``bearer``,
``authorization``). A label whose value is only the scheme word is not the
value: the token after it is. A git hash, a UUID and the head of a base64
image are not those shapes and are left as written, unless a label stands
before them: after a label the value is masked whatever its shape. An
assignment whose name ends in a secret word (``OPENAI_API_KEY=…``,
``db_password: …``, ``"client_secret": "…"``) is a labelled value; the table
of what counts, and of the values that read as code and stay, is above
``secret_word``.

Where a value ends is one function, ``value_at``, which every rule uses: at
white space, at a tag, before a closing quote, bracket, comma or semicolon,
or at the partner of the quote it opens with. ``[REDACTED]`` is one token
that no rule looks into, and no rule depends on where a line stands in the
text.

Each step runs only when the text holds what its pattern cannot match
without (a guard that is a necessary condition of the pattern, so a guarded
step gives what the unguarded one gives). ``mask`` is idempotent: a masked
text is a fixed point. What the rule cannot see (a code with no label word,
a value split over lines, a label word the settings do not list) is said in
``docs/privacy-and-trust.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Final

from . import archive as forest_archive
from . import layout
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import LockToken, atomic_write, writer_lock
from .safety import require_real_root
from .view import read_text

REDACTED: Final[str] = "[REDACTED]"
# The version of what ``shown`` does to a text (this module's rules and ``numbers.star_numbers``). Raise it with
# any change that can make a read show less than before: text kept masked under the old rules (the search
# index) is then masked again.
MASKING_VERSION: Final[str] = "4"
FIX_REASON: Final[str] = "credential-masking"
MIN_BARE: Final[int] = 3
MAX_BARE: Final[int] = 64
MAX_QUOTED: Final[int] = 128
_ASSIGN_MARKS: Final[str] = ":=："
_OPENERS: Final[str] = "\"'`([{<“‘«"
_CLOSERS: Final[str] = "\"'`)]}>”’».,;:!?"
_QUOTE_PAIRS: Final[dict[str, str]] = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’", "«": "»"}
_DATE_RE: Final = re.compile(r"\d{1,4}[-./]\d{1,2}[-./]\d{1,4}")
_TIME_RE: Final = re.compile(r"\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:am|pm)?", re.IGNORECASE)
_MAIL_RE: Final = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _phrase(word: str) -> str:
    """A label word as a pattern: its parts joined by any run of spaces, tabs or hyphens (never a line break:
    nothing the rules find crosses a line, so a page and its lines are masked alike)."""
    parts = [re.escape(part) for part in re.split(r"[\s\-]+", word.strip()) if part]
    return r"[ \t\-]*".join(parts)


@dataclass(frozen=True, slots=True)
class _Rules:
    plain: re.Pattern[str] | None  # the label words with no edges: the guard
    label: re.Pattern[str] | None  # the label words as whole words
    assign: re.Pattern[str]  # after a label: the joining word, a colon or an equals sign
    bare: re.Pattern[str]  # after a label: the joining word, spaces
    count: re.Pattern[str] | None  # a count word right after a value


@lru_cache(maxsize=32)
def _rules(labels: tuple[str, ...], joining: tuple[str, ...], counts: tuple[str, ...]) -> _Rules:
    forms = sorted({_phrase(word) for word in labels if word.strip()}, key=len, reverse=True)
    plain = re.compile("|".join(forms), re.IGNORECASE) if forms else None
    label = re.compile(r"(?:(?<!\w)|(?<=\\[nrt]))(?:" + "|".join(forms) + r")(?!\w)", re.IGNORECASE) \
        if forms else None
    joins = sorted({_phrase(word) for word in joining if word.strip()}, key=len, reverse=True)
    join = r"(?:[ \t]+(?:" + "|".join(joins) + r")(?!\w))?" if joins else ""
    # A label set in bold or italics keeps its marks: `**PIN:** 4821`, `**PIN**: 4821`.
    # The value starts where these end; ``value_at`` says where it ends.
    assign = re.compile(r"[*_]{0,3}" + join + r"[ \t]*[:=：](?![:=])[*_]{0,3}[ \t]*", re.IGNORECASE)
    bare = re.compile(r"[*_]{0,3}" + join + r"[ \t]{1,3}", re.IGNORECASE)
    words = sorted({_phrase(word) for word in counts if word.strip()}, key=len, reverse=True)
    count = re.compile(r"[ \t]+(?:" + "|".join(words) + r")(?!\w)", re.IGNORECASE) if words else None
    return _Rules(plain, label, assign, bare, count)


def rules_for(config: Config) -> _Rules:
    settings = config.credentials
    return _rules(settings.label_words, settings.joining_words, settings.count_words)


_VALUE_STOPS: Final = frozenset("\"')]},;<")
# A word starts after a character that is no part of a word, or after ``\n``, ``\r`` or ``\t`` written as a
# JSON escape, so a line inside a JSON string is read as the same line on its own.
_START: Final = r"(?:(?<![A-Za-z0-9_])|(?<=\\[nrt]))"


def value_at(text: str, start: int) -> tuple[int, int, int]:
    """Where the value that starts at ``start`` lies: ``(core start, core end, end)``. Every rule uses it.

    A value ends at white space, at ``<`` (a tag: a value never takes part of one), and before any of
    ``" ' ) ] } , ;``. A value that opens with a quote (also an escaped ``\\"`` inside a JSON string) ends at
    that quote's partner on the same line, and its core is what the quotes hold. The core of a bare value
    leaves out brackets and quotes at its head and punctuation at its tail. The mark ``[REDACTED]`` is one
    token: a value that starts with it is the mark, whole.
    """
    size = len(text)
    if start >= size:
        return start, start, start
    if text.startswith(REDACTED, start):
        end = start + len(REDACTED)
        return start, end, end
    width = 0
    if text[start] in _QUOTE_PAIRS:
        width = 1
    elif text[start] == "\\" and text[start + 1:start + 2] in ('"', "'"):
        width = 2
    if width:
        opener = text[start + width - 1]
        closing = ("\\" if width == 2 else "") + _QUOTE_PAIRS[opener]
        body = start + width
        line_end = text.find("\n", body)
        line_end = size if line_end < 0 else line_end
        close = text.find(closing, body, min(line_end, body + MAX_QUOTED + len(closing)))
        if close > body:
            return body, close, close + len(closing)
        start = body  # no partner: the rest is read as a bare value
    end = start
    while end < size:
        character = text[end]
        if character.isspace() or character in _VALUE_STOPS:
            break
        if character == "\\" and text[end + 1:end + 2] in ('"', "'", "n", "r", "t"):
            break
        end += 1
    core_start, core_end = start, end
    while core_start < core_end and text[core_start] in _OPENERS:
        core_start += 1
    while core_end > core_start and text[core_end - 1] in _CLOSERS:
        core_end -= 1
    return core_start, core_end, end


def _opened_by_quote(text: str, start: int) -> bool:
    """The value at ``start`` is a quoted literal (``value_at`` gives its core after the opening quote)."""
    return start > 0 and text[start - 1] in _QUOTE_PAIRS


def _not_a_value(core: str) -> bool:
    if "REDACTED" in core:
        return True
    if "/" in core or "\\" in core or _MAIL_RE.fullmatch(core):
        return True
    return bool(_DATE_RE.fullmatch(core) or _TIME_RE.fullmatch(core))


def _counted(rules: _Rules, text: str, end: int) -> bool:
    return rules.count is not None and rules.count.match(text, end) is not None


# ─────────────────────────── what is a secret (docs/contract.md, section 4, Sign-in values) ───────────────────────────
#
# One table, which the code and the documents share:
#
# | case | rule |
# | --- | --- |
# | the name | the secret word is the last part of the name (``API_KEY``, ``db_password``, ``auth-token``, ``apiKey``, ``"key"``) |
# | a name that holds the word elsewhere | not a secret name (``TOKEN_LIMIT``, ``PASSWORD_MIN_LENGTH``, ``key_count``, ``secret_santa``) |
# | a value that reads as code | not masked, after a name or a label: an identifier followed by ``(``, ``[`` or ``.`` and a name; the words ``lambda``, ``None``, ``null``, ``true``, ``false``, ``undefined``; a type word (``str``, ``int``, ``string`` ...); a reference to the environment (``os.environ[...]``, ``process.env.X``, ``$X``, ``${X}``, ``%X%``); the name itself (``password=password``) |
# | after a name that ends in ``key`` | masked only as a literal secret: quoted, or 8 characters or more with a digit, a mix of cases or a symbol |
# | a bare number | after the names and labels whose values are numbers (password, pin, otp, passcode, a code) always; after ``token``, ``key``, ``secret``, ``credential``, ``bearer`` and ``authorization`` only with 6 digits or more |
# | a scheme word | ``basic``, ``digest`` and ``bearer`` are a scheme after ``authorization`` (``:``, ``=``, quotes or white space between); ``Bearer`` alone is one before a mixed token of 16 characters or more |
# | ``Authorization: Digest`` | the whole parameter list to the end of the line (a quoted parameter is skipped whole) |

_SECRET_WORDS: Final = frozenset({"key", "token", "secret", "password", "passwd", "passphrase", "credential"})
# A last part that joins a word to the secret word without a separator.
_JOINED_SECRET_PARTS: Final = frozenset({"apikey", "authtoken", "accesstoken", "secretkey", "privatekey",
                                          "accesskey", "refreshtoken", "clientsecret"})
# Names and labels whose values are tokens: a bare number after them is a value only with 6 digits or more.
_TOKEN_WORDS: Final = frozenset({"key", "token", "secret", "credential", "bearer", "authorization", "apikey",
                                 "authtoken", "accesstoken", "secretkey", "privatekey", "accesskey",
                                 "refreshtoken", "clientsecret"})
_TOKEN_NUMBER_DIGITS: Final = 6
_KEY_LITERAL_LENGTH: Final = 8
_CODE_WORDS: Final = frozenset({"lambda", "none", "null", "true", "false", "undefined", "nil"})
_TYPE_WORDS: Final = frozenset({"str", "bytes", "int", "bool", "float", "string", "number", "boolean", "any",
                                "object", "secretstr"})
_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENVIRONMENT_RE: Final = re.compile(r"\$\{?[A-Za-z_]|%[A-Za-z_][A-Za-z0-9_]*%")
_NAME_PART_RE: Final = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")
# A necessary condition of a secret name: the named rule looks no further when the text holds none.
_SECRET_WORD_RE: Final = re.compile(r"(?i)key|token|secret|passw|passphrase|credential")


def _name_parts(name: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"[^A-Za-z0-9]+", name):
        parts.extend(found.group(0).casefold() for found in _NAME_PART_RE.finditer(piece))
    return parts


def secret_word(name: str) -> str | None:
    """The secret word that is the last part of ``name``, or None: ``db_password`` is one, ``TOKEN_LIMIT`` not."""
    parts = _name_parts(name)
    if not parts:
        return None
    last = parts[-1]
    if last in _SECRET_WORDS or last in _JOINED_SECRET_PARTS:
        return last
    if last == "credentials":
        return "credential"
    return None


def _reads_as_code(text: str, start: int, end: int, *, name: str = "") -> bool:
    """The value at ``start``..``end`` reads as code, not as a secret (the table above)."""
    core = text[start:end]
    if not core:
        return False
    folded = core.casefold()
    if folded in _CODE_WORDS or folded in _TYPE_WORDS:
        return True
    if _ENVIRONMENT_RE.match(core):
        return True
    if name and folded == name.casefold().strip("\"'"):
        return True
    found = _IDENTIFIER_RE.match(text, start)
    if found is not None:
        after = text[found.end():found.end() + 2]
        if after[:1] in ("(", "["):
            return True
        # A dotted name reads as code when its head holds no digit: ``os.environ``, ``settings.db_password``,
        # not ``ya29.A0Ar…``, the head of a token.
        if after[:1] == "." and after[1:2] and (after[1].isalpha() or after[1] == "_") \
                and not any(character.isdigit() for character in found.group(0)):
            return True
    return False


def _code_value(text: str, start: int, end: int, *, name: str = "") -> bool:
    """A value that is code is not masked. A quoted value is a literal, unless it names the environment."""
    if _ENVIRONMENT_RE.match(text, start, end):
        return True
    return not _opened_by_quote(text, start) and _reads_as_code(text, start, end, name=name)


def _number_ok(core: str, word: str) -> bool:
    """A bare number after ``word``: always a value after a number word, with 6 digits after a token word."""
    if not core.isdigit():
        return True
    return word not in _TOKEN_WORDS or len(core) >= _TOKEN_NUMBER_DIGITS


def _key_literal(core: str, quoted: bool) -> bool:
    """After a name that ends in ``key``: a literal secret, not ``id`` or ``user_id``."""
    if quoted:
        return True
    if len(core) < _KEY_LITERAL_LENGTH:
        return False
    digit = any(character.isdigit() for character in core)
    mixed = any(character.islower() for character in core) and any(character.isupper() for character in core)
    symbol = any(not character.isalnum() for character in core)
    return digit or mixed or symbol


def _label_word(label: str) -> str:
    """The last word of a label phrase, for the number rule (``api key`` → ``key``)."""
    words = re.split(r"[\s\-]+", label.strip().casefold())
    return words[-1] if words else ""


# A tag that marks a pasted, attached or quoted span is not a value. The words are the part marks
# the intake record uses. The tag is skipped and the text after it is the value.
_PART_TAG_RE: Final = re.compile(
    r"</?(?:pasted_contents|pasted_text|attached_files|file_contents|ide_opened_file|quoted_message"
    r"|quoted_selection|ide_selection)\b[^>]*>",
    re.IGNORECASE,
)
_NAMED_ASSIGN_RE: Final = re.compile(
    r"""(?x)
    (?:(?<![A-Za-z0-9_.\\])|(?<=\\[nrt]))
    (?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)
    (?:\\?["'])?
    [ \t]*
    (?::(?![:=])|=>|=(?![=>]))
    [ \t]*
    """
)


def _skip_tags(text: str, pos: int) -> int:
    while pos < len(text):
        if text[pos] in " \t":
            pos += 1
            continue
        tag = _PART_TAG_RE.match(text, pos)
        if tag is None:
            break
        pos = tag.end()
    return pos


def _value_after(text: str, pos: int) -> tuple[int, int, int]:
    """The value at ``pos``, past a part tag that opens there."""
    if text[pos:pos + 1] == "<":
        skipped = _skip_tags(text, pos)
        if skipped != pos:
            pos = skipped
    return value_at(text, pos)


def _scheme_value(text: str, start: int, end: int, raw_end: int) -> tuple[int, int, int]:
    """A value that is only a scheme word is not the value. The token after it is."""
    if text[start:end].casefold() not in _SCHEME_WORDS:
        return start, end, raw_end
    pos = _skip_tags(text, raw_end)
    if pos >= len(text) or text[pos].isspace() or pos == raw_end:
        return start, end, raw_end
    return value_at(text, pos)


def _step(rules: _Rules, text: str, after: re.Pattern[str], accept: Callable[[str], bool]) -> str:
    """Each value that ``after`` finds right after a label word and ``accept`` takes, replaced by REDACTED."""
    assert rules.label is not None
    pieces: list[str] = []
    cursor = 0
    for found in rules.label.finditer(text):
        if found.start() < cursor:
            continue
        match = after.match(text, found.end())
        if match is None:
            continue
        value_start = match.end()
        if value_start >= len(text) or text[value_start].isspace():
            continue
        start, end, raw_end = _value_after(text, value_start)
        if after is rules.assign:
            start, end, raw_end = _scheme_value(text, start, end, raw_end)
        core = text[start:end]
        if not core or "REDACTED" in text[max(0, start - 1):raw_end + 1] or not accept(core):
            continue
        if _counted(rules, text, raw_end):
            continue
        if not _number_ok(core, _label_word(found.group(0))) or _code_value(text, start, end, name=found.group(0)):
            continue
        pieces += [text[cursor:start], REDACTED]
        cursor = end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def _mask_named(text: str, *, guarded: bool = True) -> str:
    """An assignment whose name ends in a secret word (the table above). The value is masked."""
    if guarded and _SECRET_WORD_RE.search(text) is None:
        return text
    pieces: list[str] = []
    cursor = 0
    for found in _NAMED_ASSIGN_RE.finditer(text):
        if found.start() < cursor:
            continue
        word = secret_word(found.group("name"))
        if word is None:
            continue
        value_start = found.end()
        if value_start >= len(text) or text[value_start].isspace():
            continue
        start, end, raw_end = _value_after(text, value_start)
        core = text[start:end]
        if not core or "REDACTED" in text[max(0, start - 1):raw_end + 1]:
            continue
        if not any(character.isalnum() for character in core):
            continue
        quoted = _opened_by_quote(text, start)
        if _code_value(text, start, end, name=found.group("name")):
            continue
        if not _number_ok(core, word):
            continue
        if word in {"key", "apikey", "secretkey", "privatekey", "accesskey"} and not _key_literal(core, quoted):
            continue
        pieces += [text[cursor:start], REDACTED]
        cursor = end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


# A git hash (40 or 64 hex digits, with a letter so a long numeric code still counts), a UUID, and the
# head of a base64 image. These are not sign-in values. A password that is exactly one of them is
# indistinguishable from it, so the automatic rules leave it; a reader can still judge it.
_HEX_HASH_RE: Final = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_UUID_RE: Final = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_IMAGE_HEADS: Final = ("iVBORw0KGgo", "/9j/", "R0lGODlh", "UklGR")
# Published key prefixes. Each tail has a floor so a short word with the same head is left alone.
# Model providers: OpenAI ``sk-``, ``sk-proj-``, ``sk-svcacct-``, ``sk-admin-`` (platform.openai.com admin
# and project keys), Anthropic ``sk-ant-``, OpenRouter ``sk-or-``, Groq ``gsk_``, xAI ``xai-``,
# Perplexity ``pplx-``, Fireworks ``fw_``, Hugging Face ``hf_``, Google ``AIza``.
# Code hosts: GitHub ``ghp_`` ``gho_`` ``ghu_`` ``ghs_`` ``ghr_`` ``github_pat_``, GitLab ``glpat-``
# ``gldt-`` ``glrt-``.
# Cloud consoles: AWS ``AKIA`` ``ASIA``, Google ``AIza`` and ``GOCSPX-``, DigitalOcean ``dop_v1_``.
# Payment and chat: Stripe ``sk_live_`` ``sk_test_`` ``rk_live_`` ``rk_test_`` ``whsec_``, Slack
# ``xoxb-`` ``xoxp-`` ``xoxa-`` ``xoxr-`` ``xoxs-``, Square ``sq0atp-`` ``sq0csp-``, SendGrid ``SG.``.
# Package registries: npm ``npm_``, PyPI ``pypi-``.
# A JWT is three base64url segments, the first starting ``eyJ``.
_KEY_RE: Final = re.compile(
    r"(?:(?<![A-Za-z0-9])|(?<=\\[nrt]))(?:"
    r"sk-svcacct-[A-Za-z0-9_\-]{16,}"
    r"|sk-admin-[A-Za-z0-9_\-]{16,}"
    r"|sk-proj-[A-Za-z0-9_\-]{16,}"
    r"|sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|sk-or-[A-Za-z0-9_\-]{16,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{16,}"
    r"|gh[pousr]_[A-Za-z0-9_]{16,}"
    r"|glpat-[A-Za-z0-9_\-]{16,}"
    r"|gldt-[A-Za-z0-9_\-]{16,}"
    r"|glrt-[A-Za-z0-9_\-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{16,}"
    r"|sk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|rk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|whsec_[A-Za-z0-9]{16,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|ASIA[A-Z0-9]{16}"
    r"|AIza[0-9A-Za-z_\-]{20,}"
    r"|GOCSPX-[A-Za-z0-9_\-]{16,}"
    r"|SG\.[A-Za-z0-9_\-]{16,}(?:\.[A-Za-z0-9_\-]{16,})?"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|gsk_[A-Za-z0-9]{20,}"
    r"|xai-[A-Za-z0-9_\-]{16,}"
    r"|pplx-[A-Za-z0-9_\-]{16,}"
    r"|fw_[A-Za-z0-9_\-]{16,}"
    r"|npm_[A-Za-z0-9]{16,}"
    r"|pypi-[A-Za-z0-9_\-]{16,}"
    r"|dop_v1_[A-Za-z0-9]{16,}"
    r"|shpat_[A-Za-z0-9]{16,}"
    r"|sq0(?:atp|csp)-[A-Za-z0-9_\-]{16,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r")(?![A-Za-z0-9])"
)
_SCHEME_WORDS: Final = frozenset({"bearer", "basic", "token", "digest"})
_AUTH_VALUE_FLOOR: Final = 16
# ``basic``, ``bearer`` and ``digest`` are a scheme only where a header can stand: after ``authorization``,
# with ``:``, ``=``, quotes or white space between. ``Bearer`` alone is one before a mixed token.
_AUTH_HEADER_RE: Final = re.compile(
    r"(?i)" + _START + r"authorization(?:\\?[\"'])?[ \t]*(?P<sep>[:=]?)[ \t]*(?P<quote>\\?[\"'])?"
    r"(?P<scheme>basic|bearer|digest)(?![A-Za-z0-9_\-])[ \t]+"
)
# A header masked under an earlier rule kept its token: ``Authorization: [REDACTED] <token>``. The mixed token
# after that mark is the value. This step runs last, after the label step, so a mark the label step wrote is
# never a reason for a second pass to mask more.
_OLD_HEADER_RE: Final = re.compile(
    r"(?i)" + _START + r"authorization(?:\\?[\"'])?[ \t]*[:=][ \t]*(?:\\?[\"'])?\[REDACTED\][ \t]+"
)
_BEARER_RE: Final = re.compile(r"(?i)" + _START + r"bearer[ \t]+")
_AUTH_GUARD_RE: Final = re.compile(r"(?i)authorization|bearer")
_PRIVATE_KEY_RE: Final = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_ACCESS_WORD_RE: Final = re.compile(
    r"(?i)(?:(?<!\w)|(?<=\\[nrt]))(?:api[ \t\-]*key|access[ \t\-]*key|secret|token|bearer|authorization"
    r"|credential)s?(?!\w)"
)
_LONG_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9+/_.=-]{20,}")


def _lookalike(core: str) -> bool:
    """A git hash, a UUID, or the head of a base64 image. Not a sign-in value."""
    if _UUID_RE.fullmatch(core):
        return True
    if _HEX_HASH_RE.fullmatch(core) and re.search(r"[a-fA-F]", core):
        return True
    if core.startswith(_IMAGE_HEADS) or core.startswith("data:image/") or "base64,iVBORw0KGgo" in core:
        return True
    return False


def _assigned(core: str) -> bool:
    """After a label, a look-alike is still the value. A look-alike is exempt only with no label before it."""
    return "REDACTED" not in core and any(character.isalnum() for character in core)


def _bare(core: str) -> bool:
    return MIN_BARE <= len(core) <= MAX_BARE and any(character.isdigit() for character in core) \
        and not _not_a_value(core)


def _mixed_token(token: str, *, labelled: bool = False) -> bool:
    """A long token that is not a word: a digit, or both cases.

    A look-alike is not one, unless a label stands before it: then it is the value.
    """
    if "REDACTED" in token:
        return False
    if not labelled and _lookalike(token):
        return False
    lower = any(character.islower() for character in token)
    upper = any(character.isupper() for character in token)
    digit = any(character.isdigit() for character in token)
    return digit or (lower and upper)


def _digest_end(text: str, pos: int) -> int:
    """The end of a Digest parameter list: the end of the line, a tag, or a quote that opens no parameter's
    value. A quoted parameter value is skipped whole; its fields are not read."""
    size = len(text)
    index = pos
    while index < size:
        character = text[index]
        if character in "\n<":
            break
        if character == "=":
            for opener in ('\\"', '"'):
                if text.startswith(opener, index + 1):
                    close = text.find(opener, index + 1 + len(opener))
                    line_end = text.find("\n", index)
                    if close >= 0 and (line_end < 0 or close < line_end):
                        index = close + len(opener)
                        break
            else:
                index += 1
                continue
            continue
        if character in "\"'" or (character == "\\" and text[index + 1:index + 2] in ('"', "'")):
            break
        index += 1
    while index > pos and text[index - 1] in " \t,":
        index -= 1
    return index


def _mask_auth(text: str) -> str:
    """An ``Authorization`` header's value, and a mixed token after ``Bearer`` alone."""
    pieces: list[str] = []
    cursor = 0
    for found in _AUTH_HEADER_RE.finditer(text):
        if found.start() < cursor:
            continue
        pos = _skip_tags(text, found.end())
        separated = bool(found.group("sep") or found.group("quote"))
        if found.group("scheme").casefold() == "digest" and separated:
            end = _digest_end(text, pos)
            body = text[pos:end]
            if not body.strip() or body == REDACTED:
                continue
            pieces += [text[cursor:pos], REDACTED]
            cursor = end
            continue
        start, end, raw_end = value_at(text, pos)
        core = text[start:end]
        if not core or "REDACTED" in text[pos:raw_end]:
            continue
        if separated:
            if not any(character.isalnum() for character in core):
                continue
        elif len(core) < _AUTH_VALUE_FLOOR or not _mixed_token(core):
            continue
        pieces += [text[cursor:start], REDACTED]
        cursor = end
    if pieces:
        pieces.append(text[cursor:])
        text = "".join(pieces)
    pieces = []
    cursor = 0
    for found in _BEARER_RE.finditer(text):
        if found.start() < cursor:
            continue
        start, end, raw_end = value_at(text, found.end())
        core = text[start:end]
        if len(core) < _AUTH_VALUE_FLOOR or "REDACTED" in text[found.end():raw_end] or not _mixed_token(core):
            continue
        pieces += [text[cursor:start], REDACTED]
        cursor = end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def _mask_after_old_mark(text: str) -> str:
    if "REDACTED" not in text:
        return text
    pieces: list[str] = []
    cursor = 0
    for found in _OLD_HEADER_RE.finditer(text):
        if found.start() < cursor:
            continue
        start, end, raw_end = value_at(text, found.end())
        core = text[start:end]
        if len(core) < _AUTH_VALUE_FLOOR or "REDACTED" in text[found.end():raw_end] or not _mixed_token(core):
            continue
        pieces += [text[cursor:start], REDACTED]
        cursor = end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def mask_shapes(text: str, *, guarded: bool = True) -> str:
    """Sign-in values recognised by shape, not by a label word, and the named assignments.

    The private-key block is the one pattern that crosses lines, because that is
    how the block is written. Every other pattern stays on one line. ``guarded=False`` runs each step
    whatever the text holds; it exists to prove the guards change nothing.
    """
    if not guarded or "PRIVATE KEY" in text:
        text = _PRIVATE_KEY_RE.sub(REDACTED, text)
        # A block torn across a call still loses the line that names it.
        lines = []
        for line in text.split("\n"):
            if "PRIVATE KEY-----" in line:
                line = _PRIVATE_KEY_RE.sub(REDACTED, line) if "-----" in line else REDACTED
                if "PRIVATE KEY-----" in line:
                    line = re.sub(r"-*BEGIN [A-Z0-9 ]*PRIVATE KEY-*", REDACTED, line)
                    line = re.sub(r"-*END [A-Z0-9 ]*PRIVATE KEY-*", REDACTED, line)
            lines.append(line)
        text = "\n".join(lines)
    if not guarded or _KEY_RE.search(text):
        text = _KEY_RE.sub(REDACTED, text)
    if not guarded or _AUTH_GUARD_RE.search(text):
        text = _mask_auth(text)
    text = _mask_named(text, guarded=guarded)
    if not guarded or _ACCESS_WORD_RE.search(text):
        text = _mask_tokens_near_access(text)
    return text


def _beside(gap: str, *, word_first: bool) -> bool:
    """The access word nearest the token shares its line, with only separators between them."""
    line = gap.rsplit("\n", 1)[-1] if word_first else gap.split("\n", 1)[0]
    matches = list(_ACCESS_WORD_RE.finditer(line))
    if not matches:
        return False
    found = matches[-1] if word_first else matches[0]
    between = line[found.end():] if word_first else line[:found.start()]
    return re.fullmatch(r"[ \t:=\"'`.\-]*", between) is not None


def _mask_tokens_near_access(text: str) -> str:
    """A long mixed token on the same line as a word that names access, separators only between them."""
    pieces: list[str] = []
    cursor = 0
    for token in _LONG_TOKEN_RE.finditer(text):
        if token.start() < cursor or REDACTED in token.group(0):
            continue
        before = text[max(0, token.start() - 48):token.start()]
        after = text[token.end():token.end() + 48]
        labelled = _beside(before, word_first=True) or _beside(after, word_first=False)
        if not labelled or not _mixed_token(token.group(0), labelled=True):
            continue
        if _code_value(text, token.start(), token.end()):
            continue
        pieces.append(text[cursor:token.start()])
        pieces.append(REDACTED)
        cursor = token.end()
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def mask_labelled(text: str, *, config: Config, guarded: bool = True) -> str:
    """``text`` with every value after a label word replaced by ``[REDACTED]`` (the two steps above, in turn).

    ``guarded=False`` runs each step whatever the text holds; it exists to prove the guards change nothing.
    """
    rules = rules_for(config)
    if rules.plain is None or rules.label is None:
        return text
    holds_label = (not guarded) or rules.plain.search(text) is not None
    if not holds_label:
        return text
    if not guarded or any(mark in text for mark in _ASSIGN_MARKS):
        text = _step(rules, text, rules.assign, _assigned)
    if not guarded or any(character.isdigit() for character in text):
        text = _step(rules, text, rules.bare, _bare)
    return text


_LOOKUP: Final[Any] = object()  # ``ledger`` not given: find the forest's ledger from ``config``


def mask(text: str, *, config: Config, ledger: Any = _LOOKUP) -> str:
    """Every sign-in value ``text`` holds replaced by ``[REDACTED]``: after a label word, and, in a forest
    that keeps a judged ledger (``credential_ledger``), every value a reader judged a sign-in value.

    A caller masking many texts passes the ledger it loaded once (or None for none); without it the ledger is
    looked up for each call."""
    from . import credential_ledger

    # A shape is masked first, whatever stands before it. The label step runs after, so a scheme
    # word is not taken for the token that follows it.
    text = _mask_after_old_mark(mask_labelled(mask_shapes(text), config=config))
    known = credential_ledger.for_config(config) if ledger is _LOOKUP else ledger
    return credential_ledger.mask_known(text, known)


def shown(value: Any, *, config: Config, ledger: Any = _LOOKUP) -> Any:
    """What a tool shows (a text, or the strings of a JSON-like value) with its sign-in values masked and its
    numbers starred: the read path hides what the write path keeps out."""
    from . import credential_ledger, numbers

    if ledger is _LOOKUP:
        ledger = credential_ledger.for_config(config)
    if isinstance(value, str):
        return numbers.star_numbers(mask(value, config=config, ledger=ledger), config=config)[0]
    if isinstance(value, dict):
        return {key: shown(item, config=config, ledger=ledger) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [shown(item, config=config, ledger=ledger) for item in value]
    return value


def fingerprint(config: Config, ledger: Any = _LOOKUP) -> str:
    """One digest of everything ``shown`` reads: ``MASKING_VERSION``, the number and sign-in settings, and the
    set of sign-in digests of the ledger. A text masked under one fingerprint may show more under another, so
    whatever keeps masked text (the search index) is rebuilt when it changes. The digest names no value."""
    from . import credential_ledger

    if ledger is _LOOKUP:
        ledger = credential_ledger.for_config(config)
    signed = sorted(ledger.sign_in) if ledger is not None else []
    material = {
        "credentials": asdict(config.credentials),
        "ledger": hashlib.sha256("\n".join(signed).encode("ascii")).hexdigest() if signed else "",
        "numbers": asdict(config.numbers),
        "version": MASKING_VERSION,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def name_problem(names: Iterable[str], *, config: Config) -> str | None:
    """Why one of ``names`` cannot stand as a name (contract, section 2), or None.

    A name says who or what a subject is. One built from the words of what was sent before digits
    (``Courier verification code 4821``) reads as a sign-in value: every line that carries it is a
    ``credential_in_full`` line, every read shows it masked, and ``fix`` would take the name off the subject's
    own pages. A name that already holds the mark has lost that part. The reason shows the masked form, never
    the name.
    """
    for name in names:
        text = str(name or "")
        if REDACTED in text:
            return (f"the name `{text}` holds a masked value, which names nothing; "
                    "name it by who sends or what it is")
        masked = mask(text, config=config) if text else text
        if masked != text:
            return (f"the name `{masked}` reads as a sign-in value, which every read masks; "
                    "name it by who sends or what it is, not by the words of what was sent")
    return None


def changed_lines(text: str, *, config: Config) -> list[int]:
    """The 1-based numbers of the lines ``mask`` would change (never the values themselves)."""
    if mask(text, config=config) == text:
        return []
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        if mask(line, config=config) != line:
            found.append(number)
    return found


# ─────────────────────────────── pages ───────────────────────────────


@dataclass(slots=True)
class PageCredentials:
    path: str
    lines: list[int]
    written: bool = False
    archive_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"archive": self.archive_path, "lines": self.lines, "path": self.path, "written": self.written}


def _chosen(root: Any, paths: Iterable[str], all_pages: bool) -> list[str]:
    chosen = [layout.normalize(path) for path in paths]
    for path in chosen:
        if not layout.classify(path).in_forest:
            raise MemoryForestError("credentials_path_invalid", "Only pages of the four forest layers are masked.",
                                    details={"path": path})
    if all_pages or not chosen:
        chosen += [path for path in layout.iter_pages(root) if path not in chosen]
    return chosen


def check(
    root: str | os.PathLike[str], paths: Iterable[str] = (), *, all_pages: bool = False, config: Config | None = None
) -> list[PageCredentials]:
    """The pages that hold a sign-in value (every page when none is named): their line numbers only."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    found = []
    for path in _chosen(safe_root, paths, all_pages):
        lines = changed_lines(read_text(layout.resolve(safe_root, path, must_exist=True)), config=settings)
        if lines:
            found.append(PageCredentials(path, lines))
    return found


def fix(
    root: str | os.PathLike[str],
    paths: Iterable[str] = (),
    *,
    all_pages: bool = False,
    config: Config | None = None,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
) -> list[PageCredentials]:
    """Mask the sign-in values of the named pages (every page when none is named).

    Each page is archived first (a kept copy with the reason ``credential-masking``); the copy must read back
    to the page's bytes before the masked page is written whole. No record line is written: a record line
    would have to say what was removed.
    """
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    found = check(safe_root, paths, all_pages=all_pages, config=settings)
    if dry_run or not found:
        return found
    with writer_lock(safe_root, token=lock, label="credentials fix") as held:
        for item in found:
            target = layout.resolve(safe_root, item.path, must_exist=True)
            data = target.read_bytes()
            text = data.decode("utf-8", "surrogateescape")
            new_text = mask(text, config=settings)
            if changed_lines(new_text, config=settings):
                raise MemoryForestError("credentials_fix_incomplete", "A sign-in value would still stand; not written.",
                                        details={"path": item.path})
            entry = forest_archive.put(safe_root, item.path, reason=FIX_REASON, keep=True, lock=held)
            if forest_archive.read_copy(safe_root, entry) != data or target.read_bytes() != data:
                raise MemoryForestError("credentials_archive_mismatch",
                                        "The archive copy does not hold the page as it was; not written.",
                                        details={"path": item.path})
            atomic_write(target, new_text.encode("utf-8", "surrogateescape"))
            item.written = True
            item.archive_path = entry.archive_path
    return found


__all__ = [
    "FIX_REASON",
    "MASKING_VERSION",
    "REDACTED",
    "PageCredentials",
    "changed_lines",
    "check",
    "fingerprint",
    "fix",
    "mask",
    "mask_labelled",
    "shown",
]
