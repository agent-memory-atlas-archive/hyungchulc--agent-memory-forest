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

from .errors import MemoryForestError


CONFIG_FILENAME: Final[str] = "forest.toml"
MAX_CONFIG_BYTES: Final[int] = 256 * 1024

POLICIES: Final[tuple[str, ...]] = ("subject-first", "agent-first")
SCOPES: Final[tuple[str, ...]] = ("own+shared", "all")
EMBEDDING_PROVIDERS: Final[tuple[str, ...]] = ("none", "openai-compatible")
TOOL_OUTPUTS: Final[tuple[str, ...]] = ("omit", "keep")

# Types every forest has, whatever its settings say (contract, section 2).
REQUIRED_TYPES: Final[tuple[str, ...]] = ("agent", "collection")

_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9-]*")
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
    gives may become a member by itself. The group's id is not an agent.
    """

    id: str
    members: tuple[str, ...] = ()
    open: bool = False


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
    return MemoryForestError("config_invalid", f"{key}: {message}", details=details)


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


def _known_agents(key: str, raw: Any, source: str | None) -> tuple[KnownAgent, ...]:
    """``[agents.known.<id>]`` tables: each id a slug, each table with an optional ``name`` and ``role``."""
    if not isinstance(raw, Mapping):
        raise _error(key, "must be a table of agents, one `[agents.known.<id>]` each", source)
    found = []
    for agent_id, table in raw.items():
        dotted = f"{key}.{agent_id}"
        if _SLUG_RE.fullmatch(agent_id) is None:
            raise _error(dotted, f"{agent_id!r} is not a slug", source)
        if not isinstance(table, Mapping):
            raise _error(dotted, "must be a table with `name` and `role`", source)
        values: dict[str, str] = {}
        for field_name, value in table.items():
            if field_name not in ("name", "role"):
                raise _error(f"{dotted}.{field_name}", "is not a known setting", source)
            values[field_name] = " ".join(_string(f"{dotted}.{field_name}", value, source).split())
        found.append(KnownAgent(agent_id, **values))
    return tuple(sorted(found, key=lambda item: item.id))


def _agent_groups(key: str, raw: Any, source: str | None) -> tuple[AgentGroup, ...]:
    """``[agents.groups.<id>]`` tables: member ids, and whether a new name may join."""
    if not isinstance(raw, Mapping):
        raise _error(key, "must be a table of groups, one `[agents.groups.<id>]` each", source)
    found = []
    for group_id, table in raw.items():
        dotted = f"{key}.{group_id}"
        if _SLUG_RE.fullmatch(group_id) is None:
            raise _error(dotted, f"{group_id!r} is not a slug", source)
        if not isinstance(table, Mapping):
            raise _error(dotted, "must be a table with `members` and `open`", source)
        members: tuple[str, ...] = ()
        open_group = False
        for field_name, value in table.items():
            item_key = f"{dotted}.{field_name}"
            if field_name == "members":
                members = _strings(item_key, value, source)
                for item in members:
                    if _SLUG_RE.fullmatch(item) is None:
                        raise _error(item_key, f"{item!r} is not a slug", source)
            elif field_name == "open":
                if not isinstance(value, bool):
                    raise _error(item_key, "must be true or false", source)
                open_group = value
            else:
                raise _error(item_key, "is not a known setting", source)
        if group_id in members:
            raise _error(dotted, "the group's own id is not a member", source)
        found.append(AgentGroup(group_id, members, open_group))
    return tuple(sorted(found, key=lambda item: item.id))


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
        elif name == "agents" and key == "known":
            values[key] = _known_agents(dotted, value, source)
        elif name == "agents" and key == "groups":
            values[key] = _agent_groups(dotted, value, source)
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
    for tool in config.intake.tools:
        if tool.agent and _SLUG_RE.fullmatch(tool.agent) is None:
            raise _error(
                f"intake.tools.{tool.id}.agent",
                f"{tool.agent!r} is not a slug",
                source,
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
            "config_invalid",
            f"forest.toml is not valid TOML: {exc}",
            details={"source": source} if source else None,
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
    """The settings of the forest at ``root``; the defaults when it has no ``forest.toml``."""
    path = config_path(root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return DEFAULT_CONFIG
    if not path.is_file() or path.is_symlink():
        raise MemoryForestError(
            "config_invalid",
            "forest.toml must be a regular file, never a symlink.",
            details={"source": str(path)},
        )
    if info.st_size > MAX_CONFIG_BYTES:
        raise MemoryForestError(
            "config_invalid",
            "forest.toml is larger than any settings file needs to be.",
            details={"source": str(path), "size": info.st_size},
        )
    return parse_config(path.read_text(encoding="utf-8"), source=str(path))


def find_root(start: str | os.PathLike[str]) -> Path | None:
    """The nearest folder at or above ``start`` that holds a ``forest.toml``."""
    current = Path(os.path.abspath(start))
    for folder in (current, *current.parents):
        if (folder / CONFIG_FILENAME).is_file():
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
            if name == "agents" and item.name in {"known", "groups"}:
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
    return "\n".join(lines) + "\n"
