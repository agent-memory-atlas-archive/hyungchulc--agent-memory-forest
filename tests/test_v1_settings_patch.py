"""An admission patches forest.toml byte for byte, or hands the owner the edits, never a file that breaks.

The bakery's night shift and its clerks are invented.
"""

from __future__ import annotations

import itertools
import re
import tomllib
from pathlib import Path
from typing import Any
from unittest import mock

from memory_forest import groups
from memory_forest.config import BOM, load_config, parse_config
from memory_forest.errors import MemoryForestError

from v1_support import ForestTestCase

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _settings(newline: str, final: bool, bom: bool, header: str, form: str, key: str) -> bytes:
    """A forest.toml with comments in every place, the night-shift group written one way."""
    endings = {"lf": ["\n"], "crlf": ["\r\n"], "mixed": ["\n", "\r\n"]}[newline]
    titles = {"plain": "[agents.groups.night-shift]", "quoted": '[agents.groups."night-shift"]',
              "spaced": "[ agents . groups . night-shift ]"}
    name = {"bare": "members", "double": '"members"', "single": "'members'"}[key]
    lists = {
        "one": [f'{name} = ["kiln-bot"] # one line'],
        "one-comma": [f'{name} = ["kiln-bot",] # a comma after the last'],
        "empty": [f"{name} = [] # none yet"],
        "multi-comma": [f"{name} = [ # the clerks", '  "kiln-bot", # first [main]', "  # nobody else yet",
                        "] # end"],
        "multi-plain": [f"{name} = [", '  "kiln-bot" # first', "] # end"],
        "missing": [],
    }[form]
    rows = [
        "# the forest's settings [agents.groups.night-shift]",
        "[forest] # where",
        'timezone = "UTC" # the zone',
        "",
        "[agents] # who",
        'orchestrators = ["front-desk"] # the main agent',
        "",
        "# the night shift's app",
        f"{titles[header]} # one app",
        *lists,
        "open = true # anyone may join",
        "",
        "[agents.known.kiln-bot] # declared",
        'name = "Kiln Bot" # as the app calls it',
        "# the last comment",
    ]
    text = ""
    for index, row in enumerate(rows):
        text += row + endings[index % len(endings)]
    if not final:
        text = text.rstrip("\r\n")
    data = text.encode("utf-8")
    return BOM + data if bom else data


def _runs(original: bytes, updated: bytes) -> list[tuple[int, bytes]] | None:
    """Where ``updated`` adds to ``original``: (offset in the original, the bytes added), or None when a byte of
    the original is gone or out of order."""
    runs: list[tuple[int, bytes]] = []
    index = position = 0
    while index < len(original):
        if position < len(updated) and updated[position] == original[index]:
            index += 1
            position += 1
            continue
        start = position
        while position < len(updated) and updated[position] != original[index]:
            position += 1
        if position >= len(updated):
            return None
        runs.append((index, updated[start:position]))
    if position < len(updated):
        runs.append((len(original), updated[position:]))
    return runs


def _key(part: str) -> str:
    return part if BARE_KEY.fullmatch(part) else '"' + part.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "[" + ", ".join(_value(item) for item in value) + "]"


def _emit(data: dict, path: tuple[str, ...] = ()) -> str:
    """A small TOML writer of the test's own: plain values first, then one table per nested table."""
    plain = [f"{_key(name)} = {_value(value)}" for name, value in data.items() if not isinstance(value, dict)]
    out = ""
    if path and (plain or not any(isinstance(value, dict) for value in data.values())):
        out += "[" + ".".join(_key(part) for part in path) + "]\n"
    if plain:
        out += "\n".join(plain) + "\n"
    for name, value in data.items():
        if isinstance(value, dict):
            out += "\n" + _emit(value, (*path, name))
    return out


def _apply(text: str, edits: list[dict]) -> str:
    """The edits a refusal carries, applied by a routine of the test's own: read, change, write again."""
    data = tomllib.loads(text)
    for edit in edits:
        if "add_to_list" in edit:
            table = data
            for part in edit["table"].split("."):
                table = table[part]
            table.setdefault(edit["add_to_list"], []).append(edit["value"])
        else:
            table = data
            parts = edit["new_table"].split(".")
            for part in parts[:-1]:
                table = table.setdefault(part, {})
            assert parts[-1] not in table
            table[parts[-1]] = dict(edit["values"])
    return _emit(data)


class PatchTests(ForestTestCase):
    def _admit(self, data: bytes, name: str, number: int) -> tuple[Path, bytes]:
        root = self.base / f"patch-{number}"
        root.mkdir()
        (root / "forest.toml").write_bytes(data)
        groups.resolve_caller(root, "night-shift", name)
        return root, (root / "forest.toml").read_bytes()

    def test_every_byte_stays_and_what_is_added_is_one_run_at_each_place_the_patch_owns(self) -> None:
        options = itertools.product(("lf", "crlf", "mixed"), (True, False), (False, True),
                                    ("plain", "quoted", "spaced"),
                                    ("one", "one-comma", "empty", "multi-comma", "multi-plain", "missing"),
                                    ("bare", "double", "single"))
        number = 0
        for newline, final, bom, header, form, key in options:
            if form == "missing" and key != "bare":
                continue
            with self.subTest(newline=newline, final=final, bom=bom, header=header, form=form, key=key):
                original = _settings(newline, final, bom, header, form, key)
                before = parse_config(original[len(BOM):].decode() if bom else original.decode())
                root, updated = self._admit(original, "New Clerk", number)
                number += 1
                runs = _runs(original, updated)
                self.assertIsNotNone(runs, updated)
                assert runs is not None
                self.assertEqual(len(runs), 2, runs)
                group_start = original.index(b"night-shift ]" if header == "spaced" else b"night-shift")
                known_at = original.index(b"[agents.known.kiln-bot]")
                self.assertTrue(group_start < runs[0][0] < known_at, runs)
                self.assertEqual(runs[1][0], len(original), runs)
                self.assertEqual(updated.startswith(BOM), bom)
                self.assertEqual(updated.endswith(b"\n"), final)
                after = load_config(root)
                self.assertEqual(after.agents.group("night-shift").members,
                                 (*before.agents.group("night-shift").members, "new-clerk"))
                self.assertEqual(after.agents.declared("new-clerk").name, "New Clerk")
                self.assertEqual(after.agents.declared("kiln-bot").name, "Kiln Bot")
                # A second admission of the same name changes no byte.
                groups.resolve_caller(root, "night-shift", "new clerk")
                self.assertEqual((root / "forest.toml").read_bytes(), updated)
        self.assertGreater(number, 300)

    def test_the_settings_reader_accepts_a_byte_order_mark(self) -> None:
        root = self.base / "marked"
        root.mkdir()
        (root / "forest.toml").write_bytes(BOM + b'[forest]\ntimezone = "UTC"\n')
        self.assertEqual(load_config(root).forest.timezone, "UTC")


class EditTests(ForestTestCase):
    """Where the patch does not go, the owner gets edits that can be carried out."""

    FORMS = {
        "inline": '[agents.groups]\nnight-shift = { members = ["kiln-bot"], open = true }\n',
        "dotted": '[agents.groups]\nnight-shift.members = ["kiln-bot"]\nnight-shift.open = true\n',
        "parent": '[agents]\ngroups.night-shift.members = ["kiln-bot"]\ngroups.night-shift.open = true\n',
        "top": 'agents.groups.night-shift.members = ["kiln-bot"]\nagents.groups.night-shift.open = true\n',
    }

    def _refused(self, text: str, name: str, *, read_only: bool = False) -> tuple[MemoryForestError, Path]:
        root = self.base / f"edit-{len(list(self.base.iterdir()))}"
        root.mkdir()
        path = root / "forest.toml"
        path.write_text(text, encoding="utf-8")
        if read_only:
            path.chmod(0o444)
        before = path.read_bytes()
        try:
            with self.assertRaises(MemoryForestError) as caught:
                groups.resolve_caller(root, "night-shift", name)
        finally:
            path.chmod(0o644)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(caught.exception.next_step, "report")
        return caught.exception, root

    def test_each_form_the_patch_does_not_change_is_refused_with_edits_that_can_be_carried_out(self) -> None:
        for form, text in self.FORMS.items():
            with self.subTest(form=form):
                refused, _root = self._refused(text, "New Clerk")
                self.assertEqual(refused.code, "settings_unpatched")
                self._carried_out(text, refused, "new-clerk", "New Clerk")

    def test_a_file_that_cannot_be_written_gets_the_edits_and_an_existing_table_is_not_restated(self) -> None:
        text = ('[agents.groups.night-shift]\nmembers = ["kiln-bot"]\nopen = true\n'
                '\n[agents.known.till-bot]\nname = "Till Bot"\n')
        refused, _root = self._refused(text, "Till Bot", read_only=True)
        self.assertEqual(refused.code, "settings_unwritable")
        self.assertEqual(refused.details["edits"],
                         [{"add_to_list": "members", "table": "agents.groups.night-shift", "value": "till-bot"}])
        self._carried_out(text, refused, "till-bot", "Till Bot")
        new, _root = self._refused(text, "Crate Bot", read_only=True)
        self._carried_out(text, new, "crate-bot", "Crate Bot")

    def test_a_patch_that_would_break_the_file_is_caught_before_anything_is_written(self) -> None:
        text = '[agents.groups.night-shift]\nmembers = ["kiln-bot"]\nopen = true\n'
        with mock.patch.object(groups, "_patch", lambda original, *args, **kwargs: original + "members = [\n"):
            refused, _root = self._refused(text, "New Clerk")
        self.assertEqual(refused.code, "settings_unpatched")
        with mock.patch.object(groups, "_patch", lambda original, *args, **kwargs: original.replace("kiln-bot", "x")):
            refused, _root = self._refused(text, "New Clerk")
        self.assertEqual(refused.code, "settings_unpatched")
        self._carried_out(text, refused, "new-clerk", "New Clerk")

    def _carried_out(self, text: str, refused: MemoryForestError, member: str, name: str) -> None:
        edits = refused.details["edits"]
        for edit in edits:
            if "add_to_list" in edit:
                self.assertIn(f'Add "{edit["value"]}" to the `members` list of the table [{edit["table"]}]',
                              refused.message)
            else:
                self.assertIn(f"Add a new table [{edit['new_table']}]", refused.message)
        before = parse_config(text)
        after = parse_config(_apply(text, edits))
        self.assertEqual(after.agents.group("night-shift").members,
                         (*before.agents.group("night-shift").members, member))
        self.assertEqual(after.agents.declared(member).name, name)
        self.assertEqual(after.agents.group("night-shift").open, before.agents.group("night-shift").open)
