"""The third intake review. Every name is from the invented kiln.

A test asserts the thing that must not happen: the secret alone, the number of lines in a lane, the bytes of
the tree. Nothing here is copied from a real transcript.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_intake import IntakeTestCase, record  # noqa: E402

from memory_forest import intake  # noqa: E402
from memory_forest.adapters.common import Part  # noqa: E402


class HomeFolderTests(IntakeTestCase):
    def test_the_home_folder_is_folded_before_the_parts_are_mapped(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        home = "/srv/kilnkeeper"
        text = f"see {home}/kiln then <pasted_text>pasted words</pasted_text>"
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text=text), encoding="utf-8")
        root = self.forest(self.tool(folder))
        with mock.patch("pathlib.Path.home", return_value=Path(home)):
            self.invoke(root, "--from", "jsonl", "--once")
        row = self.rows(root)[0]
        self.assertEqual(row["text"], "see ~/kiln then <pasted_text>pasted words</pasted_text>")
        spans = [row["text"][part["start"]:part["end"]] for part in row["parts"]]
        self.assertEqual(spans, ["pasted words"])

    def test_the_home_folder_is_folded_only_at_a_path_boundary(self) -> None:
        with mock.patch("pathlib.Path.home", return_value=Path("/srv/al")):
            folded, parts = intake._fold_home_parts(
                "/srv/alice/x and /srv/al/x and /srv/al. and /opt/srv/al/y", (Part(0, 13, "paste"),)
            )
        self.assertEqual(folded, "/srv/alice/x and ~/x and ~. and /opt/srv/al/y")
        self.assertEqual(parts, (Part(0, 13, "paste"),))


SECRET = "hunter22"


class Interrupted(Exception):
    """An interruption injected at one write step. Not an OSError: any failure after an append counts."""


class TurnOnceTests(IntakeTestCase):
    """B: a turn is written once."""

    def test_two_different_raw_ids_never_become_one(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            record(id="b:c", session="a", text="First.") + record(id="c", session="a:b", text="Second."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        rows = self.rows(root)
        self.assertEqual([row["text"] for row in rows], ["First.", "Second."])
        self.assertEqual(len({row["id"] for row in rows}), 2)

    def test_an_interruption_at_each_write_step_of_a_second_batch_leaves_every_turn_once(self) -> None:
        for bounded in (False, True):
            for step in ("append", "ids", "end", "cursor"):
                with self.subTest(hook=bounded, step=step):
                    self._interrupted(step, bounded=bounded)

    def _interrupted(self, step: str, *, bounded: bool) -> None:
        tag = f"{int(bounded)}-{step}"
        folder = self.base / f"notes-{tag}"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="The shelf cooled.")
                        + record(id="turn-2", text=f"password: {SECRET}"), encoding="utf-8")
        root = self.forest(self.tool(folder), name=f"oven-{tag}")
        args = ["--from", "jsonl", "--once"]
        stdin = ""
        if bounded:
            self.turn_on(root)
            args.append("--hook")
            stdin = json.dumps({"transcript_path": str(path), "cwd": "/studio/kiln"})
        self.invoke(root, *args, stdin=stdin)
        self.assertEqual(len(self.rows(root)), 2)
        cursor = json.loads(intake.cursor_file(root, "jsonl", "kiln.jsonl").read_text(encoding="utf-8"))
        self.assertGreater(cursor["offset"], 0)  # the second batch starts past 0, so the ids file is what guards it
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record(id="turn-3", text="The glaze crawled.")
                         + record(id="turn-4\u2028b", text="An id with a line separator."))

        def boom(name: str) -> None:
            if name == step:
                raise Interrupted(name)

        intake._CHECKPOINT = boom
        try:
            code, _out, _err = self.invoke(root, *args, stdin=stdin)
        finally:
            intake._CHECKPOINT = None
        self.assertEqual(code, 2 if step == "append" and not bounded else 0)
        wrote = 0 if step == "append" else 2
        self.assertIn(f"{wrote} turns, then failed", self.invoke(root, "status")[1])
        self.invoke(root, *args, stdin=stdin)
        lane = self.lane(root)
        self.assertEqual(lane.count("\n"), 4, lane)
        self.assertEqual(len({row["id"] for row in self.rows(root)}), 4)
        self.assertNotIn(SECRET, lane)
        # The id file holds no id twice: the end mark keeps a run from appending what it already holds.
        ids = (root / ".forest" / "intake" / "ids" / "glaze-bot" / "2051-03-02.ids").read_text(encoding="utf-8")
        listed = [line for line in ids.split("\n") if line]
        self.assertEqual(len(listed), len(set(listed)), listed)

    def test_an_adapter_decides_per_id_whether_it_stands_alone_or_is_composed_with_the_session(self) -> None:
        from plugin_adapter import plugged

        logs = self.base / "kiln-logs"
        logs.mkdir()
        opaque = "call_" + "a1b2c3d4" * 4
        for index, (session, tool_id) in enumerate(
            (("ses-kiln-1", "call_1"), ("ses-kiln-2", "call_1"), ("ses-kiln-3", opaque)), start=1
        ):
            lines = [
                {"head": {"session": session, "cwd": "/studio/kiln", "origin": "desk"}},
                {"at": f"2051-03-02T09:0{index}:00+00:00", "role": "owner", "text": f"Look at the kiln ({session})."},
                {"at": f"2051-03-02T09:0{index}:01+00:00", "role": "tool", "name": "read_file", "id": tool_id},
            ]
            (logs / f"{session}.kiln").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        root = self.forest(f"\n[intake.tools.kiln-log]\npath = {json.dumps(str(logs))}\nimport_earlier = true\n"
                           "agent = \"glaze-bot\"\n")
        with plugged():
            self.invoke(root, "--from", "kiln-log", "--once")
        tools = [row for row in self.rows(root) if row["role"] == "tool"]
        self.assertEqual(len(tools), 3)
        self.assertEqual(sorted(row["session"] for row in tools), ["ses-kiln-1", "ses-kiln-2", "ses-kiln-3"])
        self.assertIn(opaque, [row["id"] for row in tools])

    def test_reread_never_goes_before_the_point_intake_started_from(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-0", text="Before intake was on."), encoding="utf-8")
        (folder / "shop.jsonl").write_text(record(id="turn-s", session="ses-shop-1", cwd="/studio/shop",
                                                  text="Shop."), encoding="utf-8")
        root = self.forest(self.tool(folder, earlier=False, exclude="/studio/shop"))
        self.invoke(root, "--from", "jsonl", "--once")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record(id="turn-1", text="After."))
        self.invoke(root, "--from", "jsonl", "--once")
        excluded = intake.cursor_file(root, "jsonl", "shop.jsonl")
        before = excluded.read_bytes()
        kiln = intake.cursor_file(root, "jsonl", "kiln.jsonl")
        state = json.loads(kiln.read_text(encoding="utf-8"))
        state["carry"] = {"turn_id": "call-old", "at": "2051-03-02T09:00:00+00:00", "text": "carried"}
        kiln.write_text(json.dumps(state), encoding="utf-8")
        self.invoke(root, "reread", "--from", "jsonl")
        self.assertEqual(excluded.read_bytes(), before)
        self.assertNotIn("carry", json.loads(kiln.read_text(encoding="utf-8")))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["After."])

    def test_a_dry_run_writes_nothing_when_the_state_cannot_be_read(self) -> None:
        from test_v1_intake import _tree_bytes

        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1"), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        state = root / ".forest" / "intake" / "tools" / "jsonl" / "state.json"
        state.write_text("{not json", encoding="utf-8")
        before = _tree_bytes(root)
        code, _out, _err = self.invoke(root, "--from", "jsonl", "--once", "--dry-run")
        self.assertEqual(code, 0)
        code, _out, _err = self.invoke(root, "--from", "jsonl", "--once", "--dry-run", "--hook", stdin=json.dumps({
            "transcript_path": str(folder / "kiln.jsonl"), "cwd": "/studio/kiln",
        }))
        self.assertEqual(code, 0)
        self.assertEqual(_tree_bytes(root), before)


class OwnershipTests(IntakeTestCase):
    """C: a tool's transcripts are kept by tool, an agent's lane by agent."""

    def test_a_second_agent_reading_other_transcripts_works(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "a.jsonl").write_text(record(id="turn-a", session="ses-a", text="For Ada."), encoding="utf-8")
        (folder / "b.jsonl").write_text(record(id="turn-b", session="ses-b", text="For Bo."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--as", "ada", "--transcript", str(folder / "a.jsonl"), "--once")
        code, out, err = self.invoke(root, "--from", "jsonl", "--as", "bo", "--transcript", str(folder / "b.jsonl"),
                                     "--once")
        self.assertEqual(code, 0, err)
        self.assertEqual([row["text"] for row in self.rows(root, "ada")], ["For Ada."])
        self.assertEqual([row["text"] for row in self.rows(root, "bo")], ["For Bo."])

    def test_a_hook_reads_only_its_own_cursor_and_not_another_agents_transcript(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="For the kiln bot."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--as", "kiln-bot", "--once")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record(id="turn-2", text="Still the kiln bot's."))
        real = intake._each_cursor
        calls = []

        def counted(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with mock.patch.object(intake, "_each_cursor", counted):
            code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
                "transcript_path": str(path), "cwd": "/studio/kiln",
            }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(calls, [])
        self.assertEqual(self.lane(root, "glaze-bot"), "")
        self.assertEqual(len(self.rows(root, "kiln-bot")), 1)
        status = self.invoke(root, "status")[1]
        self.assertIn("1 transcripts kept for kiln-bot are not read while the agent is glaze-bot", status)
        self.assertIn("forest intake rename-agent kiln-bot glaze-bot", status)

    def test_rename_agent_completes_after_an_interruption_at_any_step(self) -> None:
        for step in ("rename-lane", "rename-ids", "rename-cursor"):
            with self.subTest(step=step):
                self._renamed(step)

    def _renamed(self, step: str) -> None:
        folder = self.base / f"notes-{step}"
        folder.mkdir()
        (folder / "a.jsonl").write_text(record(id="turn-a", session="ses-a", text="First."), encoding="utf-8")
        (folder / "b.jsonl").write_text(record(id="turn-b", session="ses-b", text="Second."), encoding="utf-8")
        root = self.forest(self.tool(folder), name=f"oven-{step}")
        self.invoke(root, "--from", "jsonl", "--as", "kiln-bot", "--once")

        def boom(name: str) -> None:
            if name == step:
                raise Interrupted(name)

        intake._CHECKPOINT = boom
        try:
            result = intake.rename_agent(root, "kiln-bot", "glaze-bot")
            self.assertEqual(result.data["stored"], "renamed")
            self.assertTrue(any(item["state"] == "pending" for item in result.data["derived"].values()))
        finally:
            intake._CHECKPOINT = None
        intake.rename_agent(root, "kiln-bot", "glaze-bot")
        self.assertFalse((root / "06 istm" / "kiln-bot").exists())
        self.assertFalse((root / ".forest" / "intake" / "ids" / "kiln-bot").exists())
        owners = {cursor["agent"] for _path, cursor in intake._each_cursor(root, "jsonl")}
        self.assertEqual(owners, {"glaze-bot"})
        with (folder / "a.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(record(id="turn-c", session="ses-a", text="Third."))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(sorted(row["text"] for row in self.rows(root)), ["First.", "Second.", "Third."])

    def test_renaming_to_the_same_name_says_so_in_one_line(self) -> None:
        root = self.forest()
        code, out, _err = self.invoke(root, "rename-agent", "glaze-bot", "glaze-bot")
        self.assertEqual(code, 0)
        self.assertEqual(out, "glaze-bot is already the name\n")


class HookTests(IntakeTestCase):
    """D: a hook does one small thing."""

    def test_the_hook_returns_while_the_client_keeps_stdin_open(self) -> None:
        from memory_forest.cli_intake import read_hook_payload

        read_end, write_end = os.pipe()
        try:
            os.write(write_end, b'{"session_id": "ses-kiln-1"}')
            with os.fdopen(read_end, "rb") as stream:
                value = read_hook_payload(stream)
            # The write end is still open here: the hook returned while it was.
            os.fstat(write_end)
        finally:
            os.close(write_end)
        self.assertEqual(value, {"session_id": "ses-kiln-1"})

    def test_a_stage_session_is_one_stat_and_a_hook_opens_none_of_ten_thousand(self) -> None:
        from memory_forest import stage

        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        root = self.forest(self.tool(folder))
        self.turn_on(root)
        sessions = root / ".forest" / "intake" / "stage-sessions"
        sessions.mkdir(parents=True)
        for number in range(10_000):
            (sessions / f"{number:032x}").touch()
        stage.record_session(root, "ses-stage-1")
        path.write_text(record(id="turn-1", text="An ordinary turn."), encoding="utf-8")
        opened: list[str] = []
        real_open, real_os_open, real_scandir, real_listdir = io.open, os.open, os.scandir, os.listdir

        def spy(real):
            def wrapped(target, *args, **kwargs):
                if str(sessions) in os.fsdecode(os.fspath(target)) if not isinstance(target, int) else False:
                    opened.append(real.__name__)
                return real(target, *args, **kwargs)
            return wrapped

        with mock.patch.object(io, "open", spy(real_open)), mock.patch.object(os, "open", spy(real_os_open)), \
                mock.patch.object(os, "scandir", spy(real_scandir)), mock.patch.object(os, "listdir", spy(real_listdir)):
            code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
                "transcript_path": str(path), "cwd": "/studio/kiln",
            }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(opened, [])
        self.assertEqual([row["text"] for row in self.rows(root)], ["An ordinary turn."])
        # A stage session is still found, and a sweep removes the files older than the stated days.
        (folder / "stage.jsonl").write_text(record(id="turn-s", session="ses-stage-1", text="The stage prompt."),
                                            encoding="utf-8")
        old = sessions / f"{1:032x}"
        os.utime(old, (1, 1))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertNotIn("The stage prompt.", [row["text"] for row in self.rows(root)])
        self.assertFalse(old.exists())
        self.assertTrue(stage.session_recorded(root, "ses-stage-1"))

    def test_the_marker_name_outside_the_first_owner_turn_never_drops_a_session(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        arguments = json.dumps({"MEMORY_FOREST_INTERNAL": "1", "nested": {"MEMORY_FOREST_INTERNAL": True}})
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text="Owner first.")
            + record(id="turn-2", text="Then.", internal=True)
            + record(id="turn-3", role="tool", name="Bash", text="", arguments=arguments)
            + record(id="turn-4", text="A later line.", MEMORY_FOREST_INTERNAL="1")
            + record(id="turn-5", text="MEMORY_FOREST_INTERNAL"),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        texts = [row["text"] for row in self.rows(root)]
        for text in ("Owner first.", "Then.", "A later line.", "MEMORY_FOREST_INTERNAL"):
            self.assertIn(text, texts)
        self.assertEqual(len([row for row in self.rows(root) if row["role"] == "tool"]), 1)

    def test_a_line_longer_than_a_hook_reads_is_the_sweeps(self) -> None:
        from memory_forest.adapters import jsonl as jsonl_adapter

        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        root = self.forest(self.tool(folder))
        self.turn_on(root)
        path.write_text(record(id="turn-1", text="x" * 6000) + record(id="turn-2", text="Short."), encoding="utf-8")
        payload = json.dumps({"transcript_path": str(path), "cwd": "/studio/kiln"})
        reads = []
        real = jsonl_adapter.JsonlAdapter.read

        def counted(self_, *args, **kwargs):
            reads.append(kwargs.get("max_bytes"))
            return real(self_, *args, **kwargs)

        with mock.patch.object(intake, "HOOK_READ_BYTES", 2048), \
                mock.patch.object(jsonl_adapter.JsonlAdapter, "read", counted):
            self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=payload)
            self.assertEqual(self.lane(root), "")
            self.assertIn("1 waiting for a sweep", self.invoke(root, "status")[1])
            self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=payload)
            self.assertEqual(reads, [2048])  # the second hook returned without reading
            self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([len(row["text"]) for row in self.rows(root)], [6000, 6])
        self.assertNotIn("waiting for a sweep", self.invoke(root, "status")[1])

    def test_a_plugins_discover_generator_is_consumed_inside_the_guard_and_bounded(self) -> None:
        from memory_forest.adapters import jsonl as jsonl_adapter

        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text="Taken."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        real = jsonl_adapter.JsonlAdapter.discover

        def ends(self_, settings):
            yield from real(self_, settings)
            raise SystemExit(3)

        with mock.patch.object(jsonl_adapter.JsonlAdapter, "discover", ends):
            code, _out, err = self.invoke(root, "--from", "jsonl", "--once")
        self.assertNotEqual(code, 0)
        self.assertIn("plugin_failed", err)
        self.assertEqual(self.lane(root), "")

        def endless(self_, settings):
            while True:
                yield from real(self_, settings)

        with mock.patch.object(jsonl_adapter.JsonlAdapter, "discover", endless), \
                mock.patch.object(intake, "DISCOVER_LIMIT", 3):
            code, out, err = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 0, err)
        self.assertEqual([row["text"] for row in self.rows(root)], ["Taken."])
