"""The second intake review. Every name is from the invented kiln.

A test asserts the thing that must not happen: the secret alone, the number of
lines, the bytes of the tree. Nothing here is copied from a real transcript.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_adapter import plugged  # noqa: E402
from test_v1_intake import IntakeTestCase, record  # noqa: E402

from memory_forest import intake  # noqa: E402
from memory_forest.adapters import jsonl as jsonl_adapter  # noqa: E402
from memory_forest.cli import forest_main  # noqa: E402
from memory_forest.cli_intake import read_hook_payload  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.intake import HOOK_READ_BYTES, TURNS_BEFORE_OWNER  # noqa: E402


SECRET = "hunter22"


class ReviewTests(IntakeTestCase):
    def test_a_part_tag_is_not_the_value_and_the_span_still_covers_it(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        text = "password: <pasted_text>hunter22</pasted_text>"
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text=text), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        stored = self.lane(root)
        self.assertNotIn(SECRET, stored)
        self.assertIn("pasted_text", stored)
        row = self.rows(root)[0]
        part = row["parts"][0]
        self.assertIn("[REDACTED]", row["text"][part["start"]:part["end"]])

    def test_a_home_path_in_text_or_a_kept_argument_is_folded_and_the_id_is_not(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        home = str(Path.home())
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text=f"the shelf is {home}/kiln")
            + record(
                id="turn-2",
                role="tool",
                name="read",
                text="the log",
                arguments=f"{home}/kiln/log",
            ),
            encoding="utf-8",
        )
        root = self.base / "kept"
        from memory_forest.layout import initialize

        initialize(root)
        settings = (root / "forest.toml").read_text(encoding="utf-8").replace(
            'tool_output = "omit"', 'tool_output = "keep"', 1
        )
        (root / "forest.toml").write_text(settings + self.tool(folder), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        stored = self.lane(root)
        self.assertNotIn(home, stored)
        self.assertIn("~/kiln", stored)
        tool = next(row for row in self.rows(root) if row["role"] == "tool")
        self.assertNotIn(home, tool["arguments"])
        self.assertTrue(tool["arguments"].startswith("~/"))
        for row in self.rows(root):
            self.assertNotIn("glaze-bot", row["id"])

    def test_a_role_name_is_masked_and_a_surrogate_id_is_stable(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text="Once.")
            + record(id="turn-\ud800", role="other:password: hunter22", text="Named."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        self.invoke(root, "--from", "jsonl", "--once")
        rows = self.rows(root)
        self.assertEqual(len(rows), 2)
        named = next(row for row in rows if row["text"] == "Named.")
        self.assertNotIn(SECRET, named["role"])
        self.assertNotIn("\ud800", self.lane(root))
        named["id"].encode("utf-8")

    def test_one_turn_that_raises_does_not_stop_the_rest_and_reread_retries_it(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text="Before.")
            + record(id="turn-2", text="Boom in the kiln.")
            + record(id="turn-3", text="After."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        real = intake._record

        def boom(*args, **kwargs):
            turn = args[4]
            if "Boom" in turn.text:
                raise RuntimeError("kiln")
            return real(*args, **kwargs)

        intake._record = boom
        try:
            code, out, err = self.invoke(root, "--from", "jsonl", "--once")
        finally:
            intake._record = real
        self.assertEqual(code, 0, err + out)
        self.assertEqual([row["text"] for row in self.rows(root)], ["Before.", "After."])
        code, status_out, _ = self.invoke(root, "status")
        self.assertIn("turn faults 1", status_out)
        self.invoke(root, "reread", "--from", "jsonl")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["Before.", "After.", "Boom in the kiln."])
        self.assertEqual([row["text"] for row in self.rows(root)].count("Before."), 1)
        # The reread ended without a fault, so status no longer shows the old one.
        self.assertNotIn("turn faults", self.invoke(root, "status")[1])

    def test_a_copied_turn_id_is_stored_once(self) -> None:
        # The kiln log's ids stand alone: the same id in a forked session is the same turn.
        folder = self.base / "kiln-logs"
        folder.mkdir()
        line = {"at": "2051-03-02T09:00:00+00:00", "role": "owner", "text": "The same glaze.", "id": "k-copied"}
        for session in ("ses-kiln-1", "ses-fork-1"):
            (folder / f"{session}.kiln").write_text(
                json.dumps({"head": {"session": session, "cwd": "/studio/kiln", "origin": "desk"}}) + "\n"
                + json.dumps(line) + "\n",
                encoding="utf-8",
            )
        root = self.forest(
            f"\n[intake.tools.kiln-log]\npath = {json.dumps(str(folder))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
        )
        with plugged():
            self.invoke(root, "--from", "kiln-log", "--once")
        texts = [row["text"] for row in self.rows(root)]
        self.assertEqual(texts.count("The same glaze."), 1)
        self.assertEqual(self.rows(root)[0]["id"], "k-copied")

    def test_tool_output_that_arrives_in_a_later_read_is_kept(self) -> None:
        folder = self.base / "kiln-logs"
        folder.mkdir()
        path = folder / "kiln.kiln"
        path.write_text(
            json.dumps({"head": {"session": "ses-kiln-1", "cwd": "/studio/kiln", "origin": "desk"}}) + "\n"
            + json.dumps({"at": "2051-03-02T09:00:00+00:00", "role": "owner", "text": "How hot?", "id": "k-1"}) + "\n"
            + json.dumps({"at": "2051-03-02T09:00:01+00:00", "role": "tool", "name": "read_file", "id": "k-call-9",
                          "pending": True}) + "\n",
            encoding="utf-8",
        )
        root = self.base / "kept"
        from memory_forest.layout import initialize

        initialize(root)
        settings = (root / "forest.toml").read_text(encoding="utf-8").replace(
            'tool_output = "omit"', 'tool_output = "keep"', 1
        )
        (root / "forest.toml").write_text(
            settings + f"\n[intake.tools.kiln-log]\npath = {json.dumps(str(folder))}\nimport_earlier = true\nagent = \"glaze-bot\"\n",
            encoding="utf-8",
        )
        with plugged():
            self.invoke(root, "--from", "kiln-log", "--once")
            self.assertNotIn("secret kiln log", self.lane(root))
            self.assertEqual([row["role"] for row in self.rows(root)], ["owner"])
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"output_for": "k-call-9", "text": "secret kiln log"}) + "\n")
            self.invoke(root, "--from", "kiln-log", "--once")
        tools = [row for row in self.rows(root) if row["role"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertIn("secret kiln log", tools[0]["text"])

    def test_the_marker_is_only_the_first_owner_turn(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "quoted.jsonl").write_text(
            record(id="turn-1", text="We talked about MEMORY_FOREST_INTERNAL in the docs.")
            + record(id="turn-2", text="MEMORY_FOREST_INTERNAL"),
            encoding="utf-8",
        )
        (folder / "stage.jsonl").write_text(
            record(id="turn-3", session="ses-stage-1", text="MEMORY_FOREST_INTERNAL")
            + record(id="turn-4", session="ses-stage-1", text="The stage went on."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        texts = [row["text"] for row in self.rows(root)]
        self.assertIn("We talked about MEMORY_FOREST_INTERNAL in the docs.", texts)
        self.assertNotIn("The stage went on.", texts)
        code, out, _ = self.invoke(root, "status")
        self.assertIn("internal 1", out)

    def test_a_short_session_with_no_owner_waits_then_is_ordinary(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", role="system", text="The kiln is warming."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(self.lane(root), "")
        extra = "".join(
            record(id=f"turn-{index}", role="system", text=f"Note {index}.")
            for index in range(2, TURNS_BEFORE_OWNER + 1)
        )
        path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(len(self.rows(root)), TURNS_BEFORE_OWNER)
        self.assertTrue(all(row["role"] == "system" for row in self.rows(root)))

    def test_a_hook_before_the_first_sweep_reads_nothing_and_is_counted(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="Already there."), encoding="utf-8")
        root = self.forest(self.tool(folder, earlier=False))
        payload = json.dumps({"transcript_path": str(path), "cwd": "/studio/kiln"})
        for _ in range(2):
            code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=payload)
            self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(self.lane(root), "")
        self.assertFalse((root / ".forest" / "intake" / "tools" / "jsonl" / "cursors").exists())
        status = self.invoke(root, "status")[1]
        self.assertIn("not turned on yet: 2 hook calls read nothing", status)
        self.assertIn("forest intake --from jsonl", status)
        # The first sweep turns intake on: the cursor goes to the end and the earlier turn is not taken.
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(self.lane(root), "")
        self.assertIn("2 hook calls came before intake was turned on", self.invoke(root, "status")[1])
        path.write_text(path.read_text(encoding="utf-8") + record(id="turn-2", text="After the sweep."), encoding="utf-8")
        (folder / "new.jsonl").write_text(record(id="turn-3", session="ses-new-1", text="A new one."), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=payload)
        self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(folder / "new.jsonl"), "cwd": "/studio/kiln",
        }))
        self.assertEqual([row["text"] for row in self.rows(root)], ["After the sweep.", "A new one."])

    def test_exclude_uses_the_transcript_folder_not_the_payload(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "shop.jsonl"
        path.write_text(record(id="turn-1", session="ses-shop-1", cwd="/studio/shop", text="Shop."), encoding="utf-8")
        root = self.forest(self.tool(folder, exclude="/studio/shop"))
        self.invoke(root, "--from", "jsonl", "--once")
        self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(path), "cwd": "/studio/kiln", "session_id": "ses-shop-1",
        }))
        self.assertEqual(self.lane(root), "")

    def test_rename_agent_moves_the_lane_and_a_clash_of_names_takes_nothing(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text="For the glaze bot."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--as", "kiln-bot", "--once")
        code, out, err = self.invoke(root, "rename-agent", "kiln-bot", "glaze-bot")
        self.assertEqual(code, 0, err + out)
        self.assertEqual([row["text"] for row in self.rows(root, "glaze-bot")], ["For the glaze bot."])
        self.assertFalse((root / "06 istm" / "kiln-bot").exists())
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(len(self.rows(root, "glaze-bot")), 1)

    def test_a_hook_argument_error_prints_nothing(self) -> None:
        root = self.forest("")
        err = io.StringIO()
        out = io.StringIO()
        old = sys.stderr, sys.stdout
        sys.stderr, sys.stdout = err, out
        try:
            code = forest_main(["intake", "--hook", "--from"])
        finally:
            sys.stderr, sys.stdout = old
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_a_hook_payload_that_does_not_arrive_is_a_failure(self) -> None:
        class Pipe:
            def fileno(self) -> int:
                return 0

            def read(self, _count: int) -> bytes:
                raise AssertionError("the wait should end the read")

        with self.assertRaises(MemoryForestError) as caught:
            read_hook_payload(Pipe(), seconds=5, wait=lambda _stream, _remaining: False, clock=lambda: 0)
        self.assertEqual(caught.exception.code, "hook_timeout")

    def test_a_plugin_cannot_end_the_process(self) -> None:
        real = jsonl_adapter.adapter.hook

        def boom(_payload, _settings):
            raise SystemExit(7)

        jsonl_adapter.adapter.hook = boom
        try:
            folder = self.base / "notes"
            folder.mkdir()
            root = self.forest(self.tool(folder))
            self.turn_on(root)
            code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin="{}")
        finally:
            jsonl_adapter.adapter.hook = real
        self.assertEqual((code, out, err), (0, "", ""))
        log = (root / ".forest" / "intake" / "failures.jsonl").read_text(encoding="utf-8")
        self.assertIn("plugin_failed", log)

    def test_a_hook_failure_is_logged_from_a_child_of_the_forest(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        root = self.forest(self.tool(folder))
        child = root / "studio"
        child.mkdir()
        old = os.getcwd()
        os.chdir(child)
        try:
            code = forest_main(["intake", "--from", "jsonl", "--hook"])
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        log = root / ".forest" / "intake" / "failures.jsonl"
        self.assertTrue(log.is_file())
        self.assertIn("hook", log.read_text(encoding="utf-8"))

    def test_ahead_uses_the_adapter_root_when_path_is_unset(self) -> None:
        folder = self.base / "sessions"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text="Waiting."), encoding="utf-8")
        root = self.forest('\n[intake.tools.jsonl]\nagent = "glaze-bot"\nenabled = true\n')
        cursor = intake.cursor_file(root, "jsonl", "kiln.jsonl")
        cursor.parent.mkdir(parents=True)
        cursor.write_text(json.dumps({"offset": 0, "source": "kiln.jsonl"}) + "\n", encoding="utf-8")
        real = jsonl_adapter.JsonlAdapter.transcript_root
        jsonl_adapter.JsonlAdapter.transcript_root = lambda self, settings: folder
        try:
            code, out, _ = self.invoke(root, "status")
        finally:
            jsonl_adapter.JsonlAdapter.transcript_root = real
        self.assertEqual(code, 0)
        self.assertIn("ahead kiln.jsonl", out)

    def test_a_hook_on_a_large_transcript_does_bounded_work(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        line = record(id="turn-0", text="The glaze crawled.")
        extra = record(id="turn-x", text="x" * 200)
        count = (HOOK_READ_BYTES // len(extra)) + 50
        path = folder / "kiln.jsonl"
        path.write_text(line + extra * count, encoding="utf-8")
        root = self.forest(self.tool(folder).replace("import_earlier = true", "import_earlier = true\nenabled = true"))
        self.turn_on(root)
        code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(path), "cwd": "/studio/kiln",
        }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertGreater(path.stat().st_size, HOOK_READ_BYTES)
        cursor = next((root / ".forest" / "intake" / "tools" / "jsonl" / "cursors").glob("*.json"))
        offset = json.loads(cursor.read_text(encoding="utf-8"))["offset"]
        self.assertLess(offset, path.stat().st_size)
        self.assertLessEqual(offset, HOOK_READ_BYTES + len(extra) + len(line))
        code, status_out, _ = self.invoke(root, "status")
        self.assertIn("ahead", status_out)

    def test_a_hook_the_adapter_defers_is_counted_and_the_sweep_takes_the_transcript(self) -> None:
        folder = self.base / "kiln-logs"
        folder.mkdir()
        (folder / "target.kiln").write_text(
            json.dumps({"head": {"session": "ses-target-1", "cwd": "/studio/kiln", "origin": "desk"}}) + "\n"
            + json.dumps({"at": "2051-03-02T09:00:01+00:00", "role": "owner", "text": "Found on the sweep.",
                          "id": "k-target"}) + "\n",
            encoding="utf-8",
        )
        root = self.forest(
            f"\n[intake.tools.kiln-log]\npath = {json.dumps(str(folder))}\nenabled = true\nimport_earlier = true\nagent = \"glaze-bot\"\n"
        )
        self.turn_on(root, "kiln-log")
        with plugged():
            code, out, err = self.invoke(root, "--from", "kiln-log", "--hook", "--once", stdin=json.dumps({
                "defer": True,
            }))
            self.assertEqual((code, out, err), (0, "", ""))
            self.assertEqual(self.lane(root), "")
            code, status_out, _ = self.invoke(root, "status")
            self.assertIn("waiting 1", status_out)
            self.invoke(root, "--from", "kiln-log", "--once")
        self.assertIn("Found on the sweep.", [row["text"] for row in self.rows(root)])
