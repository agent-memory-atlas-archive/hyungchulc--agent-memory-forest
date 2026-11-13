"""Time phrases and ``forest timeline`` (design, section 9).

A phrase is a row in a language table. English is built in. Another language
is a JSON file under ``memory_forest/phrases/``; Korean ships as the example.
A forest adds or replaces a table with a JSON file in ``.forest/phrases/``,
without editing the installed package. The package tables are the default.
The matcher only knows the table's slots (a number, a unit, a day part, a
date). It has no sentences of its own.

A day-part phrase is judged in each entry's own time zone, so "yesterday
evening" is that entry's local evening. A duration is one window of instants.
A raw-lane turn is included whether or not a daily block or a leaf has filed
it yet.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import layout
from .config import Config, load_config
from .errors import MemoryForestError
from .model import TimelineLine
from .safety import require_real_root
from .scope import for_reader
from . import registry as registry_mod
from . import search


_PHRASE_DIR = Path(__file__).resolve().parent / "phrases"
_RANGE_RE = re.compile(
    r"^(?P<a>\d{4}-\d{2}-\d{2})(?:[T ](?P<a_time>\d{2}:\d{2}))?"
    r"(?:\.\.(?P<b>\d{4}-\d{2}-\d{2})(?:[T ](?P<b_time>\d{2}:\d{2}))?)?$"
)
_SLOT_RE = re.compile(r"\{[a-z0-9]+\}")

# English is data, not a branch in the matcher. The example file is Korean.
ENGLISH: dict[str, Any] = {
    "language": "en",
    "fold": True,
    "dayparts": {
        "morning": [5, 12],
        "afternoon": [12, 17],
        "evening": [17, 22],
        "night": [22, 5],
    },
    "units": {
        "minute": 60,
        "minutes": 60,
        "hour": 3600,
        "hours": 3600,
        "day": 86400,
        "days": 86400,
        "week": 604800,
        "weeks": 604800,
    },
    "numbers": {
        "twelve": 12,
        "eleven": 11,
        "ten": 10,
        "nine": 9,
        "eight": 8,
        "seven": 7,
        "six": 6,
        "five": 5,
        "four": 4,
        "three": 3,
        "two": 2,
        "one": 1,
        "a": 1,
        "an": 1,
    },
    "phrases": [
        {"pattern": "yesterday {daypart}", "use": "day", "day_shift": -1},
        {"pattern": "yesterday", "use": "day", "day_shift": -1},
        {"pattern": "today {daypart}", "use": "day", "day_shift": 0},
        {"pattern": "today", "use": "day", "day_shift": 0},
        {"pattern": "tomorrow {daypart}", "use": "day", "day_shift": 1},
        {"pattern": "tomorrow", "use": "day", "day_shift": 1},
        {"pattern": "this {daypart}", "use": "day", "day_shift": 0},
        {"pattern": "the last {n} {unit}", "use": "back"},
        {"pattern": "last {n} {unit}", "use": "back"},
        {"pattern": "from {date} to {date}", "use": "dates"},
        {"pattern": "{date} to {date}", "use": "dates"},
        {"pattern": "{date}", "use": "dates"},
    ],
}


@dataclass(frozen=True, slots=True)
class When:
    """A resolved phrase: an absolute window, or a local day in each entry's zone."""

    kind: str
    label: str
    start: datetime | None = None
    end: datetime | None = None
    day_shift: int = 0
    daypart: tuple[int, int] | None = None


def languages(root: str | os.PathLike[str] | None = None) -> list[str]:
    """Language tags from the package, plus any table in the forest's ``.forest/phrases``."""
    return [str(table["language"]) for table in _tables(root)]


def query(
    root: str | os.PathLike[str],
    phrase: str,
    *,
    reader: str | None = None,
    named: Iterable[str] = (),
    now: datetime | None = None,
    limit: int = 50,
    config: Config | None = None,
) -> dict[str, Any]:
    """What falls in ``phrase`` across the raw lanes, the daily pages and the forest."""
    safe = require_real_root(root)
    config = config or load_config(safe)
    stale = search.refresh_lanes_and_daily(safe)
    moment = _aware(now or datetime.now(config.zone), config.zone)
    when = resolve(phrase, config, moment, root=safe)
    payload = _collect(safe, config, reader, named, when, limit=limit, phrase=phrase.strip())
    if stale:
        payload["stale"] = stale
    return payload


def subject(
    root: str | os.PathLike[str],
    subject_id: str,
    *,
    reader: str | None = None,
    named: Iterable[str] = (),
    limit: int = 50,
    config: Config | None = None,
) -> dict[str, Any]:
    """The indexed sections whose subjects include ``subject_id``, in time order."""
    safe = require_real_root(root)
    config = config or load_config(safe)
    stale = search.refresh_lanes_and_daily(safe)
    built = registry_mod.build(safe, config=config)
    scope = for_reader(config, built, reader, named=named, root=safe)
    def read(connection: sqlite3.Connection | None) -> dict[str, Any]:
        if connection is None:
            raise MemoryForestError(
                "index_missing",
                "The forest has no search index. Run `forest index build`.",
                details={"path": search.index_relative(safe)},
                next_step="run",
                command="forest index build",
            )
        try:
            earliest = {
                int(row[0]): row[1]
                for row in connection.execute("SELECT unit_id, MIN(start_utc) FROM times GROUP BY unit_id")
            }
            pages: dict[str, dict[str, Any]] = {}
            page_stamp: dict[str, str] = {}
            for row in connection.execute("SELECT * FROM units"):
                unit = search._row_dict(row)
                if subject_id not in unit["subjects"] or not search._visible(scope, unit["path"]):
                    continue
                stamp = earliest.get(int(row["id"])) or "9999"
                current = page_stamp.get(unit["path"])
                if current is None or stamp < current:
                    page_stamp[unit["path"]] = stamp
                search._add_row(
                    pages, unit, f"subject: {subject_id}", tier=0, score=0, scope=scope,
                )
            ranked = sorted(pages.values(), key=lambda item: (page_stamp.get(item["path"], "9999"), item["path"]))
            payload = search._envelope(scope, ranked[:limit])
            payload["subject"] = subject_id
            if stale:
                payload["stale"] = stale
            if id(connection) in search._IMMUTABLE_IDS:
                payload["read_only"] = True
            return payload
        except sqlite3.Error as exc:
            raise search.finish_read(exc, path=search.index_relative(safe)) from exc

    return search._checked_read(safe, read)


def resolve(
    phrase: str,
    config: Config,
    now: datetime,
    *,
    root: str | os.PathLike[str] | None = None,
) -> When:
    """The window ``phrase`` names, from the tables and the ``..`` range syntax."""
    text = " ".join(phrase.strip().split())
    if not text:
        raise MemoryForestError("time_phrase_empty", "The time phrase is empty.")
    ranged = _RANGE_RE.fullmatch(text)
    if ranged is not None:
        return _range_when(ranged, config.zone, text)
    tables = _tables(root)
    best: tuple[int, str, re.Match[str], dict[str, Any]] | None = None
    for table in tables:
        fold = bool(table.get("fold"))
        haystack = text.casefold() if fold else text
        for row in table["phrases"]:
            pattern = row["pattern"]
            expression = _compile(pattern, table, fold)
            matched = expression.fullmatch(haystack if fold else text)
            if matched is None:
                continue
            weight = len(pattern) + (2 if table["language"] == config.language else 0)
            if best is None or weight > best[0]:
                best = (weight, table["language"], matched, {**row, "table": table})
    if best is None:
        known = ", ".join(languages(root))
        raise MemoryForestError(
            "time_phrase_unknown",
            f"No time phrase matches {text!r}. The tables are: {known}. "
            "A range is written YYYY-MM-DD..YYYY-MM-DD.",
            details={"languages": known},
        )
    _weight, _language, matched, row = best
    return _from_row(row, matched, config.zone, now, text)


def _collect(
    root: Path,
    config: Config,
    reader: str | None,
    named: Iterable[str],
    when: When,
    *,
    limit: int,
    phrase: str,
) -> dict[str, Any]:
    built = registry_mod.build(root, config=config)
    scope = for_reader(config, built, reader, named=named, root=root)
    def read(connection: sqlite3.Connection | None) -> dict[str, Any]:
        if connection is None:
            raise MemoryForestError(
                "index_missing",
                "The forest has no search index. Run `forest index build`.",
                details={"path": search.index_relative(root)},
                next_step="run",
                command="forest index build",
            )
        try:
            pages: dict[str, dict[str, Any]] = {}
            stamps: dict[str, str] = {}
            rows = connection.execute(
                "SELECT units.*, times.start_utc, times.end_utc, times.zone AS time_zone "
                "FROM times JOIN units ON units.id = times.unit_id"
            ).fetchall()
            for row in rows:
                start = search._parse_stamp(row["start_utc"])
                end = search._parse_stamp(row["end_utc"])
                if not _in_when(start, end, row["time_zone"] or "UTC", when, config.zone):
                    continue
                unit = search._row_dict(row)
                if not search._visible(scope, unit["path"]):
                    continue
                page_zone = _page_zone(row["page_zone"] if "page_zone" in row.keys() else "", config)
                matched_lines = _matching_lines(unit["text"], when, config, page_zone)
                if matched_lines:
                    unit = {**unit, "text": "\n".join(matched_lines)}
                why = f"time: {row['start_utc']} falls in {phrase}"
                search._add_row(pages, unit, why, tier=0, score=0, scope=scope)
                stamps.setdefault(unit["path"], row["start_utc"])
                if row["start_utc"] < stamps[unit["path"]]:
                    stamps[unit["path"]] = row["start_utc"]
            ordered = sorted(pages.values(), key=lambda item: stamps.get(item["path"], ""))[:limit]
            payload = search._envelope(scope, ordered)
            payload["phrase"] = phrase
            payload["when"] = _when_dict(when)
            if id(connection) in search._IMMUTABLE_IDS:
                payload["read_only"] = True
            return payload
        except sqlite3.Error as exc:
            raise search.finish_read(exc, path=search.index_relative(root)) from exc

    return search._checked_read(root, read)


def _matching_lines(
    text: str, when: When, config: Config, page_zone: ZoneInfo | timezone
) -> list[str]:
    """Timeline lines in ``text`` whose own zone falls in ``when``. Empty when the text has none."""
    found = []
    for line in text.splitlines():
        parsed = TimelineLine.match(line)
        if parsed is None:
            continue
        interval = search._timeline_interval(parsed, page_zone, config.zone)
        if interval is None:
            continue
        start, end, zone_name = interval
        if _in_when(start, end, zone_name, when, config.zone):
            found.append(line)
    return found


def _in_when(start: datetime, end: datetime, zone_name: str, when: When, forest_zone: ZoneInfo) -> bool:
    if when.kind == "absolute":
        assert when.start is not None and when.end is not None
        return start < when.end and end > when.start
    zone = _zone_from(zone_name, forest_zone)
    window_start, window_end = _local_window(when, zone)
    return start < window_end and end > window_start


def _local_window(when: When, zone: ZoneInfo | timezone) -> tuple[datetime, datetime]:
    """The local window stored on ``when`` was computed for a reference ``now``.

    A local phrase keeps the day shift and day part, and the reference instant,
    so each entry can place "yesterday" on its own calendar. The reference is
    ``when.start`` only as a carrier of ``now`` for kind ``local``: see
    ``_from_row``.
    """
    assert when.start is not None
    local_now = when.start.astimezone(zone)
    day = local_now.date() + timedelta(days=when.day_shift)
    if when.daypart is None:
        start = datetime(day.year, day.month, day.day, tzinfo=zone)
        return start, start + timedelta(days=1)
    start_hour, end_hour = when.daypart
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=zone)
    if end_hour <= start_hour:
        next_day = day + timedelta(days=1)
        stop = datetime(next_day.year, next_day.month, next_day.day, end_hour, tzinfo=zone)
    else:
        stop = datetime(day.year, day.month, day.day, end_hour, tzinfo=zone)
    return start, stop


def _from_row(
    row: dict[str, Any], matched: re.Match[str], zone: ZoneInfo, now: datetime, label: str
) -> When:
    use = row.get("use")
    table = row["table"]
    if use == "back":
        number = _number(matched.group("n"), table) if "n" in matched.groupdict() and matched.group("n") else 1
        seconds = number * int(table["units"][_key(matched.group("unit"), table["units"])])
        return When("absolute", label, now - timedelta(seconds=seconds), now)
    if use == "dates":
        groups = matched.groupdict()
        first = groups.get("date1") or ""
        second = groups.get("date2") or first
        start = _forest_day(first, zone, groups.get("time1"), end=False)
        stop = _forest_day(second, zone, groups.get("time2"), end=True)
        if stop <= start:
            raise MemoryForestError("time_phrase_unknown", "That date range ends before it starts.")
        return When("absolute", label, start, stop)
    if use == "day":
        daypart = None
        if matched.groupdict().get("daypart"):
            bounds = table["dayparts"][_key(matched.group("daypart"), table["dayparts"])]
            daypart = (int(bounds[0]), int(bounds[1]))
        return When("local", label, now, None, int(row.get("day_shift") or 0), daypart)
    raise MemoryForestError("time_phrase_unknown", "A phrase row names a use this matcher does not know.")


def _range_when(matched: re.Match[str], zone: ZoneInfo, label: str) -> When:
    start = _forest_day(matched["a"], zone, matched["a_time"], end=False)
    stop_day = matched["b"] or matched["a"]
    stop = _forest_day(stop_day, zone, matched["b_time"], end=matched["b_time"] is None and matched["a_time"] is None)
    if matched["a_time"] and not matched["b"]:
        hour, minute = (int(part) for part in matched["a_time"].split(":"))
        start = datetime(start.year, start.month, start.day, hour, minute, tzinfo=zone)
        stop = start + timedelta(microseconds=1)
    if stop <= start:
        raise MemoryForestError("time_phrase_unknown", "That date range ends before it starts.")
    return When("absolute", label, start, stop)


def _forest_day(day: str, zone: ZoneInfo, clock: str | None, *, end: bool) -> datetime:
    year, month, date = (int(part) for part in day.split("-"))
    try:
        if clock:
            hour, minute = (int(part) for part in clock.split(":"))
            if hour > 23 or minute > 59:
                raise ValueError
            moment = datetime(year, month, date, hour, minute, tzinfo=zone)
            return moment + timedelta(microseconds=1) if end else moment
        start = datetime(year, month, date, tzinfo=zone)
    except ValueError as exc:
        raise MemoryForestError("time_phrase_unknown", "That date or time does not exist.") from exc
    return start + timedelta(days=1) if end else start


def _number(text: str, table: dict[str, Any]) -> int:
    if text.isdigit():
        return int(text)
    return int(table["numbers"][_key(text, table["numbers"])])


def _key(text: str, table: dict[str, Any]) -> str:
    if text in table:
        return text
    folded = text.casefold()
    for key in table:
        if key.casefold() == folded:
            return key
    return text


def _compile(pattern: str, table: dict[str, Any], fold: bool) -> re.Pattern[str]:
    parts = _SLOT_RE.split(pattern)
    slots = _SLOT_RE.findall(pattern)
    date_index = 0
    time_index = 0
    pieces = []
    for index, part in enumerate(parts):
        pieces.append(re.escape(part.casefold() if fold else part))
        if index >= len(slots):
            continue
        slot = slots[index]
        if slot == "{n}":
            words = sorted(table.get("numbers", {}), key=len, reverse=True)
            alt = "|".join(re.escape(word.casefold() if fold else word) for word in words)
            pieces.append(rf"(?P<n>{alt}|\d+)" if alt else r"(?P<n>\d+)")
        elif slot == "{unit}":
            words = sorted(table["units"], key=len, reverse=True)
            alt = "|".join(re.escape(word.casefold() if fold else word) for word in words)
            pieces.append(rf"(?P<unit>{alt})")
        elif slot == "{daypart}":
            words = sorted(table["dayparts"], key=len, reverse=True)
            alt = "|".join(re.escape(word.casefold() if fold else word) for word in words)
            pieces.append(rf"(?P<daypart>{alt})")
        elif slot == "{date}":
            date_index += 1
            pieces.append(rf"(?P<date{date_index}>\d{{4}}-\d{{2}}-\d{{2}})")
        elif slot == "{time}":
            time_index += 1
            pieces.append(rf"(?P<time{time_index}>\d{{2}}:\d{{2}})")
        else:
            raise MemoryForestError(
                "time_phrase_unknown",
                f"A phrase table uses a slot this matcher does not know: {slot}.",
            )
    flags = re.IGNORECASE if fold else 0
    return re.compile("^" + "".join(pieces) + "$", flags)


def _tables(root: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    tables = [ENGLISH, *_files()]
    if root is None:
        return tables
    extra = _forest_files(root)
    replaced = {str(table.get("language")) for table in extra}
    return [table for table in tables if str(table.get("language")) not in replaced] + extra


def _page_zone(name: str, config: Config) -> ZoneInfo | timezone:
    if not name:
        return config.zone
    return search._zone_of(name, config.zone, config.zone)


def _forest_files(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Phrase tables the forest added. A file for a language replaces the package table."""
    try:
        folder = layout.resolve(root, f"{layout.STATE_DIR}/phrases")
    except MemoryForestError:
        return []
    if folder.is_symlink() or not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MemoryForestError(
                "time_phrase_unknown",
                f"{path.name} is not a time-phrase table.",
            ) from exc
        if not isinstance(value, dict) or not isinstance(value.get("language"), str) or not isinstance(value.get("phrases"), list):
            raise MemoryForestError(
                "time_phrase_unknown",
                f"{path.name} is not a time-phrase table.",
            )
        found.append(value)
    return found


def _files() -> list[dict[str, Any]]:
    if not _PHRASE_DIR.is_dir():
        return []
    found = []
    for path in sorted(_PHRASE_DIR.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or "language" not in value or "phrases" not in value:
            raise MemoryForestError(
                "time_phrase_unknown",
                f"{path.name} is not a time-phrase table.",
            )
        found.append(value)
    return found


def _zone_from(name: str, forest_zone: ZoneInfo) -> ZoneInfo | timezone:
    if name in {"UTC", "Z"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", name)
    if match is not None:
        sign = 1 if match.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))
    try:
        return ZoneInfo(name)
    except Exception:
        return forest_zone


def _aware(moment: datetime, zone: ZoneInfo) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=zone)
    return moment


def _when_dict(when: When) -> dict[str, Any]:
    payload: dict[str, Any] = {"day_shift": when.day_shift, "kind": when.kind, "label": when.label}
    if when.daypart is not None:
        payload["daypart"] = list(when.daypart)
    if when.kind == "absolute" and when.start is not None and when.end is not None:
        payload["end"] = search._stamp(when.end)
        payload["start"] = search._stamp(when.start)
    return payload
