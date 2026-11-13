"""The forest's settings, read from ``forest.toml`` at the forest root.

Every key has a default (design, section 12), so a missing file or a missing
section means the defaults. A key the loader does not know is an error that
names it: a misspelt setting must never fall back silently to its default.

The settings that the contract leaves to each forest (the subject types, the
words that mark a channel, a stage or a dated name, the identity-number
formats) live here too, so that no rule in the code names one forest's world.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import system
from .errors import MemoryForestError
from .names import NAME_MAX


CONFIG_FILENAME: Final[str] = "forest.toml"
MAX_CONFIG_BYTES: Final[int] = 256 * 1024

POLICIES: Final[tuple[str, ...]] = ("subject-first", "agent-first")
SCOPES: Final[tuple[str, ...]] = ("own+shared", "all")
EMBEDDING_PROVIDERS: Final[tuple[str, ...]] = ("none", "openai-compatible")
TOOL_OUTPUTS: Final[tuple[str, ...]] = ("omit", "keep")

# Types every forest has, whatever its settings say (contract, section 2).
REQUIRED_TYPES: Final[tuple[str, ...]] = ("agent", "collection")

_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9-]*")
# The UTF-8 byte-order mark. The settings reader accepts a file that begins with one, and a patch keeps it.
BOM: Final[bytes] = b"\xef\xbb\xbf"
# The greatest length of an agent's name, and of an id that becomes a file name (``names.NAME_MAX``).
# A directory and `<id>_MTM.md` both have to fit where a file name may be 255
# bytes. 200 leaves that room, including an `-agent` suffix.
AGENT_TEXT_MAX: Final[int] = NAME_MAX
_LANGUAGE_RE: Final[re.Pattern[str]] = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_HIDE_RE: Final[re.Pattern[str]] = re.compile(r"after-hyphen|last-(?P<count>[1-9][0-9]?)")
_SHAPE_RE: Final[re.Pattern[str]] = re.compile(r"[NA][NA \-./]*[NA]|[NA]")


@dataclass(frozen=True, slots=True)
class ForestSettings:
    timezone: str = "UTC"
    language: str = "en"


@dataclass(frozen=True, slots=True)
class OwnerSettings:
    id: str = "owner"
    name: str = "the owner"
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """The owner's name followed by its aliases, each once."""
        seen: list[str] = []
        for value in (self.name, *self.aliases):
            if value not in seen:
                seen.append(value)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class LimitSettings:
    leaf_cap_bytes: int = 8192
    leaf_max_bytes: int = 20480
    record_keep_bytes: int = 2048
    category_max_leaves: int = 100
    about_max_ids: int = 8
    entry_admit_margin_bytes: int = 1024
    repeat_min_lines: int = 3
    repeat_min_chars: int = 40

    @property
    def entry_max_bytes(self) -> int:
        """The most material a collection entry holds before it is admitted as a leaf."""
        return self.leaf_cap_bytes - self.entry_admit_margin_bytes


@dataclass(frozen=True, slots=True)
class IdentityFormat:
    """One national identity number shape to mask.

    The setting is written ``"<shape>:<hide>"``. In the shape ``N`` stands for
    a digit, ``A`` for a Latin letter, and a space, ``-``, ``.`` or ``/`` for
    itself. ``<hide>`` is ``after-hyphen`` (every character after the first
    hyphen) or ``last-<n>`` (the last n letters and digits).
    """

    shape: str
    hide: str

    @property
    def text(self) -> str:
        return f"{self.shape}:{self.hide}"

    def pattern(self) -> re.Pattern[str]:
        parts = []
        for character in self.shape:
            if character == "N":
                parts.append("[0-9]")
            elif character == "A":
                parts.append("[A-Za-z]")
            else:
                parts.append(re.escape(character))
        return re.compile(r"(?<![0-9A-Za-z])" + "".join(parts) + r"(?![0-9A-Za-z])")

    def mask(self, number: str) -> str:
        """``number`` (one match of ``pattern``) with its hidden characters starred."""
        if self.hide == "after-hyphen":
            head, hyphen, tail = number.partition("-")
            if not hyphen:
                return number
            return head + hyphen + "".join("*" if c.isalnum() else c for c in tail)
        count = int(self.hide.removeprefix("last-"))
        characters = list(number)
        for position in range(len(characters) - 1, -1, -1):
            if count == 0:
                break
            if characters[position].isalnum():
                characters[position] = "*"
                count -= 1
        return "".join(characters)


@dataclass(frozen=True, slots=True)
class NumberSettings:
    identity_formats: tuple[IdentityFormat, ...] = ()
    # Words before a number that make it an account number (contract, section 4): account
    # words in the forest's languages, and the names of the banks the owner uses.
    account_words: tuple[str, ...] = ("account", "acct", "a/c")
    # Words before a number that make it a reference that stays whole: an order, a parcel,
    # an invoice, a booking.
    reference_words: tuple[str, ...] = ("order", "parcel", "tracking", "invoice", "reference", "booking")
    # Words before a number that, with a configured identity shape, make it an identity number.
    identity_words: tuple[str, ...] = ("identity number", "id number", "national id", "personal number")
    # Regular expressions for the national forms of phone numbers this forest meets; the
    # international form (a plus sign and 8 to 15 digits) is always recognised.
    phone_patterns: tuple[str, ...] = ()
    # Regular expressions for a company's or a service's line, which stays whole.
    keep_whole: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CredentialSettings:
    # Words that name a value sent to sign in or to pay; the value after one is removed from what
    # the forest writes and shows (contract, section 4, Sign-in values). English by default: a
    # forest adds the words of its other languages (a word that a particle or an ending joins
    # without a space is listed in that joined form too).
    label_words: tuple[str, ...] = (
        "password", "passcode", "passphrase", "passwd", "pin", "pin code", "pin number", "one-time code",
        "one-time password", "otp", "verification code", "security code", "confirmation code", "login code",
        "sign-in code", "access code", "authentication code", "2fa code", "door code", "gate code",
        "entry code", "alarm code", "cvc", "cvv", "token", "api key", "secret", "bearer",
        "authorization",
    )
    # Words between a label and its value without a colon: "the PIN is 4821".
    joining_words: tuple[str, ...] = ("is", "was")
    # Words after a number that make it a count, not a value: "a password of 12 characters".
    count_words: tuple[str, ...] = (
        "characters", "chars", "digits", "letters", "symbols", "times", "tries", "attempts", "minutes", "hours",
        "days", "weeks", "months", "years",
    )


@dataclass(frozen=True, slots=True)
class LevelSettings:
    leaf_days: int = 2
    branch_days: int = 5
    tree_subtopics: int = 3
    tree_subtopic_days: int = 5
    tree_subtopic_leaves: int = 3
    tree_types: tuple[str, ...] = ("project", "topic", "product", "device", "title", "agent")
    # Leaf kinds that are a sender's stream or the system's notices: a sub-topic made only of
    # them does not count toward a tree (contract, section 3).
    stream_kinds: tuple[str, ...] = ("received", "notice", "brief")
    # Words that, in the sub-topic of a leaf with no `kind:`, mark it as such a stream.
    stream_subtopic_words: tuple[str, ...] = (
        "offer",
        "offers",
        "alert",
        "alerts",
        "newsletter",
        "newsletters",
        "mail",
        "notice",
        "notices",
        "digest",
        "digests",
        "run-evidence",
    )
    # A Timeline line that opens with an agent's id or name, or with one of these stage
    # names, followed by one of `work_verbs`, records the system's own work and does not
    # count as a day of the subject (contract, section 3).
    stage_names: tuple[str, ...] = ("Daily", "the filing run", "the upkeep run")
    work_verbs: tuple[str, ...] = (
        "notified",
        "reminded",
        "reported",
        "alerted",
        "checked",
        "re-checked",
        "ran",
        "sent",
        "logged",
        "recorded",
        "noted",
    )
    # A line that opens with one of these tool names followed by `alerted` does not count either.
    tool_names: tuple[str, ...] = ("Calendar", "Reminders")
    # The branch that takes a category's other leaves when a subject promoted to a branch takes
    # over the category's board (contract, section 3, Levels).
    shared_branch: str = "shared"
    # The sub-topic a leaf subject's only leaf takes in its own folder when the leaf names none.
    default_subtopic: str = "profile"


@dataclass(frozen=True, slots=True)
class KnownAgent:
    """An agent declared in the settings, ``[agents.known.<id>]``: its name and role, each optional."""

    id: str
    name: str = ""
    role: str = ""


@dataclass(frozen=True, slots=True)
class AgentGroup:
    """Several agents behind one connection, ``[agents.groups.<id>]``.

    ``members`` are agent ids. ``open`` is false until a new name the caller
    gives may become a member by itself. ``max_members`` is unset until the
    owner sets a bound. ``source`` names a reader of an app's own member list.
    The group's id is not an agent.
    """

    id: str
    members: tuple[str, ...] = ()
    open: bool = False
    max_members: int | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    policy: str = "subject-first"
    orchestrators: tuple[str, ...] = ()
    default_scope: str = "own+shared"
    # Whether the first file in a raw lane `06 istm/<id>/` makes its agent's tree (contract,
    # section 3, Agent trees); with false, every agent is declared under `[agents.known.<id>]`.
    auto_create: bool = True
    # The type an agent's subject takes when `fold-tree` turns its tree into a branch.
    folded_type: str = "component"
    # The agents declared in the settings, by id.
    known: tuple[KnownAgent, ...] = ()
    # One connection that several of the owner's agents share, by group id.
    groups: tuple[AgentGroup, ...] = ()
    # Ids the settings name that cannot be a file name. A pass reports each and goes on.
    unplaceable: tuple[tuple[str, str], ...] = ()
    # Values of the agents' tables that are wrong for one entry: (where, what was left out and why). The entry
    # is left out, and everything else loads; `forest check` reports each.
    left_out: tuple[tuple[str, str], ...] = ()

    def declared(self, agent_id: str) -> KnownAgent | None:
        return next((item for item in self.known if item.id == agent_id), None)

    def group(self, group_id: str) -> AgentGroup | None:
        return next((item for item in self.groups if item.id == group_id), None)


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    provider: str = "none"


@dataclass(frozen=True, slots=True)
class IntakeToolSettings:
    """One adapter's intake, ``[intake.tools.<adapter>]``.

    ``enabled`` stays false until setup turns intake on for that tool. ``include``
    and ``exclude`` are folders the session ran in; an empty ``include`` is every
    folder. ``import_earlier`` stays false, so the first run places cursors at the
    end of what already exists. ``path`` is the folder of ``jsonl`` files, and for
    another adapter the place to look instead of the tool's own.
    """

    id: str
    enabled: bool = False
    agent: str = ""
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    import_earlier: bool = False
    path: str = ""


@dataclass(frozen=True, slots=True)
class IntakeSettings:
    """How conversations enter the raw lane (design, section 15.1).

    ``tool_output`` is ``omit`` or ``keep``. Omitted, a tool turn records that the
    tool ran and not what it printed.
    """

    tool_output: str = "omit"
    tools: tuple[IntakeToolSettings, ...] = ()

    def tool(self, adapter_id: str) -> IntakeToolSettings:
        """The settings for ``adapter_id``, or the defaults when it is not written."""
        for item in self.tools:
            if item.id == adapter_id:
                return item
        return IntakeToolSettings(adapter_id)


@dataclass(frozen=True, slots=True)
class SubjectSettings:
    types: tuple[str, ...] = (
        "person",
        "group",
        "organization",
        "project",
        "product",
        "app",
        "device",
        "place",
        "event",
        "title",
        "topic",
        "admin-item",
        "component",
        "agent",
        "collection",
    )
    # The id of the system as a whole, when the forest registers one: like the owner's id it
    # is never named in `about:` (contract, section 3, Pointers). Empty when there is none.
    system_id: str = ""


@dataclass(frozen=True, slots=True)
class NameSettings:
    # Words that make a file name dated (contract, section 4, Names): month and season words
    # in the forest's language. Digits and periods are recognised by shape.
    date_words: tuple[str, ...] = (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "spring",
        "summer",
        "autumn",
        "winter",
    )


@dataclass(frozen=True, slots=True)
class LintSettings:
    # Words that name a channel (contract, section 2): a sub-topic made only of them is a
    # channel, never a sub-topic.
    channel_words: tuple[str, ...] = (
        "mail",
        "email",
        "inbox",
        "chat",
        "chats",
        "messages",
        "texts",
        "sms",
        "calls",
        "call-log",
        "notifications",
        "browser",
        "screen",
    )
    # Phrases that mark a writer's remark on verification (contract, section 4, Open points).
    remark_phrases: tuple[str, ...] = (
        "could not be verified",
        "not verified",
        "unverified",
        "it is unclear whether",
        "remains unconfirmed",
        "not yet confirmed by any source",
    )


@dataclass(frozen=True, slots=True)
class RecordSettings:
    # How a record line marks the owner's own words (contract, section 4, What a record line
    # is): one of these name forms, then within the same clause a quotation between one of
    # the quote-mark pairs. Empty names means the owner's `name` and `aliases`.
    owner_word_names: tuple[str, ...] = ()
    # Each pair is two characters, the opening and the closing mark.
    quote_marks: tuple[str, ...] = ('""', "“”", "«»", "„“")


@dataclass(frozen=True, slots=True)
class Config:
    forest: ForestSettings = field(default_factory=ForestSettings)
    owner: OwnerSettings = field(default_factory=OwnerSettings)
    limits: LimitSettings = field(default_factory=LimitSettings)
    numbers: NumberSettings = field(default_factory=NumberSettings)
    credentials: CredentialSettings = field(default_factory=CredentialSettings)
    levels: LevelSettings = field(default_factory=LevelSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)
    embeddings: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    intake: IntakeSettings = field(default_factory=IntakeSettings)
    subjects: SubjectSettings = field(default_factory=SubjectSettings)
    names: NameSettings = field(default_factory=NameSettings)
    lint: LintSettings = field(default_factory=LintSettings)
    record: RecordSettings = field(default_factory=RecordSettings)
    source: str | None = None

    @property
    def timezone(self) -> str:
        return self.forest.timezone

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.forest.timezone)

    @property
    def language(self) -> str:
        return self.forest.language

    @property
    def subject_types(self) -> frozenset[str]:
        return frozenset(self.subjects.types)

    @property
    def about_excluded_ids(self) -> frozenset[str]:
        """The ids no leaf names in `about:`: the owner's, and the system's when it has one."""
        return frozenset(value for value in (self.owner.id, self.subjects.system_id) if value)

    def is_orchestrator(self, agent_id: str) -> bool:
        return agent_id in self.agents.orchestrators


DEFAULT_CONFIG: Final[Config] = Config()

_SECTIONS: Final[dict[str, type]] = {
    "forest": ForestSettings,
    "owner": OwnerSettings,
    "limits": LimitSettings,
    "numbers": NumberSettings,
    "credentials": CredentialSettings,
    "levels": LevelSettings,
    "agents": AgentSettings,
    "embeddings": EmbeddingSettings,
    "intake": IntakeSettings,
    "subjects": SubjectSettings,
    "names": NameSettings,
    "lint": LintSettings,
    "record": RecordSettings,
}


def _error(key: str, message: str, source: str | None) -> MemoryForestError:
    details: dict[str, Any] = {"key": key}
    if source:
        details["source"] = source
    return MemoryForestError("settings_invalid", f"forest.toml: {key}: {message}", details=details, next_step="report")


def _string(key: str, value: Any, source: str | None) -> str:
    if not isinstance(value, str):
        raise _error(key, "must be a string", source)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _error(key, "must not hold control characters", source)
    return value


def _strings(key: str, value: Any, source: str | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _error(key, "must be a list of strings", source)
    items = tuple(_string(key, item, source) for item in value)
    if len(set(items)) != len(items):
        raise _error(key, "lists a value twice", source)
    return items


def _integer(key: str, value: Any, source: str | None) -> int:
    if type(value) is not int or value <= 0:
        raise _error(key, "must be a positive integer", source)
    return value


def parse_identity_format(text: str, *, key: str = "numbers.identity_formats", source: str | None = None) -> IdentityFormat:
    shape, separator, hide = text.rpartition(":")
    if not separator or _SHAPE_RE.fullmatch(shape) is None or _HIDE_RE.fullmatch(hide) is None:
        raise _error(
            key,
            f"{text!r} is not '<shape>:<hide>' (shape of N, A, space, '-', '.', '/'; "
            "hide 'after-hyphen' or 'last-<n>')",
            source,
        )
    if hide == "after-hyphen" and "-" not in shape:
        raise _error(key, f"{text!r} hides after a hyphen its shape does not have", source)
    return IdentityFormat(shape=shape, hide=hide)


def _id_ok(value: str) -> bool:
    """An id that can be a file name: a slug no longer than :data:`AGENT_TEXT_MAX`."""
    return isinstance(value, str) and _SLUG_RE.fullmatch(value) is not None and len(value) <= AGENT_TEXT_MAX


def _known_agents(
    key: str, raw: Any, source: str | None
) -> tuple[tuple[KnownAgent, ...], list[tuple[str, str]], list[tuple[str, str]]]:
    """``[agents.known.<id>]`` tables. A value wrong for one entry is reported and left out; the rest load."""
    unplaceable: list[tuple[str, str]] = []
    left_out: list[tuple[str, str]] = []
    if not isinstance(raw, Mapping):
        return (), unplaceable, [(key, "is not a table of agents, one `[agents.known.<id>]` each; it is left out")]
    found = []
    for agent_id, table in raw.items():
        dotted = f"{key}.{agent_id}"
        if not _id_ok(str(agent_id)):
            unplaceable.append((dotted, str(agent_id)))
            continue
        if not isinstance(table, Mapping):
            left_out.append((dotted, "is not a table with `name` and `role`; the agent is left out"))
            continue
        values: dict[str, str] = {}
        for field_name, value in table.items():
            where = f"{dotted}.{field_name}"
            if field_name not in ("name", "role"):
                left_out.append((where, "is not a known setting; it is left out"))
                continue
            if field_name == "name":
                from .names import bad_reason

                why = bad_reason(value)
                if why is not None:
                    left_out.append((where, f"is not a name ({why}); it is left out"))
                    continue
                values["name"] = str(value).strip()
                continue
            if not isinstance(value, str) or any(ord(character) < 32 or ord(character) == 127 for character in value):
                left_out.append((where, "is not text without control characters; it is left out"))
                continue
            values[field_name] = " ".join(value.split())
        found.append(KnownAgent(str(agent_id), **values))
    return tuple(sorted(found, key=lambda item: item.id)), unplaceable, left_out


def _agent_groups(
    key: str, raw: Any, source: str | None, known: tuple[KnownAgent, ...]
) -> tuple[tuple[AgentGroup, ...], list[tuple[str, str]], list[tuple[str, str]]]:
    """``[agents.groups.<id>]`` tables. A value wrong for one entry is reported and left out; the rest load.

    A member written as an agent's name is that agent's id (:func:`memory_forest.names.find`). A member
    written twice, by id or by name, is counted once and reported.
    """
    unplaceable: list[tuple[str, str]] = []
    left_out: list[tuple[str, str]] = []
    if not isinstance(raw, Mapping):
        return (), unplaceable, [(key, "is not a table of groups, one `[agents.groups.<id>]` each; it is left out")]
    from . import names

    rows = [(item.id, item.name) for item in known]
    found = []
    for group_id, table in raw.items():
        dotted = f"{key}.{group_id}"
        if not _id_ok(str(group_id)):
            unplaceable.append((dotted, str(group_id)))
            continue
        if not isinstance(table, Mapping):
            left_out.append((dotted, "is not a table with `members` and `open`; the group is left out"))
            continue
        members: list[str] = []
        open_group = False
        max_members: int | None = None
        member_source = ""
        for field_name, value in table.items():
            item_key = f"{dotted}.{field_name}"
            if field_name == "members":
                if not isinstance(value, list):
                    left_out.append((item_key, "is not a list of names; the group has no members from it"))
                    continue
                for item in value:
                    if not isinstance(item, str) or names.bad_reason(item) is not None:
                        left_out.append((item_key, f"holds {item!r}, which is not a name; it is left out"))
                        continue
                    hits = names.find(item, rows)
                    if len(hits) == 1 and _id_ok(hits[0]):
                        chosen = hits[0]
                    elif not hits and _id_ok(item):
                        chosen = item
                    else:
                        unplaceable.append((item_key, item))
                        continue
                    if chosen in members:
                        left_out.append((item_key, f"lists `{chosen}` twice ({item!r}); it is counted once"))
                        continue
                    if chosen == group_id:
                        left_out.append((item_key, "lists the group's own id, which is not a member; it is left out"))
                        continue
                    members.append(chosen)
            elif field_name == "open":
                if not isinstance(value, bool):
                    left_out.append((item_key, "is not true or false; the group is closed"))
                    continue
                open_group = value
            elif field_name == "max_members":
                if type(value) is not int or value <= 0:
                    left_out.append((item_key, "is not a positive integer; the group has no greatest number"))
                    continue
                max_members = value
            elif field_name == "source":
                if not isinstance(value, str) or (value and not _id_ok(value)):
                    left_out.append((item_key, f"{value!r} is not a slug; the group has no source of members"))
                    continue
                member_source = value
            else:
                left_out.append((item_key, "is not a known setting; it is left out"))
        found.append(AgentGroup(str(group_id), tuple(members), open_group, max_members, member_source))
    return tuple(sorted(found, key=lambda item: item.id)), unplaceable, left_out


_INTAKE_FIELDS: Final[tuple[str, ...]] = ("enabled", "agent", "include", "exclude", "import_earlier", "path")


def _intake_tools(key: str, raw: Any, source: str | None) -> tuple[IntakeToolSettings, ...]:
    """``[intake.tools.<adapter>]`` tables. The adapter id is a slug; an unknown field is an error."""
    if not isinstance(raw, Mapping):
        raise _error(key, "must be a table of adapters, one `[intake.tools.<adapter>]` each", source)
    found = []
    for adapter_id, table in raw.items():
        dotted = f"{key}.{adapter_id}"
        if _SLUG_RE.fullmatch(str(adapter_id)) is None:
            raise _error(dotted, f"{adapter_id!r} is not a slug", source)
        if not isinstance(table, Mapping):
            raise _error(dotted, "must be a table", source)
        values: dict[str, Any] = {}
        defaults = IntakeToolSettings(str(adapter_id))
        for field_name, value in table.items():
            item_key = f"{dotted}.{field_name}"
            if field_name not in _INTAKE_FIELDS:
                raise _error(item_key, "is not a known setting", source)
            default = getattr(defaults, field_name)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise _error(item_key, "must be true or false", source)
                values[field_name] = value
            elif isinstance(default, tuple):
                items = _strings(item_key, value, source)
                if any(not item.strip() for item in items):
                    raise _error(item_key, "must not hold an empty path", source)
                values[field_name] = items
            else:
                values[field_name] = _string(item_key, value, source)
        found.append(IntakeToolSettings(str(adapter_id), **values))
    return tuple(sorted(found, key=lambda item: item.id))


def _section(name: str, raw: Any, source: str | None) -> Any:
    settings_type = _SECTIONS[name]
    if not isinstance(raw, Mapping):
        raise _error(name, "must be a table", source)
    known = {item.name: item for item in fields(settings_type)}
    values: dict[str, Any] = {}
    defaults = settings_type()
    for key, value in raw.items():
        dotted = f"{name}.{key}"
        if key not in known:
            raise _error(dotted, "is not a known setting", source)
        default = getattr(defaults, key)
        if name == "numbers" and key == "identity_formats":
            values[key] = tuple(
                parse_identity_format(item, key=dotted, source=source)
                for item in _strings(dotted, value, source)
            )
        elif name == "agents" and key in ("known", "groups", "orchestrators"):
            continue
        elif name == "intake" and key == "tools":
            values[key] = _intake_tools(dotted, value, source)
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise _error(dotted, "must be true or false", source)
            values[key] = value
        elif isinstance(default, int):
            values[key] = _integer(dotted, value, source)
        elif isinstance(default, tuple):
            values[key] = _strings(dotted, value, source)
        else:
            values[key] = _string(dotted, value, source)
    if name == "agents":
        known_agents, bad_known, left_known = _known_agents(f"{name}.known", raw.get("known", {}), source)
        groups, bad_groups, left_groups = _agent_groups(f"{name}.groups", raw.get("groups", {}), source, known_agents)
        kept_orchestrators: list[str] = []
        bad_orchestrators: list[tuple[str, str]] = []
        left_orchestrators: list[tuple[str, str]] = []
        listed = raw.get("orchestrators", list(defaults.orchestrators))
        if not isinstance(listed, list):
            left_orchestrators.append((f"{name}.orchestrators", "is not a list of ids; there is no main agent"))
            listed = []
        for item in listed:
            if not isinstance(item, str):
                left_orchestrators.append((f"{name}.orchestrators", f"holds {item!r}, which is not an id; it is left out"))
            elif not _id_ok(item):
                bad_orchestrators.append((f"{name}.orchestrators", item))
            elif item in kept_orchestrators:
                left_orchestrators.append((f"{name}.orchestrators", f"lists `{item}` twice; it is counted once"))
            else:
                kept_orchestrators.append(item)
        values["orchestrators"] = tuple(kept_orchestrators)
        values["known"] = known_agents
        values["groups"] = groups
        values["unplaceable"] = tuple([*bad_known, *bad_groups, *bad_orchestrators])
        values["left_out"] = tuple([*left_known, *left_groups, *left_orchestrators])
    return replace(defaults, **values)


def _validate(config: Config) -> Config:
    source = config.source
    try:
        ZoneInfo(config.forest.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _error("forest.timezone", f"{config.forest.timezone!r} is not a known time zone", source) from exc
    if _LANGUAGE_RE.fullmatch(config.forest.language) is None:
        raise _error("forest.language", f"{config.forest.language!r} is not a language tag", source)
    if _SLUG_RE.fullmatch(config.owner.id) is None:
        raise _error("owner.id", f"{config.owner.id!r} is not a slug", source)
    if not config.owner.name.strip():
        raise _error("owner.name", "must not be empty", source)
    if any(not alias.strip() for alias in config.owner.aliases):
        raise _error("owner.aliases", "must not hold an empty name", source)
    limits = config.limits
    if limits.leaf_max_bytes < limits.leaf_cap_bytes:
        raise _error("limits.leaf_max_bytes", "must not be smaller than limits.leaf_cap_bytes", source)
    if limits.entry_admit_margin_bytes >= limits.leaf_cap_bytes:
        raise _error("limits.entry_admit_margin_bytes", "must be smaller than limits.leaf_cap_bytes", source)
    levels = config.levels
    if levels.branch_days < levels.leaf_days:
        raise _error("levels.branch_days", "must not be smaller than levels.leaf_days", source)
    types = config.subjects.types
    for value in types:
        if _SLUG_RE.fullmatch(value) is None:
            raise _error("subjects.types", f"{value!r} is not a slug", source)
    missing = [value for value in REQUIRED_TYPES if value not in types]
    if missing:
        config = replace(config, subjects=replace(config.subjects, types=(*types, *missing)))
    for value in levels.tree_types:
        if value not in config.subjects.types:
            raise _error("levels.tree_types", f"{value!r} is not one of subjects.types", source)
    if config.subjects.system_id and _SLUG_RE.fullmatch(config.subjects.system_id) is None:
        raise _error("subjects.system_id", f"{config.subjects.system_id!r} is not a slug", source)
    if config.agents.policy not in POLICIES:
        raise _error("agents.policy", f"must be one of {', '.join(POLICIES)}", source)
    if config.agents.default_scope not in SCOPES:
        raise _error("agents.default_scope", f"must be one of {', '.join(SCOPES)}", source)
    for value in config.agents.orchestrators:
        if _SLUG_RE.fullmatch(value) is None:
            raise _error("agents.orchestrators", f"{value!r} is not a slug", source)
    folded = config.agents.folded_type
    if _SLUG_RE.fullmatch(folded) is None or folded in REQUIRED_TYPES:
        raise _error("agents.folded_type", f"{folded!r} is not a slug other than `agent` and `collection`", source)
    for key in ("shared_branch", "default_subtopic"):
        if _SLUG_RE.fullmatch(getattr(levels, key)) is None:
            raise _error(f"levels.{key}", f"{getattr(levels, key)!r} is not a slug", source)
    if config.embeddings.provider not in EMBEDDING_PROVIDERS:
        raise _error("embeddings.provider", f"must be one of {', '.join(EMBEDDING_PROVIDERS)}", source)
    if config.intake.tool_output not in TOOL_OUTPUTS:
        raise _error("intake.tool_output", f"must be one of {', '.join(TOOL_OUTPUTS)}", source)
    intake_tools = []
    intake_bad: list[tuple[str, str]] = []
    for tool in config.intake.tools:
        if tool.agent and not _id_ok(tool.agent):
            intake_bad.append((f"intake.tools.{tool.id}.agent", tool.agent))
            intake_tools.append(replace(tool, agent=""))
        else:
            intake_tools.append(tool)
    if intake_bad:
        config = replace(
            config,
            intake=replace(config.intake, tools=tuple(intake_tools)),
            agents=replace(config.agents, unplaceable=(*config.agents.unplaceable, *intake_bad)),
        )
    for key in ("phone_patterns", "keep_whole"):
        for value in getattr(config.numbers, key):
            try:
                re.compile(value)
            except re.error as exc:
                raise _error(f"numbers.{key}", f"{value!r} is not a regular expression: {exc}", source) from exc
    for key in ("label_words", "joining_words", "count_words"):
        for value in getattr(config.credentials, key):
            if not value.strip():
                raise _error(f"credentials.{key}", "must not hold an empty word", source)
            if key == "label_words" and value.strip().casefold() == "redacted":
                raise _error(f"credentials.{key}", f"{value!r} would find the mark that replaces a value", source)
    for value in config.record.quote_marks:
        if len(value) != 2:
            raise _error("record.quote_marks", f"{value!r} is not two characters (an opening and a closing mark)", source)
    return config


def parse_config(text: str, *, source: str | None = None) -> Config:
    """Settings from the text of a ``forest.toml``."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MemoryForestError(
            "settings_invalid",
            f"forest.toml is not valid TOML: {exc}",
            details={"source": source} if source else None,
            next_step="report",
        ) from exc
    sections: dict[str, Any] = {}
    for name, value in raw.items():
        if name not in _SECTIONS:
            raise _error(name, "is not a known settings section", source)
        sections[name] = _section(name, value, source)
    return _validate(Config(**sections, source=source))


def config_path(root: str | os.PathLike[str]) -> Path:
    return Path(root) / CONFIG_FILENAME


def load_config(root: str | os.PathLike[str]) -> Config:
    """The settings of the forest at ``root``; the defaults when it has no ``forest.toml``.

    A ``forest.toml`` that is a link, not a regular file, larger than any settings file needs to be, not
    UTF-8 or not valid settings is ``settings_invalid``: the owner's file, so the next move is ``report``.
    Any other answer of the door is raised as it is.
    """
    path = config_path(root)
    try:
        data = system.read_bytes(path, limit=MAX_CONFIG_BYTES + 1)
    except system.Absent:
        return DEFAULT_CONFIG
    except system.Link as exc:
        raise MemoryForestError(
            "settings_invalid",
            "forest.toml is a link; the forest's settings are a regular file, never a link.",
            details={"source": CONFIG_FILENAME},
            next_step="report",
        ) from exc
    except system.NotAFile as exc:
        raise MemoryForestError(
            "settings_invalid",
            f"forest.toml is a {exc.found}, not a regular file." if exc.found else "forest.toml is not a regular file.",
            details={"source": CONFIG_FILENAME},
            next_step="report",
        ) from exc
    return settings_from_bytes(data, source=str(path))  # the ledger is found beside the settings it came from


def settings_from_bytes(data: bytes, *, source: str) -> Config:
    """The settings a ``forest.toml`` holds. Too large, not UTF-8 or not valid settings is ``settings_invalid``."""
    if len(data) > MAX_CONFIG_BYTES:
        raise MemoryForestError(
            "settings_invalid",
            "forest.toml is larger than any settings file needs to be.",
            details={"source": source},
            next_step="report",
        )
    try:
        # An editor on some systems writes a byte-order mark first; the settings are the text after it.
        text = data[len(BOM):].decode("utf-8") if data.startswith(BOM) else data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MemoryForestError(
            "settings_invalid",
            "forest.toml is not UTF-8 text.",
            details={"source": source},
            next_step="report",
        ) from exc
    return parse_config(text, source=source)


def find_root(start: str | os.PathLike[str]) -> Path | None:
    """The nearest folder at or above ``start`` that holds a ``forest.toml`` (a file, not a link)."""
    current = system.absolute(start)
    for folder in (current, *current.parents):
        found = system.look_or_none(folder / CONFIG_FILENAME)
        if found is not None and found.kind == "file":
            return folder
    return None


def _toml_value(value: Any) -> str:
    if isinstance(value, IdentityFormat):
        return _toml_value(value.text)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(type(value).__name__)


def render_config(config: Config = DEFAULT_CONFIG) -> str:
    """A complete ``forest.toml`` for ``config``, every setting written out."""
    lines = ["# Settings of this forest. Every key is listed with its value; see the design, section 12."]
    for name in _SECTIONS:
        section = getattr(config, name)
        lines.append("")
        lines.append(f"[{name}]")
        for item in fields(section):
            if name == "agents" and item.name in {"known", "groups", "unplaceable", "left_out"}:
                continue
            if name == "intake" and item.name == "tools":
                continue
            lines.append(f"{item.name} = {_toml_value(getattr(section, item.name))}")
        if name == "intake":
            for tool in section.tools:
                lines.append("")
                lines.append(f"[intake.tools.{tool.id}]")
                for key in _INTAKE_FIELDS:
                    lines.append(f"{key} = {_toml_value(getattr(tool, key))}")
        if name == "agents":
            for agent in section.known:
                lines.append("")
                lines.append(f"[agents.known.{agent.id}]")
                for key in ("name", "role"):
                    if getattr(agent, key):
                        lines.append(f"{key} = {_toml_value(getattr(agent, key))}")
            for group in section.groups:
                lines.append("")
                lines.append(f"[agents.groups.{group.id}]")
                lines.append(f"members = {_toml_value(group.members)}")
                lines.append(f"open = {_toml_value(group.open)}")
                if group.max_members is not None:
                    lines.append(f"max_members = {_toml_value(group.max_members)}")
                if group.source:
                    lines.append(f"source = {_toml_value(group.source)}")
    return "\n".join(lines) + "\n"
