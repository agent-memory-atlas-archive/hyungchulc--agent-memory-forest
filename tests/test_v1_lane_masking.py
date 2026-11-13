"""Lanes written under earlier masking rules, and an index stored under an earlier masking version.

Every value is made up for the test; the names are from the invented kiln.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_intake import IntakeTestCase  # noqa: E402

from memory_forest import credentials, intake, layout, search  # noqa: E402
from memory_forest.cli import forest_main  # noqa: E402
from memory_forest.config import load_config  # noqa: E402

R = credentials.REDACTED
TOKEN = "Ab3" * 12


def _old_line(**fields: object) -> str:
    """A lane line as the writer encodes it."""
    body = {
        "at": "2051-03-02T09:00:00+00:00",
        "id": "ses-kiln-1:turn-1",
        "origin": "interactive",
        "role": "owner",
        "session": "ses-kiln-1",
        "source": {"adapter": "jsonl", "place": "byte 0", "transcript": "kiln.jsonl"},
        "text": "The glaze crawled.",
    }
    body.update(fields)
    return json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n"


class LaneMaskingTests(IntakeTestCase):
    def _cli(self, root: Path, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = forest_main(["credentials", *args, "--root", str(root)])
        return code, out.getvalue()

    def test_check_counts_the_lane_lines_and_fix_rewrites_only_the_masked_spans(self) -> None:
        root = self.forest()
        lane = root / "06 istm" / "glaze-bot" / "2051-03-02.jsonl"
        lane.parent.mkdir(parents=True)
        lines = [
            _old_line(id="t-1", text=f"curl -H 'Authorization: {R} {TOKEN}' the kiln"),
            _old_line(id="t-2", text="The shelf cooled."),
            _old_line(id="t-3", role="tool", name="shell", arguments=json.dumps({"command": "export DB_PASSWORD=hunter22"}),
                      text="ran shell"),
            # Not in the writer's encoding (the keys are not sorted): counted, kept as written.
            json.dumps({"text": "password: tulip7x", "id": "t-4", "at": "2051-03-02T09:00:04+00:00",
                        "session": "ses-kiln-1", "role": "owner", "origin": "interactive"}) + "\n",
        ]
        lane.write_text("".join(lines), encoding="utf-8")
        intake._write_ids(root, "glaze-bot", "2051-03-02", {"t-1", "t-2", "t-3", "t-4"}, lane.stat().st_size)
        before = lane.read_bytes()
        inode = lane.stat().st_ino

        code, out = self._cli(root, "check")
        self.assertEqual(code, 1)
        self.assertIn(f"{layout.ISTM}/glaze-bot: 3 lines", out)
        for secret in (TOKEN, "hunter22", "tulip7x"):
            self.assertNotIn(secret, out)
        self.assertEqual(lane.read_bytes(), before)  # check writes nothing

        code, out = self._cli(root, "fix", "--all")
        self.assertEqual(code, 0)
        after = lane.read_bytes()
        self.assertNotEqual(lane.stat().st_ino, inode)  # replaced whole, never edited in place
        self.assertEqual(after.count(b"\n"), before.count(b"\n"))
        old_lines = before.decode("utf-8").splitlines()
        new_lines = after.decode("utf-8").splitlines()
        self.assertEqual(new_lines[0], old_lines[0].replace(TOKEN, R))
        self.assertEqual(new_lines[1], old_lines[1])
        self.assertEqual(new_lines[2], old_lines[2].replace("hunter22", R))
        self.assertEqual(new_lines[3], old_lines[3])
        self.assertNotIn(TOKEN, after.decode("utf-8"))
        self.assertNotIn("hunter22", after.decode("utf-8"))

        self.assertEqual(intake.lane_masking(root), [{"kept": 1, "lane": f"{layout.ISTM}/glaze-bot",
                                                      "lines": 1, "written": 0}])
        # The id index follows the rewritten file: a second fix and a new intake store nothing twice.
        self._cli(root, "fix", "--all")
        self.assertEqual(lane.read_bytes(), after)
        self.assertEqual(intake._covered(root, "glaze-bot", "2051-03-02"), (len(after), len(after)))

    def test_an_index_stored_under_masking_version_2_is_rebuilt(self) -> None:
        root = self.forest()
        lane = root / "06 istm" / "glaze-bot" / "2051-03-02.jsonl"
        lane.parent.mkdir(parents=True)
        lane.write_text(_old_line(text=f"Authorization: {R} {TOKEN}"), encoding="utf-8")
        search.build(root)
        with mock.patch.object(credentials, "MASKING_VERSION", "2"):
            old = credentials.fingerprint(load_config(root))
        database = sqlite3.connect(root / search.index_relative(root))
        database.execute("UPDATE metadata SET value = ? WHERE key = 'masking'", (old,))
        database.commit()
        database.close()
        self.assertEqual(search.status(root)["masking"], "changed")
        found = search.query(root, TOKEN, reader="front-desk")
        self.assertEqual(found["results"], [])
        self.assertEqual(search.status(root)["masking"], "current")


if __name__ == "__main__":
    import unittest

    unittest.main()
