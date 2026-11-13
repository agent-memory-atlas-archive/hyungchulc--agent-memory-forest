"""The agent is its name. These cases are the ones the first review found missing.

The bakery, Ilse, Juno and the bots are invented. Nothing here is a real person.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from memory_forest import agents, groups, intake, levels, mcp, search
from memory_forest.cli import forest_main
from memory_forest.cli_find import COMMANDS, argument_dest, flag_effect
from memory_forest.config import AGENT_TEXT_MAX, load_config, parse_config
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS, COOLING, SECRET, SRC, STUDIO, _call, _stdin, _tool_body
from v1_support import ForestTestCase


READ_TOOLS = ("memory_search", "memory_read", "memory_timeline", "memory_navigate")
READ_COMMANDS = (
    ("search", "secret cooling curve"),
    ("read", COOLING),
    ("timeline", "subject", "oven-bot"),
    ("navigate",),
)


class NameRuleTests(ForestTestCase):
    def _studio(self) -> Path:
        return self.make_forest({"forest.toml": STUDIO}, name="studio")

    def _bakery(self, name: str = "bakery") -> Path:
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name=name)
        search.build(root)
        return root

    def test_a_bad_agent_argument_is_the_callers_to_fix_and_forty_names_are_admitted(self) -> None:
        root = self._studio()
        before = (root / "forest.toml").read_bytes()
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "night-kiln", "y" * (AGENT_TEXT_MAX + 1))
        self.assertEqual(caught.exception.code, "agent_name_invalid")
        self.assertEqual(caught.exception.next_step, "fix")
        self.assertEqual((root / "forest.toml").read_bytes(), before)
        with self.assertRaises(MemoryForestError) as empty:
            groups.resolve_caller(root, "night-kiln", "!!!")
        self.assertEqual(empty.exception.code, "agent_name_invalid")
        self.assertEqual(empty.exception.next_step, "fix")
        for number in range(40):
            resolved = groups.resolve_caller(root, "night-kiln", f"Clerk {number}")
            self.assertEqual(resolved.id, f"clerk-{number}")
        members = load_config(root).agents.group("night-kiln").members
        self.assertEqual(len([item for item in members if item.startswith("clerk-")]), 40)

    def test_max_members_is_unset_until_the_owner_sets_it(self) -> None:
        root = self._studio()
        text = (root / "forest.toml").read_text(encoding="utf-8")
        text = text.replace("open = true\n", "open = true\nmax_members = 2\n", 1)
        (root / "forest.toml").write_text(text, encoding="utf-8")
        self.assertIsNone(parse_config(STUDIO).agents.group("night-kiln").max_members)
        first = groups.resolve_caller(root, "night-kiln", "First Clerk")
        self.assertEqual(first.id, "first-clerk")
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "night-kiln", "Second Clerk")
        self.assertEqual(caught.exception.code, "group_full")
        self.assertEqual(caught.exception.next_step, "report")
        self.assertNotIn("second-clerk", load_config(root).agents.group("night-kiln").members)

    def test_a_taken_id_gets_its_own_and_the_subject_stays(self) -> None:
        root = self._bakery()
        page = (root / "02 ltm" / "bakery_LTM.md").read_bytes()
        area = groups.resolve_caller(root, "night-shift", "bakery")
        self.assertEqual(area.id, "bakery-agent")
        self.assertEqual((root / "02 ltm" / "bakery_LTM.md").read_bytes(), page)
        owner = groups.resolve_caller(root, "night-shift", "Ilse")
        self.assertEqual(owner.id, "ilse-agent")
        self.assertEqual(load_config(root).agents.declared("ilse-agent").name, "Ilse")
        self.assertNotIn("ilse", load_config(root).agents.group("night-shift").members)
        group = groups.resolve_caller(root, "night-shift", "bakery-bots")
        self.assertEqual(group.id, "bakery-bots-agent")
        self.assertEqual(load_config(root).agents.group("bakery-bots").members, ("till-bot", "oven-bot"))

    def test_every_kind_of_taken_id_is_walked_from_the_tables(self) -> None:
        root = self.copy_synthetic()
        settings = (root / "forest.toml").read_text(encoding="utf-8")
        settings += (
            '\n[subjects]\nsystem_id = "studio-clock"\n'
            '\n[agents.groups.clay-bench]\nmembers = ["kiln-bot"]\nopen = true\n'
        )
        (root / "forest.toml").write_text(settings, encoding="utf-8")
        index = root / "03 mtm" / "studio" / "studio_MTM.md"
        text = index.read_text(encoding="utf-8")
        extra = []
        for kind in (
            "group", "project", "product", "app", "place", "event", "title", "topic", "admin-item", "component",
        ):
            extra.append(
                f"- `sample-{kind}` — Sample {kind} · {kind} · leaf · `04 stm/studio/kiln-two/sample-{kind}.md`"
            )
        index.write_text(text.replace("## Subjects\n", "## Subjects\n" + "\n".join(extra) + "\n", 1), encoding="utf-8")
        config = load_config(root)
        found: set[str] = set()
        for kinds in agents.taken(root, config).values():
            found.update(kinds)
        found.update(agents.kinds_of("notes-seen-once", agents.taken(root, config)))
        missing = [kind for kind in agents.id_kind_names(config) if kind not in found]
        self.assertEqual(missing, [])

    def test_two_new_names_at_once_by_threads_and_by_processes(self) -> None:
        root = self._studio()
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def work(name: str) -> None:
            try:
                barrier.wait(30)
                groups.resolve_caller(root, "night-kiln", name)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(name,)) for name in ("Alpha Clerk", "Beta Clerk")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        members = load_config(root).agents.group("night-kiln").members
        self.assertIn("alpha-clerk", members)
        self.assertIn("beta-clerk", members)

        other = self.make_forest({"forest.toml": STUDIO}, name="studio-two")
        if (other / ".forest").exists():
            shutil.rmtree(other / ".forest")
        wrote = _two_processes(other, ("Gamma Clerk", "Delta Clerk"))
        self.assertEqual(sorted(wrote), ["wrote", "wrote"])
        again = load_config(other).agents.group("night-kiln").members
        self.assertIn("gamma-clerk", again)
        self.assertIn("delta-clerk", again)

    def test_the_same_new_name_in_two_groups_is_one_agent(self) -> None:
        root = self._bakery()
        text = (root / "forest.toml").read_text(encoding="utf-8")
        text += "\n[agents.groups.day-shift]\nmembers = []\nopen = true\n"
        (root / "forest.toml").write_text(text, encoding="utf-8")
        real = groups._interpret
        barrier = threading.Barrier(2)
        inside: list[object] = []
        local = threading.local()

        def wrapped(*args, **kwargs):
            result = real(*args, **kwargs)
            if getattr(local, "passed", False):
                inside.append(result)
                return result
            if isinstance(result, groups.Admit):
                local.passed = True
                barrier.wait(30)
            return result

        def admit(group_id: str) -> None:
            groups.resolve_caller(root, group_id, "Crate Bot")

        with mock.patch("memory_forest.groups._interpret", wrapped):
            threads = [
                threading.Thread(target=admit, args=("night-shift",)),
                threading.Thread(target=admit, args=("day-shift",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
                self.assertFalse(thread.is_alive())
        config = load_config(root)
        self.assertEqual(config.agents.declared("crate-bot").name, "Crate Bot")
        self.assertIn("crate-bot", config.agents.group("night-shift").members)
        self.assertIn("crate-bot", config.agents.group("day-shift").members)
        self.assertEqual(len([item for item in config.agents.known if item.id.startswith("crate-bot")]), 1)
        self.assertTrue(any(isinstance(item, groups.Resolved) or (isinstance(item, groups.Admit) and not item.create) for item in inside))
        fixed = datetime(2051, 3, 2, 18, 0, tzinfo=timezone.utc)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz is None else fixed.astimezone(tz)

        with mock.patch("memory_forest.intake.datetime", Frozen):
            first = mcp.record_turn(root, "crate-bot", "from the night door", origin="interactive", session="shift", door="mcp", group="night-shift")
            second = mcp.record_turn(root, "crate-bot", "from the day door", origin="interactive", session="shift", door="mcp", group="day-shift")
        self.assertTrue(first["wrote"])
        self.assertTrue(second["wrote"])
        self.assertTrue((root / "02 ltm" / "crate-bot_LTM.md").is_file())
        lane = (root / first["path"]).read_text(encoding="utf-8")
        self.assertIn("night-shift", lane)
        self.assertIn("day-shift", lane)
        night = search.read(root, first["path"], reader="crate-bot", source="night-shift")
        day = search.read(root, first["path"], reader="crate-bot", source="day-shift")
        self.assertIn("night door", json.dumps(night))
        self.assertNotIn("day door", json.dumps(night))
        self.assertIn("day door", json.dumps(day))
        self.assertNotIn("night door", json.dumps(day))

    def test_the_second_admission_inside_the_lock_finds_the_member(self) -> None:
        root = self._studio()
        real = groups._interpret
        barrier = threading.Barrier(2)
        inside: list[object] = []
        local = threading.local()

        def wrapped(*args, **kwargs):
            result = real(*args, **kwargs)
            if getattr(local, "passed", False):
                inside.append(result)
                return result
            if isinstance(result, groups.Admit):
                local.passed = True
                barrier.wait(30)
            return result

        found: list[bool] = []

        def work() -> None:
            found.append(groups.resolve_caller(root, "night-kiln", "Parcel Clerk").wrote)

        with mock.patch("memory_forest.groups._interpret", wrapped):
            threads = [threading.Thread(target=work) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
                self.assertFalse(thread.is_alive())
        self.assertEqual(sum(found), 1)
        self.assertTrue(any(isinstance(item, groups.Resolved) for item in inside))
        self.assertEqual(load_config(root).agents.group("night-kiln").members.count("parcel-clerk"), 1)

    def test_a_member_source_is_read_once_when_the_name_is_missing(self) -> None:
        root = self._bakery()
        calls = []

        def reader(group_id: str) -> list[str]:
            calls.append(group_id)
            return ["Crate Bot"]

        groups.register_member_source("crate-app", reader)
        self.addCleanup(groups.unregister_member_source, "crate-app")
        text = (root / "forest.toml").read_text(encoding="utf-8")
        text += '\n[agents.groups.crate-app]\nmembers = []\nopen = false\nsource = "crate-app"\n'
        (root / "forest.toml").write_text(text, encoding="utf-8")
        admitted = groups.resolve_caller(root, "crate-app", "Crate Bot")
        self.assertEqual(admitted.id, "crate-bot")
        self.assertEqual(calls, ["crate-app"])
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "crate-app", "Other Bot")
        self.assertEqual(caught.exception.code, "agent_not_in_group")
        self.assertEqual(caught.exception.next_step, "fix")
        hub = groups.resolve_caller(root, "crate-app", "front-desk")
        self.assertEqual((hub.id, hub.wrote), ("front-desk", False))
        self.assertNotIn("front-desk", load_config(root).agents.group("crate-app").members)

    def test_the_main_agent_reads_every_kind_of_group(self) -> None:
        root = self._bakery()
        text = (root / "forest.toml").read_text(encoding="utf-8")
        text += '\n[agents.groups.listed-bots]\nmembers = ["till-bot"]\nopen = false\nsource = "listed-bots"\n'
        (root / "forest.toml").write_text(text, encoding="utf-8")

        def reader(group_id: str) -> list[str]:
            return ["till-bot"]

        groups.register_member_source("listed-bots", reader)
        self.addCleanup(groups.unregister_member_source, "listed-bots")
        for group_id in ("bakery-bots", "night-shift", "listed-bots"):
            self.assertNotIn("front-desk", load_config(root).agents.group(group_id).members)
            for name in READ_TOOLS:
                arguments = {"agent": "front-desk"}
                if name == "memory_search":
                    arguments["words"] = "secret cooling curve"
                elif name == "memory_read":
                    arguments["path"] = COOLING
                elif name == "memory_timeline":
                    arguments["subject"] = "oven-bot"
                reply, _version = mcp.handle_message(
                    root, "", _call(name, arguments), "stdio", "2025-11-25", group=group_id
                )
                assert isinstance(reply, dict)
                body = reply["result"]["content"][0]["text"]
                self.assertFalse(reply["result"]["isError"], body)
                if name == "memory_navigate":
                    self.assertIn("oven-bot", body)
                else:
                    self.assertIn(SECRET, body)
            for command in READ_COMMANDS:
                code, out, err = _cli(root, *command, "--as-group", group_id, "--agent", "front-desk")
                self.assertEqual(code, 0, err)
                if command[0] == "navigate":
                    self.assertIn("oven-bot", out)
                else:
                    self.assertIn(SECRET, out)

    def test_as_refuses_an_agent_argument_on_every_tool(self) -> None:
        root = self._bakery()
        for tool in mcp._TOOLS:
            reply, _version = mcp.handle_message(
                root,
                "till-bot",
                _call(tool["name"], {"agent": "oven-bot", "words": "x", "path": COOLING, "text": "x", "phrase": "today"}),
                "stdio",
                "2025-11-25",
            )
            assert isinstance(reply, dict)
            self.assertTrue(reply["result"]["isError"], tool["name"])
            self.assertEqual(_tool_body(reply)["code"], "arguments_invalid")
            self.assertNotIn(SECRET, reply["result"]["content"][0]["text"])

    def test_a_scope_flag_is_refused_on_an_agents_door(self) -> None:
        doors = {"search", "read", "timeline", "navigate", "log"}
        seen = []
        for command in COMMANDS:
            if command.words[0] not in doors:
                continue
            for argument in command.arguments:
                dest = argument_dest(argument)
                effect = flag_effect(dest)
                self.assertIn(effect, {"plain", "refused"})
                seen.append((command.words, dest, effect))
        self.assertIn((("read",), "named", "refused"), seen)
        root = self._bakery()
        code, out, err = _cli(root, "read", COOLING, "--as-group", "bakery-bots", "--agent", "till-bot", "--named", "oven-bot")
        self.assertEqual(code, 2, out)
        self.assertEqual(json.loads(out)["error"]["code"], "scope_widened")
        self.assertNotIn(SECRET, out + err)
        code, out, err = _cli(root, "read", COOLING, "--as", "till-bot", "--named", "oven-bot")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "scope_widened")
        self.assertNotIn(SECRET, out)

    def test_both_doors_store_one_turn_and_name_the_door(self) -> None:
        root = self._bakery()
        fixed = datetime(2051, 3, 2, 16, 0, tzinfo=timezone.utc)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz is None else fixed.astimezone(tz)

        with mock.patch("memory_forest.intake.datetime", Frozen):
            first = mcp.record_turn(root, "till-bot", "the same tray count", origin="interactive", session="shift", door="cli")
            second = mcp.record_turn(root, "till-bot", "the same tray count", origin="interactive", session="shift", door="mcp")
        self.assertTrue(first["wrote"])
        self.assertFalse(second["wrote"])
        lines = [json.loads(line) for line in (root / first["path"]).read_text(encoding="utf-8").splitlines() if "same tray" in line]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["source"]["door"], "cli")
        other = self._bakery("bakery-other")
        with mock.patch("memory_forest.intake.datetime", Frozen):
            first = mcp.record_turn(other, "till-bot", "the same tray count", origin="interactive", session="shift", door="mcp")
            second = mcp.record_turn(other, "till-bot", "the same tray count", origin="interactive", session="shift", door="cli")
        self.assertTrue(first["wrote"])
        self.assertFalse(second["wrote"])
        lines = [json.loads(line) for line in (other / first["path"]).read_text(encoding="utf-8").splitlines() if "same tray" in line]
        self.assertEqual(lines[0]["source"]["door"], "mcp")

        left = self._bakery("bakery-shape")
        right_agent_root = left
        with mock.patch("memory_forest.intake.datetime", Frozen):
            by_cli = mcp.record_turn(right_agent_root, "till-bot", "one cooling note", origin="interactive", session="shift", door="cli")
            by_mcp = mcp.record_turn(right_agent_root, "oven-bot", "one cooling note", origin="interactive", session="shift", door="mcp")
        cli_line = json.loads((left / by_cli["path"]).read_text(encoding="utf-8").splitlines()[-1])
        mcp_line = json.loads((left / by_mcp["path"]).read_text(encoding="utf-8").splitlines()[-1])
        for key, value in cli_line.items():
            if key == "source":
                continue
            self.assertEqual(mcp_line[key], value, key)
        self.assertEqual(cli_line["source"]["door"], "cli")
        self.assertEqual(mcp_line["source"]["door"], "mcp")
        self.assertNotEqual(cli_line["source"]["adapter"], mcp_line["source"]["adapter"])

    def test_forest_mcp_with_neither_flag_names_both(self) -> None:
        root = self._studio()
        code, out, err = _cli(root, "mcp")
        self.assertEqual(code, 2)
        text = out + err
        self.assertIn("mcp_agent_unset", text)
        self.assertIn("--as", text)
        self.assertIn("--as-group", text)

    def test_forest_log_bounds_match_the_tool_and_name_the_log(self) -> None:
        with self.assertRaises(MemoryForestError) as large:
            intake.read_bounded(BytesIO(b"a" * (mcp.MESSAGE_LIMIT + 1)), limit=mcp.MESSAGE_LIMIT, seconds=2)
        self.assertEqual(large.exception.code, "log_too_large")
        self.assertIn("forest log", large.exception.message)
        with self.assertRaises(MemoryForestError) as broken:
            intake.read_bounded(BytesIO(b"\xff"), limit=mcp.MESSAGE_LIMIT, seconds=2)
        self.assertEqual(broken.exception.code, "invalid_utf8")
        self.assertIn("forest log", broken.exception.message)
        read_end, write_end = os.pipe()
        stream = os.fdopen(read_end, "rb")
        try:
            with self.assertRaises(MemoryForestError) as timed:
                intake.read_bounded(stream, limit=mcp.MESSAGE_LIMIT, seconds=0.2)
        finally:
            stream.close()
            os.close(write_end)
        self.assertEqual(timed.exception.code, "log_read_timeout")
        self.assertEqual(timed.exception.next_step, "retry")
        self.assertIn("forest log", timed.exception.message)

        root = self._bakery()
        code, out, err = _cli(root, "log", "--as", "till-bot", "--as-group", "bakery-bots", "--agent", "till-bot")
        self.assertEqual(code, 2)
        error = json.loads(out)["error"]
        self.assertEqual(error["code"], "reader_conflict")
        self.assertIn("forest log", error["message"])
        self.assertNotIn("forest search", error["message"])

    def test_a_settings_file_that_cannot_be_written_names_the_lines(self) -> None:
        root = self._studio()
        path = root / "forest.toml"
        before = path.read_bytes()
        path.chmod(0o444)
        try:
            with self.assertRaises(MemoryForestError) as caught:
                groups.resolve_caller(root, "night-kiln", "Quiet Clerk")
        finally:
            path.chmod(0o644)
        self.assertEqual(caught.exception.code, "settings_unwritable")
        self.assertEqual(caught.exception.next_step, "report")
        self.assertIn("[agents.groups.night-kiln]", caught.exception.message)
        self.assertNotIn("[[agents.groups.night-kiln]]", caught.exception.message)
        self.assertIn("[agents.known.quiet-clerk]", caught.exception.message)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((root / "06 istm" / "quiet-clerk").exists())

    def test_an_unplaceable_id_does_not_stop_the_level_pass(self) -> None:
        root = self._bakery()
        text = (root / "forest.toml").read_text(encoding="utf-8")
        text += '\n[agents.known.slip-bot]\nname = "Slip Bot"\n'
        text += f'\n[agents.known.{"y" * 300}]\nname = "Too Long"\n'
        (root / "forest.toml").write_text(text, encoding="utf-8")
        self.assertTrue(any(len(agent_id) > AGENT_TEXT_MAX for _where, agent_id in load_config(root).agents.unplaceable))
        result = levels.apply(root)
        self.assertIn("slip-bot", [item["subject"] for item in result["changes"]])
        self.assertTrue(any(item["subject"].startswith("y") for item in result["refused"]))
        self.assertTrue((root / "02 ltm" / "slip-bot_LTM.md").is_file())

    def test_a_patch_keeps_every_line_it_does_not_own(self) -> None:
        cases = []
        for crlf in (False, True):
            for header in ("plain", "quoted", "spaced"):
                for form in ("one", "multi", "nocomma", "empty"):
                    for open_first in (False, True):
                        cases.append((crlf, header, form, open_first, True))
        self.assertGreaterEqual(len(cases), 16)
        for number, (crlf, header, form, open_first, decoy) in enumerate(cases):
            with self.subTest(crlf=crlf, header=header, form=form, open_first=open_first):
                root = self.base / f"patch-{number}"
                root.mkdir()
                original = _settings(crlf, header, form, open_first, decoy).encode("utf-8")
                (root / "forest.toml").write_bytes(original)
                resolved = groups.resolve_caller(root, "night-shift", "New Clerk")
                self.assertEqual(resolved.id, "new-clerk")
                updated = (root / "forest.toml").read_bytes()
                parsed = parse_config(updated.decode("utf-8"))
                self.assertIn("new-clerk", parsed.agents.group("night-shift").members)
                self.assertEqual(parsed.agents.declared("new-clerk").name, "New Clerk")
                self.assertTrue(_lines_kept(original, updated), (crlf, header, form, open_first))
                self.assertIn(b"# [agents.known.new-clerk]", updated)
                self.assertIn(b"[agents.known.new-clerk]", updated.replace(b"# [agents.known.new-clerk]", b""))

    def test_an_inline_group_is_refused_with_one_header(self) -> None:
        root = self.make_forest({
            "forest.toml": '[agents.groups]\nnight-shift = { members = ["kiln-bot"], open = true }\n'
        })
        before = (root / "forest.toml").read_bytes()
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "night-shift", "New Clerk")
        self.assertEqual(caught.exception.code, "settings_unpatched")
        self.assertEqual(caught.exception.next_step, "report")
        self.assertNotIn("[[", caught.exception.message)
        self.assertIn("[agents.groups.night-shift]", caught.exception.message)
        self.assertIn("[agents.known.new-clerk]", caught.exception.message)
        self.assertEqual((root / "forest.toml").read_bytes(), before)

    def test_forest_agents_remarks_on_a_near_name_and_shows_sources(self) -> None:
        root = self._studio()
        groups.resolve_caller(root, "night-kiln", "kiln-bott")
        report = groups.listing(root)
        self.assertTrue(any(item["how"] == "one edit" and "kiln-bot" in item["line"] for item in report["remarks"]))
        lines = "\n".join(groups.listing_lines(report))
        self.assertIn("may be one agent:", lines)
        self.assertIn("give them one name", lines)

    def test_a_seen_record_is_not_rewritten_every_call_and_a_busy_lock_does_not_slow_it(self) -> None:
        root = self._bakery()
        reply, _version = mcp.handle_message(
            root, "", _call("memory_search", {"agent": "till-bot", "words": "bakery"}), "stdio", "2025-11-25",
            group="bakery-bots",
        )
        assert isinstance(reply, dict)
        self.assertFalse(reply["result"]["isError"])
        path = root / ".forest" / "mcp" / "seen.json"
        first = path.read_bytes()
        marked = first + b" \n"
        path.write_bytes(marked)
        again, _version = mcp.handle_message(
            root, "", _call("memory_search", {"agent": "till-bot", "words": "trays"}, call_id=2), "stdio",
            "2025-11-25", group="bakery-bots",
        )
        assert isinstance(again, dict)
        self.assertFalse(again["result"]["isError"])
        self.assertEqual(path.read_bytes(), marked)
        seen = json.loads(first)
        seen["till-bot"]["last"] = "2000-01-01T00:00:00Z"
        path.write_text(json.dumps(seen), encoding="utf-8")
        mcp.handle_message(
            root, "", _call("memory_search", {"agent": "till-bot", "words": "again"}, call_id=3), "stdio",
            "2025-11-25", group="bakery-bots",
        )
        self.assertNotEqual(json.loads(path.read_text(encoding="utf-8"))["till-bot"]["last"], "2000-01-01T00:00:00Z")

        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with groups.exclusive(root, "mcp-seen", wait_seconds=30):
                held.set()
                release.wait(30)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(held.wait(30))
        started = datetime.now(timezone.utc)
        reply, _version = mcp.handle_message(
            root, "", _call("memory_read", {"agent": "oven-bot", "path": COOLING}, call_id=4), "stdio",
            "2025-11-25", group="bakery-bots",
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        release.set()
        thread.join(30)
        assert isinstance(reply, dict)
        self.assertFalse(reply["result"]["isError"])
        self.assertLess(elapsed, 1.0)

    def test_two_processes_admit_on_a_forest_with_no_tool_folder(self) -> None:
        for number in range(20):
            root = self.make_forest({"forest.toml": STUDIO}, name=f"race-{number}")
            if (root / ".forest").exists():
                shutil.rmtree(root / ".forest")
            wrote = _two_processes(root, ("Parcel Clerk", "Parcel Clerk"))
            self.assertEqual(sorted(wrote), ["kept", "wrote"], number)
            self.assertEqual(
                load_config(root).agents.group("night-kiln").members.count("parcel-clerk"), 1
            )


def _cli(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    from contextlib import redirect_stderr, redirect_stdout

    with redirect_stdout(out), redirect_stderr(err), _stdin(""):
        code = forest_main([*args, "--root", str(root), "--json"])
    return code, out.getvalue(), err.getvalue()


def _two_processes(root: Path, names: tuple[str, str]) -> list[str]:
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from memory_forest.groups import resolve_caller\n"
        "sys.stdout.write('ready\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
        "result = resolve_caller(sys.argv[1], 'night-kiln', sys.argv[3])\n"
        "sys.stdout.write('wrote\\n' if result.wrote else 'kept\\n')\n"
        "sys.stdout.flush()\n"
    )
    procs = []
    for name in names:
        procs.append(subprocess.Popen(
            [sys.executable, "-c", code, str(root), SRC, name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
    ready = 0
    deadline = datetime.now(timezone.utc).timestamp() + 30
    while ready < 2 and datetime.now(timezone.utc).timestamp() < deadline:
        streams = [proc.stdout for proc in procs if proc.stdout is not None]
        remaining = max(0.0, deadline - datetime.now(timezone.utc).timestamp())
        readable, _ignored, _also = select.select(streams, [], [], remaining)
        for stream in readable:
            if stream.readline().strip() == "ready":
                ready += 1
    if ready != 2:
        for proc in procs:
            proc.kill()
        raise AssertionError("the two processes did not both become ready")
    for proc in procs:
        assert proc.stdin is not None
        proc.stdin.write("go\n")
        proc.stdin.flush()
    wrote = []
    for proc in procs:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        code_ = proc.wait(timeout=30)
        if code_ != 0:
            raise AssertionError(stderr)
        wrote.append(line)
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
    return wrote


def _settings(crlf: bool, header: str, form: str, open_first: bool, decoy: bool) -> str:
    newline = "\r\n" if crlf else "\n"
    titles = {
        "plain": "[agents.groups.night-shift]",
        "quoted": '[agents.groups."night-shift"]',
        "spaced": "[ agents.groups.night-shift ]",
    }
    if form == "one":
        members = 'members = ["kiln-bot"]'
    elif form == "multi":
        members = "members = [" + newline + '  "kiln-bot", # first [main]' + newline + "] # end"
    elif form == "nocomma":
        members = "members = [" + newline + '  "kiln-bot"' + newline + "] # end"
    else:
        members = "members = []"
    rows = [titles[header]]
    if open_first:
        rows.extend(["open = true", members])
    else:
        rows.extend([members, "open = true"])
    text = newline.join(rows) + newline
    if decoy:
        text = "# [agents.groups.night-shift]" + newline + "# [agents.known.new-clerk]" + newline + text
    return text


def _lines_kept(original: bytes, updated: bytes) -> bool:
    """Every original line is still there, in order, except at most the one line that grew."""
    old = original.decode("utf-8").splitlines()
    new = updated.decode("utf-8").splitlines()

    def covers(lines: list[str]) -> bool:
        index = 0
        for line in lines:
            while index < len(new) and new[index] != line:
                index += 1
            if index >= len(new):
                return False
            index += 1
        return True

    if covers(old):
        return True
    return any(covers(old[:drop] + old[drop + 1 :]) for drop in range(len(old)))


if __name__ == "__main__":
    import unittest

    unittest.main()
