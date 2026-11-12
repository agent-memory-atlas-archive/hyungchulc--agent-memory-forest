"""The second intake review. Every name is from the invented kiln.

A test asserts the thing that must not happen: the secret alone, the number of
lines, the bytes of the tree. Nothing here is copied from a real transcript.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_intake import IntakeTestCase, _claude, record  # noqa: E402

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

    def test_a_copied_turn_id_is_stored_once(self) -> None:
        folder = self.base / "claude"
        folder.mkdir()
        line = _claude("user", "The same glaze.", turn="c-copied")
        (folder / "ses-kiln-1.jsonl").write_text(line, encoding="utf-8")
        forked = json.loads(line)
        forked["sessionId"] = "ses-fork-1"
        (folder / "ses-fork-1.jsonl").write_text(json.dumps(forked) + "\n", encoding="utf-8")
        root = self.forest(
            f"\n[intake.tools.claude-code]\npath = {json.dumps(str(folder))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
        )
        self.invoke(root, "--from", "claude-code", "--once")
        texts = [row["text"] for row in self.rows(root)]
        self.assertEqual(texts.count("The same glaze."), 1)
        self.assertEqual(self.rows(root)[0]["id"], "c-copied")

    def test_grok_tool_output_that_arrives_later_is_kept(self) -> None:
        session = self.base / "grok" / "studio" / "ses-grok-1"
        session.mkdir(parents=True)
        (session / "summary.json").write_text(
            json.dumps({"info": {"id": "ses-grok-1", "cwd": "/studio/kiln"}, "session_kind": "interactive"}),
            encoding="utf-8",
        )
        stamp = 2560000000000
        call = {
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "tool_call", "toolCallId": "call-9",
                                  "_meta": {"x.ai/tool": {"name": "read_file"}}}},
            "timestamp": stamp,
        }
        user = {
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "user_message_chunk", "prompt_id": "p1",
                                  "content": {"type": "text", "text": "How hot?"}}},
            "timestamp": stamp - 1000,
        }
        path = session / "updates.jsonl"
        path.write_text(json.dumps(user) + "\n" + json.dumps(call) + "\n", encoding="utf-8")
        root = self.base / "kept"
        from memory_forest.layout import initialize

        initialize(root)
        settings = (root / "forest.toml").read_text(encoding="utf-8").replace(
            'tool_output = "omit"', 'tool_output = "keep"', 1
        )
        (root / "forest.toml").write_text(
            settings + f"\n[intake.tools.grok]\npath = {json.dumps(str(self.base / 'grok'))}\nimport_earlier = true\nagent = \"glaze-bot\"\n",
            encoding="utf-8",
        )
        self.invoke(root, "--from", "grok", "--once")
        self.assertNotIn("secret kiln log", self.lane(root))
        update = {
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "call-9",
                                  "status": "completed", "rawOutput": {"message": "secret kiln log"}}},
            "timestamp": stamp + 1000,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(update) + "\n")
        self.invoke(root, "--from", "grok", "--once")
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

    def test_a_hook_before_the_first_placement_does_not_import_history(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="Already there."), encoding="utf-8")
        root = self.forest(self.tool(folder, earlier=False))
        code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(path), "cwd": "/studio/kiln",
        }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(self.lane(root), "")
        path.write_text(path.read_text(encoding="utf-8") + record(id="turn-2", text="After the hook."), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(path), "cwd": "/studio/kiln",
        }))
        self.assertEqual([row["text"] for row in self.rows(root)], ["After the hook."])

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

    def test_compaction_and_command_output_are_not_the_owner(self) -> None:
        folder = self.base / "claude"
        folder.mkdir()
        compact = json.loads(_claude("user", "continued after compaction", turn="c-compact"))
        compact["isCompactSummary"] = True
        command = json.loads(_claude(
            "user", "<local-command-stdout>kiln status</local-command-stdout>", turn="c-command",
        ))
        owner = json.loads(_claude("user", "The glaze crawled.", turn="c-owner"))
        (folder / "ses-kiln-1.jsonl").write_text(
            json.dumps(compact) + "\n" + json.dumps(command) + "\n" + json.dumps(owner) + "\n",
            encoding="utf-8",
        )
        codex = self.base / "codex"
        codex.mkdir()
        lines = [
            {"timestamp": "2051-03-02T09:00:00+00:00", "type": "session_meta",
             "payload": {"id": "ses-codex-1", "cwd": "/studio/kiln", "source": "cli", "thread_source": "user"}},
            {"timestamp": "2051-03-02T09:00:01+00:00", "type": "response_item", "payload": {
                "type": "message", "role": "user", "id": "msg-env",
                "content": [{"type": "input_text", "text": "<environment_context>the studio</environment_context>"}],
            }},
            {"timestamp": "2051-03-02T09:00:02+00:00", "type": "response_item", "payload": {
                "type": "message", "role": "user", "id": "msg-owner",
                "content": [{"type": "input_text", "text": "Check the shelf."}],
            }},
            {"timestamp": "2051-03-02T09:00:03+00:00", "type": "compacted"},
        ]
        (codex / "rollout-kiln.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        root = self.forest(
            f"\n[intake.tools.claude-code]\npath = {json.dumps(str(folder))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
            f"\n[intake.tools.codex]\npath = {json.dumps(str(codex))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
        )
        self.invoke(root, "--from", "claude-code", "--once")
        self.invoke(root, "--from", "codex", "--once")
        rows = self.rows(root)
        self.assertEqual(next(row["role"] for row in rows if "compaction" in row["text"]), "system")
        self.assertEqual(next(row["role"] for row in rows if "kiln status" in row["text"]), "system")
        self.assertEqual(next(row["role"] for row in rows if "the studio" in row["text"]), "system")
        self.assertEqual(next(row["role"] for row in rows if row["text"] == "Check the shelf."), "owner")
        self.assertEqual(next(row["role"] for row in rows if row["text"] == "The glaze crawled."), "owner")

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

    def test_a_hook_on_a_large_transcript_does_bounded_work(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        line = record(id="turn-0", text="The glaze crawled.")
        extra = record(id="turn-x", text="x" * 200)
        count = (HOOK_READ_BYTES // len(extra)) + 50
        path = folder / "kiln.jsonl"
        path.write_text(line + extra * count, encoding="utf-8")
        root = self.forest(self.tool(folder).replace("import_earlier = true", "import_earlier = true\nenabled = true"))
        code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(path), "cwd": "/studio/kiln",
        }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertGreater(path.stat().st_size, HOOK_READ_BYTES)
        cursor = next((root / ".forest" / "intake" / "cursors" / "glaze-bot" / "jsonl").glob("*.json"))
        offset = json.loads(cursor.read_text(encoding="utf-8"))["offset"]
        self.assertLess(offset, path.stat().st_size)
        self.assertLessEqual(offset, HOOK_READ_BYTES + len(extra) + len(line))
        code, status_out, _ = self.invoke(root, "status")
        self.assertIn("ahead", status_out)

    def test_a_hook_does_not_walk_the_whole_tree(self) -> None:
        folder = self.base / "codex"
        folder.mkdir()
        for index in range(60):
            name = f"rollout-a{index:02d}.jsonl"
            (folder / name).write_text("{}\n", encoding="utf-8")
        target = folder / "rollout-z-ses-target-1.jsonl"
        target.write_text(
            json.dumps({"timestamp": "2051-03-02T09:00:00+00:00", "type": "session_meta",
                        "payload": {"id": "ses-target-1", "cwd": "/studio/kiln", "source": "cli"}}) + "\n"
            + json.dumps({"timestamp": "2051-03-02T09:00:01+00:00", "type": "response_item", "payload": {
                "type": "message", "role": "user", "id": "msg-target",
                "content": [{"type": "input_text", "text": "Found on the sweep."}]}}) + "\n",
            encoding="utf-8",
        )
        root = self.forest(
            f"\n[intake.tools.codex]\npath = {json.dumps(str(folder))}\nenabled = true\nimport_earlier = true\nagent = \"glaze-bot\"\n"
        )
        code, out, err = self.invoke(root, "--from", "codex", "--hook", "--once", stdin=json.dumps({
            "session_id": "ses-target-1",
        }))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(self.lane(root), "")
        code, status_out, _ = self.invoke(root, "status")
        self.assertIn("waiting", status_out)
        self.invoke(root, "--from", "codex", "--once")
        self.assertIn("Found on the sweep.", [row["text"] for row in self.rows(root)])

    def test_an_interruption_after_each_write_leaves_every_turn_once(self) -> None:
        steps = ("append", "ids", "end", "cursor")
        for bounded in (False, True):
            for step in steps:
                with self.subTest(bounded=bounded, step=step):
                    self._interrupted(step, bounded=bounded)

    def _interrupted(self, step: str, *, bounded: bool) -> None:
        folder = self.base / f"notes-{int(bounded)}-{step}"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(
            record(id="turn-1", text=f"password: {SECRET}")
            + record(id="turn-2", text="The shelf cooled."),
            encoding="utf-8",
        )
        claude = self.base / f"claude-{int(bounded)}-{step}"
        claude.mkdir()
        (claude / "ses-kiln-1.jsonl").write_text(_claude("user", "Claude glaze.", turn="c-1"), encoding="utf-8")
        codex = self.base / f"codex-{int(bounded)}-{step}"
        codex.mkdir()
        (codex / "rollout-kiln.jsonl").write_text(
            json.dumps({"timestamp": "2051-03-02T09:00:00+00:00", "type": "session_meta",
                        "payload": {"id": "ses-codex-1", "cwd": "/studio/kiln", "source": "cli", "thread_source": "user"}}) + "\n"
            + json.dumps({"timestamp": "2051-03-02T09:00:01+00:00", "type": "response_item", "payload": {
                "type": "message", "role": "user", "id": "msg-1",
                "content": [{"type": "input_text", "text": "Codex glaze."}]}}) + "\n",
            encoding="utf-8",
        )
        grok = self.base / f"grok-{int(bounded)}-{step}" / "studio" / "ses-grok-1"
        grok.mkdir(parents=True)
        (grok / "summary.json").write_text(
            json.dumps({"info": {"id": "ses-grok-1", "cwd": "/studio/kiln"}, "session_kind": "interactive"}),
            encoding="utf-8",
        )
        (grok / "updates.jsonl").write_text(json.dumps({
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "user_message_chunk", "prompt_id": "p1",
                                  "content": {"type": "text", "text": "Grok glaze."}}},
            "timestamp": 2560000000000,
        }) + "\n", encoding="utf-8")
        root = self.forest(
            self.tool(folder)
            + f"\n[intake.tools.claude-code]\npath = {json.dumps(str(claude))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
            + f"\n[intake.tools.codex]\npath = {json.dumps(str(codex))}\nimport_earlier = true\nagent = \"glaze-bot\"\n"
            + f"\n[intake.tools.grok]\npath = {json.dumps(str(grok.parents[1]))}\nimport_earlier = true\nagent = \"glaze-bot\"\n",
            name=f"oven-{int(bounded)}-{step}",
        )

        def boom(name: str) -> None:
            if name == step:
                raise OSError("injected")

        intake._CHECKPOINT = boom
        try:
            for name, payload in (
                ("jsonl", {"transcript_path": str(path), "cwd": "/studio/kiln"}),
                ("claude-code", {"transcript_path": str(claude / "ses-kiln-1.jsonl"), "cwd": "/studio/kiln"}),
                ("codex", {"transcript_path": str(codex / "rollout-kiln.jsonl"), "cwd": "/studio/kiln", "session_id": "ses-codex-1"}),
                ("grok", {"sessionId": "ses-grok-1", "cwd": "/studio/kiln"}),
            ):
                args = ["--from", name, "--once"]
                stdin = ""
                if bounded:
                    args.append("--hook")
                    stdin = json.dumps(payload)
                self.invoke(root, *args, stdin=stdin)
        finally:
            intake._CHECKPOINT = None
        for name, payload in (
            ("jsonl", {"transcript_path": str(path), "cwd": "/studio/kiln"}),
            ("claude-code", {"transcript_path": str(claude / "ses-kiln-1.jsonl"), "cwd": "/studio/kiln"}),
            ("codex", {"transcript_path": str(codex / "rollout-kiln.jsonl"), "cwd": "/studio/kiln", "session_id": "ses-codex-1"}),
            ("grok", {"sessionId": "ses-grok-1", "cwd": "/studio/kiln"}),
        ):
            args = ["--from", name, "--once"]
            stdin = ""
            if bounded:
                args.append("--hook")
                stdin = json.dumps(payload)
            self.invoke(root, *args, stdin=stdin)
        texts = [row["text"] for row in self.rows(root)]
        for text in ("The shelf cooled.", "Claude glaze.", "Codex glaze.", "Grok glaze."):
            self.assertEqual(texts.count(text), 1, texts)
        self.assertNotIn(SECRET, self.lane(root))
        code, out, _ = self.invoke(root, "status")
        self.assertEqual(code, 0)
        self.assertIn("jsonl:", out)
