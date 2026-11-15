"""``forest doctor``: whether this agent's memory works from end to end, by evidence.

Each check says what it found (with times), its state (``ok``, ``todo``, ``problem`` or ``skipped``) and,
when it is not ``ok``, the next move with its command. The result is ``passed`` when no check is ``todo``
or ``problem``, ``problem`` when one is a problem, and ``todo`` otherwise. Doctor writes nothing but its own
phrase file: the proof of the turns is a phrase it makes, which the agent says in its conversation and brings
in its real way, and which a later doctor finds in the agent's lane.
"""

from __future__ import annotations

import json
import secrets
import shlex
from datetime import date, datetime, timedelta
from pathlib import PurePath as Path
from typing import Any

from . import checks, groups, journal, layout, names, registry, search, system
from . import lint as forest_lint
from . import setup_agent
from .config import Config, load_config
from .errors import MemoryForestError

# A phrase is three words of this list: letters only, so the masking of numbers and sign-in values never
# touches it, and new enough (about 260,000 phrases) that an old turn does not hold it by chance.
WORDS = (
    "amber", "anvil", "apricot", "basalt", "birch", "bramble", "cedar", "chalk", "cinder", "clover", "cobalt",
    "copper", "dune", "ember", "fennel", "fern", "flint", "garnet", "glaze", "granite", "harbor", "hazel",
    "heron", "indigo", "ivory", "juniper", "kestrel", "lantern", "larch", "linen", "loam", "maple", "marble",
    "meadow", "moss", "nettle", "ochre", "olive", "orchid", "otter", "pebble", "plover", "quarry", "quill",
    "raven", "reed", "russet", "saffron", "sage", "slate", "sorrel", "spruce", "thistle", "thyme", "tinder",
    "umber", "velvet", "walnut", "willow", "wren", "yarrow", "zephyr", "kiln", "clay",
)
PHRASE_PREFIX = "forest doctor phrase"


def _check(name: str, state: str, evidence: str, *, command: str = "", next_move: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "state": state, "evidence": evidence}
    if state in ("todo", "problem"):
        body["next"] = next_move
        if command:
            body["command"] = command
    return body


def new_phrase(root: Path, agent_id: str, *, now: datetime) -> str:
    """Write a new phrase for this agent and return it. The only file doctor writes."""
    phrase = f"{PHRASE_PREFIX} {' '.join(secrets.choice(WORDS) for _ in range(3))}"
    layout.ensure_folder(root, setup_agent.SETUP_DIR)
    from .locking import atomic_write

    atomic_write(layout.resolve(root, f"{setup_agent.SETUP_DIR}/{agent_id}.phrase"),
                 json.dumps({"phrase": phrase, "made": now.isoformat()}, ensure_ascii=False) + "\n")
    return phrase


def _phrase(root: Path, agent_id: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(system.read_text(layout.resolve(root, f"{setup_agent.SETUP_DIR}/{agent_id}.phrase")))
    except (system.Outcome, UnicodeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) and isinstance(loaded.get("phrase"), str) else None


def resolve(root: Path, config: Config, given: str) -> tuple[str | None, str]:
    """The agent ``given`` names over the whole forest (None when no agent has it) and the name to show."""
    name = names.check(given)
    rows = groups._agent_rows(root, config)
    found = names.find(name, rows)
    if len(found) != 1:
        return None, setup_agent.display(name)
    return found[0], dict(rows).get(found[0]) or found[0]


def run(root: Path, given: str, *, shown_root: str, now: datetime, phrase: bool = False) -> dict[str, Any]:
    """Every check for the agent ``given`` names. ``shown_root`` is the forest as the caller named it."""
    root_flag = f" --root {shlex.quote(shown_root)}" if shown_root else ""
    config = load_config(root)
    agent_id, stored = resolve(root, config, given)
    found: list[dict[str, Any]] = [_forest(root, config, root_flag)]
    rec = setup_agent.record(root, agent_id) if agent_id else None
    setup_command = (f"forest setup --as {shlex.quote(stored)}{root_flag} --json")
    if rec is None:
        found.append(_check("setup", "todo", f"no setup record for {stored}", next_move="run",
                            command=setup_command))
    else:
        found.append(_check("setup", "ok", f"answered at {rec.get('answered_at')}: "
                            + ", ".join(f"{key} {value}" for key, value in sorted((rec.get('answers') or {}).items())
                                        if value)))
    answers = (rec or {}).get("answers") or {}
    said = ""
    if agent_id is not None and phrase:
        said = new_phrase(root, agent_id, now=now)
    found.append(_agent(root, config, agent_id, stored, setup_command))
    found.append(_turns(root, agent_id, stored, answers, root_flag))
    found.append(_finding(root, agent_id, stored, root_flag))
    found.append(_routine(root, config, answers, now, root_flag))
    states = {item["state"] for item in found}
    result = "problem" if "problem" in states else "todo" if "todo" in states else "passed"
    body: dict[str, Any] = {"result": result, "agent": {"id": agent_id, "name": stored}, "checks": found,
                            "at": now.isoformat()}
    if said:
        body["phrase"] = said
    return body


def _forest(root: Path, config: Config, root_flag: str) -> dict[str, Any]:
    try:
        standing = journal.read(root)
        _chosen, problems = checks.run_checks(root, config, ["journal", "structure"])
    except MemoryForestError as exc:
        return _check("forest", "problem", f"{exc.code}: {exc.message}", next_move=exc.next_step or "report",
                      command=exc.command or "")
    if standing is not None:
        return _check("forest", "problem", "an unfinished commit's journal stands", next_move="run",
                      command=f"forest recover{root_flag}")
    if problems:
        return _check("forest", "problem", f"{len(problems)} structure problems, first: {problems[0].render()}",
                      next_move="run", command=f"forest check{root_flag}")
    return _check("forest", "ok", f"the forest opens; time zone {config.timezone}; no journal; structure sound")


def _agent(root: Path, config: Config, agent_id: str | None, stored: str, setup_command: str) -> dict[str, Any]:
    if agent_id is None:
        return _check("agent", "todo", f"no agent of this forest is named {stored}", next_move="run",
                      command=setup_command)
    lane = system.kind(layout.resolve(root, layout.raw_lane(agent_id))) == "folder"
    try:
        compiled = registry.build(root, config=config)
        tree = agent_id in compiled.agents
    except MemoryForestError:
        tree = False
    declared = config.agents.declared(agent_id) is not None
    words = (f"id {agent_id}; declared in forest.toml: {'yes' if declared else 'no'}; "
             f"raw lane: {'yes' if lane else 'not yet'}; tree: "
             + ("made" if tree else "pending (the next write or `forest levels apply` makes it)"))
    return _check("agent", "ok", words)


def _lane_turns(root: Path, agent_id: str, *, since: str = "") -> list[dict[str, Any]]:
    """The lane's turns, oldest day first, from the day ``since`` on."""
    folder = layout.resolve(root, layout.raw_lane(agent_id))
    try:
        entries = system.listing(folder)
    except system.Absent:
        return []
    found = []
    for entry in sorted(entries, key=lambda item: item.name):
        if entry.kind != "file" or not entry.name.endswith(".jsonl") or entry.name[:10] < since:
            continue
        for line in system.read_text(entry.path, errors="replace").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                found.append(value)
    return found


def _source(turn: dict[str, Any]) -> str:
    listed = turn.get("sources")
    if not isinstance(listed, list):
        arrivals = turn.get("arrivals")
        listed = [label for item in arrivals if isinstance(item, dict) for label in item.get("sources") or []] \
            if isinstance(arrivals, list) else []
    if listed:
        return ", ".join(str(item) for item in dict.fromkeys(listed))
    source = turn.get("source")
    return f"adapter:{source.get('adapter')}" if isinstance(source, dict) else "unknown"


def _turns(root: Path, agent_id: str | None, stored: str, answers: dict[str, Any], root_flag: str) -> dict[str, Any]:
    if answers.get("conversations") == "none":
        return _check("turns", "skipped", "the owner chose to bring in no conversations")
    if agent_id is None:
        return _check("turns", "todo", "no agent yet", next_move="run",
                      command=f"forest setup --as {shlex.quote(stored)}{root_flag} --json")
    asking = f"forest doctor --as {shlex.quote(stored)}{root_flag} --phrase"
    held = _phrase(root, agent_id)
    # A lane day is the forest's local day: start one day before the phrase's own date, whatever the zone.
    try:
        since = (date.fromisoformat(str(held.get("made"))[:10]) - timedelta(days=1)).isoformat() if held else ""
    except ValueError:
        since = ""
    turns = _lane_turns(root, agent_id, since=since)
    newest = max(turns, key=lambda turn: str(turn.get("at") or ""), default=None)
    seen = (f"newest turn at {newest.get('at')} through {_source(newest)}" if newest is not None
            else "no turn in the lane since the phrase was made" if held else "no turn in the lane yet")
    if held is None:
        return _check("turns", "todo", f"{seen}; no phrase yet", next_move="run", command=asking)
    phrase = held["phrase"]
    hits = [turn for turn in turns if phrase in str(turn.get("text") or "")]
    if not hits:
        return _check("turns", "todo",
                      f"{seen}; the phrase \"{phrase}\" (made {held.get('made')}) is not in the lane yet: say it in "
                      "your conversation, bring that turn in your real way, then run the doctor again",
                      next_move="run", command=asking.removesuffix(" --phrase"))
    first = min(hits, key=lambda turn: str(turn.get("at") or ""))
    return _check("turns", "ok", f"{seen}; the phrase \"{phrase}\" came in at {first.get('at')} through "
                  f"{_source(first)}")


def _finding(root: Path, agent_id: str | None, stored: str, root_flag: str) -> dict[str, Any]:
    build = f"forest index build{root_flag}"
    try:
        state = search.status(root)
    except MemoryForestError as exc:
        return _check("finding", "problem" if exc.next_step == "report" else "todo", f"{exc.code}: {exc.message}",
                      next_move=exc.next_step or "report", command=exc.command or "")
    if not state.get("exists") or not state.get("available", True):
        return _check("finding", "todo", "no search index yet", next_move="run", command=build)
    stale = state.get("stale") or []
    if state.get("state") != "sound" or stale:
        return _check("finding", "todo", f"index built {state.get('built_at') or 'earlier'}, state "
                      f"{state.get('state')}, {len(stale)} files changed since", next_move="run", command=build)
    if agent_id is None:
        return _check("finding", "todo", "no agent yet to search as", next_move="run",
                      command=f"forest setup --as {shlex.quote(stored)}{root_flag} --json")
    try:
        answer = search.query(root, agent_id, reader=agent_id, limit=1)
    except MemoryForestError as exc:
        return _check("finding", "problem", f"a search as {agent_id} was refused: {exc.code}: {exc.message}",
                      next_move=exc.next_step or "report", command=exc.command or "")
    searched = f"index built {state.get('built_at')}; a search as {agent_id} answers (trees: " \
               f"{', '.join(answer.get('trees') or []) or 'none'})"
    seen = groups._read_seen(root).get(agent_id)
    if not isinstance(seen, dict):
        return _check("finding", "todo", f"{searched}; never seen on a connection", next_move="run",
                      command=f"forest mcp --as {agent_id}{root_flag}")
    return _check("finding", "ok", f"{searched}; last seen on a connection at {seen.get('last') or seen.get('at')} "
                  f"({seen.get('tool')}, {seen.get('transport')})")


def _routine(root: Path, config: Config, answers: dict[str, Any], now: datetime, root_flag: str) -> dict[str, Any]:
    if answers.get("keeper") != "this":
        return _check("routine", "skipped", "this agent does not keep the forest")
    days: set[str] = set()
    try:
        lanes = system.listing(layout.resolve(root, layout.ISTM))
    except system.Absent:
        lanes = []
    for lane in lanes:
        if lane.kind == "folder":
            days.update(entry.name[:10] for entry in system.listing(lane.path)
                        if entry.kind == "file" and entry.name.endswith(".jsonl"))
    try:
        pages = {entry.name[:10] for entry in system.listing(layout.resolve(root, layout.DAILY))
                 if entry.kind == "file" and entry.name.endswith(".md")}
    except system.Absent:
        pages = set()
    today = now.astimezone(config.zone).date().isoformat()
    report = forest_lint.lint(root, config=config)
    counts = f"lint: {len(report.violations)} violations, {len(report.upkeep)} upkeep, {len(report.due)} due"
    newest = f"newest day of turns {max(days) if days else 'none'}, newest daily page {max(pages) if pages else 'none'}"
    behind = sorted(day for day in days - pages if day < today)
    if behind:
        return _check("routine", "todo", f"{newest}; {len(behind)} days of turns have no daily page "
                      f"({behind[0]} to {behind[-1]}); {counts}", next_move="run", command="forest guide routine")
    return _check("routine", "ok", f"{newest}; every day before today has its daily page; {counts}")
