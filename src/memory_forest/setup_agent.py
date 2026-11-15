"""``forest setup``: the owner's answers, this agent in the settings, and what the agent does in its own tool.

An agent runs it for its owner (design, section 15.4). One call answers one object whose ``next`` is
``ask_owner`` (only the questions the owner alone can decide that are still unanswered; nothing is written),
``done`` (what was written, and the ordered ``todo`` the agent carries out in its own tool), or a refusal.
Every answer is a flag the owner chose: setup fills none in by itself. It knows no agent program and writes
nothing outside the forest's folder.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePath as Path
from typing import Any

from . import agents, groups, layout, names, system
from .config import BOM, CONFIG_FILENAME, DEFAULT_CONFIG, Config, _toml_value, config_path, load_config, parse_config
from .errors import MemoryForestError
from .locking import atomic_write, writer_lock
from .safety import prepare_new_root

SETUP_DIR = f"{layout.STATE_DIR}/setup"
# Each agent's record file: one name per agent in one folder, so the cursor intake keeps for a file (its name,
# relative to its folder) is never another agent's.
TRANSCRIPTS = "transcripts"
INTAKE_COMMAND = "forest intake --from jsonl --as {id} --transcript {file} --once --root {root}"


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    flag: str
    question: str
    why_owner: str
    choices: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"key": self.key, "flag": self.flag, "question": self.question,
                                "why_owner": self.why_owner}
        if self.choices:
            body["choices"] = list(self.choices)
        return body


_BUDGET = "It spends the owner's model budget, so only the owner chooses it."
QUESTIONS: tuple[Question, ...] = (
    Question("root", "--root", "Where does the forest live? A new or empty folder makes a new forest; the folder "
             "of an existing forest joins it.",
             "The forest is the owner's memory on the owner's disk, and which forest an agent joins is the "
             "owner's choice."),
    Question("name", "--as", "What is this agent's name? Any script; the forest makes its id from the name.",
             "The name is how the owner and the other agents call this agent."),
    Question("main", "--main", "Is this agent the owner's main agent, which reads the whole forest, other "
             "agents' own trees included?", "Reading every tree is access only the owner gives.", ("yes", "no")),
    Question("conversations", "--conversations", "Which of this agent's conversations are brought into the "
             "forest: all of them from now on, only those the owner asks for, or none?",
             "Recording the owner's conversations needs the owner's consent; it is never a default.",
             ("all", "asked", "none")),
    Question("earlier", "--earlier", "Are this agent's conversations from before today brought in too? (Not "
             "asked when the answer above is none.)",
             "Earlier conversations were held without a forest in mind; bringing them in is the owner's consent.",
             ("yes", "no")),
    Question("keeper", "--keeper", "Who keeps the forest growing (the daily record, filing and upkeep): this "
             "agent, another agent, or nobody yet?",
             "Keeping writes the forest every day and spends the owner's model budget; a forest has one keeper.",
             ("this", "other", "nobody")),
    Question("when", "--when", "When does this agent run the routine, for example \"every night at 23:30\"? "
             "(Asked when this agent keeps the forest.)",
             "The routine runs on the owner's machine at times that suit the owner."),
    Question("daily_model", "--daily-model", "Which of the owner's models may the daily record use? (Asked "
             "when this agent keeps the forest.)", _BUDGET),
    Question("filing_model", "--filing-model", "Which of the owner's models may filing and upkeep use? "
             "(Asked when this agent keeps the forest.)", _BUDGET),
)
_KEEPER_ONLY = ("when", "daily_model", "filing_model")
# The answers kept in the setup record: everything but where the forest is and who is asking.
RECORDED = tuple(question.key for question in QUESTIONS if question.key not in ("root", "name"))


def asked(answers: Mapping[str, str | None]) -> list[Question]:
    """The questions still unanswered. A question that follows from an answer given otherwise is not asked."""
    missing = []
    for question in QUESTIONS:
        if answers.get(question.key):
            continue
        if question.key == "earlier" and answers.get("conversations") == "none":
            continue
        if question.key in _KEEPER_ONLY and answers.get("keeper") not in (None, "this"):
            continue
        missing.append(question)
    return missing


def _checked_choices(answers: Mapping[str, str | None]) -> None:
    for question in QUESTIONS:
        value = answers.get(question.key)
        if value and question.choices and value not in question.choices:
            raise MemoryForestError(
                "arguments_invalid",
                f"{question.flag} is one of {', '.join(question.choices)}.",
                details={"flag": question.flag, "choices": list(question.choices)},
                next_step="fix",
            )


def detected_timezone() -> str:
    """The system's zone: the name /etc/localtime links to under a zoneinfo folder, else UTC."""
    try:
        linked = str(system.real_path("/etc/localtime"))
    except (OSError, ValueError):
        return "UTC"
    _before, found, name = linked.partition("zoneinfo/")
    if not found or not name:
        return "UTC"
    try:
        system.timezone_info(name)
    except (KeyError, ValueError, OSError):
        return "UTC"
    return name


def _proposals(timezone: str) -> dict[str, Any]:
    return {"timezone": {"value": timezone, "flag": "--timezone",
                         "note": "Used for a new forest. A proposal the owner may change, not a question."}}


def run(
    given: Mapping[str, str | None],
    *,
    timezone: str | None = None,
    dry_run: bool = False,
    now: datetime,
    detect: Callable[[], str] = detected_timezone,
) -> dict[str, Any]:
    """One setup call. ``given`` holds the flags by question key; ``now`` is an aware time."""
    answers = {key: (value if value else None) for key, value in given.items()}
    _checked_choices(answers)
    proposed = timezone or detect()
    missing = asked(answers)
    if missing:
        return {"next": "ask_owner", "questions": [question.as_dict() for question in missing],
                "proposals": _proposals(proposed), "written": False}
    name = names.check(answers["name"])
    shown_root = str(answers["root"])
    root, existed = prepare_new_root(shown_root)
    new = not existed or (system.kind(root / CONFIG_FILENAME) is None and not system.listing(root))
    if not new and system.kind(root / CONFIG_FILENAME) is None:
        raise MemoryForestError("root_not_empty", "forest init needs a new or empty folder.",
                                details={"path": shown_root})
    if new:
        try:
            system.timezone_info(proposed)
        except (KeyError, ValueError, OSError):
            raise MemoryForestError("arguments_invalid", "--timezone names no time zone this system knows.",
                                    details={"flag": "--timezone"}, next_step="fix") from None
    recorded = {key: answers.get(key) for key in RECORDED}
    if recorded["conversations"] == "none":
        recorded["earlier"] = None
    if recorded["keeper"] != "this":
        for key in _KEEPER_ONLY:
            recorded[key] = None
    config = replace(DEFAULT_CONFIG, forest=replace(DEFAULT_CONFIG.forest, timezone=proposed))
    if new:
        if dry_run:
            agent_id, stored = _new_id(name, root, config), name
            plan = _plan(None, config, agent_id, stored, recorded)
            return _done(shown_root, agent_id, stored, recorded, [f"a new forest in {shown_root} (time zone "
                         f"{proposed})", *plan.changed], now=now, config=config, dry_run=True, new=True)
        layout.initialize(root, config=config)
    with writer_lock(root, wait_seconds=30, label="forest setup") if not dry_run else _nothing():
        config = load_config(root)
        rows = groups._agent_rows(root, config)
        found = names.find(name, rows)
        if len(found) > 1:
            raise MemoryForestError(
                "agent_ambiguous",
                "The name is the name of more than one agent of this forest. Pass one agent's exact stored id "
                "with --as.",
                details={"agents": [{"id": agent_id, "name": dict(rows).get(agent_id) or agent_id}
                                    for agent_id in found]},
                next_step="fix",
            )
        agent_id = found[0] if found else _new_id(name, root, config)
        stored = dict(rows).get(agent_id) or name if found else name
        if recorded["keeper"] == "this":
            present = [record for record in records(root, besides=agent_id)
                       if (record.get("answers") or {}).get("keeper") == "this"]
            if present:
                keeper = str(present[0].get("agent"))
                return {"next": "ask_owner", "agent": {"id": agent_id, "name": display(stored)}, "written": False,
                        "questions": [replace(QUESTIONS[5], question=f"{keeper} keeps this forest already, and a "
                                              "forest has one keeper. Does another agent keep it, or nobody yet? "
                                              f"To make this agent the keeper, first run setup for {keeper} "
                                              "with --keeper other.", choices=("other", "nobody")).as_dict()],
                        "proposals": _proposals(proposed)}
        if recorded["conversations"] != "none" and config.intake.tool("jsonl").path:
            raise MemoryForestError(
                "setup_jsonl_path_set",
                "forest.toml sets `path` under [intake.tools.jsonl]: intake then reads record files only in that "
                "folder, and a sweep gives every file there to one agent. Setup keeps each agent's record file "
                "apart in transcripts/. The owner takes that `path` out of forest.toml, then this call runs.",
                details={"setting": "intake.tools.jsonl.path"},
                next_step="report",
            )
        plan = _plan(root, config, agent_id, stored, recorded)
        if not dry_run:
            _apply(root, config, plan, agent_id, stored, recorded, now=now)
    changed = ([f"a new forest in {shown_root} (time zone {config.timezone})"] if new else []) + plan.changed
    return _done(shown_root, agent_id, stored, recorded, changed, now=now, config=config, dry_run=dry_run, new=new)


class _nothing:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


def _new_id(name: str, root: Path, config: Config) -> str:
    """A new agent's id by the name contract, never one something in the forest (or the new one) holds."""
    return agents.new_id(name, agents.taken(root, config))


@dataclass(slots=True)
class _Plan:
    changed: list[str]
    add_known: bool
    orchestrators: tuple[str, ...]
    record_changed: bool
    make_transcript: bool


def _plan(root: Path | None, config: Config, agent_id: str, stored: str, recorded: Mapping[str, Any]) -> _Plan:
    changed: list[str] = []
    add_known = config.agents.declared(agent_id) is None
    if add_known:
        changed.append(f"{CONFIG_FILENAME}: [agents.known.{agent_id}] name = {_toml_value(stored)}")
    current = tuple(config.agents.orchestrators)
    if recorded["main"] == "yes" and agent_id not in current:
        wanted = (*current, agent_id)
        changed.append(f"{CONFIG_FILENAME}: {agent_id} added to agents.orchestrators (the main agent)")
    elif recorded["main"] == "no" and agent_id in current:
        wanted = tuple(item for item in current if item != agent_id)
        changed.append(f"{CONFIG_FILENAME}: {agent_id} taken out of agents.orchestrators (not the main agent)")
    else:
        wanted = current
    make_transcript = False
    if recorded["conversations"] != "none":
        file = f"{TRANSCRIPTS}/{agent_id}.jsonl"
        make_transcript = root is None or system.kind(layout.resolve(root, file)) is None
        if make_transcript:
            changed.append(f"{file}: this agent's record file, empty")
    before = None if root is None else record(root, agent_id)
    old = (before or {}).get("answers") or {}
    record_changed = before is None or dict(old) != dict(recorded)
    if before is None:
        changed.append(f"{SETUP_DIR}/{agent_id}.json: the owner's answers")
    elif record_changed:
        differences = [f"{key} was {old.get(key) or 'unset'}, now {recorded[key] or 'unset'}"
                       for key in RECORDED if old.get(key) != recorded[key]]
        changed.append(f"{SETUP_DIR}/{agent_id}.json: " + "; ".join(differences))
    return _Plan(changed, add_known, wanted, record_changed, make_transcript)


def _apply(root: Path, config: Config, plan: _Plan, agent_id: str, stored: str, recorded: Mapping[str, Any], *,
           now: datetime) -> None:
    if plan.add_known or plan.orchestrators != tuple(config.agents.orchestrators):
        _write_settings(root, config, agent_id, stored, add_known=plan.add_known, orchestrators=plan.orchestrators)
    if plan.make_transcript:
        layout.ensure_folder(root, TRANSCRIPTS)
        system.create_new(layout.resolve(root, f"{TRANSCRIPTS}/{agent_id}.jsonl"), b"")
    if plan.record_changed:
        layout.ensure_folder(root, SETUP_DIR)
        body = {"agent": agent_id, "name": stored, "answers": dict(recorded), "answered_at": now.isoformat(),
                "since": now.astimezone(config.zone).date().isoformat(),
                "transcript": f"{TRANSCRIPTS}/{agent_id}.jsonl" if recorded["conversations"] != "none" else None}
        atomic_write(layout.resolve(root, f"{SETUP_DIR}/{agent_id}.json"),
                     json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# ─────────────────────────────── the settings, patched in place ───────────────────────────────


def _write_settings(root: Path, config: Config, agent_id: str, stored: str, *, add_known: bool,
                    orchestrators: tuple[str, ...]) -> None:
    """Add the agent's table and set the orchestrators, keeping every other byte of forest.toml."""
    edits: list[dict[str, Any]] = []
    if add_known:
        edits.append({"new_table": f"agents.known.{agent_id}", "values": {"name": stored}})
    if orchestrators != tuple(config.agents.orchestrators):
        edits.append({"set_value": "orchestrators", "table": "agents", "value": list(orchestrators)})
    path = config_path(root)
    try:
        data = system.read_bytes(path)
        marked = data.startswith(BOM)
        text = (data[len(BOM):] if marked else data).decode("utf-8")
        updated = _patched(text, agent_id, stored, add_known=add_known, orchestrators=orchestrators)
        parsed = parse_config(updated, source=config.source)
    except (system.Outcome, UnicodeError, ValueError, MemoryForestError) as exc:
        raise _unpatched(agent_id, edits) from exc
    declared = parsed.agents.declared(agent_id)
    rest = replace(parsed, agents=replace(parsed.agents, orchestrators=config.agents.orchestrators,
                                          known=config.agents.known))
    # The name reads back in the same spelling: compared as TOML text, the form it was written in.
    if (declared is None or (add_known and _toml_value(declared.name) != _toml_value(stored)) or rest != config
            or tuple(parsed.agents.orchestrators) != orchestrators):
        raise _unpatched(agent_id, edits)
    try:
        atomic_write(path, (BOM if marked else b"") + updated.encode("utf-8"))
    except (system.Outcome, MemoryForestError) as exc:
        raise MemoryForestError(
            "settings_unwritable",
            "forest.toml could not be written, so setup wrote nothing more. The owner makes these edits: "
            + groups.edit_sentences(edits),
            details={"agent": agent_id, "edits": edits},
            next_step="report",
        ) from exc


def _unpatched(agent_id: str, edits: list[dict[str, Any]]) -> MemoryForestError:
    return MemoryForestError(
        "settings_unpatched",
        "forest.toml is written in a form setup does not patch safely, so setup wrote nothing more. The owner "
        "makes these edits: " + groups.edit_sentences(edits),
        details={"agent": agent_id, "edits": edits},
        next_step="report",
    )


def _patched(text: str, agent_id: str, stored: str, *, add_known: bool, orchestrators: tuple[str, ...]) -> str:
    newline = groups._file_ending(text)
    rendered = f"orchestrators = {_toml_value(orchestrators)}"
    span = groups._table_span(text, "agents")
    if span is None:
        text += ("" if text.endswith("\n") else newline) + f"{newline}[agents]{newline}{rendered}{newline}"
    else:
        start, end = span
        block = text[start:end]
        key_at = groups._find_key(block, "orchestrators")
        if key_at is None:
            header = groups._lines(block)[0]
            block = header + rendered + groups._ending(header) + block[len(header):]
        else:
            value_at = groups._value_at(block, key_at)
            close = None if value_at is None or block[value_at] != "[" else groups._array_end(block, value_at)
            if value_at is None or close is None:
                raise ValueError("orchestrators")
            block = block[:value_at] + _toml_value(orchestrators) + block[close + 1:]
        text = text[:start] + block + text[end:]
    if add_known and groups._table_span(text, f"agents.known.{agent_id}") is None:
        table = f"[agents.known.{agent_id}]{newline}name = {_toml_value(stored)}"
        text += f"{newline}{table}{newline}" if text.endswith("\n") else f"{newline}{newline}{table}"
    return text


# ─────────────────────────────── the records ───────────────────────────────


def record(root: Path, agent_id: str) -> dict[str, Any] | None:
    """This agent's setup record, or None when there is none that can be read."""
    try:
        loaded = json.loads(system.read_text(layout.resolve(root, f"{SETUP_DIR}/{agent_id}.json")))
    except (system.Outcome, UnicodeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def display(name: str) -> str:
    """A name as display text in an answer. It is printed, never compared, keyed or stored again."""
    return f"{name}"


def records(root: Path, *, besides: str = "") -> list[dict[str, Any]]:
    """Every setup record of the forest, by file name, but the one of the agent ``besides`` names."""
    try:
        entries = system.listing(layout.resolve(root, SETUP_DIR))
    except system.Absent:
        return []
    found = []
    for entry in sorted(entries, key=lambda item: item.name):
        if entry.kind == "file" and entry.name.endswith(".json") and entry.name != f"{besides}.json":
            loaded = record(root, entry.name[: -len(".json")])
            if loaded is not None:
                found.append(loaded)
    return found


# ─────────────────────────────── what the agent does now ───────────────────────────────


def _done(shown_root: str, agent_id: str, stored: str, recorded: Mapping[str, Any], changed: list[str], *,
          now: datetime, config: Config, dry_run: bool, new: bool) -> dict[str, Any]:
    return {"next": "done", "agent": {"id": agent_id, "name": display(stored)}, "forest": shown_root, "new": new,
            "dry_run": dry_run, "changed": changed,
            "todo": todo(shown_root, agent_id, stored, recorded, today=now.astimezone(config.zone).date().isoformat())}


def todo(shown_root: str, agent_id: str, stored: str, recorded: Mapping[str, Any], *, today: str) -> list[dict[str, Any]]:
    """What the agent now does in its own tool, in order, paths as the caller gave them."""
    root = shlex.quote(shown_root)
    steps: list[dict[str, Any]] = [{
        "step": "register_mcp",
        "why": "This is how you find what the forest holds: search, read, timeline, navigate and memory_log, "
               "inside your own scope.",
        "command": f"forest mcp --as {agent_id} --root {root}",
        "text": "Register this command the way your own tool registers an MCP server (use the forest's full path "
                "if your tool starts servers in another folder). A tool without MCP runs the same doors as "
                f"commands: forest search, read, timeline, navigate and log, each with --as {agent_id}.",
    }]
    if recorded["conversations"] != "none":
        file = str(Path(shown_root) / TRANSCRIPTS / f"{agent_id}.jsonl")
        which = ("every conversation of yours" if recorded["conversations"] == "all"
                 else "only the conversations the owner asks you to bring in")
        since = ("from the start of what your tool keeps" if recorded["earlier"] == "yes"
                 else f"from {today} on, nothing earlier")
        steps.append({
            "step": "bring_turns",
            "why": f"The owner chose to bring in {which}, {since}. The forest grows from these turns.",
            "file": file,
            "record": {"fields": ["at", "session", "role", "text"],
                       "example": json.dumps({"at": f"{today}T09:00:00+00:00", "session": "s1", "role": "owner",
                                              "text": "Fire the blue bowls on Friday."}, ensure_ascii=False)},
            "command": INTAKE_COMMAND.format(id=agent_id, file=shlex.quote(file), root=root),
            "text": f"Write an exporter for where your own tool keeps your conversation: it appends one JSON line "
                    f"per turn ({which}, {since}) to {file}, then runs the command. Run it after each session, or "
                    "from your tool's own turn-end hook if it has one. Leave out what a tool printed, your own "
                    "memory-work sessions (they open with the line MEMORY_FOREST_INTERNAL) and anything the owner "
                    "said not to record.",
        })
        steps.append({
            "step": "own_turn",
            "why": "Where you have no transcript to export, you write your own turn.",
            "command": f"forest log --as {agent_id} --root {root}",
            "text": "Call memory_log on the MCP server, or pipe the turn's words to this command on stdin.",
        })
    if recorded["keeper"] == "this":
        steps.append({
            "step": "routine",
            "why": "The owner chose this agent to keep the forest growing.",
            "command": "forest guide routine",
            "when": recorded["when"],
            "models": {"daily": recorded["daily_model"], "filing": recorded["filing_model"],
                       "upkeep": recorded["filing_model"]},
            "text": f"Read the routine and follow it. Then set up its schedule yourself ({recorded['when']}), as "
                    "its section \"Make it run by itself\" says, with the daily record on "
                    f"{recorded['daily_model']} and filing and upkeep on {recorded['filing_model']}, and tell the "
                    "owner what you scheduled and how to turn it off.",
        })
    steps.append({
        "step": "verify",
        "why": "The doctor proves by evidence that your memory works from end to end.",
        "command": f"forest doctor --as {shlex.quote(stored)} --root {root} --json",
        "text": "Run it and follow each check's next move until it says passed, then tell the owner in five lines "
                "what was set up.",
    })
    return steps
