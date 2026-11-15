"""``forest guide``, ``forest setup`` and ``forest doctor``: an agent connects itself, and the doctor proves it.

The walk plays the agent: it follows ``next`` from setup, carries out ``todo`` with the package's own doors,
and runs the doctor until it passes; then a second agent does the same in the same forest. Juno's studio,
Kiln Bot, Ledger Bot and Studio Hub are invented; nothing here is anyone's data.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import bounds  # noqa: F401  (installs a deadline for every unittest invocation)
from memory_forest import agents, cli, cli_setup, doctor, mcp, setup_agent
from memory_forest.config import DEFAULT_CONFIG, load_config

REPOSITORY = Path(__file__).resolve().parents[1]
ANSWERS = {
    "--as": "Kiln Bot", "--main": "yes", "--conversations": "all", "--earlier": "no", "--keeper": "this",
    "--when": "every night at 23:30", "--daily-model": "small model", "--filing-model": "large model",
}
FLAG_OF = {question.key: question.flag for question in setup_agent.QUESTIONS}
NOW = datetime(2042, 5, 14, 9, 30, tzinfo=timezone.utc)


def forest(*argv: str, stdin: bytes = b"") -> tuple[int, str, str]:
    """``forest`` in this process, in the current folder."""
    out, err = io.StringIO(), io.StringIO()
    feed = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
    with redirect_stdout(out), redirect_stderr(err), mock.patch.object(sys, "stdin", feed):
        try:
            code = cli.forest_main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def as_json(*argv: str, stdin: bytes = b"") -> tuple[int, dict]:
    code, out, err = forest(*argv, "--json", stdin=stdin)
    return code, json.loads(out) if out.strip() else {"stderr": err}


def answers(root: str = "studio-forest", **changes: str | None) -> list[str]:
    given = {"--root": root, **ANSWERS, **{f"--{key.replace('_', '-')}": value for key, value in changes.items()}}
    given = {"--as" if flag == "--as-name" else flag: value for flag, value in given.items()}
    return [part for flag, value in given.items() if value is not None for part in (flag, value)]


def snapshot(folder: Path) -> dict[str, bytes]:
    """Every file under ``folder`` with its bytes, and every folder."""
    if not folder.exists():
        return {}
    found: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        relative = str(path.relative_to(folder))
        found[relative] = path.read_bytes() if path.is_file() else b"<folder>"
    return found


def parses(command: str) -> None:
    """A command a check or a todo names exists in the parser, with its flags."""
    words = shlex.split(command)
    assert words[0] == "forest", command
    cli.build_forest_parser().parse_args(words[1:])


class Scratch(unittest.TestCase):
    """Each test in a new folder, as the current folder, so a relative --root is a path as the caller gave it."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="amf-setup-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        before = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, before)
        self.root = self.base / "studio-forest"

    def setup_done(self, *argv: str) -> dict:
        code, body = as_json("setup", *argv, "--timezone", "UTC")
        self.assertEqual((code, body.get("next")), (0, "done"), body)
        return body


# ═══════════════════════════════ the walk ═══════════════════════════════


class WalkTests(Scratch):
    def play(self, name: str, *extra: str) -> dict:
        """One agent from nothing to a passing doctor, following only what the answers say."""
        before = snapshot(self.base)
        code, asked = as_json("setup")
        self.assertEqual((code, asked["next"]), (1, "ask_owner"))
        self.assertEqual(snapshot(self.base), before, "ask_owner wrote something")
        done = self.setup_done(*answers(**{"as_name": name}), *extra)
        steps = {step["step"]: step for step in done["todo"]}
        for step in done["todo"]:
            if step["command"].startswith("forest "):
                parses(step["command"])
        turns = Path(steps["bring_turns"]["file"])
        self.assertFalse(turns.is_absolute(), "a path as the caller gave it")
        intake = shlex.split(steps["bring_turns"]["command"])[1:]
        now = datetime.now(timezone.utc)
        lines = [{"at": (now - timedelta(seconds=30)).isoformat(), "session": "s1", "role": "owner",
                  "text": f"{name}, the blue bowls go in the next glaze firing."},
                 {"at": (now - timedelta(seconds=20)).isoformat(), "session": "s1", "role": "agent",
                  "text": "Noted: the blue bowls are queued."}]
        with turns.open("a", encoding="utf-8") as handle:
            handle.writelines(json.dumps(line) + "\n" for line in lines)
        self.assertEqual(forest(*intake)[0], 0)
        verify = shlex.split(steps["verify"]["command"])[1:]
        code, first = as_json(*verify[:-1])
        self.assertEqual((code, first["result"]), (1, "todo"), first)
        agent_id = done["agent"]["id"]
        report = first
        for _round in range(6):
            if report["result"] == "passed":
                break
            for check in report["checks"]:
                if check["state"] == "problem":
                    self.fail(check)
                if check["state"] != "todo":
                    continue
                parses(check["command"])
                if check["command"].endswith("--phrase"):
                    _code, said = as_json(*shlex.split(check["command"])[1:])
                    with turns.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "session": "s1",
                                                 "role": "agent", "text": f"Saying it: {said['phrase']}."}) + "\n")
                    self.assertEqual(forest(*intake)[0], 0)
                elif check["command"].startswith("forest mcp"):
                    # The agent registered the server in its own tool and calls memory_search once.
                    reply = mcp._call(self.root, agent_id, {"name": "memory_search",
                                                            "arguments": {"words": "blue bowls"}}, "stdio")
                    self.assertFalse(reply["isError"], reply)
                else:
                    self.assertEqual(forest(*shlex.split(check["command"])[1:])[0], 0, check)
            code, report = as_json(*verify[:-1])
        self.assertEqual((code, report["result"]), (0, "passed"), report)
        turns_check = next(check for check in report["checks"] if check["name"] == "turns")
        self.assertIn("adapter:jsonl", turns_check["evidence"])
        # A line appended earlier is read once: the printed command again takes nothing.
        lane = sorted((self.root / "06 istm" / agent_id).glob("*.jsonl"))
        held = sum(len(path.read_text().splitlines()) for path in lane)
        self.assertEqual(forest(*intake)[0], 0)
        self.assertEqual(sum(len(path.read_text().splitlines()) for path in lane), held)
        self.assertEqual(held, 3)
        return done

    def test_two_agents_connect_themselves_to_one_forest_and_both_pass(self) -> None:
        kiln = self.play("Kiln Bot")
        ledger = self.play("Ledger Bot", "--main", "no", "--keeper", "other")
        self.assertEqual((kiln["agent"]["id"], ledger["agent"]["id"]), ("kiln-bot", "ledger-bot"))
        for agent_id, words in (("kiln-bot", "Kiln Bot,"), ("ledger-bot", "Ledger Bot,")):
            texts = [json.loads(line)["text"] for path in (self.root / "06 istm" / agent_id).glob("*.jsonl")
                     for line in path.read_text().splitlines()]
            self.assertTrue(any(text.startswith(words) for text in texts), texts)
            self.assertFalse(any(text.startswith(("Kiln Bot,", "Ledger Bot,")) and not text.startswith(words)
                                 for text in texts), f"{agent_id} holds another agent's turn")
        for name in ("Kiln Bot", "Ledger Bot"):
            code, report = as_json("doctor", "--as", name, "--root", "studio-forest")
            self.assertEqual((code, report["result"]), (0, "passed"), report)
        routine = {check["name"]: check["state"] for check in
                   as_json("doctor", "--as", "Ledger Bot", "--root", "studio-forest")[1]["checks"]}
        self.assertEqual(routine["routine"], "skipped")


# ═══════════════════════════════ setup ═══════════════════════════════


class SetupTests(Scratch):
    def test_no_answers_asks_every_question_and_writes_nothing(self) -> None:
        code, body = as_json("setup")
        self.assertEqual(code, 1)
        self.assertEqual([item["key"] for item in body["questions"]], [q.key for q in setup_agent.QUESTIONS])
        for item in body["questions"]:
            self.assertTrue(item["why_owner"] and item["question"] and item["flag"])
        self.assertEqual(body["proposals"]["timezone"]["flag"], "--timezone")
        self.assertEqual(snapshot(self.base), {})

    def test_each_missing_answer_is_asked_and_never_assumed(self) -> None:
        for question in setup_agent.QUESTIONS:
            with self.subTest(question=question.key):
                argv = answers(**{("as_name" if question.key == "name" else question.key): None})
                code, body = as_json("setup", *argv, "--timezone", "UTC")
                self.assertEqual((code, [item["key"] for item in body["questions"]]), (1, [question.key]), body)
                self.assertEqual(snapshot(self.base), {})

    def test_an_answer_that_makes_a_question_moot_drops_it(self) -> None:
        code, body = as_json("setup", "--conversations", "none", "--keeper", "other")
        keys = [item["key"] for item in body["questions"]]
        self.assertEqual(keys, ["root", "name", "main"])
        code, body = as_json("setup", "--conversations", "all", "--keeper", "this")
        self.assertIn("earlier", [item["key"] for item in body["questions"]])
        self.assertIn("filing_model", [item["key"] for item in body["questions"]])

    def test_a_wrong_choice_is_refused_with_fix(self) -> None:
        code, body = as_json("setup", *answers(main="maybe"))
        self.assertEqual((code, body["error"]["code"], body["error"]["next"]), (2, "arguments_invalid", "fix"))
        self.assertEqual(snapshot(self.base), {})

    def test_a_new_forest_writes_its_settings_record_and_file_and_a_repeat_changes_nothing(self) -> None:
        done = self.setup_done(*answers())
        self.assertTrue(done["new"])
        config = load_config(self.root)
        self.assertEqual(config.timezone, "UTC")
        self.assertEqual(config.agents.declared("kiln-bot").name, "Kiln Bot")
        self.assertEqual(config.agents.orchestrators, ("kiln-bot",))
        self.assertEqual((self.root / "transcripts" / "kiln-bot.jsonl").read_bytes(), b"")
        record = setup_agent.record(self.root, "kiln-bot")
        self.assertEqual(record["answers"]["keeper"], "this")
        self.assertEqual([step["step"] for step in done["todo"]],
                         ["register_mcp", "bring_turns", "own_turn", "routine", "verify"])
        before = snapshot(self.root)
        again = self.setup_done(*answers())
        self.assertEqual(again["changed"], [])
        self.assertEqual(snapshot(self.root), before)

    def test_a_changed_answer_says_what_changed(self) -> None:
        self.setup_done(*answers())
        settings = (self.root / "forest.toml").read_text()
        changed = self.setup_done(*answers(main="no", keeper="nobody"))["changed"]
        self.assertEqual(len(changed), 2, changed)
        self.assertIn("taken out of agents.orchestrators", changed[0])
        self.assertIn("main was yes, now no", changed[1])
        self.assertIn("keeper was this, now nobody", changed[1])
        self.assertEqual(load_config(self.root).agents.orchestrators, ())
        after = (self.root / "forest.toml").read_text()
        self.assertEqual(after.replace('orchestrators = []', 'orchestrators = ["kiln-bot"]'), settings)

    def test_dry_run_writes_nothing_for_a_new_forest_and_for_an_existing_one(self) -> None:
        code, body = as_json("setup", *answers(), "--timezone", "UTC", "--dry-run")
        self.assertEqual((code, body["next"], body["dry_run"]), (0, "done", True))
        self.assertEqual(len(body["changed"]), 6, body["changed"])
        self.assertIn("02 ltm/kiln-bot_LTM.md: this agent's own tree", body["changed"])
        self.assertEqual(snapshot(self.base), {})
        self.setup_done(*answers())
        before = snapshot(self.root)
        code, body = as_json("setup", *answers(**{"as_name": "Ledger Bot", "keeper": "other"}), "--dry-run")
        self.assertEqual((code, body["agent"]["id"]), (0, "ledger-bot"))
        self.assertEqual(snapshot(self.root), before)

    def test_a_join_leaves_a_forest_its_own_judge_passes(self) -> None:
        """A declared agent without its tree is a level the judge calls due: setup makes the tree itself."""
        first = self.setup_done(*answers())
        self.assertIn("02 ltm/kiln-bot_LTM.md: this agent's own tree", first["changed"])
        second = self.setup_done(*answers(**{"as_name": "Ledger Bot", "main": "no", "keeper": "other"}))
        self.assertIn("02 ltm/ledger-bot_LTM.md: this agent's own tree", second["changed"])
        for agent_id in ("kiln-bot", "ledger-bot"):
            self.assertTrue((self.root / "02 ltm" / f"{agent_id}_LTM.md").is_file())
        code, out, err = forest("check", "--sync", "--root", "studio-forest")
        self.assertEqual((code, out, err), (0, "", ""))
        code, out, err = forest("lint", "--root", "studio-forest")
        self.assertEqual(code, 0, out + err)
        self.assertIn("0 violations, 0 upkeep, 0 due", out)
        code, report = as_json("doctor", "--as", "ledger-bot", "--root", "studio-forest")
        agent = next(check for check in report["checks"] if check["name"] == "agent")
        self.assertIn("tree: made", agent["evidence"])
        # A repeat finds the tree and says nothing about it.
        self.assertEqual(self.setup_done(*answers())["changed"], [])

    def test_every_printed_command_names_the_agent_by_its_id(self) -> None:
        done = self.setup_done(*answers(**{"as_name": "스카우트 봇"}))
        agent_id = done["agent"]["id"]
        for step in done["todo"]:
            words = shlex.split(step["command"])
            if "--as" in words:
                self.assertEqual(words[words.index("--as") + 1], agent_id, step["step"])

    def test_a_second_keeper_is_asked_about_and_nothing_is_written(self) -> None:
        self.setup_done(*answers())
        before = snapshot(self.root)
        code, body = as_json("setup", *answers(**{"as_name": "Ledger Bot", "main": "no"}))
        self.assertEqual((code, body["next"]), (1, "ask_owner"))
        (question,) = body["questions"]
        self.assertEqual((question["key"], question["choices"]), ("keeper", ["other", "nobody"]))
        self.assertIn("kiln-bot keeps this forest", question["question"])
        self.assertEqual(snapshot(self.root), before)
        # The keeper itself may answer again.
        self.assertEqual(self.setup_done(*answers())["changed"], [])

    def test_a_keeper_that_is_not_the_main_agent_is_asked_about_and_nothing_is_written(self) -> None:
        # The routine reads every agent's turns, and only a main agent's scope holds them.
        code, body = as_json("setup", *answers(main="no"))
        self.assertEqual((code, body["next"]), (1, "ask_owner"))
        (question,) = body["questions"]
        self.assertEqual((question["key"], question["flag"], question["choices"]), ("main", "--main", ["yes", "no"]))
        self.assertIn("--keeper other", question["question"])
        self.assertEqual(snapshot(self.base), {})
        # With other answers still missing it is asked with them, in the same round.
        code, body = as_json("setup", "--root", "studio-forest", "--as", "Kiln Bot", "--main", "no",
                             "--conversations", "none", "--keeper", "this")
        self.assertEqual([item["key"] for item in body["questions"]], ["when", "daily_model", "filing_model", "main"])
        self.assertEqual(snapshot(self.base), {})
        # In a forest that exists the answer is the same question, and nothing changes.
        self.setup_done(*answers(keeper="nobody"))
        before = snapshot(self.root)
        code, body = as_json("setup", *answers(**{"as_name": "Ledger Bot", "main": "no"}))
        self.assertEqual((code, [item["key"] for item in body["questions"]]), (1, ["main"]))
        self.assertEqual(snapshot(self.root), before)
        # A keeper that is the main agent reads another agent's turns, as the routine's commands do.
        self.setup_done(*answers())
        ledger = self.setup_done(*answers(**{"as_name": "Ledger Bot", "main": "no", "keeper": "other"}))
        steps = {step["step"]: step for step in ledger["todo"]}
        Path(steps["bring_turns"]["file"]).write_text(json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(), "session": "s1", "role": "owner",
             "text": "Ledger Bot, the clay order is due on Friday."}) + "\n")
        self.assertEqual(forest(*shlex.split(steps["bring_turns"]["command"])[1:])[0], 0)
        (lane,) = sorted((self.root / "06 istm" / "ledger-bot").glob("*.jsonl"))
        code, out, err = forest("read", f"06 istm/ledger-bot/{lane.name}", "--as", "kiln-bot", "--root", "studio-forest")
        self.assertEqual(code, 0, err)
        self.assertIn("clay order", out)

    def test_an_owner_who_answers_none_closes_both_write_doors(self) -> None:
        done = self.setup_done(*answers(keeper="nobody"))
        steps = {step["step"]: step for step in done["todo"]}
        turns = Path(steps["bring_turns"]["file"])
        intake = shlex.split(steps["bring_turns"]["command"])[1:]

        def said(text: str) -> str:
            return json.dumps({"at": datetime.now(timezone.utc).isoformat(), "session": "s1", "role": "owner",
                               "text": text}) + "\n"

        def lane() -> list[str]:
            return [json.loads(row)["text"] for path in sorted((self.root / "06 istm" / "kiln-bot").glob("*.jsonl"))
                    for row in path.read_text().splitlines()]

        turns.write_text(said("The blue bowls go in the next glaze firing."))
        self.assertEqual(forest(*intake)[0], 0)
        self.assertEqual(len(lane()), 1)
        # The owner answers none. The todo says to turn the exporter off, and the doors refuse what still runs.
        changed = self.setup_done(*answers(keeper="nobody", conversations="none", earlier=None))
        self.assertIn("conversations was all, now none", " ".join(changed["changed"]))
        self.assertEqual([step["step"] for step in changed["todo"]], ["register_mcp", "no_recording", "verify"])
        with turns.open("a", encoding="utf-8") as handle:
            handle.write(said("The green bowls wait for Friday."))
        for argv in (intake, [*intake, "--dry-run"]):
            code, out, _err = forest(*argv)
            refused = json.loads(out)["error"]
            self.assertEqual((code, refused["code"], refused["next"]), (2, "recording_off", "report"), argv)
        code, body = as_json("log", "--as", "kiln-bot", "--root", "studio-forest", stdin=b"A turn of my own.")
        self.assertEqual((code, body["error"]["code"]), (2, "recording_off"))
        reply = mcp._call(self.root, "kiln-bot", {"name": "memory_log", "arguments": {"text": "A turn of my own."}},
                          "stdio")
        self.assertTrue(reply["isError"], reply)
        self.assertIn("recording_off", json.dumps(reply))
        self.assertEqual(len(lane()), 1)
        # Another agent of the forest is not touched by that answer.
        other = self.setup_done(*answers(**{"as_name": "Ledger Bot", "main": "no", "keeper": "nobody"}))
        code, _body = as_json("log", "--as", other["agent"]["id"], "--root", "studio-forest", stdin=b"Ledger checked.")
        self.assertEqual(code, 0)
        # The owner answers all again: the same command takes the turn that waited in the file.
        self.setup_done(*answers(keeper="nobody"))
        self.assertEqual(forest(*intake)[0], 0)
        self.assertEqual(len(lane()), 2)

    def test_an_agent_renamed_in_the_forest_is_the_same_agent_by_either_name(self) -> None:
        self.setup_done(*answers(keeper="nobody"))
        code, _out, err = forest("move", "rename", "kiln-bot", "--name", "Glaze Bot", "--root", "studio-forest")
        self.assertEqual(code, 0, err)
        config = load_config(self.root)
        for name in ("Glaze Bot", "glaze bot", "Kiln Bot", "kiln-bot"):
            self.assertEqual(doctor.resolve(self.root, config, name)[0], "kiln-bot", name)
        self.assertEqual(doctor.resolve(self.root, config, "Glaze Bot")[1], "Glaze Bot")
        # Setup under the new name finds the agent it already has: no second identity, nothing written.
        before = snapshot(self.root)
        again = self.setup_done(*answers(**{"as_name": "Glaze Bot", "keeper": "nobody"}))
        self.assertEqual((again["agent"], again["changed"]), ({"id": "kiln-bot", "name": "Glaze Bot"}, []))
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual([known.id for known in load_config(self.root).agents.known], ["kiln-bot"])
        code, out, err = forest("check", "--sync", "--root", "studio-forest")
        self.assertEqual(code, 0, out + err)

    def test_a_hangul_name_gets_its_id_by_the_name_contract(self) -> None:
        name = "스카우트 봇"
        expected = agents.new_id(name, agents.taken(self.root, DEFAULT_CONFIG))
        done = self.setup_done(*answers(**{"as_name": name, "keeper": "nobody"}))
        self.assertEqual(done["agent"], {"id": expected, "name": name})
        self.assertEqual(load_config(self.root).agents.declared(expected).name, name)
        self.assertRegex(expected, r"^[a-z0-9][a-z0-9-]*$")
        # The same agent by another spelling of its key, and the doctor finds it the same way.
        again = self.setup_done(*answers(**{"as_name": "스카우트봇", "keeper": "nobody"}))
        self.assertEqual((again["agent"]["id"], again["changed"]), (expected, []))
        resolved = doctor.resolve(self.root, load_config(self.root), "스카우트봇")
        self.assertEqual(resolved[0], expected)

    def test_a_jsonl_folder_in_the_settings_is_reported(self) -> None:
        self.setup_done(*answers())
        with (self.root / "forest.toml").open("a") as handle:
            handle.write('\n[intake.tools.jsonl]\npath = "exports"\n')
        code, body = as_json("setup", *answers(**{"as_name": "Ledger Bot", "keeper": "other"}))
        self.assertEqual((code, body["error"]["code"], body["error"]["next"]), (2, "setup_jsonl_path_set", "report"))

    def test_guide_and_setup_in_a_folder_that_is_no_forest_write_nothing(self) -> None:
        for argv in (("guide",), ("guide", "routine"), ("setup",), ("setup", "--as", "Kiln Bot")):
            with self.subTest(argv=argv):
                forest(*argv)
                self.assertEqual(snapshot(self.base), {})

    def test_a_folder_that_holds_something_else_is_refused(self) -> None:
        self.root.mkdir()
        (self.root / "notes.txt").write_text("glaze notes\n")
        code, body = as_json("setup", *answers())
        self.assertEqual((code, body["error"]["code"]), (2, "root_not_empty"))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["notes.txt"])


# ═══════════════════════════════ doctor ═══════════════════════════════


class DoctorTests(Scratch):
    def made(self, *extra: str) -> None:
        self.setup_done(*answers(), *extra)

    def check(self, name: str, given: str = "Kiln Bot", now: datetime = NOW) -> dict:
        report = doctor.run(self.root, given, shown_root="studio-forest", now=now)
        found = next(item for item in report["checks"] if item["name"] == name)
        if found["state"] in ("todo", "problem"):
            self.assertIn(found["next"], ("fix", "retry", "run", "report"))
            if found.get("command"):
                parses(found["command"])
        return found

    def test_a_structure_error_is_a_problem_of_the_forest(self) -> None:
        self.made()
        self.assertEqual(self.check("forest")["state"], "ok")
        (self.root / "02 ltm" / "bogus_LTM.md").write_text("no frontmatter\n")
        found = self.check("forest")
        self.assertEqual((found["state"], found["command"]), ("problem", "forest check --root studio-forest"))

    def test_setup_and_agent_are_todo_for_a_name_no_agent_has(self) -> None:
        self.made()
        self.assertEqual(self.check("setup")["state"], "ok")
        self.assertEqual(self.check("agent")["state"], "ok")
        for name in ("setup", "agent", "turns"):
            found = self.check(name, given="Glaze Bot")
            self.assertEqual(found["state"], "todo", found)
            self.assertIn("forest setup --as 'Glaze Bot'", found["command"])

    def test_turns_wait_for_the_phrase_to_come_the_agents_way(self) -> None:
        self.made()
        found = self.check("turns")
        self.assertEqual(found["state"], "todo")
        self.assertTrue(found["command"].endswith("--phrase"))
        phrase = doctor.new_phrase(self.root, "kiln-bot", now=NOW)
        self.assertTrue(re.fullmatch(r"forest doctor phrase [a-z]+ [a-z]+ [a-z]+", phrase))
        found = self.check("turns")
        self.assertEqual(found["state"], "todo")
        self.assertIn(phrase, found["evidence"])
        transcript = self.root / "transcripts" / "kiln-bot.jsonl"
        # A session is taken in once its first owner turn shows it is no memory work (contract, section 1).
        transcript.write_text(json.dumps({"at": NOW.isoformat(), "session": "s1", "role": "owner",
                                          "text": "Say the doctor's phrase."}) + "\n"
                              + json.dumps({"at": NOW.isoformat(), "session": "s1", "role": "agent",
                                            "text": f"Saying it: {phrase}"}) + "\n")
        intake = ["intake", "--from", "jsonl", "--as", "kiln-bot", "--transcript",
                  "studio-forest/transcripts/kiln-bot.jsonl", "--once", "--root", "studio-forest"]
        self.assertEqual(forest(*intake)[0], 0)
        found = self.check("turns")
        self.assertEqual(found["state"], "ok", found)
        self.assertIn(NOW.isoformat(), found["evidence"])
        self.assertIn("adapter:jsonl", found["evidence"])

    def test_turns_are_skipped_when_the_owner_brings_none_in(self) -> None:
        self.made("--conversations", "none")
        self.assertEqual(self.check("turns")["state"], "skipped")

    def test_finding_asks_for_the_index_then_for_a_connection(self) -> None:
        self.made()
        found = self.check("finding")
        self.assertEqual((found["state"], found["command"]), ("todo", "forest index build --root studio-forest"))
        self.assertEqual(forest("index", "build", "--root", "studio-forest")[0], 0)
        found = self.check("finding")
        self.assertEqual((found["state"], found["command"]), ("todo", "forest mcp --as kiln-bot --root studio-forest"))
        self.assertIn("never seen", found["evidence"])
        mcp._call(self.root, "kiln-bot", {"name": "memory_search", "arguments": {"words": "kiln"}}, "stdio")
        self.assertEqual(self.check("finding")["state"], "ok")

    def test_finding_follows_a_refusal_of_the_index(self) -> None:
        self.made()
        (self.root / ".forest" / "index.sqlite").write_bytes(b"not a database, only glaze notes\n" * 64)
        found = self.check("finding")
        self.assertIn(found["state"], ("todo", "problem"))
        self.assertIn("index_", found["evidence"])

    def test_routine_counts_days_of_turns_without_a_daily_page(self) -> None:
        self.made()
        lane = self.root / "06 istm" / "kiln-bot"
        lane.mkdir(parents=True)
        (lane / "2042-05-12.jsonl").write_text(json.dumps({"at": "2042-05-12T10:00:00+00:00", "text": "x"}) + "\n")
        found = self.check("routine")
        self.assertEqual((found["state"], found["command"]), ("todo", "forest guide routine"))
        self.assertIn("2042-05-12", found["evidence"])
        (self.root / "05 daily" / "2042-05-12.md").write_text("# 2042-05-12\n")
        self.assertEqual(self.check("routine")["state"], "ok")
        self.made("--keeper", "nobody")
        self.assertEqual(self.check("routine")["state"], "skipped")

    def test_a_tool_without_mcp_passes_by_the_commands_it_uses(self) -> None:
        self.made("--conversations", "none", "--keeper", "nobody")
        self.assertEqual(forest("index", "build", "--root", "studio-forest")[0], 0)
        self.assertEqual(forest("search", "blue bowls", "--as", "kiln-bot", "--root", "studio-forest")[0], 0)
        code, report = as_json("doctor", "--as", "kiln-bot", "--root", "studio-forest")
        finding = next(check for check in report["checks"] if check["name"] == "finding")
        self.assertEqual((code, report["result"], finding["state"]), (1, "todo", "todo"), report)
        self.assertIn("--doors commands", finding["evidence"])
        code, report = as_json("doctor", "--as", "kiln-bot", "--root", "studio-forest", "--doors", "commands")
        self.assertEqual((code, report["result"]), (0, "passed"), report)
        # With conversations to prove, the phrase's follow-up keeps the doors the caller named.
        self.made("--keeper", "nobody")
        code, report = as_json("doctor", "--as", "kiln-bot", "--root", "studio-forest", "--doors", "commands")
        turns = next(check for check in report["checks"] if check["name"] == "turns")
        self.assertEqual((turns["state"], turns["command"]),
                         ("todo", "forest doctor --as kiln-bot --root studio-forest --doors commands --phrase"))
        parses(turns["command"])

    def test_the_exit_is_zero_only_when_it_passed_and_the_phrase_file_is_all_it_writes(self) -> None:
        self.made()
        before = snapshot(self.root)
        code, report = as_json("doctor", "--as", "Kiln Bot", "--root", "studio-forest")
        self.assertEqual((code, report["result"]), (1, "todo"))
        self.assertEqual(snapshot(self.root), before)
        code, report = as_json("doctor", "--as", "Kiln Bot", "--root", "studio-forest", "--phrase")
        written = set(snapshot(self.root)) - set(before)
        self.assertEqual(written, {".forest/setup/kiln-bot.phrase"})
        self.assertIn(report["phrase"], (self.root / ".forest/setup/kiln-bot.phrase").read_text())
        home = str(Path.home())
        self.assertNotIn(home, json.dumps(report))
        self.assertNotIn(str(self.base), json.dumps(report))


# ═══════════════════════════════ the guide ═══════════════════════════════


def option_strings(parser) -> set[str]:
    """Every flag of the parser and its subcommands, written out in full."""
    import argparse

    found = set()
    for action in parser._actions:
        found.update(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                found |= option_strings(child)
    return found


class GuideTests(unittest.TestCase):
    def test_every_forest_line_of_the_guide_parses(self) -> None:
        parser = cli.build_forest_parser()
        flags = option_strings(parser)
        counted = 0
        for part in cli_setup.GUIDE_PARTS:
            text = cli_setup.guide_text(part)
            self.assertNotRegex(text, r"https?://", f"{part}: the guide sends no reader to the web")
            for block in re.findall(r"```sh\n(.*?)```", text, re.DOTALL):
                for line in block.splitlines():
                    line = line.strip()
                    if not line.startswith("forest "):
                        continue
                    with self.subTest(part=part, line=line):
                        words = shlex.split(line, comments=True)
                        # argparse takes a prefix of a flag; the guide writes every flag in full.
                        written = [word.split("=")[0] for word in words if word.startswith("--")]
                        self.assertEqual([word for word in written if word not in flags], [], line)
                        try:
                            parser.parse_args(words[1:])
                        except SystemExit as exc:  # --version and --help end here, with 0
                            self.assertEqual(exc.code, 0, f"{part}: `{line}` does not parse")
                        counted += 1
        self.assertGreater(counted, 20)

    def test_each_part_is_printed_and_ships_with_the_package(self) -> None:
        for part in cli_setup.GUIDE_PARTS:
            code, out, _err = forest("guide", part)
            self.assertEqual((code, out), (0, cli_setup.guide_text(part)))
        self.assertEqual(forest("guide")[1], cli_setup.guide_text("setup"))
        self.assertIn('"guide/*.md"', (REPOSITORY / "pyproject.toml").read_text())
        code, out, _err = forest("guide", "recipes", "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "arguments_invalid"))


class GuideExampleTests(Scratch):
    """The routine's worked example is carried out exactly as the guide writes it, and the judge passes it."""

    def test_the_first_filing_example_as_written_passes_the_judge(self) -> None:
        import textwrap

        text = cli_setup.guide_text("routine")
        start = text.index("### A new name in a forest that has no place for it yet")
        section = text[start:text.index("## 3. The upkeep run")]
        commands = [line.strip() for block in re.findall(r"```sh\n(.*?)```", section, re.DOTALL)
                    for line in block.splitlines() if line.strip().startswith("forest ")]
        pages = [textwrap.dedent(block) for block in re.findall(r"```markdown\n(.*?)```", section, re.DOTALL)]
        written = re.findall(r"Write `([^`]+)`:", section)
        index = re.search(r"under `## Subjects` in\s+`([^`]+)`:", section).group(1)
        self.assertEqual((len(commands), len(pages), len(written)), (4, 3, 2), (commands, written))
        self.setup_done(*answers(**{"as_name": "Studio Hub"}))

        def run(line: str) -> tuple[int, str, str]:
            return forest(*shlex.split(line)[1:])

        self.assertEqual(run(commands[0])[0], 0, commands[0])  # the area tree, by code
        board, registry_line, collection = pages
        for relative, body in ((written[0], board), (written[1], collection)):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        tree_index = self.root / index
        listed = tree_index.read_text(encoding="utf-8")
        self.assertEqual(listed.count("## Subjects\n"), 1)
        tree_index.write_text(listed.replace("## Subjects\n", "## Subjects\n\n" + registry_line, 1), encoding="utf-8")
        self.assertIn(written[1], registry_line)
        code, out, err = run(commands[1])  # check --sync writes the lists
        self.assertEqual((code, err), (0, ""), out)
        self.assertTrue(all("brought up to the pages" in line for line in out.splitlines()), out)
        self.assertEqual(run(commands[1]), (0, "", ""))
        code, out, err = run(commands[2])
        self.assertEqual(code, 0, out + err)
        self.assertIn("0 violations, 0 upkeep, 0 due", out)
        code, out, err = run(commands[3])
        self.assertEqual(code, 0, out + err)
        shown = json.loads(out)
        self.assertEqual((shown["id"], shown["name"], shown["collection"]), ("kiln-two", "Kiln Two", "device-seen-once"))


if __name__ == "__main__":
    unittest.main()
