"""An admission patches forest.toml byte for byte, or hands the owner the edits, never a file that breaks.

The bakery's night shift and its clerks are invented.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import contextlib
import itertools
import re
import tomllib
from pathlib import Path
from typing import Any
from unittest import mock

from memory_forest import groups, move, system
from memory_forest.config import BOM, load_config, parse_config
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS
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
                # What is added uses the file's own line ending.
                for _offset, added in runs:
                    if newline == "lf":
                        self.assertNotIn(b"\r", added, runs)
                    elif newline == "crlf":
                        self.assertEqual(added.count(b"\n"), added.count(b"\r\n"), runs)
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


WRONG = ('[forest]\ntimezone = "UTC"\n\n[agents.groups.bakery-bots]\nmembers = ["till-bot"]\nopen = false\n'
         '\n[agents.groups.night-shift]\nmembers = ["kiln-bot"]\nopen = true\n'
         '\n[agents.known.kiln-bot]\nname = "Kiln Bot"\n\n[agents.known.till-bot]\nname = "Till Bot"\n')


class _System:
    """The system module as groups sees it, with one call changed."""

    def __init__(self, **changed: Any) -> None:
        self.changed = changed

    def __getattr__(self, name: str) -> Any:
        return self.changed.get(name) or getattr(system, name)


class AdmitTests(ForestTestCase):
    """What happens between the decision under the lock and the write: each way out names what it did."""

    def _refused(self, name: str, **patches: Any) -> MemoryForestError:
        root = self.base / f"admit-{len(list(self.base.iterdir()))}"
        root.mkdir()
        path = root / "forest.toml"
        path.write_text(WRONG, encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(MemoryForestError) as caught:
            with contextlib.ExitStack() as stack:
                for attribute, value in patches.items():
                    stack.enter_context(mock.patch.object(groups, attribute, value))
                groups.resolve_caller(root, "night-shift", name)
        self.assertEqual(path.read_bytes(), before)
        return caught.exception

    def test_a_member_found_by_the_name_on_its_registry_line_is_that_member_in_a_full_group(self) -> None:
        settings = BAKERY_GROUPS + ('\n[agents.groups.kiln]\nmembers = ["kiln-bot"]\nopen = true\nmax_members = 1\n'
                                    '\n[agents.known.kiln-bot]\nrole = "fires the kiln"\n')
        root = self.make_forest({**FILES, "forest.toml": settings}, name="registry-name")
        move.execute(root, "new-agent", id="kiln-bot", name="Kiln Keeper", role="fires the kiln", today="2051-03-02")
        path = root / "forest.toml"
        before = (path.read_bytes(), path.stat().st_ino)
        resolved = groups.resolve_caller(root, "kiln", "Kiln Keeper")
        self.assertEqual((resolved.id, resolved.wrote), ("kiln-bot", False))
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)

    def test_a_refusal_decided_again_under_the_lock_is_that_refusal(self) -> None:
        real = groups._interpret
        answers: list[Any] = []

        def interpret(*args: Any, **kwargs: Any) -> Any:
            answers.append(real(*args, **kwargs))
            if len(answers) == 1:
                return answers[0]
            return MemoryForestError("agent_ambiguous", "another caller made the name ambiguous", next_step="fix")

        refused = self._refused("New Clerk", _interpret=interpret)
        self.assertEqual(refused.code, "agent_ambiguous")

    def test_a_settings_file_that_cannot_be_read_or_written_at_the_patch_gets_the_edits(self) -> None:
        def denied(*_args: Any, **_kwargs: Any) -> Any:
            raise system.Denied("forest.toml")

        for label, patches, code in (
            ("unreadable", {"system": _System(read_bytes=denied)}, "settings_unwritable"),
            ("not UTF-8", {"system": _System(read_bytes=lambda _path: b"\xff\xfe[")}, "settings_unpatched"),
            ("unwritable", {"atomic_write": denied}, "settings_unwritable"),
        ):
            with self.subTest(label):
                refused = self._refused("New Clerk", **patches)
                self.assertEqual(refused.code, code)
                self.assertEqual([edit.get("value") or edit.get("new_table") for edit in refused.details["edits"]],
                                 ["new-clerk", "agents.known.new-clerk"])

    def test_a_patch_that_means_anything_but_the_one_member_and_its_table_is_refused(self) -> None:
        real = groups._patch
        wrong = {
            "the group is gone": ("New Clerk", lambda text: text.replace("[agents.groups.night-shift]", "[agents.groups.day]")),
            "another name": ("New Clerk", lambda text: text.replace('name = "New Clerk"', 'name = "Old Clerk"')),
            "another agent renamed": ("New Clerk", lambda text: text.replace('"Kiln Bot"', '"Kiln Robot"')),
            "another agent renamed, no new table": ("Till Bot", lambda text: text.replace('"Kiln Bot"', '"Kiln Robot"')),
            "another group opened": ("New Clerk", lambda text: text.replace("open = false", "open = true")),
            "another setting": ("New Clerk", lambda text: text.replace('timezone = "UTC"', 'timezone = "Europe/Oslo"')),
            "a member dropped": ("New Clerk", lambda text: text.replace('["kiln-bot", "new-clerk"]', '["new-clerk"]')),
        }
        for label, (name, change) in wrong.items():
            with self.subTest(label):
                def patch(original: str, *args: Any, change=change, **kwargs: Any) -> str:
                    return change(real(original, *args, **kwargs))

                refused = self._refused(name, _patch=patch)
                self.assertEqual(refused.code, "settings_unpatched")


class FormTests(ForestTestCase):
    """The exact bytes an admission adds, for each way a members list is written."""

    HEAD = '[forest]\ntimezone = "UTC"\n\n[agents.groups.night-shift]\n'
    TAIL = 'open = true\n\n[agents.known.kiln-bot]\nname = "Kiln Bot"\n'
    ADDED = '\n[agents.known.new-clerk]\nname = "New Clerk"\n'

    def _admitted(self, members: str, *, head: str = HEAD, tail: str = TAIL) -> str:
        root = self.base / f"form-{len(list(self.base.iterdir()))}"
        root.mkdir()
        (root / "forest.toml").write_text(head + members + tail, encoding="utf-8")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "New Clerk").id, "new-clerk")
        text = (root / "forest.toml").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(head) and text.endswith(tail + self.ADDED), text)
        return text[len(head) : len(text) - len(tail + self.ADDED)]

    def test_each_form_of_the_list_gets_the_member_where_its_items_are(self) -> None:
        forms = {
            'members = ["kiln-bot"]\n': 'members = ["kiln-bot", "new-clerk"]\n',
            'members = ["kiln-bot",] # one\n': 'members = ["kiln-bot", "new-clerk"] # one\n',
            "members = []\n": 'members = ["new-clerk"]\n',
            'members = [\n    "kiln-bot",\n]\n': 'members = [\n    "kiln-bot",\n    "new-clerk",\n]\n',
            'members = [\n  "kiln-bot",\n  # [nobody] else\n]\n': 'members = [\n  "kiln-bot",\n  # [nobody] else\n  "new-clerk",\n]\n',
            'members = [ # all\n  "kiln-bot" # first\n]\n': 'members = [ # all\n  "kiln-bot" # first\n, "new-clerk"]\n',
            'members = [\n  "kiln-bot", ]\n': 'members = [\n  "kiln-bot",  "new-clerk"]\n',
            "": 'members = ["new-clerk"]\n',
        }
        for written, wanted in forms.items():
            with self.subTest(written=written):
                self.assertEqual(self._admitted(written), wanted)

    def test_a_literal_string_ends_at_its_quote_and_brackets_in_other_values_are_not_tables(self) -> None:
        tail = self.TAIL + "\n[agents.known.slash-bot]\nname = 'Slash\\'\n"
        self.assertEqual(self._admitted("members = ['Slash\\']\n", tail=tail),
                         "members = ['Slash\\', \"new-clerk\"]\n")
        # A value of the group over several lines, holding brackets in strings and comments, and a line that
        # would be the group's own header if it were not inside the value.
        grid = ('grid = [ # rows [of cells\n  ["a]", \'b[\', "c\\"]"],\n  ["agents.groups.night-shift"],\n'
                '  [ "]" ], # ]]\n]\n')
        self.assertEqual(self._admitted(grid + 'members = ["kiln-bot"]\n'),
                         grid + 'members = ["kiln-bot", "new-clerk"]\n')

    def test_a_group_that_ends_the_file_without_a_line_ending_is_read_to_its_last_line(self) -> None:
        root = self.base / "last"
        root.mkdir()
        head = '[forest]\ntimezone = "UTC"\n\n[agents.known.kiln-bot]\nname = "Kiln Bot"\n\n'
        (root / "forest.toml").write_text(head + '[agents.groups.night-shift]\nopen = true\nmembers = ["kiln-bot"]',
                                          encoding="utf-8")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "New Clerk").id, "new-clerk")
        self.assertEqual(load_config(root).agents.group("night-shift").members, ("kiln-bot", "new-clerk"))

    def test_brackets_in_strings_and_comments_of_an_earlier_table_hide_no_header_and_make_none(self) -> None:
        # In the table before the group: a comment that opens two brackets, a literal string ending in a
        # backslash, a basic string with an escaped quote, and a line shaped like the group's own header.
        grid = ('grid = [ # rows [[of cells\n  [\'C:\\\', "]"],\n  ["c\\"[", "d"],\n'
                '  ["agents.groups.night-shift"],\n]\n')
        head = '[forest]\ntimezone = "UTC"\n\n[agents.groups.bakery-bots]\n' + grid + 'members = []\nopen = false\n\n'
        self.assertEqual(self._admitted('members = ["kiln-bot"]\n', head=head + "[agents.groups.night-shift]\n"),
                         'members = ["kiln-bot", "new-clerk"]\n')

    def test_a_members_value_that_is_not_a_list_is_refused_with_the_edits(self) -> None:
        root = self.base / "not-a-list"
        root.mkdir()
        path = root / "forest.toml"
        path.write_text(self.HEAD + 'members = "kiln-bot"\n' + self.TAIL, encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "night-shift", "New Clerk")
        self.assertEqual(caught.exception.code, "settings_unpatched")
        self.assertEqual(path.read_bytes(), before)

    def test_a_folder_that_cannot_be_written_is_refused_before_any_write(self) -> None:
        root = self.base / "read-only"
        root.mkdir()
        (root / "forest.toml").write_text(self.HEAD + 'members = ["kiln-bot"]\n' + self.TAIL, encoding="utf-8")
        (root / ".forest").mkdir()  # the locks' folder stays writable; the settings' folder does not
        writes: list[Any] = []
        root.chmod(0o555)
        try:
            with mock.patch.object(groups, "atomic_write", lambda *args, **kwargs: writes.append(args)):
                with self.assertRaises(MemoryForestError) as caught:
                    groups.resolve_caller(root, "night-shift", "New Clerk")
        finally:
            root.chmod(0o755)
        self.assertEqual(caught.exception.code, "settings_unwritable")
        self.assertEqual(writes, [])
