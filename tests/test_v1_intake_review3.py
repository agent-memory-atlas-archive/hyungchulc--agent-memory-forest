"""The third intake review. Every name is from the invented kiln.

A test asserts the thing that must not happen: the secret alone, the number of lines in a lane, the bytes of
the tree. Nothing here is copied from a real transcript.
"""

from __future__ import annotations

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
