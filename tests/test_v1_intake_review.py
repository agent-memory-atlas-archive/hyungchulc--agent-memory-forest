"""The intake review: a hook must not break its caller, and one bad line must not stop the rest.

Every name here is from the invented kiln. Nothing is copied from a real transcript.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_adapter import plugged  # noqa: E402
from test_v1_intake import IntakeTestCase, record  # noqa: E402

from memory_forest.adapters.common import speaks_as_owner  # noqa: E402
from memory_forest.intake import cursor_file  # noqa: E402
from memory_forest.layout import initialize  # noqa: E402
from memory_forest.stage import prepare  # noqa: E402


class HookSafetyTests(IntakeTestCase):
    def test_a_hook_failure_exits_zero_and_is_only_a_log_line(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        root = self.forest(self.tool(folder))
        self.turn_on(root)
        missing = json.dumps({"transcript_path": str(folder / "missing.jsonl"), "cwd": "/studio/kiln"})
        code, out, err = self.invoke(root, "--from", "jsonl", "--as", "glaze-bot", "--once", "--hook", stdin=missing)
        self.assertEqual((code, out, err), (0, "", ""))
        outside = json.dumps({"transcript_path": "/private/tmp/not-a-kiln.jsonl", "cwd": "/studio/kiln"})
        code, out, err = self.invoke(root, "--from", "jsonl", "--as", "glaze-bot", "--once", "--hook", stdin=outside)
        self.assertEqual((code, out, err), (0, "", ""))
        climbed = json.dumps({"transcript_path": str(folder / ".." / "notes" / "kiln.jsonl")})
        code, out, err = self.invoke(root, "--from", "jsonl", "--as", "glaze-bot", "--once", "--hook", stdin=climbed)
        self.assertEqual((code, out, err), (0, "", ""))
        bare = self.forest("\n[intake.tools.jsonl]\npath = \"/studio/kiln\"\n", name="no-agent")
        code, out, err = self.invoke(bare, "--from", "jsonl", "--once", "--hook", stdin=missing)
        self.assertEqual((code, out, err), (0, "", ""))
        broken = self.forest(self.tool(folder), name="broken-toml")
        (broken / "forest.toml").write_text("this is not = [\n", encoding="utf-8")
        code, out, err = self.invoke(broken, "--from", "jsonl", "--once", "--hook", stdin=missing)
        self.assertEqual((code, out, err), (0, "", ""))
        log = (root / ".forest" / "intake" / "failures.jsonl").read_text(encoding="utf-8")
        self.assertIn("transcript_missing", log)
        self.assertIn("unsafe_transcript", log)
        self.assertNotIn("/Users", log)
        self.assertNotIn("private", log)
        code, out, _ = self.invoke(root, "status")
        self.assertEqual(code, 0)
        self.assertIn("failures", out)
        self.assertIn("transcript_missing", out)
        self.assertIn("unsafe_transcript", out)

    def test_a_corrupt_cursor_is_set_aside_and_the_source_is_read_again(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="Still here."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        cursor = cursor_file(root, "jsonl", "kiln.jsonl")
        cursor.write_text("{not json", encoding="utf-8")
        code, out, err = self.invoke(root, "--from", "jsonl", "--as", "glaze-bot", "--once", "--hook", stdin=json.dumps({
            "transcript_path": str(path), "cwd": "/studio/kiln", "session_id": "ses-kiln-1",
        }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual([row["text"] for row in self.rows(root)], ["Still here."])
        self.assertTrue(any(item.name.startswith(cursor.name) and "aside" in item.name for item in cursor.parent.iterdir()))
        self.assertIn("intake_state_invalid", (root / ".forest" / "intake" / "failures.jsonl").read_text(encoding="utf-8"))

    def test_a_surrogate_is_repaired_and_one_bad_line_does_not_stop_the_next_file(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text="A lone surrogate \ud83d in the glaze.")
            + "{\"nope\": true}\n"
            + record(id="turn-2", text="The shelf cooled."),
            encoding="utf-8",
        )
        (folder / "shop.jsonl").write_text(
            record(id="turn-3", session="ses-shop-1", cwd="/studio/shop", text="The shop bell."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        code, out, err = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 0, err + out)
        rows = self.rows(root)
        repaired = next(row for row in rows if row["id"].endswith(":turn-1"))
        self.assertTrue(repaired.get("repaired"))
        self.assertNotIn("\ud83d", repaired["text"])
        self.assertIn("The shelf cooled.", [row["text"] for row in rows])
        self.assertIn("The shop bell.", [row["text"] for row in rows])
        code, out, _ = self.invoke(root, "status")
        self.assertIn("unknown 1", out)
        self.assertIn("record 1", out)
        # The cursor moved past the bad line: a later run adds nothing from these files.
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertIn("0 turns taken in", out)

    def test_a_torn_lane_line_is_cut_back_and_a_short_write_still_finishes(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="First."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        lane = next((root / "06 istm" / "glaze-bot").glob("*.jsonl"))
        with lane.open("ab") as handle:
            handle.write(b'{"id": "torn"')
        path.write_text(path.read_text(encoding="utf-8") + record(id="turn-2", text="Second."), encoding="utf-8")
        real = os.write

        def short(descriptor: int, data: bytes) -> int:
            if len(data) > 8:
                return real(descriptor, data[:1])
            return real(descriptor, data)

        os.write = short
        try:
            code, out, err = self.invoke(root, "--from", "jsonl", "--once")
        finally:
            os.write = real
        self.assertEqual(code, 0, err + out)
        text = lane.read_text(encoding="utf-8")
        self.assertNotIn('{"id": "torn"', text)
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([row["text"] for row in rows], ["First.", "Second."])

    def test_a_rewritten_file_is_read_again_and_status_counts_the_rewind(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="First.") + record(id="turn-2", text="Second."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        replacement = folder / "kiln-next.jsonl"
        replacement.write_text(
            record(id="turn-1", text="First.")
            + record(id="turn-2", text="Second.")
            + record(id="turn-3", text="Third."),
            encoding="utf-8",
        )
        os.replace(replacement, path)
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["First.", "Second.", "Third."])
        code, out, _ = self.invoke(root, "status")
        self.assertEqual(code, 0)
        self.assertIn("rewinds 1", out)

    def test_a_hook_does_not_read_the_history(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        lines = [
            record(id=f"turn-{index}", text=f"Glaze note {index}.")
            for index in range(3000)
        ]
        path = folder / "kiln.jsonl"
        path.write_text("".join(lines), encoding="utf-8")
        (folder / "other.jsonl").write_text("".join(lines[:800]), encoding="utf-8")
        root = self.forest(self.tool(folder).replace("import_earlier = true", "import_earlier = true\nenabled = true"))
        self.invoke(root, "--from", "jsonl")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record(id="turn-new", text="One new line."))
        opened: list[str] = []
        read_bytes = {"transcript": 0, "lane": 0, "all": 0}
        real_open = io.open

        class Wrap:
            def __init__(self, handle, name: str) -> None:
                self._handle = handle
                self._name = name

            def read(self, size=-1):
                data = self._handle.read(size)
                if isinstance(data, (bytes, str)):
                    count = len(data) if isinstance(data, bytes) else len(data.encode())
                    read_bytes["all"] += count
                    if self._name.endswith("/notes/kiln.jsonl"):
                        read_bytes["transcript"] += count
                    if "06 istm" in self._name:
                        read_bytes["lane"] += count
                return data

            def __iter__(self):
                return self

            def __next__(self):
                line = next(self._handle)
                if isinstance(line, (bytes, str)):
                    count = len(line) if isinstance(line, bytes) else len(line.encode())
                    read_bytes["all"] += count
                    if self._name.endswith("/notes/kiln.jsonl"):
                        read_bytes["transcript"] += count
                return line

            def __getattr__(self, item):
                return getattr(self._handle, item)

            def __enter__(self):
                self._handle.__enter__()
                return self

            def __exit__(self, *exc):
                return self._handle.__exit__(*exc)

        def counting(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            if isinstance(file, int):
                return handle
            name = os.fspath(file)
            opened.append(name)
            return Wrap(handle, name)

        io.open = counting
        try:
            code, out, err = self.invoke(root, "--from", "jsonl", "--hook", stdin=json.dumps({
                "transcript_path": str(path), "cwd": "/studio/kiln", "session_id": "ses-kiln-1",
            }))
        finally:
            io.open = real_open
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertIn("One new line.", [row["text"] for row in self.rows(root)])
        self.assertFalse(any(name.endswith("/notes/other.jsonl") for name in opened), opened)
        self.assertLess(read_bytes["transcript"], 8192, read_bytes)
        self.assertLess(read_bytes["lane"], 8192, read_bytes)
        # One day's id list is read once. The transcript itself stays under the cap above.
        self.assertLess(read_bytes["all"], 131072, read_bytes)

    def test_cursors_belong_to_the_agent(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="For the glaze bot."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--as", "kiln-bot", "--once")
        self.assertEqual([row["text"] for row in self.rows(root, "kiln-bot")], ["For the glaze bot."])
        self.assertEqual(self.lane(root, "glaze-bot"), "")
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 0)
        self.assertIn("kept for kiln-bot", out)
        self.assertEqual(self.lane(root, "glaze-bot"), "")
        code, out, _ = self.invoke(root, "status")
        self.assertIn("1 transcripts kept for kiln-bot", out)
        self.assertIn("rename-agent kiln-bot glaze-bot", out)

    def test_ids_survive_a_rewind_a_resume_and_a_fork(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        first = record(text="The same glaze.", at="2051-03-02T09:00:00+00:00")
        # No id: the id is a hash of the content and the time, not of the byte offset.
        path.write_text(first, encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        first_id = self.rows(root)[0]["id"]
        os.replace(path, folder / "old.jsonl")
        path.write_text(first, encoding="utf-8")
        # A new inode. The cursor's device and inode no longer match, so it rewinds.
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["id"] for row in self.rows(root)], [first_id])
        (folder / "resume.jsonl").write_text(
            record(id="turn-1", text="Copied.") + record(id="turn-2", session="ses-kiln-2", text="Added on resume."),
            encoding="utf-8",
        )
        (folder / "fork.jsonl").write_text(
            record(id="fork-1", session="ses-fork-1", text="Copied."),
            encoding="utf-8",
        )
        self.invoke(root, "--from", "jsonl", "--once")
        texts = [row["text"] for row in self.rows(root)]
        self.assertEqual(texts.count("Copied."), 2)
        self.assertIn("Added on resume.", texts)

    def test_an_excluded_source_is_followed_and_not_imported_when_the_exclude_lifts(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1", cwd="/studio/kiln", text="Kiln."), encoding="utf-8")
        shop = folder / "shop.jsonl"
        shop.write_text(record(id="turn-2", session="ses-shop-1", cwd="/studio/shop", text="While excluded."), encoding="utf-8")
        root = self.forest(self.tool(folder, exclude="/studio/shop"))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["Kiln."])
        shop.write_text(shop.read_text(encoding="utf-8") + record(
            id="turn-3", session="ses-shop-1", cwd="/studio/shop", text="After the exclude.",
        ), encoding="utf-8")
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8").replace('exclude = ["/studio/shop"]\n', ""), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["Kiln.", "After the exclude."])
        self.assertNotIn("While excluded.", self.lane(root))

    def test_the_transcript_path_is_relative_and_sign_in_shapes_are_masked(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        secret = "ghp_" + "Ab9" * 12
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text=f"The kiln token is {secret}.", name=secret),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        stored = self.lane(root)
        self.assertNotIn(secret, stored)
        self.assertNotIn(str(Path.home()), stored)
        row = self.rows(root)[0]
        self.assertEqual(row["source"]["transcript"], "kiln.jsonl")
        self.assertFalse(row["source"]["transcript"].startswith("/"))

    def test_unknown_origin_is_not_the_owner_and_a_parent_id_is_not_a_subagent(self) -> None:
        self.assertFalse(speaks_as_owner("owner", "unknown"))
        self.assertFalse(speaks_as_owner("owner", "headless"))
        self.assertTrue(speaks_as_owner("owner", "interactive"))
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            json.dumps({
                "at": "2051-03-02T09:00:00+00:00",
                "cwd": "/studio/kiln",
                "role": "owner",
                "session": "ses-kiln-1",
                "text": "Nobody signed this.",
            }) + "\n",
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(self.rows(root)[0]["origin"], "unknown")
        logs = self.base / "kiln-logs"
        logs.mkdir()
        lines = [
            {"head": {"session": "ses-parent-1", "cwd": "/studio/kiln", "origin": "desk",
                      "parent": "ses-older-1", "version": "0.0-test"}},
            {"at": "2051-03-02T09:00:01+00:00", "role": "owner", "text": "Still the owner.", "id": "k-parent-1"},
        ]
        (logs / "parent.kiln").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        settings = root / "forest.toml"
        settings.write_text(
            settings.read_text(encoding="utf-8")
            + f"\n[intake.tools.kiln-log]\npath = {json.dumps(str(logs))}\nimport_earlier = true\nagent = \"glaze-bot\"\n",
            encoding="utf-8",
        )
        with plugged():
            self.invoke(root, "--from", "kiln-log", "--once")
            row = next(item for item in self.rows(root) if item["text"] == "Still the owner.")
            self.assertEqual(row["origin"], "interactive")
            self.assertEqual(row["source"]["parent"], "ses-older-1")
            self.assertEqual(row["source"]["origin_raw"], "desk")
            code, out, _ = self.invoke(root, "status")
        self.assertIn("version 0.0-test", out)

    def test_a_system_turn_and_a_harness_span_an_adapter_names_are_kept(self) -> None:
        logs = self.base / "kiln-logs"
        logs.mkdir()
        mixed = "Hello.\n<kiln-note>a note</kiln-note>"
        lines = [
            {"head": {"session": "ses-kiln-1", "cwd": "/studio/kiln", "origin": "desk"}},
            {"at": "2051-03-02T09:00:00+00:00", "role": "system", "text": "<kiln-note>check the kiln</kiln-note>",
             "id": "k-meta", "parts": [[0, 38, "harness"]]},
            {"at": "2051-03-02T09:00:01+00:00", "role": "owner", "text": mixed, "id": "k-mixed",
             "parts": [[7, len(mixed), "harness"]]},
        ]
        (logs / "kiln.kiln").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        root = self.forest(
            f"\n[intake.tools.kiln-log]\npath = {json.dumps(str(logs))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
        )
        with plugged():
            self.invoke(root, "--from", "kiln-log", "--once")
        rows = self.rows(root)
        meta = next(row for row in rows if "check the kiln" in row["text"])
        self.assertEqual(meta["role"], "system")
        self.assertEqual([part["kind"] for part in meta["parts"]], ["harness"])
        owner = next(row for row in rows if row["text"].startswith("Hello."))
        self.assertEqual(owner["role"], "owner")
        self.assertEqual(owner["origin"], "interactive")
        span = next(part for part in owner["parts"] if part["kind"] == "harness")
        self.assertEqual(owner["text"][span["start"]:span["end"]], "<kiln-note>a note</kiln-note>")

    def test_a_hook_runs_for_the_built_in_and_for_a_plugged_in_adapter_when_enabled(self) -> None:
        notes = self.base / "notes"
        notes.mkdir()
        (notes / "kiln.jsonl").write_text(record(id="turn-hook", text="Record hook."), encoding="utf-8")
        logs = self.base / "kiln-logs"
        logs.mkdir()
        (logs / "helper.kiln").write_text(
            json.dumps({"head": {"session": "ses-helper-1", "cwd": "/studio/kiln", "origin": "desk"}}) + "\n"
            + json.dumps({"at": "2051-03-02T09:00:00+00:00", "role": "owner", "text": "Check the glaze.",
                          "id": "k-ask"}) + "\n"
            + json.dumps({"at": "2051-03-02T09:00:01+00:00", "role": "tool", "name": "glaze-helper",
                          "text": "Helper hook.", "id": "k-helper"}) + "\n",
            encoding="utf-8",
        )
        root = self.forest(
            self.tool(notes).replace("import_earlier = true", "import_earlier = true\nenabled = true")
            + f"\n[intake.tools.kiln-log]\nenabled = true\npath = {json.dumps(str(logs))}\n"
            "import_earlier = true\nagent = \"glaze-bot\"\n"
        )
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8").replace('tool_output = "omit"', 'tool_output = "keep"', 1),
                            encoding="utf-8")
        self.turn_on(root, "jsonl", "kiln-log")
        code, out, err = self.invoke(root, "--from", "jsonl", "--hook", stdin=json.dumps({
            "transcript_path": str(notes / "kiln.jsonl"), "cwd": "/studio/kiln", "session_id": "ses-kiln-1",
        }))
        self.assertEqual((code, out, err), (0, "", ""))
        with plugged():
            code, out, err = self.invoke(root, "--from", "kiln-log", "--hook", stdin=json.dumps({
                "kiln_log": str(logs / "helper.kiln"), "helper": True,
            }))
        self.assertEqual((code, out, err), (0, "", ""))
        rows = self.rows(root)
        self.assertIn("Record hook.", [row["text"] for row in rows])
        helper = next(row for row in rows if row["text"] == "Helper hook.")
        # The hook named a helper's transcript: its turns are a sub-agent's, whatever the file's head says.
        self.assertEqual((helper["role"], helper["origin"]), ("tool", "subagent"))
        self.assertEqual(next(row for row in rows if row["text"] == "Check the glaze.")["origin"], "subagent")
        self.assertEqual(helper["source"]["adapter"], "kiln-log")

    def test_status_names_a_source_ahead_of_its_cursor(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="Read."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        path.write_text(path.read_text(encoding="utf-8") + record(id="turn-2", text="Not yet."), encoding="utf-8")
        code, out, _ = self.invoke(root, "status")
        self.assertEqual(code, 0)
        self.assertIn("ahead kiln.jsonl", out)

    def test_a_stage_session_is_left_out_by_the_environment_the_list_and_the_marker(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "env.jsonl").write_text(record(id="turn-env", text="From the environment."), encoding="utf-8")
        (folder / "listed.jsonl").write_text(
            record(id="turn-listed", session="ses-stage-1", text="From the list."),
            encoding="utf-8",
        )
        (folder / "marked.jsonl").write_text(
            record(id="turn-marked", session="ses-marked-1", text="MEMORY_FOREST_INTERNAL\nThe stage prompt."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.turn_on(root)
        env, prompt = prepare(root, "ses-stage-1", "Look at the kiln.")
        self.assertEqual(env["MEMORY_FOREST_INTERNAL"], "1")
        self.assertTrue(prompt.startswith("MEMORY_FOREST_INTERNAL\n"))
        old = os.environ.get("MEMORY_FOREST_INTERNAL")
        os.environ["MEMORY_FOREST_INTERNAL"] = "1"
        try:
            code, out, err = self.invoke(
                root, "--from", "jsonl", "--once", "--hook",
                stdin=json.dumps({"transcript_path": str(folder / "env.jsonl"), "cwd": "/studio/kiln", "session_id": "ses-env"}),
            )
        finally:
            if old is None:
                os.environ.pop("MEMORY_FOREST_INTERNAL", None)
            else:
                os.environ["MEMORY_FOREST_INTERNAL"] = old
        self.assertEqual((code, out, err), (0, "", ""))
        self.invoke(root, "--from", "jsonl", "--once")
        texts = [row["text"] for row in self.rows(root)]
        self.assertNotIn("From the environment.", texts)
        self.assertNotIn("From the list.", texts)
        self.assertNotIn("The stage prompt.", texts)


class PluginTests(IntakeTestCase):
    def test_a_name_clash_names_both_and_a_failed_plugin_disables_only_itself(self) -> None:
        from memory_forest import adapters as adapter_loader

        class Other:
            name = "jsonl"

            def load(self):
                return object()

        class Broken:
            name = "kiln-extra"

            def load(self):
                raise RuntimeError("the kiln plugin did not start")

        real = adapter_loader.entry_points

        def fake(group):
            self.assertEqual(group, "memory_forest.adapters")
            return [Other(), Broken()]

        adapter_loader.entry_points = fake
        adapter_loader.clear_adapter_cache()
        try:
            loaded = adapter_loader.load_adapters()
            self.assertIn("jsonl", loaded)
            self.assertEqual(loaded["jsonl"].name, "jsonl")
            self.assertEqual(adapter_loader.load_failures().get("jsonl"), "intake_adapter_clash")
        finally:
            adapter_loader.entry_points = real
            adapter_loader.clear_adapter_cache()

        def only_broken(group):
            return [Broken()]

        adapter_loader.entry_points = only_broken
        adapter_loader.clear_adapter_cache()
        try:
            folder = self.base / "notes"
            folder.mkdir()
            (folder / "kiln.jsonl").write_text(record(id="turn-1"), encoding="utf-8")
            root = self.forest(self.tool(folder))
            code, out, err = self.invoke(root, "--from", "jsonl", "--once")
            self.assertEqual(code, 0, err + out)
            self.assertEqual(len(self.rows(root)), 1)
            code, out, _ = self.invoke(root, "status")
            self.assertIn("kiln-extra: failed to load (RuntimeError)", out)
            self.assertIn("jsonl:", out)
        finally:
            adapter_loader.entry_points = real
            adapter_loader.clear_adapter_cache()


if __name__ == "__main__":
    unittest.main()
