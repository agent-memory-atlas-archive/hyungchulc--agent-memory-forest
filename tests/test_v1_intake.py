"""Intake: a made-up transcript becomes one agent's raw lane, and a second run adds nothing.

Every name here belongs to the invented bakery (Ilse Varrow, her oven, the glaze
bot). Nothing is copied from a real transcript.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import REPOSITORY_ROOT, ForestTestCase  # noqa: E402

from memory_forest.cli import forest_main  # noqa: E402
from memory_forest.credentials import REDACTED  # noqa: E402
from memory_forest.intake import cursor_file  # noqa: E402
from memory_forest.layout import initialize  # noqa: E402


def _tree_bytes(root: Path) -> dict[str, tuple[int, bytes]]:
    found = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            stat = path.stat()
            found[str(path.relative_to(root))] = (stat.st_mtime_ns, path.read_bytes())
    return found


def record(**fields: object) -> str:
    body = {
        "at": "2051-03-02T09:00:00+00:00",
        "cwd": "/studio/kiln",
        "origin": "interactive",
        "role": "owner",
        "session": "ses-kiln-1",
        "text": "The glaze crawled.",
    }
    body.update(fields)
    return json.dumps(body) + "\n"


class IntakeTestCase(ForestTestCase):
    def forest(self, extra: str = "", name: str = "oven") -> Path:
        root = self.base / name
        initialize(root)
        if extra:
            path = root / "forest.toml"
            path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
        return root

    def tool(self, folder: Path, *, earlier: bool = True, include: str = "", exclude: str = "") -> str:
        lines = [
            "\n[intake.tools.jsonl]",
            f"path = {json.dumps(str(folder))}",
            f"import_earlier = {'true' if earlier else 'false'}",
            "agent = \"glaze-bot\"",
        ]
        if include:
            lines.append(f"include = [{json.dumps(include)}]")
        if exclude:
            lines.append(f"exclude = [{json.dumps(exclude)}]")
        return "\n".join(lines) + "\n"

    def turn_on(self, root: Path, *adapters: str) -> None:
        """Intake turned on for these tools, as when their first sweep ran before these transcripts existed."""
        from memory_forest import intake

        for name in adapters or ("jsonl",):
            intake._write_meta(root, name, {"placed": True})

    def invoke(self, root: Path, *args: str, stdin: str = "") -> tuple[int, str, str]:
        old = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib_stdout(out, err):
                code = forest_main(["intake", *args, "--root", str(root)])
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def lane(self, root: Path, agent: str = "glaze-bot") -> str:
        folder = root / "06 istm" / agent
        if not folder.is_dir():
            return ""
        return "".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.jsonl")))

    def rows(self, root: Path, agent: str = "glaze-bot") -> list[dict]:
        text = self.lane(root, agent)
        return [json.loads(line) for line in text.split("\n") if line]


class Redirect:
    def __init__(self, out: io.StringIO, err: io.StringIO) -> None:
        self.out = out
        self.err = err
        self._out = None
        self._err = None

    def __enter__(self) -> None:
        self._out, self._err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.out, self.err

    def __exit__(self, *exc: object) -> None:
        sys.stdout, sys.stderr = self._out, self._err


def contextlib_stdout(out: io.StringIO, err: io.StringIO) -> Redirect:
    return Redirect(out, err)


class WriterTests(IntakeTestCase):
    def test_a_second_run_adds_nothing_and_the_cursor_resumes(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="The glaze crawled."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 0, out)
        self.assertEqual([row["text"] for row in self.rows(root)], ["The glaze crawled."])
        path.write_text(path.read_text(encoding="utf-8") + record(id="turn-2", text="Hold it at cone 6."), encoding="utf-8")
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 0, out)
        self.assertIn("1 turn taken in", out)
        self.assertEqual([row["text"] for row in self.rows(root)], ["The glaze crawled.", "Hold it at cone 6."])
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertIn("0 turns taken in", out)
        self.assertEqual(len(self.rows(root)), 2)

    def test_a_half_written_last_line_is_left(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1") + "{\"text\": \"still", encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(len(self.rows(root)), 1)
        path.write_text(
            record(id="turn-1") + record(id="turn-2", text="The shelf cooled."),
            encoding="utf-8",
        )
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["The glaze crawled.", "The shelf cooled."])

    def test_a_cursor_can_start_in_the_middle(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        first = record(id="turn-1", text="First.")
        path = folder / "kiln.jsonl"
        path.write_text(first + record(id="turn-2", text="Second."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        state = {
            "described": True,
            "offset": len(first.encode("utf-8")),
            "origin": "interactive",
            "session": "ses-kiln-1",
            "source": "kiln.jsonl",
        }
        target = cursor_file(root, "jsonl", "kiln.jsonl")
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(state), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["Second."])

    def test_an_unknown_line_is_counted(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1") + "{\"nope\": true}\n", encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        code, out, _ = self.invoke(root, "status")
        self.assertEqual(code, 0)
        self.assertIn("jsonl: off, agent glaze-bot, unknown 1", out)
        self.assertEqual(len(self.rows(root)), 1)

    def test_numbers_and_sign_in_values_are_masked_at_the_door(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        text = "Account 1234 5678 9012 and PIN: 4821."
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text=text), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        stored = self.lane(root)
        self.assertNotIn("1234 5678 9012", stored)
        self.assertNotIn("4821", stored)
        self.assertIn(REDACTED, stored)
        self.assertIn("123*", stored)

    def test_a_marked_paste_keeps_its_span(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        text = "See this.\n<pasted_contents>\nPIN: 4821\n</pasted_contents>"
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text=text), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        row = self.rows(root)[0]
        part = row["parts"][0]
        self.assertEqual(part["kind"], "paste")
        self.assertIn(REDACTED, row["text"][part["start"]:part["end"]])
        self.assertIn("<pasted_contents>", row["text"])

    def test_tool_output_is_left_out_unless_kept(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            record(id="turn-0", text="Look at the log.")
            + record(id="turn-1", role="tool", name="read", text="secret kiln log"),
            encoding="utf-8",
        )
        omitted = self.forest(self.tool(folder))
        self.invoke(omitted, "--from", "jsonl", "--once")
        row = next(item for item in self.rows(omitted) if item["role"] == "tool")
        self.assertEqual(row["text"], "ran read")
        self.assertEqual(row["name"], "read")
        self.assertNotIn("secret kiln log", self.lane(omitted))
        kept_root = self.base / "kept"
        initialize(kept_root)
        settings = (kept_root / "forest.toml").read_text(encoding="utf-8").replace(
            'tool_output = "omit"', 'tool_output = "keep"', 1
        )
        (kept_root / "forest.toml").write_text(settings + self.tool(folder), encoding="utf-8")
        self.invoke(kept_root, "--from", "jsonl", "--once")
        kept = next(item for item in self.rows(kept_root) if item["role"] == "tool")
        self.assertEqual(kept["text"], "secret kiln log")

    def test_include_and_exclude_follow_the_session_folder(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1", cwd="/studio/kiln", text="Kiln."), encoding="utf-8")
        (folder / "shop.jsonl").write_text(
            record(id="turn-2", session="ses-shop-1", cwd="/studio/shop", text="Shop."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder, include="/studio/kiln"))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["Kiln."])
        other = self.forest(self.tool(folder, exclude="/studio/shop"), name="oven-exclude")
        self.invoke(other, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(other)], ["Kiln."])

    def test_import_earlier_is_false_then_true(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="Earlier.") + record(id="turn-2", text="Also earlier."), encoding="utf-8")
        root = self.forest(self.tool(folder, earlier=False))
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertIn("0 turns taken in", out)
        self.assertEqual(self.lane(root), "")
        path.write_text(path.read_text(encoding="utf-8") + record(id="turn-3", text="After."), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["After."])
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8").replace("import_earlier = false", "import_earlier = true", 1), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        # The lane is append-only, so the turns taken after import_earlier is turned
        # on follow the turn that was already written.
        self.assertEqual(
            [row["text"] for row in self.rows(root)],
            ["After.", "Earlier.", "Also earlier."],
        )

    def test_a_new_file_after_the_first_run_is_read(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "old.jsonl").write_text(record(id="turn-1", text="Old."), encoding="utf-8")
        root = self.forest(self.tool(folder, earlier=False))
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(self.lane(root), "")
        (folder / "new.jsonl").write_text(record(id="turn-2", session="ses-kiln-2", text="New."), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["New."])

    def test_off_takes_nothing_until_once(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1"), encoding="utf-8")
        root = self.forest(self.tool(folder))
        code, out, _ = self.invoke(root, "--from", "jsonl")
        self.assertEqual(code, 0)
        self.assertIn("intake for jsonl is off", out)
        self.assertIn("[intake.tools.jsonl] enabled = true", out)
        self.assertEqual(self.lane(root), "")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(len(self.rows(root)), 1)

    def test_a_missing_agent_says_how_to_set_it(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        root = self.forest(f"\n[intake.tools.jsonl]\npath = {json.dumps(str(folder))}\n")
        code, _, err = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 2)
        self.assertIn("intake_agent_unset", err)
        self.assertIn("--as", err)
        self.assertIn("[intake.tools.jsonl]", err)

    def test_the_hook_reads_json_on_stdin(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        path = folder / "kiln.jsonl"
        path.write_text(record(id="turn-1", text="From the hook."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.turn_on(root)
        payload = json.dumps({"transcript_path": str(path), "session_id": "ses-kiln-1", "cwd": "/studio/kiln"})
        # The hook reads only this transcript. It prints nothing: a client's hook
        # treats stdout as model context and a non-zero exit as a block.
        code, out, err = self.invoke(root, "--from", "jsonl", "--as", "glaze-bot", "--once", "--hook", stdin=payload)
        self.assertEqual(code, 0, err + out)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        self.assertEqual([row["text"] for row in self.rows(root)], ["From the hook."])

    def test_the_lock_gives_way(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1"), encoding="utf-8")
        (folder / "shelf.jsonl").write_text(record(id="turn-s", session="ses-shelf-1", text="Shelf."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.turn_on(root)
        holder = (
            "import sys\n"
            "from memory_forest.locking import hold_lock\n"
            "with hold_lock(sys.argv[1], 'intake', label='held'):\n"
            "    sys.stdout.write('held\\n')\n"
            "    sys.stdout.flush()\n"
            "    sys.stdin.readline()\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
        proc = subprocess.Popen(
            [sys.executable, "-c", holder, str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )
        try:
            self.assertEqual(proc.stdout.readline() if proc.stdout else b"", b"held\n")
            code, out, err = self.invoke(root, "--from", "jsonl", "--once")
            self.assertEqual(code, 0, err)
            self.assertIn("left its cursor", out)
            self.assertEqual(self.lane(root), "")
            code, out, err = self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
                "transcript_path": str(folder / "kiln.jsonl"), "cwd": "/studio/kiln",
            }))
            self.assertEqual((code, out, err), (0, "", ""))
            code, status_out, _ = self.invoke(root, "status")
            self.assertEqual(code, 0)
            self.assertIn("busy 1", status_out)
        finally:
            if proc.stdin is not None:
                proc.stdin.write(b"\n")
                proc.stdin.close()
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait(timeout=5)
        # The marker is the transcript's: a run for another transcript leaves it, the run that reads it removes it.
        self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(folder / "shelf.jsonl"), "cwd": "/studio/kiln",
        }))
        self.assertIn("busy 1", self.invoke(root, "status")[1])
        self.invoke(root, "--from", "jsonl", "--hook", "--once", stdin=json.dumps({
            "transcript_path": str(folder / "kiln.jsonl"), "cwd": "/studio/kiln",
        }))
        self.assertNotIn("busy", self.invoke(root, "status")[1])

    def test_the_index_is_rebuilt_from_the_lane(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1", text="Once."), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        ids = root / ".forest" / "intake" / "ids" / "glaze-bot" / "2051-03-02.ids"
        self.assertTrue(ids.is_file())
        ids.unlink()
        end = ids.with_suffix(".end")
        if end.is_file():
            end.unlink()
        state_path = cursor_file(root, "jsonl", "kiln.jsonl")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["offset"] = 0
        state.pop("dev", None)
        state.pop("prefix", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual([row["text"] for row in self.rows(root)], ["Once."])
        rebuilt = ids.read_text(encoding="utf-8")
        self.assertIn(":turn-1", rebuilt)

    def test_intake_then_levels_check_reports_the_agent_due(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1"), encoding="utf-8")
        root = self.forest(self.tool(folder))
        self.invoke(root, "--from", "jsonl", "--once")
        out, err = io.StringIO(), io.StringIO()
        with Redirect(out, err):
            code = forest_main(["levels", "check", "--root", str(root)])
        self.assertEqual(code, 1)
        self.assertIn("glaze-bot", out.getvalue())
        self.assertIn("none -> tree", out.getvalue())
        from memory_forest.model import parse_relative_route

        lane = next((root / "06 istm" / "glaze-bot").glob("*.jsonl"))
        route = parse_relative_route(f"06 istm/glaze-bot/{lane.name}")
        self.assertEqual(route.domain, "glaze-bot")
        self.assertEqual(route.path, f"06 istm/glaze-bot/{lane.name}")

    def test_dry_run_writes_nothing(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(record(id="turn-1"), encoding="utf-8")
        root = self.forest(self.tool(folder))
        before = _tree_bytes(root)
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("1 turn would be taken in", out)
        self.assertEqual(_tree_bytes(root), before)
        self.assertEqual(self.lane(root), "")

    def test_an_internal_session_is_not_taken_in(self) -> None:
        folder = self.base / "notes"
        folder.mkdir()
        (folder / "kiln.jsonl").write_text(
            record(id="turn-1", text="MEMORY_FOREST_INTERNAL\nA forest stage."),
            encoding="utf-8",
        )
        root = self.forest(self.tool(folder))
        code, out, _ = self.invoke(root, "--from", "jsonl", "--once")
        self.assertEqual(code, 0)
        self.assertIn("MEMORY_FOREST_INTERNAL", out)
        self.assertEqual(self.lane(root), "")


class AdapterTests(IntakeTestCase):
    def test_claude_code_codex_and_grok(self) -> None:
        root = self.forest(self._adapters())
        self._claude()
        self._codex()
        self._grok()
        code, out, err = self.invoke(root, "--from", "claude-code", "--as", "glaze-bot", "--once")
        self.assertEqual(code, 0, err + out)
        code, out, err = self.invoke(root, "--from", "codex", "--as", "glaze-bot", "--once")
        self.assertEqual(code, 0, err + out)
        code, out, err = self.invoke(root, "--from", "grok", "--as", "glaze-bot", "--once")
        self.assertEqual(code, 0, err + out)
        rows = self.rows(root)
        texts = [row["text"] for row in rows]
        self.assertTrue(any(text.startswith("The glaze crawled.") for text in texts))
        self.assertIn("ran read", texts)
        self.assertNotIn("secret kiln log", "\n".join(texts))
        self.assertEqual(next(row["origin"] for row in rows if row["text"] == "Run it."), "headless")
        self.assertEqual(next(row["origin"] for row in rows if row["text"] == "How hot is the kiln?"), "headless")
        self.assertTrue(any(row["origin"] == "subagent" and row["role"] == "tool" for row in rows))
        self.assertFalse(any(row["role"] == "owner" and row["origin"] == "subagent" for row in rows))
        owners = [row["text"] for row in rows if row["role"] == "owner"]
        self.assertNotIn("The helper heard this.", owners)
        code, out, _ = self.invoke(root, "status")
        self.assertIn("unknown 1", out)
        # A second run of each adapter adds nothing.
        before = len(rows)
        for name in ("claude-code", "codex", "grok"):
            self.invoke(root, "--from", name, "--as", "glaze-bot", "--once")
        self.assertEqual(len(self.rows(root)), before)

    def test_claude_keeps_tool_output_and_a_file_part(self) -> None:
        self._claude()
        root = self.base / "kept"
        initialize(root)
        settings = (root / "forest.toml").read_text(encoding="utf-8").replace(
            'tool_output = "omit"', 'tool_output = "keep"', 1
        )
        (root / "forest.toml").write_text(settings + self._adapters(), encoding="utf-8")
        self.invoke(root, "--from", "claude-code", "--as", "glaze-bot", "--once")
        rows = self.rows(root)
        self.assertTrue(any(row["text"] == "secret kiln log" and row["name"] == "read" for row in rows))
        files = [row for row in rows if row.get("parts") and row["parts"][0]["kind"] == "file"]
        self.assertTrue(files)
        self.assertIn("a note from the bench", files[0]["text"])

    def _adapters(self) -> str:
        return (
            f"\n[intake.tools.claude-code]\npath = {json.dumps(str(self.base / 'claude'))}\nimport_earlier = true\n"
            f"\n[intake.tools.codex]\npath = {json.dumps(str(self.base / 'codex'))}\nimport_earlier = true\n"
            f"\n[intake.tools.grok]\npath = {json.dumps(str(self.base / 'grok'))}\nimport_earlier = true\n"
        )

    def _claude(self) -> None:
        folder = self.base / "claude"
        folder.mkdir(exist_ok=True)
        lines = [
            _claude("user", "The glaze crawled.\n<pasted_contents>\ncone 6\n</pasted_contents>", turn="c-user"),
            _claude(
                "assistant",
                "",
                turn="c-agent",
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I will check the log."},
                        {"type": "tool_use", "id": "tool-kiln-1", "name": "read", "input": {"path": "log.txt"}},
                    ],
                },
            ),
            _claude(
                "user",
                "",
                turn="c-result",
                message={
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tool-kiln-1", "content": "secret kiln log"}],
                },
            ),
            json.dumps(
                {
                    "type": "attachment",
                    "uuid": "c-file",
                    "sessionId": "ses-kiln-1",
                    "timestamp": "2051-03-02T09:02:00+00:00",
                    "cwd": "/studio/kiln",
                    "entrypoint": "cli",
                    "attachment": {"type": "file", "filename": "note.txt", "content": "a note from the bench"},
                }
            ),
            _claude("user", "The helper heard this.", turn="c-side", side=True, agent="glaze-helper"),
            _claude("assistant", "The helper checked the cone.", turn="c-side-2", side=True, agent="glaze-helper"),
            json.dumps({"type": "not-a-claude-line", "uuid": "c-odd"}) + "\n",
        ]
        (folder / "ses-kiln-1.jsonl").write_text("\n".join(line.rstrip("\n") for line in lines) + "\n", encoding="utf-8")
        (folder / "ses-head-1.jsonl").write_text(
            _claude("user", "Run it.", turn="c-head", entry="sdk-cli", session="ses-head-1"),
            encoding="utf-8",
        )

    def _codex(self) -> None:
        folder = self.base / "codex"
        folder.mkdir(exist_ok=True)
        lines = [
            {
                "timestamp": "2051-03-02T09:00:00+00:00",
                "type": "session_meta",
                "payload": {"id": "ses-codex-1", "cwd": "/studio/kiln", "source": "cli", "thread_source": "user"},
            },
            {
                "timestamp": "2051-03-02T09:00:01+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "id": "msg-1",
                    "content": [{"type": "input_text", "text": "Check the kiln shelf."}],
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "user"}}]
                    },
                },
            },
            {
                "timestamp": "2051-03-02T09:00:02+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "id": "msg-dev",
                    "content": [{"type": "input_text", "text": "system instructions that are not the owner"}],
                },
            },
            {
                "timestamp": "2051-03-02T09:00:03+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "id": "msg-2",
                    "content": [{"type": "output_text", "text": "Looking now."}],
                },
            },
            {
                "timestamp": "2051-03-02T09:00:04+00:00",
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "call-1", "input": "ls"},
            },
            {
                "timestamp": "2051-03-02T09:00:05+00:00",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": "secret kiln log"}],
                },
            },
            {"timestamp": "2051-03-02T09:00:06+00:00", "type": "event_msg", "payload": {"type": "token_count"}},
            {"timestamp": "2051-03-02T09:00:07+00:00", "type": "not-a-codex-line"},
        ]
        sub = {
            "timestamp": "2051-03-02T09:03:00+00:00",
            "type": "session_meta",
            "payload": {"id": "ses-codex-sub", "cwd": "/studio/kiln", "thread_source": "subagent", "source": {"subagent": {}}},
        }
        sub_user = {
            "timestamp": "2051-03-02T09:03:01+00:00",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "id": "msg-sub",
                "content": [{"type": "output_text", "text": "Subagent note."}],
            },
        }
        (folder / "rollout-kiln.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        (folder / "rollout-sub.jsonl").write_text(json.dumps(sub) + "\n" + json.dumps(sub_user) + "\n", encoding="utf-8")

    def _grok(self) -> None:
        session = self.base / "grok" / "studio" / "ses-grok-1"
        session.mkdir(parents=True)
        (session / "summary.json").write_text(
            json.dumps({"info": {"id": "ses-grok-1", "cwd": "/studio/kiln"}, "session_kind": "headless"}),
            encoding="utf-8",
        )
        stamp = int(datetime(2051, 3, 2, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)
        lines = [
            {
                "method": "session/update",
                "params": {
                    "sessionId": "ses-grok-1",
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "prompt_id": "p1",
                        "content": {"type": "text", "text": "How hot is the kiln?"},
                    },
                },
                "timestamp": stamp,
            },
            {
                "method": "session/update",
                "params": {
                    "sessionId": "ses-grok-1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "prompt_id": "p1",
                        "content": {"type": "text", "text": "Hot enough."},
                    },
                },
                "timestamp": stamp + 1000,
            },
            {
                "method": "session/update",
                "params": {
                    "sessionId": "ses-grok-1",
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "call-9",
                        "_meta": {"x.ai/tool": {"name": "read_file"}},
                    },
                },
                "timestamp": stamp + 2000,
            },
            {
                "method": "session/update",
                "params": {
                    "sessionId": "ses-grok-1",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "call-9",
                        "status": "completed",
                        "rawOutput": {"message": "secret kiln log"},
                    },
                },
                "timestamp": stamp + 3000,
            },
            {"method": "nope", "timestamp": stamp + 4000},
        ]
        (session / "updates.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def _claude(
    kind: str,
    text: str,
    *,
    turn: str,
    message: dict | None = None,
    side: bool = False,
    agent: str = "",
    entry: str = "cli",
    session: str = "ses-kiln-1",
) -> str:
    if message is None:
        role = "assistant" if kind == "assistant" else "user"
        message = {"role": role, "content": [{"type": "text", "text": text}]}
    body = {
        "cwd": "/studio/kiln",
        "entrypoint": entry,
        "isSidechain": side,
        "message": message,
        "sessionId": session,
        "timestamp": "2051-03-02T09:00:00+00:00",
        "type": kind,
        "uuid": turn,
    }
    if agent:
        body["agentName"] = agent
    return json.dumps(body) + "\n"


if __name__ == "__main__":
    unittest.main()
