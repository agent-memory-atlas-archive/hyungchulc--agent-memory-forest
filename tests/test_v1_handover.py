"""The record file is a hand-over, not an archive: intake says when it holds nothing that waits.

An agent's exporter appends its turns to ``transcripts/<id>.jsonl`` as it had them, unmasked, and the lane
keeps the masked copy. ``read_to_end`` in intake's answer is the one fact that says the file may be emptied:
every byte of it has been read and decided. A line that waits (for the owner turn, for its newline) keeps
the file, so emptying it by that fact never loses a turn. Juno's studio and Kiln Bot are invented.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import bounds  # noqa: F401  (installs a deadline for every unittest invocation)
from memory_forest import cli


def forest(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    feed = io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8")
    with redirect_stdout(out), redirect_stderr(err), mock.patch.object(sys, "stdin", feed):
        try:
            code = cli.forest_main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def line(at: str, role: str, text: str, session: str = "s1") -> str:
    return json.dumps({"at": f"2042-05-14T{at}-03:00", "session": session, "role": role, "text": text}) + "\n"


class HandOverTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "studio-forest"
        code, out, err = forest(
            "setup", "--root", str(self.root), "--as", "Kiln Bot", "--main", "yes", "--conversations", "all",
            "--earlier", "no", "--keeper", "nobody", "--timezone", "America/Halifax", "--json",
        )
        self.assertEqual(code, 0, out + err)
        done = json.loads(out)
        self.agent = done["agent"]["id"]
        self.file = self.root / "transcripts" / f"{self.agent}.jsonl"
        self.assertEqual(self.file.read_bytes(), b"")

    def take(self, *extra: str) -> dict:
        code, out, err = forest(
            "intake", "--from", "jsonl", "--as", self.agent, "--transcript", str(self.file), "--once",
            "--root", str(self.root), "--json", *extra,
        )
        self.assertEqual(code, 0, out + err)
        answer = json.loads(out)
        self.assertEqual([entry["transcript"] for entry in answer["files"]], [self.file.name])
        return answer["files"][0]

    def lane(self) -> list[dict]:
        rows = []
        for path in sorted((self.root / "06 istm" / self.agent).glob("*.jsonl")):
            rows += [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row]
        return rows

    def test_a_file_taken_whole_says_so_and_emptying_it_loses_nothing(self) -> None:
        self.file.write_text(line("09:15:00", "owner", "Fire glaze loads to cone 5 for now.")
                             + line("09:15:20", "agent", "Done: cone 5 until ten firings have run."))
        self.assertEqual(self.take(), {"note": "", "read_to_end": True, "transcript": self.file.name,
                                       "turns": 2, "unknown": 0})
        # The same file again: nothing new, and still nothing that waits.
        self.assertEqual(self.take(), {"note": "", "read_to_end": True, "transcript": self.file.name,
                                       "turns": 0, "unknown": 0})
        self.file.write_bytes(b"")
        self.assertTrue(self.take()["read_to_end"])
        # Shorter than what was there, then longer: both are read from the start, once.
        self.file.write_text(line("09:20:00", "owner", "Good."))
        self.assertEqual((self.take()["turns"], self.take()["turns"]), (1, 0))
        self.file.write_bytes(b"")
        self.file.write_text(
            line("09:30:00", "owner", "Start G6-slow on Kiln Two at 07:40 tomorrow, all thirty-eight pieces.")
            + line("09:30:30", "agent", "G6-slow is set for Kiln Two at 07:40 tomorrow with thirty-eight pieces.")
            + line("09:31:00", "owner", "And tell the Thursday class the kiln room is closed until Friday noon."))
        entry = self.take()
        self.assertEqual((entry["turns"], entry["read_to_end"]), (3, True))
        texts = [row["text"] for row in self.lane()]
        self.assertEqual(len(texts), 6)
        self.assertEqual(len(set(texts)), 6)

    def test_a_turn_that_waits_for_the_owner_keeps_the_file(self) -> None:
        self.file.write_text(line("09:15:00", "owner", "Fire glaze loads to cone 5 for now."))
        self.assertTrue(self.take()["read_to_end"])
        self.file.write_bytes(b"")
        self.file.write_text(line("09:20:00", "agent", "Kiln Two is loaded with thirty-eight pieces."))
        entry = self.take()
        self.assertEqual((entry["turns"], entry["read_to_end"], entry["note"]),
                         (0, False, "waiting for the owner turn"))
        self.assertEqual(len(self.lane()), 1)
        with self.file.open("a", encoding="utf-8") as handle:
            handle.write(line("09:21:00", "owner", "Good. Start it at 07:40."))
        entry = self.take()
        self.assertEqual((entry["turns"], entry["read_to_end"], entry["note"]), (2, True, ""))
        self.assertEqual([row["role"] for row in self.lane()], ["owner", "agent", "owner"])

    def test_a_line_without_its_newline_keeps_the_file(self) -> None:
        whole = line("09:15:00", "owner", "Fire glaze loads to cone 5 for now.")
        partial = line("09:15:20", "agent", "Done: cone 5 until ten firings have run.")
        self.file.write_text(whole + partial[:-1])
        entry = self.take()
        self.assertEqual((entry["turns"], entry["read_to_end"]), (1, False))
        with self.file.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        entry = self.take()
        self.assertEqual((entry["turns"], entry["read_to_end"]), (1, True))

    def test_a_dry_run_never_says_the_file_may_be_emptied(self) -> None:
        self.file.write_text(line("09:15:00", "owner", "Fire glaze loads to cone 5 for now."))
        entry = self.take("--dry-run")
        self.assertEqual((entry["turns"], entry["read_to_end"]), (1, False))
        self.assertEqual(self.lane(), [])

    def test_the_words_as_they_were_said_leave_the_forest_with_the_file(self) -> None:
        said = "The door code is 4471."
        self.file.write_text(line("10:25:00", "owner", said)
                             + line("10:26:00", "agent", "I will tell the Thursday class."))
        self.assertTrue(self.take()["read_to_end"])
        self.assertNotIn("4471", json.dumps(self.lane()))
        self.assertIn(b"4471", self.file.read_bytes())
        self.file.write_bytes(b"")
        holding = [str(path.relative_to(self.root)) for path in sorted(self.root.rglob("*"))
                   if path.is_file() and b"4471" in path.read_bytes()]
        self.assertEqual(holding, [])


if __name__ == "__main__":
    unittest.main()
