"""Levels set by code from distinct days (contract, section 3, Levels; design, section 4).

A subject's one measure is its **distinct days**: the local dates on which its
own material records something. Each ``## Timeline`` line counts on the date
it is written with (the local date in the zone the line carries, or in
``forest.timezone`` when it names none); a leaf with no Timeline counts its
``date:``; a collection entry counts the lines of its page's Timeline that open
with its name. Three kinds of line do not count: a reference line, a
superseded line (or any line of a superseded leaf), and a line about an
agent's or a tool's own work: one that opens with an agent's id or name, or a
stage name (``levels.stage_names``), followed by a work verb
(``levels.work_verbs``), or with a tool name (``levels.tool_names``) followed
by ``alerted``.

The levels, with the ``levels.*`` settings: an entry is admitted to a leaf at
``leaf_days`` days, with a ``standing`` line, or when its own material is over
the leaf cap less ``limits.entry_admit_margin_bytes``; a leaf subject gets its
branch at ``branch_days``; a branch subject of a type in ``tree_types``
becomes a tree when ``tree_subtopics`` of its sub-topics each span
``tree_subtopic_days`` days and hold ``tree_subtopic_leaves`` leaves, a
sub-topic made only of ``stream_kinds`` leaves not counting. An agent is a
tree from its first day. No level goes down by count.

``due`` lists each change; ``apply`` carries each out through the collections
tool (an admission) or the move tool (a promotion), each change one checked
and written plan, and logs it with the subject, its old and new level and the
days and leaves that set it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Final

from . import agents, collections, layout, move, registry
from .errors import MemoryForestError
from .locking import LockToken, writer_lock
from .model import TimelineLine, parse_page
from .plan import Plan, SubjectRef, encode, run
from .safety import require_real_root

ACTIONS: Final[tuple[str, ...]] = ("agent", "admit", "promote")
NO_LEVEL: Final[str] = "none"
_REFERENCE_MARK: Final[str] = ": recorded in `"


class DayCounter:
    """Counts distinct days on a planned forest, with the agents' names it must recognise."""

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        settings = plan.config.levels
        names: list[str] = []
        for ref in plan.subjects():
            if ref.type == registry.AGENT_TYPE:
                names.extend((ref.id, ref.name, *ref.line.aliases))
        names.extend(settings.stage_names)
        verbs = "|".join(re.escape(verb) for verb in settings.work_verbs)
        actors = "|".join(re.escape(name) for name in sorted(set(names), key=len, reverse=True) if name)
        tools = "|".join(re.escape(name) for name in settings.tool_names if name)
        patterns = []
        if actors and verbs:
            patterns.append(rf"(?:{actors})\s+(?:{verbs})\b")
        if tools:
            patterns.append(rf"(?:{tools})\s+alerted\b")
        self._work = re.compile("|".join(patterns), re.IGNORECASE) if patterns else None

    def is_work_line(self, text: str) -> bool:
        """Whether a line's text records the system's own work rather than the subject's."""
        return self._work is not None and self._work.match(text.lstrip()) is not None

    def line_day(self, line: str, *, prefix: str | None = None) -> str | None:
        """The day one Timeline line counts for, or None when it does not count."""
        parsed = TimelineLine.match(line)
        if parsed is None or parsed.is_superseded or not parsed.day or parsed.indent:
            return None
        text = parsed.text
        if prefix is not None:
            if not text.startswith(f"{prefix}:"):
                return None
            text = text[len(prefix) + 1 :]
        if _REFERENCE_MARK in text or self.is_work_line(text):
            return None
        return parsed.day

    def leaf_days(self, text: str) -> set[str]:
        page = parse_page(text)
        if page.field("status") == "superseded":
            return set()
        timeline = page.section("Timeline")
        if timeline is None:
            stated = page.field("date")
            return {stated} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stated) else set()
        return {day for _index, line in timeline.items() if (day := self.line_day(line)) is not None}

    def entry_days(self, text: str, name: str) -> set[str]:
        page = parse_page(text)
        if page.field("status") == "superseded":
            return set()
        timeline = page.section("Timeline")
        if timeline is None:
            return set()
        return {day for _index, line in timeline.items() if (day := self.line_day(line, prefix=name)) is not None}


@dataclass(slots=True)
class Subtopic:
    leaves: list[str] = field(default_factory=list)
    days: set[str] = field(default_factory=set)
    stream: bool = True


@dataclass(slots=True)
class Measure:
    """What sets a subject's level: its days, leaves, sub-topics and standing."""

    subject: str
    level: str
    type: str
    home: str
    days: set[str]
    leaves: list[str]
    subtopics: dict[str, Subtopic]
    standing: bool
    bytes: int = 0
    page: str | None = None

    def counting_subtopics(self, settings: Any) -> list[str]:
        return sorted(
            name for name, group in self.subtopics.items()
            if not group.stream
            and len(group.leaves) >= settings.tree_subtopic_leaves
            and len(group.days) >= settings.tree_subtopic_days
        )


@dataclass(slots=True)
class Due:
    """One level change the rule wants now."""

    subject: str
    action: str
    level: str
    new_level: str
    home: str
    days: list[str]
    leaves: int
    why: str
    subtopics: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "code": "level_due",
            "days": self.days,
            "home": self.home,
            "leaves": self.leaves,
            "level": self.level,
            "new_level": self.new_level,
            "subject": self.subject,
            "subtopics": self.subtopics,
            "why": self.why,
        }

    def render(self) -> str:
        return f"{self.home}: level_due: {self.subject} {self.level} -> {self.new_level}: {self.why}"


def _is_stream(settings: Any, kind: str, subtopic: str) -> bool:
    if kind:
        return kind in settings.stream_kinds
    parts = set(subtopic.split("-"))
    words = set(settings.stream_subtopic_words)
    return bool(parts & words) or subtopic in words


def measure(plan: Plan, ref: SubjectRef, counter: DayCounter | None = None) -> Measure:
    """A registered subject's measure, read from its leaves in the planned forest."""
    counter = counter or DayCounter(plan)
    settings = plan.config.levels
    leaves = sorted(set(plan.leaves_of(ref.id)) | ({ref.home} if layout.page_role(ref.home) is layout.PageRole.LEAF
                                                     and plan.exists(ref.home) else set()))
    days: set[str] = set()
    subtopics: dict[str, Subtopic] = {}
    standing = False
    size = 0
    for path in leaves:
        text = plan.read(path)
        page = parse_page(text)
        size += len(encode(text))
        leaf_days = counter.leaf_days(text)
        days |= leaf_days
        standing = standing or bool(page.field("standing"))
        subtopic = page.field("subtopic") or layout.split_period(PurePosixPath(path).stem)[0]
        group = subtopics.setdefault(subtopic, Subtopic())
        group.leaves.append(path)
        group.days |= leaf_days
        group.stream = group.stream and _is_stream(settings, page.field("kind"), subtopic)
    return Measure(ref.id, ref.level, ref.type, ref.home, days, leaves, subtopics, standing, size)


def _span(days: Iterable[str]) -> str:
    ordered = sorted(days)
    if not ordered:
        return "no counted day"
    if len(ordered) == 1:
        return f"1 distinct day ({ordered[0]})"
    return f"{len(ordered)} distinct days ({ordered[0]} to {ordered[-1]})"


def agent_subjects(plan: Plan) -> agents.Subjects:
    """Each registered id's type and the area (shared) trees of the planned forest."""
    refs = plan.subjects()
    agent_pages = {ref.home for ref in refs if ref.type == registry.AGENT_TYPE}
    areas = frozenset(tree for tree in plan.trees() if layout.tree_page(tree) not in agent_pages)
    return agents.Subjects({ref.id: ref.type for ref in refs}, areas)


def due(plan: Plan, subjects: Iterable[str] | None = None) -> list[Due]:
    """Every level change due in the planned forest (only for ``subjects`` when given)."""
    wanted = set(subjects) if subjects is not None else None
    settings = plan.config.levels
    counter = DayCounter(plan)
    found: list[Due] = []
    registered = {ref.id: ref for ref in plan.subjects()}
    for subject_id, ref in sorted(registered.items()):
        if wanted is not None and subject_id not in wanted:
            continue
        if ref.type == registry.COLLECTION_TYPE or ref.level == "tree":
            continue
        current = measure(plan, ref, counter)
        leaves = len(current.leaves)
        if ref.type == registry.AGENT_TYPE:
            found.append(Due(subject_id, "promote", ref.level, "branch" if ref.level == "leaf" else "tree", ref.home,
                             sorted(current.days), leaves, f"an agent is a tree from its first day; {leaves} leaves"))
            continue
        if ref.level == "leaf" and len(current.days) >= settings.branch_days:
            found.append(Due(subject_id, "promote", "leaf", "branch", ref.home, sorted(current.days), leaves,
                             f"{_span(current.days)} in {leaves} leaves (a branch from {settings.branch_days})"))
        elif ref.level == "branch" and ref.type in settings.tree_types:
            counting = current.counting_subtopics(settings)
            if len(counting) >= settings.tree_subtopics:
                found.append(Due(subject_id, "promote", "branch", "tree", ref.home, sorted(current.days), leaves,
                                 f"{len(counting)} sub-topics of {settings.tree_subtopic_leaves} leaves over "
                                 f"{settings.tree_subtopic_days} days ({', '.join(counting)}); {_span(current.days)}",
                                 counting))
    limit = plan.config.limits.entry_max_bytes
    for page, entry in plan.entries():
        if wanted is not None and entry.id not in wanted:
            continue
        if entry.id in registered:
            continue
        text = plan.read(page)
        days = counter.entry_days(text, entry.name)
        size = next((collections.material_bytes(item) for item in collections.entries_of(text) if item.id == entry.id),
                    0)
        reasons = []
        if len(days) >= settings.leaf_days:
            reasons.append(f"{_span(days)} (a leaf from {settings.leaf_days})")
        if entry.standing:
            reasons.append("a standing line")
        if size > limit:
            reasons.append(f"{size} bytes of its own material, more than a collection keeps ({limit})")
        if reasons:
            found.append(Due(entry.id, "admit", "entry", "leaf", f"{page}#{entry.id}", sorted(days), 0,
                             "; ".join(reasons)))
    for item in agents.due(plan.root, plan.config, agent_subjects(plan)):
        if wanted is not None and item.id not in wanted:
            continue
        found.append(Due(item.id, "agent", NO_LEVEL, "tree", item.source, [], 0,
                         f"an agent is a tree from its first day ({'declared in the settings' if item.source == agents.CONFIG_PATH else 'its raw lane holds a file'})"))
    order = {action: index for index, action in enumerate(ACTIONS)}
    found.sort(key=lambda item: (order[item.action], item.subject))
    return found


def check(root: str | os.PathLike[str], subjects: Iterable[str] | None = None) -> list[Due]:
    """``forest levels check``: every level change due, nothing written."""
    return due(Plan.open(root), subjects)


def carry_out(plan: Plan, item: Due) -> str:
    """Plan one due change: an admission through the collections tool, a promotion through the move tool."""
    previous = plan.why
    plan.why = f"level {item.level} -> {item.new_level}: {item.why}"
    try:
        if item.action == "admit":
            return collections.admit(plan, item.subject)
        if item.action == "agent":
            known = plan.config.agents.declared(item.subject)
            made = move.ensure_agent(plan, item.subject, name=known.name if known else None,
                                     role=known.role if known else None)
            return "tree" if made else "unchanged"
        return move.promote(plan, item.subject)
    finally:
        plan.why = previous


def apply(
    root: str | os.PathLike[str],
    subjects: Iterable[str] | None = None,
    *,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """``forest levels apply``: carry out every due change, each its own checked plan, until none is due.

    A subject that is due again after a change (an agent that is a leaf, a
    subject past two thresholds) goes on in the same run. A change the tools
    refuse is reported with its error and the others go on. With ``dry_run``
    every change is planned into one plan and reported.
    """
    safe_root = require_real_root(root)
    wanted = list(subjects) if subjects is not None else None
    if dry_run:
        plan = Plan.open(safe_root, today=today)
        done: list[dict[str, Any]] = []
        for _round in range(4):
            items = due(plan, wanted)
            if not items:
                break
            for item in items:
                carry_out(plan, item)
                done.append(item.as_dict())
        plan.verify()
        report = plan.report()
        report.update({"changes": done, "refused": [], "written": False})
        return report
    applied: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    with writer_lock(safe_root, token=lock, label="forest levels apply") as held:
        failed: set[str] = set()
        for _round in range(4):
            items = [item for item in due(Plan.open(safe_root, today=today), wanted) if item.subject not in failed]
            if not items:
                break
            for item in items:
                try:
                    result = run(safe_root, lambda plan, item=item: carry_out(plan, item), lock=held, today=today)
                except MemoryForestError as exc:
                    failed.add(item.subject)
                    refused.append({**item.as_dict(), "error": exc.as_dict()})
                    continue
                applied.append({**item.as_dict(), "moves": len(result["moves"]), "result": result.get("result")})
    return {"changes": applied, "refused": refused, "written": True}


# ─────────────────────────────── the lint code ───────────────────────────────


def due_problems(plan: Plan) -> list[registry.Problem]:
    """``level_due``: every level change due, as problem lines (``forest check`` and the lint share them)."""
    return [
        registry.Problem("level_due", item.home, None,
                         f"{item.subject} {item.level} -> {item.new_level}: {item.why} (`forest levels apply`)")
        for item in due(plan)
    ]


def _lint_level_due(view: Any) -> list[Any]:
    from . import lint

    return lint.hits_from(due_problems(Plan.open(view.root, config=view.config)))


def _register_lint() -> None:
    from . import lint

    lint.register(lint.LintCode("level_due", lint.DUE, "`forest levels apply` carries the level out", _lint_level_due))


_register_lint()
