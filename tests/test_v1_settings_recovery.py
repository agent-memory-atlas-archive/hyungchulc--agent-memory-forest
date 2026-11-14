"""Recovery edits are executable for every TOML kind; parser output is never an owner's setting.

The workshop and its clerks are fictional. This TOML writer and edit interpreter share no package code.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import itertools
import json
import tomllib
from contextlib import contextmanager
from dataclasses import fields
from datetime import date, datetime, time
from typing import Any
from unittest import mock

from memory_forest import agents, config, groups, system
from memory_forest.errors import MemoryForestError

from v1_support import ForestTestCase


KINDS = {
    "text": '"Old Clerk"',
    "empty-text": '""',
    "bad-text": '"Old\\nClerk"',
    "integer": "7",
    "float": "2.5",
    "boolean": "false",
    "offset-datetime": "2051-03-02T08:00:00Z",
    "local-datetime": "2051-03-02T08:00:00",
    "date": "2051-03-02",
    "time": "08:00:00",
    "table": '{ extra = "fictional" }',
    "list": '["old-clerk"]',
    "empty-list": "[]",
    "mixed-list": '["old-clerk", 7]',
}

# The documented owner contract, pinned independently of the parser's key table and dataclass fields.
DOCUMENTED_KEYS = {
    "forest": {"timezone", "language"},
    "owner": {"id", "name", "aliases"},
    "limits": {"leaf_cap_bytes", "leaf_max_bytes", "record_keep_bytes", "category_max_leaves", "about_max_ids",
               "entry_admit_margin_bytes", "repeat_min_lines", "repeat_min_chars"},
    "numbers": {"identity_formats", "account_words", "reference_words", "identity_words", "phone_patterns", "keep_whole"},
    "credentials": {"label_words", "joining_words", "count_words"},
    "levels": {"leaf_days", "branch_days", "tree_subtopics", "tree_subtopic_days", "tree_subtopic_leaves", "tree_types",
               "stream_kinds", "stream_subtopic_words", "stage_names", "work_verbs", "tool_names", "shared_branch", "default_subtopic"},
    "agents": {"policy", "orchestrators", "default_scope", "auto_create", "folded_type", "known", "groups"},
    "embeddings": {"provider"}, "intake": {"tool_output", "tools"}, "subjects": {"types", "system_id"},
    "names": {"date_words"}, "lint": {"channel_words", "remark_phrases"}, "record": {"owner_word_names", "quote_marks"},
    "agents.known": {"name", "role"}, "agents.groups": {"members", "open", "max_members", "source"},
    "intake.tools": {"enabled", "agent", "include", "exclude", "import_earlier", "path"},
}


def _value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_value(key)} = {_value(item)}" for key, item in value.items()) + " }"
    return "[" + ", ".join(_value(item) for item in value) + "]"


def _emit(data: dict[str, Any], parts: tuple[str, ...] = ()) -> str:
    text = "[" + ".".join(_value(part) for part in parts) + "]\n" if parts else ""
    text += "".join(f"{_value(key)} = {_value(value)}\n" for key, value in data.items()
                    if not isinstance(value, dict))
    for key, value in data.items():
        if isinstance(value, dict):
            text += "\n" + _emit(value, (*parts, key))
    return text


def _apply(text: str, edits: list[dict[str, Any]]) -> str:
    data = tomllib.loads(text)
    for edit in edits:
        path = edit.get("table", edit.get("new_table", ""))
        parts = path.split(".")
        parent = data
        for part in parts if "new_table" not in edit else parts[:-1]:
            if not isinstance(parent.get(part), dict):
                parent[part] = {}
            parent = parent[part]
        if "add_to_list" in edit:
            # Appending must really target a list; no forgiving repair in this independent interpreter.
            self_list = parent[edit["add_to_list"]]
            assert isinstance(self_list, list)
            self_list.append(edit["value"])
        elif "set_value" in edit:
            parent[edit["set_value"]] = edit["value"]
        else:
            assert parts[-1] not in parent
            parent[parts[-1]] = dict(edit["values"])
    return _emit(data)


def _settings(kind: str, form: str) -> str:
    value = KINDS.get(kind)
    members = {} if kind == "absent" else {"members": tomllib.loads("v = " + value)["v"]}
    table = {**members, "open": True}
    if form == "table":
        body = '[agents.groups.workshop]\n' + (f"members = {value}\n" if value else "") + "open = true\n"
    elif form == "inline":
        body = "[agents.groups]\nworkshop = " + _value(table) + "\n"
    else:
        prefix = {"dotted": "[agents.groups]\n", "parent": "[agents]\n", "top": ""}[form]
        key = {"dotted": "workshop", "parent": "groups.workshop", "top": "agents.groups.workshop"}[form]
        body = prefix + (f"{key}.members = {value}\n" if value else "") + f"{key}.open = true\n"
    forest = '[forest]\ntimezone = "UTC"\n\n'
    return ((body + "\n" + forest if form == "top" else forest + body)
            + '\n[agents.known.old-clerk]\nname = "Old Clerk"\nrole = "keeps the fictional shelf"\n')


@contextmanager
def _entry_failure(*_args: Any, **_kwargs: Any) -> Any:
    raise system.Denied("fictional-lock")
    yield  # pragma: no cover


class EditRecoveryTests(ForestTestCase):
    def test_a_new_exact_id_does_not_silently_replace_an_existing_members_identity(self) -> None:
        text = ('[agents.known.shadow]\nname = "crate-bot"\n'
                '[agents.groups.workshop]\nmembers = ["crate-bot"]\nopen = true\n')
        root = self.make_forest({"forest.toml": text}, name="new-id-reference")
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "workshop", "Crate.Bot")
        self.assertEqual(caught.exception.code, "settings_unpatched")
        self.assertEqual((root / "forest.toml").read_text(), text)
        edits = caught.exception.details["edits"]
        self.assertEqual(edits[0]["previous"], repr(["crate-bot"]))
        repaired = _apply(text, edits)
        parsed = config.parse_config(repaired)
        self.assertEqual(parsed.agents.group("workshop").members, ("shadow", "crate-bot"))
        (root / "forest.toml").write_text(repaired)
        self.assertEqual(groups.resolve_caller(root, "workshop", "Crate.Bot").id, "crate-bot")
        self.assertEqual(groups.resolve_caller(root, "workshop", "shadow").id, "shadow")

    def test_runtime_only_exact_id_membership_admission_idempotence_and_cap_share_one_identity_set(self) -> None:
        text = ('[agents.known.shadow]\nname = "lane-agent"\n'
                '[agents.groups.workshop]\nmembers = ["lane-agent"]\nopen = false\nmax_members = 2\n')
        turn = {"id": "fictional-turn", "at": "2051-03-02T08:00:00+00:00", "session": "fictional-session",
                "role": "agent", "text": "The fictional shelf is ready.", "origin": "interactive",
                "source": {"door": "cli"}, "sources": ["door:cli"]}
        root = self.make_forest({"forest.toml": text,
                                 "06 istm/lane-agent/2051-03-02.jsonl": json.dumps(turn) + "\n"}, name="runtime-members")
        path = root / "forest.toml"
        self.assertEqual(config.load_config(root).agents.group("workshop").members, ("shadow",))
        resolved = groups.resolve_caller(root, "workshop", "lane-agent")
        self.assertEqual((resolved.id, resolved.wrote), ("lane-agent", False))
        self.assertEqual(path.read_text(), text)
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "workshop", "shadow")
        self.assertEqual(caught.exception.code, "agent_not_in_group")
        self.assertEqual([member["id"] for member in caught.exception.details["members"]], ["lane-agent"])
        listed = groups.listing(root)
        self.assertEqual([member["id"] for member in listed["groups"][0]["members"]], ["lane-agent"])

        path.write_text(text.replace("open = false", "open = true"))
        admitted = groups.resolve_caller(root, "workshop", "shadow")
        self.assertEqual((admitted.id, admitted.wrote), ("shadow", True))
        self.assertEqual(tomllib.loads(path.read_text())["agents"]["groups"]["workshop"]["members"],
                         ["lane-agent", "shadow"])
        before = path.read_bytes()
        again = groups.resolve_caller(root, "workshop", "shadow")
        self.assertEqual((again.id, again.wrote), ("shadow", False))
        self.assertEqual(path.read_bytes(), before)
        listed = groups.listing(root)
        self.assertEqual([member["id"] for member in listed["groups"][0]["members"]], ["lane-agent", "shadow"])

        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, "workshop", "Third Clerk")
        self.assertEqual(caught.exception.code, "group_full")
        self.assertEqual(caught.exception.details["edits"],
                         [{"set_value": "max_members", "table": "agents.groups.workshop", "value": 3}])
        self.assertEqual(path.read_bytes(), before)

    def test_an_unreadable_original_kind_still_gives_an_applicable_missing_list_edit(self) -> None:
        class ChangedDoor:
            def __init__(self, returned: bytes | None) -> None:
                self.returned = returned

            def __getattr__(self, name: str) -> Any:
                return self.read_bytes if name == "read_bytes" else getattr(system, name)

            def read_bytes(self, _path: Any) -> bytes:
                if self.returned is None:
                    raise system.Denied("fictional-settings")
                return self.returned

        for number, returned in enumerate((None, b"\xff", b"[")):
            with self.subTest(returned=returned):
                text = _settings("absent", "table")
                root = self.make_forest({"forest.toml": text}, name=f"unreadable-original-{number}")
                # Only the admission's later reads fail; config has already established the original file.
                with mock.patch.object(groups, "system", ChangedDoor(returned)):
                    with self.assertRaises(MemoryForestError) as caught:
                        groups.resolve_caller(root, "workshop", "New Clerk")
                self.assertIn(caught.exception.code, {"settings_unpatched", "settings_unwritable"})
                edit = caught.exception.details["edits"][0]
                self.assertEqual(edit, {"set_value": "members", "table": "agents.groups.workshop",
                                        "value": ["new-clerk"]})
                (root / "forest.toml").write_text(_apply(text, caught.exception.details["edits"]))
                self.assertEqual(groups.resolve_caller(root, "workshop", "New Clerk").id, "new-clerk")

    def test_rejected_known_agent_entries_and_containers_have_replacement_edits(self) -> None:
        for number, (place, (kind, value)) in enumerate(itertools.product(("entry", "container"), KINDS.items())):
            if kind == "table":
                continue  # a table is a valid container/entry; its unknown fields are covered below
            with self.subTest(place=place, kind=kind):
                known = (f"[agents.known]\nnew-clerk = {value}\n" if place == "entry"
                         else f"[agents]\nknown = {value}\n")
                text = known + '\n[agents.groups.workshop]\nmembers = []\nopen = true\n'
                root = self.make_forest({"forest.toml": text}, name=f"known-repair-{number}")
                with mock.patch.object(groups, "_patch", side_effect=groups._Unpatchable("known")):
                    with self.assertRaises(MemoryForestError) as caught:
                        groups.resolve_caller(root, "workshop", "New Clerk")
                self.assertEqual(caught.exception.code, "settings_unpatched")
                edit = caught.exception.details["edits"][-1]
                self.assertEqual(edit["set_value"], "new-clerk" if place == "entry" else "known")
                self.assertIn(repr(tomllib.loads("v = " + value)["v"]), caught.exception.message)
                (root / "forest.toml").write_text(_apply(text, caught.exception.details["edits"]))
                self.assertEqual(groups.resolve_caller(root, "workshop", "New Clerk").id, "new-clerk")
                self.assertEqual(config.load_config(root).agents.declared("new-clerk").name, "New Clerk")

    def test_members_kind_form_and_permissions_each_produce_a_working_call_or_executable_edits(self) -> None:
        options = itertools.product(("absent", *KINDS), ("table", "inline", "dotted", "parent", "top"),
                                    ("writable", "file-read-only", "folder-read-only", "no-state-read-only"))
        for number, (kind, form, mode) in enumerate(options):
            with self.subTest(kind=kind, form=form, mode=mode):
                text = _settings(kind, form)
                root = self.make_forest({"forest.toml": text}, name=f"recovery-{number}")
                path = root / "forest.toml"
                if mode == "folder-read-only":
                    (root / ".forest").mkdir()
                if mode == "file-read-only":
                    path.chmod(0o444)
                if mode in {"folder-read-only", "no-state-read-only"}:
                    root.chmod(0o555)
                refusal = None
                try:
                    try:
                        result = groups.resolve_caller(root, "workshop", "New Clerk")
                    except MemoryForestError as exc:
                        refusal = exc
                    if refusal is not None:
                        self.assertIn(refusal.code, {"settings_unpatched", "settings_unwritable"})
                        if mode == "no-state-read-only":
                            self.assertEqual(refusal.code, "settings_unpatched")
                        self.assertEqual(refusal.next_step, "report")
                        self.assertEqual(refusal.details["agent"], "new-clerk")
                        self.assertEqual(refusal.details["group"], "workshop")
                        self.assertEqual(path.read_text(), text)
                        edits = refusal.details["edits"]
                        self.assertTrue(edits)
                        first = edits[0]
                        self.assertEqual(first.get("add_to_list", first.get("set_value")), "members")
                        self.assertEqual("add_to_list" in first, kind in {"list", "empty-list", "mixed-list"})
                        if kind not in {"absent", "list", "empty-list", "mixed-list"}:
                            self.assertIn(repr(tomllib.loads("v = " + KINDS[kind])["v"]), refusal.message)
                        repaired = _apply(text, edits)
                        raw = tomllib.loads(repaired)
                        self.assertEqual(raw["forest"], {"timezone": "UTC"})
                        self.assertEqual(raw["agents"]["known"]["old-clerk"],
                                         {"name": "Old Clerk", "role": "keeps the fictional shelf"})
                    else:
                        self.assertEqual((result.id, result.wrote), ("new-clerk", True))
                        repaired = path.read_text()
                finally:
                    root.chmod(0o700)
                    path.chmod(0o600)
                if refusal is not None:
                    path.write_text(repaired)
                parsed = config.load_config(root)
                wanted = ("old-clerk", "new-clerk") if kind in {"text", "list", "mixed-list"} else ("new-clerk",)
                self.assertEqual(parsed.agents.group("workshop").members, wanted)
                before = path.read_bytes()
                self.assertEqual(groups.resolve_caller(root, "workshop", "New Clerk").id, "new-clerk")
                self.assertEqual(path.read_bytes(), before)

    def test_a_members_subtable_is_replaced_and_a_table_array_gets_a_member(self) -> None:
        for index, header in enumerate(("[agents.groups.workshop.members]", "[[agents.groups.workshop.members]]")):
            text = ('[agents.groups.workshop]\nopen = true\n' + header + '\nextra = "fictional"\n')
            root = self.make_forest({"forest.toml": text}, name=f"member-table-{index}")
            with mock.patch.object(groups, "_patch", side_effect=groups._Unpatchable("members")):
                with self.assertRaises(MemoryForestError) as caught:
                    groups.resolve_caller(root, "workshop", "New Clerk")
            repaired = _apply(text, caught.exception.details["edits"])
            (root / "forest.toml").write_text(repaired)
            self.assertEqual(groups.resolve_caller(root, "workshop", "New Clerk").id, "new-clerk")

    def test_either_lock_entry_failure_carries_edits_and_does_not_catch_body_refusals(self) -> None:
        for number, lock in enumerate(("exclusive", "writer_lock")):
            text = _settings("text", "table")
            root = self.make_forest({"forest.toml": text}, name=f"lock-{number}")
            with mock.patch.object(groups, lock, _entry_failure):
                with self.assertRaises(MemoryForestError) as caught:
                    groups.resolve_caller(root, "workshop", "New Clerk")
            self.assertEqual(caught.exception.code, "settings_unpatched")
            (root / "forest.toml").write_text(_apply(text, caught.exception.details["edits"]))
            self.assertEqual(groups.resolve_caller(root, "workshop", "New Clerk").id, "new-clerk")
        # A lock failure must not turn an exact id into a replacement display name in its recovery edits.
        text = _settings("list", "table")
        root = self.make_forest({"forest.toml": text}, name="stored-display-lock")
        with mock.patch.object(groups, "writer_lock", _entry_failure), \
                mock.patch.object(groups, "_agent_rows", return_value=[("stored", "Stored Display"),
                                                                       ("old-clerk", "Old Clerk")]):
            with self.assertRaises(MemoryForestError) as caught:
                groups.resolve_caller(root, "workshop", "stored")
        self.assertEqual(caught.exception.code, "settings_unpatched")
        repaired = _apply(text, caught.exception.details["edits"])
        self.assertEqual(config.parse_config(repaired).agents.declared("stored").name, "Stored Display")
        (root / "forest.toml").write_text(repaired)
        self.assertEqual(groups.resolve_caller(root, "workshop", "stored").id, "stored")
        root = self.make_forest({"forest.toml": _settings("list", "table")}, name="body-failure")
        real = groups._interpret
        calls = []

        def interpret(*args: Any, **kwargs: Any) -> Any:
            calls.append(None)
            return real(*args, **kwargs) if len(calls) == 1 else MemoryForestError("agent_ambiguous", "fictional ambiguity")

        with mock.patch.object(groups, "_interpret", interpret):
            with self.assertRaises(MemoryForestError) as caught:
                groups.resolve_caller(root, "workshop", "New Clerk")
        self.assertEqual(caught.exception.code, "agent_ambiguous")

    def test_every_other_answer_that_requests_a_settings_edit_supplies_executable_edits(self) -> None:
        for number, text in enumerate(("", '[agents.groups.workshop]\nmembers = ["old-clerk"]\nopen = true\nmax_members = 1\n')):
            root = self.make_forest({"forest.toml": text}, name=f"other-edit-{number}")
            with self.assertRaises(MemoryForestError) as caught:
                groups.resolve_caller(root, "workshop", "New Clerk")
            self.assertEqual(caught.exception.code, ("group_unknown", "group_full")[number])
            self.assertEqual(caught.exception.details["group"], "workshop")
            if number:
                self.assertEqual(caught.exception.details["max_members"], 1)
            (root / "forest.toml").write_text(_apply(text, caught.exception.details["edits"]))
            self.assertEqual(groups.resolve_caller(root, "workshop", "New Clerk").id, "new-clerk")


class ParserKeyTests(ForestTestCase):
    def test_an_invalid_group_id_does_not_create_a_group_or_affect_the_other_entry(self) -> None:
        parsed = config.parse_config('[agents.groups."Bad.Group"]\nopen = true\nmembers = ["clerk"]\n'
                                     '[agents.groups.workshop]\nopen = true\n')
        self.assertEqual(tuple(group.id for group in parsed.agents.groups), ("workshop",))
        self.assertEqual(parsed.agents.unplaceable, (("agents.groups.Bad.Group", "Bad.Group"),))
        self.assertTrue(parsed.agents.group("workshop").open)

    def test_global_table_shapes_boolean_values_and_invalid_toml_keep_precise_refusals(self) -> None:
        for table in DOCUMENTED_KEYS:
            if "." not in table:
                for value in ("7", '"fictional"', "false", "[]"):
                    with self.assertRaises(MemoryForestError) as caught:
                        config.parse_config(f"{table} = {value}\n")
                    self.assertEqual((caught.exception.code, caught.exception.details["key"]),
                                     ("settings_invalid", table))
        with self.assertRaises(MemoryForestError) as caught:
            config.parse_config("[agents]\nauto_create = 7\n")
        self.assertEqual(caught.exception.details["key"], "agents.auto_create")
        for source, details in ((None, {}), ("fictional-settings.toml", {"source": "fictional-settings.toml"})):
            with self.assertRaises(MemoryForestError) as caught:
                config.parse_config("broken = [", source=source)
            self.assertEqual((caught.exception.code, caught.exception.details), ("settings_invalid", details))

    def test_rendering_preserves_nonempty_names_roles_and_source_membership(self) -> None:
        text = ('[agents.known.clerk]\nname = "Crumb · ` Clerk"\nrole = "keeps the fictional shelf"\n'
                '[agents.groups.workshop]\nmembers = ["clerk"]\nopen = true\nmax_members = 5\nsource = "fictional-source"\n')
        parsed = config.parse_config(text)
        rendered = config.render_config(parsed)
        self.assertEqual(config.parse_config(rendered), parsed)
        raw = tomllib.loads(rendered)
        self.assertEqual(raw["agents"]["known"]["clerk"], {"name": "Crumb · ` Clerk", "role": "keeps the fictional shelf"})
        self.assertEqual(raw["agents"]["groups"]["workshop"]["source"], "fictional-source")

    def test_member_references_use_the_same_admitted_text_before_exact_id_lookup(self) -> None:
        text = ('[agents.known.old-handle]\nname = "Different Clerk"\n'
                '[agents.groups.workshop]\nmembers = [" old-handle ", "old-handle"]\n')
        root = self.make_forest({"forest.toml": text}, name="member-id-spacing")
        parsed = config.load_config(root)
        self.assertEqual(parsed.agents.group("workshop").members, ("old-handle",))
        self.assertEqual(groups.resolve_caller(root, "workshop", " old-handle ").id, "old-handle")
        issues = agents.problems(root, parsed, agents.Subjects({}, frozenset()))
        self.assertEqual(len([issue for issue in issues if "counted once" in issue.detail or "duplicate reference" in issue.detail]), 1)

    def test_registry_only_names_are_resolved_before_member_diagnostics(self) -> None:
        from memory_forest import move
        from test_v1_find import FILES
        from test_v1_groups import BAKERY_GROUPS
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="registry-member-diagnostics")
        move.execute(root, "new-agent", id="old-handle", name="Different Clerk", today="2051-03-02")
        with (root / "forest.toml").open("a") as stream:
            stream.write('\n[agents.groups.workshop]\nmembers = [" old-handle ", "Different Clerk"]\n')
        parsed = config.load_config(root)
        self.assertEqual(groups.resolve_caller(root, "workshop", "Different Clerk").id, "old-handle")
        issues = agents.problems(root, parsed, agents.Subjects({}, frozenset()))
        self.assertFalse(any("cannot be placed" in issue.detail for issue in issues))
        self.assertEqual(len([issue for issue in issues if "duplicate reference" in issue.detail]), 1)

    def test_name_spelling_survives_and_every_bad_role_kind_is_isolated(self) -> None:
        display = "Crumb  \u3000  Clerk"
        for kind, value in {**KINDS, "bell": '"bad\\u0007role"', "del": '"bad\\u007frole"'}.items():
            text = '[agents.known.clerk]\nname = ' + _value(display) + '\nrole = ' + value + '\n'
            parsed = config.parse_config(text)
            self.assertEqual(parsed.agents.declared("clerk").name, display, kind)
            raw = tomllib.loads("v = " + value)["v"]
            good = isinstance(raw, str) and not any(ord(char) < 32 or ord(char) == 127 for char in raw)
            self.assertEqual(parsed.agents.declared("clerk").role, " ".join(raw.split()) if good else "", kind)
            self.assertEqual(len(parsed.agents.left_out), 0 if good else 1, kind)

    def test_a_later_provisional_id_does_not_become_a_false_duplicate(self) -> None:
        parsed = config.parse_config('[agents.known.scout]\nname = "Clerk"\n'
                                     '[agents.groups.workshop]\nmembers = ["scout", "clerk"]\n')
        self.assertEqual(parsed.agents.group("workshop").members, ("scout",))
        self.assertEqual(parsed.agents.group("workshop").member_refs, ("scout", "clerk"))
        self.assertEqual(parsed.agents.left_out, ())

    def test_intake_owner_keys_types_and_rendering_keep_the_declared_id_record(self) -> None:
        text = ('[intake.tools.zed]\nenabled = false\nagent = "courier-id"\n'
                'include = ["fictional/from"]\nexclude = ["fictional/else"]\n'
                'import_earlier = true\npath = "fictional/export"\n[intake.tools.alpha]\n')
        parsed = config.parse_config(text)
        self.assertEqual([tool.id for tool in parsed.intake.tools], ["alpha", "zed"])
        tool = parsed.intake.tools[1]
        self.assertEqual((tool.enabled, tool.agent, tool.include, tool.exclude, tool.import_earlier, tool.path),
                         (False, "courier-id", ("fictional/from",), ("fictional/else",), True, "fictional/export"))
        self.assertEqual(config.parse_config(config.render_config(parsed)), parsed)
        invalid = ('[intake]\ntools = 7\n', '[intake.tools]\n"bad id" = {}\n',
                   '[intake.tools]\nclerk = 7\n', '[intake.tools.clerk]\nunknown = true\n',
                   '[intake.tools.clerk]\nenabled = "yes"\n', '[intake.tools.clerk]\ninclude = [" "]\n',
                   '[intake.tools.clerk]\nexclude = [7]\n', '[intake.tools.clerk]\npath = 7\n')
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(MemoryForestError) as caught:
                config.parse_config(value)
            self.assertEqual(caught.exception.code, "settings_invalid")

    def test_original_valid_member_references_survive_the_parsers_partial_identity_set(self) -> None:
        text = ('[agents.known.shadow]\nname = "lane-agent"\n'
                '[agents.groups.workshop]\nmembers = ["lane-agent", " Shadow ", "lane-agent", 7, "bad\\nname"]\n')
        parsed = config.parse_config(text)
        group = parsed.agents.group("workshop")
        # The pure parser has no lane-agent id record. Runtime can now re-resolve these original texts.
        self.assertEqual(group.members, ("shadow",))
        self.assertEqual(group.member_refs, ("lane-agent", " Shadow ", "lane-agent"))
        rendered = tomllib.loads(config.render_config(parsed))
        self.assertNotIn("member_refs", rendered["agents"]["groups"]["workshop"])
        self.assertEqual(rendered["agents"]["groups"]["workshop"]["members"], list(group.member_refs))
        self.assertEqual(config.parse_config(config.render_config(parsed)).agents.group("workshop").member_refs,
                         group.member_refs)

    def test_undeclared_orchestrator_and_intake_ids_beat_another_agents_declared_name(self) -> None:
        agents_text = ('[agents]\norchestrators = ["front-desk"]\n'
                       '[agents.known.shadow-main]\nname = "front-desk"\n'
                       '[agents.known.shadow-intake]\nname = "courier-id"\n'
                       '[agents.groups.workshop]\nmembers = ["front-desk", "courier-id"]\n'
                       '[agents.groups.other-room]\nmembers = ["courier-id", "front-desk"]\n')
        intake = '[intake.tools.jsonl]\nagent = "courier-id"\n'
        for text in (agents_text + intake, intake + agents_text):
            parsed = config.parse_config(text)
            self.assertIsNone(parsed.agents.declared("front-desk"))
            self.assertIsNone(parsed.agents.declared("courier-id"))
            self.assertEqual(parsed.agents.group("workshop").members, ("front-desk", "courier-id"))
            self.assertEqual(parsed.agents.group("other-room").members, ("courier-id", "front-desk"))
            self.assertEqual(parsed.agents.group("workshop").member_refs, ("front-desk", "courier-id"))
            self.assertEqual(parsed.agents.left_out, ())

    def test_internal_fields_as_settings_of_every_toml_kind_are_isolated_and_reported(self) -> None:
        diagnostic = {item.name for item in fields(config.AgentSettings)} - DOCUMENTED_KEYS["agents"]
        self.assertLessEqual({"left_out", "unplaceable"}, diagnostic)
        for number, (key, (kind, value)) in enumerate(itertools.product(sorted(diagnostic), KINDS.items())):
            with self.subTest(key=key, kind=kind):
                text = ('[agents]\nauto_create = false\n' + f'{key} = {value}\n'
                        + '[agents.known.old-clerk]\nname = "Old Clerk"\n'
                        + '[agents.groups.workshop]\nmembers = ["old-clerk"]\nopen = true\n')
                root = self.make_forest({"forest.toml": text}, name=f"diagnostic-{number}")
                parsed = config.load_config(root)
                self.assertFalse(parsed.agents.auto_create)
                self.assertEqual(parsed.agents.declared("old-clerk").name, "Old Clerk")
                self.assertEqual(parsed.agents.group("workshop").members, ("old-clerk",))
                self.assertEqual(parsed.agents.unplaceable, ())
                self.assertEqual(parsed.agents.left_out, ((f"agents.{key}", "is not a known setting; it is left out"),))
                problems = agents.problems(root, parsed, agents.Subjects({}, set()))
                self.assertTrue(any(problem.code == "agent_setting_invalid" and f"agents.{key}" in problem.detail
                                    for problem in problems), problems)

    def test_every_table_has_an_explicit_owner_key_set_and_rendering_has_no_diagnostics(self) -> None:
        self.assertEqual(config.SETTING_KEYS, DOCUMENTED_KEYS)
        self.assertEqual(set(config._SECTIONS), set(config.SETTING_KEYS) - {"agents.known", "agents.groups", "intake.tools"})
        raw = tomllib.loads(config.render_config())
        for table, allowed in config.SETTING_KEYS.items():
            if "." not in table:
                self.assertLessEqual(set(raw[table]), allowed)
        for table, record in (("agents.known", config.KnownAgent), ("agents.groups", config.AgentGroup)):
            internal = {item.name for item in fields(record)} - DOCUMENTED_KEYS[table]
            for key, (kind, value) in itertools.product(sorted({"unknown", *internal}), KINDS.items()):
                text = f'[{table}.clerk]\n{key} = {value}\n'
                parsed = config.parse_config(text)
                self.assertEqual(parsed.agents.left_out,
                                 ((f"{table}.clerk.{key}", "is not a known setting; it is left out"),), kind)
