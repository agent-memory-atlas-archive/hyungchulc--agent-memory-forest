"""``forest guide``, ``forest setup`` and ``forest doctor``: an agent connects itself.

The package holds no code that knows an agent program and writes nothing into another program's settings.
The guide says what to do, setup asks the owner what only the owner decides and says what the agent does in
its own tool, and the doctor proves the result.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import PurePath as Path
from typing import Any

from . import doctor, setup_agent, system
from .cli import ForestCommand, _print_json, _print_lines, arg, forest_root
from .errors import MemoryForestError

GUIDE_DIR = Path(__file__).parent / "guide"
# The three parts an agent walks through, and the contract they point to: the forest's rules, the same text
# as docs/contract.md (a test keeps the two files equal), so the keeper reads them without the repository.
WALK_PARTS = ("setup", "routine", "use")
GUIDE_PARTS = (*WALK_PARTS, "contract")


def _as_agent_door(arguments: argparse.Namespace) -> None:
    """A refusal of these commands is read by an agent: it is folded like the doors' (no host, user or home)."""
    arguments.reader = "agent"


def guide_text(part: str) -> str:
    return system.read_text(GUIDE_DIR / f"{part}.md")


def cmd_guide(arguments: argparse.Namespace) -> int:
    _as_agent_door(arguments)
    part = arguments.part or "setup"
    if part not in GUIDE_PARTS:
        raise MemoryForestError("arguments_invalid", "The guide's parts are setup, routine, use and contract.",
                                details={"parts": list(GUIDE_PARTS)}, next_step="fix")
    text = guide_text(part)
    if arguments.json:
        _print_json({"part": part, "text": text})
    else:
        sys.stdout.write(text)
    return 0


def cmd_setup(arguments: argparse.Namespace) -> int:
    _as_agent_door(arguments)
    given = {"root": getattr(arguments, "root", None), "name": arguments.name, "main": arguments.main,
             "conversations": arguments.conversations, "earlier": arguments.earlier, "keeper": arguments.keeper,
             "when": arguments.when, "daily_model": arguments.daily_model, "filing_model": arguments.filing_model}
    result = setup_agent.run(given, timezone=arguments.timezone, dry_run=arguments.dry_run,
                             now=datetime.now(timezone.utc))
    if arguments.json:
        _print_json(result)
    else:
        _print_lines(_setup_lines(result))
    return 1 if result.get("questions") else 0  # ask_owner: not finished


def _setup_lines(result: dict[str, Any]) -> list[str]:
    lines = [f"next: {result['next']}"]
    if result["next"] == "ask_owner":
        for question in result["questions"]:
            choices = f" ({' | '.join(question['choices'])})" if question.get("choices") else ""
            lines += [f"ask the owner, then pass {question['flag']}{choices}: {question['question']}",
                      f"  why the owner: {question['why_owner']}"]
        for key, proposal in result["proposals"].items():
            lines.append(f"proposed {key}: {proposal['value']} (change it with {proposal['flag']})")
        return lines
    lines.append(f"agent: {result['agent']['name']} (id {result['agent']['id']})")
    verb = "would write" if result["dry_run"] else "wrote"
    lines += [f"{verb}: {item}" for item in result["changed"]] or [f"{verb}: nothing (already set up so)"]
    for number, step in enumerate(result["todo"], 1):
        lines.append(f"todo {number}. {step['step']}: {step['why']}")
        if step.get("command"):
            lines.append(f"  command: {step['command']}")
        if step.get("text"):
            lines.append(f"  {step['text']}")
    return lines


def cmd_doctor(arguments: argparse.Namespace) -> int:
    _as_agent_door(arguments)
    root = forest_root(arguments)
    result = doctor.run(root, arguments.name, shown_root=getattr(arguments, "root", None) or "",
                        now=datetime.now(timezone.utc), phrase=arguments.phrase, doors=arguments.doors)
    if arguments.json:
        _print_json(result)
    else:
        lines = [f"result: {result['result']}"]
        if result.get("phrase"):
            lines.append(f"phrase: {result['phrase']}  (say it in your conversation, bring that turn in, run doctor)")
        for item in result["checks"]:
            lines.append(f"{item['name']}: {item['state']}: {item['evidence']}")
            if item.get("next"):
                lines.append(f"  next: {item['next']}" + (f"  command: {item['command']}" if item.get("command") else ""))
        _print_lines(lines)
    return 0 if result["result"] == "passed" else 1


_ANSWER = "the owner's answer; setup never fills it in"
COMMANDS: list[ForestCommand] = [
    ForestCommand(
        ("guide",), cmd_guide,
        "Print the guide an agent follows: setup (the default), routine or use, or the contract they point "
        "to. It ships with the package.",
        (arg("part", nargs="?", default=None, help="setup, routine, use or contract"),), uses_root=False,
    ),
    ForestCommand(
        ("setup",), cmd_setup,
        "Run by an agent for its owner: ask what only the owner decides, write this agent into the forest, and "
        "say what the agent does in its own tool. Follow `next`.",
        (
            arg("--root", default=argparse.SUPPRESS, help="where the forest lives: a new or empty folder, or an "
                "existing forest to join (" + _ANSWER + ")"),
            arg("--as", dest="name", default=None, help="this agent's name, in any script (" + _ANSWER + ")"),
            arg("--main", default=None, help="yes or no: this agent is the owner's main agent (" + _ANSWER + ")"),
            arg("--conversations", default=None, help="all, asked or none (" + _ANSWER + ")"),
            arg("--earlier", default=None, help="yes or no: conversations from before today too (" + _ANSWER + ")"),
            arg("--keeper", default=None, help="this, other or nobody: who keeps the forest (" + _ANSWER + ")"),
            arg("--when", default=None, help="when the keeper runs the routine, free text (" + _ANSWER + ")"),
            arg("--daily-model", default=None, help="the model the daily record may use (" + _ANSWER + ")"),
            arg("--filing-model", default=None, help="the model filing and upkeep may use (" + _ANSWER + ")"),
            arg("--timezone", default=None, help="a new forest's time zone; proposed from the system"),
            arg("--dry-run", action="store_true", help="print each file and block it would write, and write nothing"),
        ),
        uses_root=False,
        epilog="A refusal names its next move in next: fix, retry, run or report. docs/refusals.md lists every "
               "code, its move and when it is given.",
    ),
    ForestCommand(
        ("doctor",), cmd_doctor,
        "Prove by evidence that this agent's memory works from end to end. Exit 0 only when it passed.",
        (
            arg("--as", dest="name", required=True, help="this agent's name or id"),
            arg("--phrase", action="store_true", help="write and print a new phrase to say and bring in"),
            arg("--doors", choices=doctor.DOORS, default=doctor.DOORS[0],
                help="how this agent's tool reaches the forest: mcp (the default; the check wants a connection "
                     "it has seen) or commands (a tool without MCP; the check is the search doctor runs)"),
        ),
        epilog="A refusal names its next move in next: fix, retry, run or report. docs/refusals.md lists every "
               "code, its move and when it is given.",
    ),
]
