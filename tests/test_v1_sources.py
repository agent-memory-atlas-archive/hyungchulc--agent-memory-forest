"""A source can be asked for exactly; a source of members is bounded and tells the truth.

The bakery, its bots and their shifts are invented.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from memory_forest import groups, mcp, search, sources, system, timeline
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS
from v1_support import ForestTestCase

NOON = datetime(2051, 3, 2, 12, 0, tzinfo=timezone.utc)
TWO_SHIFTS = BAKERY_GROUPS + "\n[agents.groups.day-shift]\nmembers = []\nopen = true\n" + \
    "\n[agents.groups.mcp]\nmembers = []\nopen = true\n"


class SourceFormTests(ForestTestCase):
    def _forest(self) -> Path:
        root = self.make_forest({**FILES, "forest.toml": TWO_SHIFTS})
        for number in range(3):
            for group in ("night-shift", "day-shift"):
                mcp.record_turn(root, "crate-bot", f"{group.split('-')[0]} glaze turn {number}", origin="interactive",
                                session="shift", group=group, at=NOON + timedelta(minutes=10 * number))
        return root

    def test_a_source_is_a_kind_and_a_name_on_the_turn_in_the_listing_and_in_the_argument(self) -> None:
        root = self._forest()
        mcp.record_turn(root, "crate-bot", "through the group called mcp", origin="interactive", session="shift",
                        group="mcp", at=NOON)
        mcp.record_turn(root, "crate-bot", "through its own connection", origin="interactive", session="shift",
                        door="mcp", at=NOON)
        lane = root / "06 istm" / "crate-bot" / "2051-03-02.jsonl"
        labels = [json.loads(line)["sources"] for line in lane.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(labels[-2:], [["group:mcp"], ["door:mcp"]])
        self.assertEqual(_row(groups.listing(root), "crate-bot")["sources"],
                         ["group:night-shift", "group:day-shift", "group:mcp", "door:mcp"])
        own = search.query(root, "through", reader="crate-bot", source="door:mcp")
        texts = [section["text"] for page in own["results"] for section in page["sections"]]
        self.assertEqual(len(texts), 1)
        self.assertIn("own connection", texts[0])
        grouped = search.query(root, "through", reader="crate-bot", source="group:mcp")
        self.assertIn("group called mcp", json.dumps(grouped))
        self.assertNotIn("own connection", json.dumps(grouped))

    def test_a_source_is_a_known_kind_and_a_name_of_one_to_two_hundred_plain_characters(self) -> None:
        for good in ("group:night-shift", "door:mcp", "adapter:jsonl", "group:" + "g" * 200, "door:ü"):
            self.assertTrue(sources.well_formed(good), good)
        for bad in (7, None, "group", "group:", "kind:x", "group:" + "g" * 201, "group:night shift", "door:a\tb",
                    "door:a\x7f"):
            self.assertFalse(sources.well_formed(bad), repr(bad))
        self.assertEqual(sources.of_record({"sources": [7, "group:night-shift", "door:", "group:night-shift"]}),
                         ["group:night-shift"])
        self.assertEqual(sources.of_record({}), [])
        self.assertEqual(sources.of_record({"source": "jsonl"}), [])
        self.assertEqual(sources.of_record({"source": {"adapter": "jsonl"}}), ["adapter:jsonl"])
        self.assertEqual(sources.of_line("not a record"), [])
        self.assertEqual(sources.of_line("[1, 2]"), [])

    def test_an_unknown_or_unformed_source_is_refused_with_the_readers_sources(self) -> None:
        root = self._forest()
        search.build(root)
        for wrong in ("night-shift", "group:noon-shift", "door:", "kind:x", "group:night shift"):
            for call in (
                lambda: search.query(root, "glaze", reader="crate-bot", source=wrong),
                lambda: search.read(root, "06 istm/crate-bot/2051-03-02.jsonl", reader="crate-bot", source=wrong),
                lambda: timeline.query(root, "2051-03-02", reader="crate-bot", source=wrong),
                lambda: timeline.subject(root, "crate-bot", reader="crate-bot", source=wrong),
            ):
                with self.assertRaises(MemoryForestError) as caught:
                    call()
                self.assertEqual((caught.exception.code, caught.exception.next_step), ("source_unknown", "fix"))
                self.assertIn(f"`{wrong}`", caught.exception.message)
                self.assertEqual(caught.exception.details["sources"], ["group:day-shift", "group:night-shift"])
        # A reader sees only the sources of the lanes it may read: till-bot's own lane came through none of these.
        with self.assertRaises(MemoryForestError) as other:
            search.query(root, "glaze", reader="till-bot", source="group:night-shift")
        self.assertEqual(other.exception.details["sources"], ["adapter:jsonl"])
        reply, _version = mcp.handle_message(
            root, "crate-bot", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "memory_search", "arguments": {"words": "glaze", "source": 7}}}, "stdio", "2025-11-25")
        assert isinstance(reply, dict)
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["code"], "arguments_invalid")

    def test_a_reading_narrowed_to_a_source_keeps_the_files_own_line_numbers(self) -> None:
        root = self._forest()
        search.build(root)
        path = "06 istm/crate-bot/2051-03-02.jsonl"
        for source in ("group:night-shift", "group:day-shift"):
            found = search.query(root, "glaze", reader="crate-bot", source=source, limit=50)
            sections = [section for page in found["results"] for section in page["sections"]]
            self.assertEqual(len(sections), 3)
            for section in sections:
                first, last = section["lines"]
                read = search.read(root, path, reader="crate-bot", source=source, lines=(first, last))
                shown = read["results"][0]["sections"][0]
                self.assertEqual(shown["lines"], [first, last])
                # The read gives the lines as written; the turn on them is the turn the search found.
                self.assertEqual([json.loads(line)["text"] for line in shown["text"].splitlines()], [section["text"]])
                self.assertEqual(shown["kept_lines"], list(range(first, last + 1)))
        whole = search.read(root, path, reader="crate-bot", source="group:day-shift")["results"][0]["sections"][0]
        self.assertEqual(whole["kept_lines"], [2, 4, 6])
        with self.assertRaises(MemoryForestError) as caught:
            search.read(root, path, reader="crate-bot", source="group:day-shift", lines=(1, 1))
        self.assertEqual(caught.exception.code, "read_lines_empty")

    def test_the_narrowing_happens_before_the_limit(self) -> None:
        root = self.make_forest({**FILES, "forest.toml": TWO_SHIFTS}, name="limit")
        for number in range(6):
            mcp.record_turn(root, "crate-bot", "glaze glaze glaze glaze cooling glaze", origin="interactive",
                            session="shift", group="night-shift", at=NOON + timedelta(days=-1, minutes=number))
        mcp.record_turn(root, "crate-bot", "one glaze", origin="interactive", session="shift", group="day-shift",
                        at=NOON + timedelta(days=1))
        search.build(root)
        found = search.query(root, "glaze", reader="crate-bot", source="group:day-shift", limit=1)
        self.assertEqual([page["path"] for page in found["results"]], ["06 istm/crate-bot/2051-03-03.jsonl"])
        days = timeline.query(root, "2051-03-01..2051-03-03", reader="crate-bot", source="group:day-shift", limit=1)
        self.assertEqual([page["path"] for page in days["results"]], ["06 istm/crate-bot/2051-03-03.jsonl"])

    def test_the_listing_reads_the_small_record_and_rebuilds_it_only_when_it_does_not_match(self) -> None:
        root = self._forest()
        record = root / sources.RECORD
        self.assertTrue(record.is_file())
        groups.listing(root)  # the lanes the fixture wrote by hand are read once, and kept
        with system.watching() as watch:
            first = groups.listing(root)
        lanes_read = [path for name, path in watch.calls if name == "read" and "06 istm" in path]
        self.assertEqual(lanes_read, [])
        record.unlink()
        with system.watching() as watch:
            again = groups.listing(root)
        self.assertTrue([path for name, path in watch.calls if name == "read" and "crate-bot" in path])
        self.assertEqual(first, again)
        self.assertTrue(record.is_file())
        with system.watching() as watch:
            groups.listing(root)
        self.assertEqual([path for name, path in watch.calls if name == "read" and "06 istm" in path], [])
        # A lane file that grows by another hand is read again, as a new one is.
        grown = root / "06 istm" / "crate-bot" / "2051-03-02.jsonl"
        with grown.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "hand-0", "at": "2051-03-02T09:00:00+00:00", "role": "agent",
                                     "session": "s", "source": {"adapter": "tray-app"}, "text": "grown"}) + "\n")
        self.assertIn("adapter:tray-app", _row(groups.listing(root), "crate-bot")["sources"])
        # What is not a lane file in the lane's folder is not read.
        (root / "06 istm" / "crate-bot" / "notes.txt").write_text(
            json.dumps({"sources": ["door:notes"], "text": "a note"}) + "\n", encoding="utf-8")
        (root / "06 istm" / "crate-bot" / "2051-03-09.jsonl").mkdir()
        # A lane file that cannot be read as text is passed over, the first in order included.
        (root / "06 istm" / "crate-bot" / "2051-01-01.jsonl").write_bytes(b"\xff\xfe not text\n")
        record.unlink()
        self.assertNotIn("door:notes", _row(groups.listing(root), "crate-bot")["sources"])
        # The record keeps the agents that have a lane, and no other.
        laned = sorted(path.name for path in (root / "06 istm").iterdir() if path.is_dir())
        self.assertEqual(sorted(json.loads(record.read_text(encoding="utf-8"))["agents"]), laned)
        # A lane written another way (here by hand) is read again for that agent, once.
        lane = root / "06 istm" / "crate-bot" / "2051-03-05.jsonl"
        lane.write_text(json.dumps({"id": "hand-1", "at": "2051-03-05T08:00:00+00:00", "role": "agent",
                                    "session": "s", "source": {"adapter": "jsonl"}, "text": "by hand"}) + "\n",
                        encoding="utf-8")
        self.assertIn("adapter:jsonl", _row(groups.listing(root), "crate-bot")["sources"])


class ListingTests(ForestTestCase):
    """`forest agents`: each group's members, then every other agent once, and the pairs that may be one agent."""

    def test_the_listing_and_its_lines_name_each_agent_once_with_what_is_known_of_it(self) -> None:
        settings = BAKERY_GROUPS.replace('members = []\nopen = true', 'members = []\nopen = true\n\n'
                                         '[agents.groups.day-shift]\nmembers = ["oven-bot", "oven-bat"]\nopen = false')
        settings += '\n[agents.known.oven-bat]\nname = "Oven Bat"\n'
        settings += '\n[agents.known.delivery-bot-two]\nname = "Delivery Bot Two"\n'
        # A name that is only separators has no key: it is no pair with anything (from either side).
        settings += '\n[agents.known.dash-0]\nname = "-"\n\n[agents.known.dash-a]\nname = "- Bot"\n'
        settings += '\n[agents.known.dash-b]\nname = "-"\n'
        lane = json.dumps({"id": "r1", "at": "2051-03-02T06:00:00+00:00", "role": "agent", "session": "s",
                           "text": "racked"}) + "\n"
        root = self.make_forest({**FILES, "forest.toml": settings, "06 istm/rack-bot/2051-03-02.jsonl": lane},
                                name="listing")
        mcp.record_turn(root, "till-bot", "counted", origin="interactive", session="shift", group="bakery-bots",
                        at=NOON)
        listed = groups.listing(root)
        self.assertEqual([item["id"] for item in listed["agents"]],
                         ["dash-0", "dash-a", "dash-b", "delivery-bot", "delivery-bot-two", "front-desk", "rack-bot"])
        rows = {item["id"]: item for item in listed["agents"]}
        self.assertEqual((rows["delivery-bot"]["name"], rows["delivery-bot"]["lane"]), ("Delivery Bot", False))
        self.assertEqual((rows["rack-bot"]["name"], rows["rack-bot"]["lane"]), ("rack-bot", True))
        self.assertEqual([item["id"] for item in listed["agents"] if item["orchestrator"]], ["front-desk"])
        self.assertEqual([(item["left"], item["right"], item["how"]) for item in listed["remarks"]],
                         [("oven-bot", "oven-bat", "one edit"), ("delivery-bot", "delivery-bot-two", "one added word")])
        lines = groups.listing_lines(listed)
        self.assertIn("group night-shift (open)", lines)
        self.assertEqual(lines[lines.index("group night-shift (open)") + 1], "  (no members)")
        self.assertIn("agent front-desk — front-desk — no lane — not seen — orchestrator", lines)
        self.assertIn("agent delivery-bot — Delivery Bot — no lane — not seen", lines)
        till = next(line for line in lines if line.startswith("  till-bot — "))
        self.assertTrue(till.endswith(" — adapter:jsonl, group:bakery-bots"), till)
        self.assertEqual(lines[-3:], ["may be one agent:", "  " + listed["remarks"][0]["line"],
                                      "  " + listed["remarks"][1]["line"]])

    def test_the_lines_of_an_empty_listing_and_of_groups_or_agents_alone(self) -> None:
        self.assertEqual(groups.listing_lines({}), ["no agents"])
        self.assertEqual(groups.listing_lines({"groups": [{"id": "g", "open": False, "members": []}]}),
                         ["group g (closed)", "  (no members)"])
        alone = {"id": "a", "name": "A", "lane": True, "last_seen": "", "sources": []}
        self.assertEqual(groups.listing_lines({"agents": [alone]}), ["agent a — A — lane — not seen"])


def _row(listed: dict, agent: str) -> dict:
    rows = [member for group in listed["groups"] for member in group["members"] if member["id"] == agent]
    rows += [item for item in listed["agents"] if item["id"] == agent]
    assert rows, agent
    return rows[0]


class MemberSourceTests(ForestTestCase):
    """A read of a source has a deadline, a greatest size and an interval; its failure is never 'not a member'."""

    def _forest(self, name: str = "listed", open_group: bool = False) -> Path:
        text = BAKERY_GROUPS + f'\n[agents.groups.crates]\nmembers = []\nopen = {"true" if open_group else "false"}\n' \
            'source = "crate-app"\n'
        return self.make_forest({**FILES, "forest.toml": text}, name=name)

    def _source(self, reader) -> None:
        groups.register_member_source("crate-app", reader)
        self.addCleanup(groups.unregister_member_source, "crate-app")
        groups._SOURCE_CACHE.clear()
        self.addCleanup(groups._SOURCE_CACHE.clear)

    def _refused(self, root: Path, name: str) -> MemoryForestError:
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "crates", name)
        return caught.exception

    def test_a_source_that_hangs_floods_fails_or_is_missing_is_the_failure_with_report(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def hangs(_group: str):
            release.wait(60)
            return ["Crate Bot"]

        def floods(_group: str):
            number = 0
            while True:
                number += 1
                yield f"Crate {number}"

        def raises(_group: str):
            raise RuntimeError("the app is down")

        for name, reader in (("hangs", hangs), ("floods", floods), ("raises", raises), ("missing", None)):
            with self.subTest(source=name):
                root = self._forest(name=f"source-{name}")
                if reader is not None:
                    self._source(reader)
                else:
                    groups._SOURCE_CACHE.clear()
                before = (root / "forest.toml").read_bytes()
                with mock.patch.object(groups, "SOURCE_SECONDS", 0.2):
                    refused = self._refused(root, "Crate Bot")
                self.assertEqual((refused.code, refused.next_step), ("member_source_failed", "report"))
                if reader is None:
                    self.assertIn("has no reader in this process", refused.message)
                self.assertEqual((root / "forest.toml").read_bytes(), before)
                groups.unregister_member_source("crate-app")
        release.set()

    def test_within_the_interval_the_last_list_is_used(self) -> None:
        root = self._forest()
        reads: list[str] = []
        self._source(lambda group: reads.append(group) or ["Crate Bot"])
        now = [1000.0]
        with mock.patch.object(groups, "_clock", lambda: now[0]):
            self.assertEqual(self._refused(root, "Other Bot").code, "agent_not_in_group")
            self.assertEqual(self._refused(root, "Other Bot").code, "agent_not_in_group")
            self.assertEqual(groups.resolve_caller(root, "crates", "Crate Bot").id, "crate-bot")
            self.assertEqual(len(reads), 1)
            state = root / groups.SOURCE_STATE
            noted = (state.read_bytes(), state.stat().st_ino)
            now[0] += groups.SOURCE_INTERVAL_SECONDS
            self.assertEqual(self._refused(root, "Other Bot").code, "agent_not_in_group")
            self.assertEqual(len(reads), 2)
            # The same answer again leaves the record as it was.
            self.assertEqual((state.read_bytes(), state.stat().st_ino), noted)

    def test_an_open_group_with_a_source_admits_only_what_the_source_lists(self) -> None:
        root = self._forest(open_group=True)
        self._source(lambda _group: ["Crate Bot"])
        self.assertEqual(self._refused(root, "Other Bot").code, "agent_not_in_group")
        self.assertEqual(groups.resolve_caller(root, "crates", "crate_bot").id, "crate-bot")

    def test_names_of_the_list_that_are_a_bad_argument_are_left_out_and_shown(self) -> None:
        root = self._forest()
        self._source(lambda _group: ["Crate Bot", "", "Shelf\nBot", 7, "y" * 300])
        self.assertEqual(groups.resolve_caller(root, "crates", "Crate Bot").id, "crate-bot")
        group = next(item for item in groups.listing(root)["groups"] if item["id"] == "crates")
        self.assertEqual(len(group["left_out"]), 4)
        lines = "\n".join(groups.listing_lines(groups.listing(root)))
        self.assertIn("left out of source crate-app", lines)
