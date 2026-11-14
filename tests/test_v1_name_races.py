"""Fictional crate agents stay distinct while their trees are written by two processes."""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import subprocess
import sys
from pathlib import Path

from memory_forest import agents, groups, model, registry
from memory_forest.config import load_config

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS
from test_v1_turns import _line_within, _stop
from v1_support import ForestTestCase


# Follow the package actually imported by the test, including a mutation runner's temporary source copy.
SOURCE = str(Path(groups.__file__).resolve().parents[1])
CHILD = r'''
import json
import select
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from memory_forest import agents, groups, mcp, plan

root, name, mode, stop_after, marker = sys.argv[2:]

def say(value):
    print(value, flush=True)

def receive():
    ready, _, _ = select.select([sys.stdin], [], [], 20)
    if not ready or sys.stdin.readline().strip() != "go":
        raise AssertionError("parent did not release the fictional writer before the deadline")

if mode == "holder":
    original_write = plan.atomic_write
    original_commit = plan.Plan.commit
    stopped = False

    def write(path, data, **kwargs):
        global stopped
        original_write(path, data, **kwargs)
        relative = str(Path(path).relative_to(root))
        if relative == stop_after and not stopped:
            stopped = True
            say("tree-paused:" + relative)
            receive()

    def commit(self, **kwargs):
        result = original_commit(self, **kwargs)
        # plan.run still owns the outer writer lock here. All planned pages and the registry are complete.
        Path(marker).write_text("complete", encoding="utf-8")
        return result

    plan.atomic_write = write
    plan.Plan.commit = commit

if mode == "waiter":
    original_lock = groups.writer_lock
    original_admit = groups._admit
    original_taken = agents.taken
    in_admission = False

    @contextmanager
    def writer_lock(*args, **kwargs):
        say("allocation-waiting-for-writer")
        with original_lock(*args, **kwargs) as token:
            yield token

    def admit(*args, **kwargs):
        global in_admission
        in_admission = True
        try:
            return original_admit(*args, **kwargs)
        finally:
            in_admission = False

    def taken(*args, **kwargs):
        if in_admission and not Path(marker).is_file():
            say("allocation-read-an-incomplete-tree")
            raise AssertionError("allocation read namespace facts before the other writer completed")
        return original_taken(*args, **kwargs)

    groups.writer_lock = writer_lock
    groups._admit = admit
    agents.taken = taken

say("ready")
receive()
resolved = groups.resolve_caller(root, "night-shift", name)
body = mcp.record_turn(root, resolved.id, "a fictional crate was checked", origin="interactive",
                       session="fictional-race", group="night-shift", tree_wait=10,
                       at=datetime(2051, 3, 2, 12, tzinfo=timezone.utc))
say(json.dumps({"agent": resolved.id, "body": body}))
'''


class NameRaceTests(ForestTestCase):
    def _forest(self, name):
        return self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name=name)

    def _start(self, root, name, *, mode="free", stop_after="", marker=""):
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD, SOURCE, str(root), name, mode, stop_after, str(marker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(_stop, proc)
        self.assertEqual(_line_within(proc.stdout, 20), "ready")
        return proc

    def _go(self, proc):
        proc.stdin.write("go\n")
        proc.stdin.flush()

    def _answer(self, proc):
        answer = json.loads(_line_within(proc.stdout, 25))
        self.assertEqual(proc.wait(timeout=20), 0, proc.stderr.read())
        self.assertEqual((answer["body"]["stored"], answer["body"]["tree"]), ("written", "made"))
        return answer

    def _assert_forest(self, root, answers):
        self.assertEqual({answer["agent"] for answer in answers}, {"crate-bot", "crate-bot-2"})
        config = load_config(root)
        declared = {item.id: item.name for item in config.agents.known if item.id.startswith("crate-bot")}
        self.assertEqual(set(declared), {"crate-bot", "crate-bot-2"})
        self.assertEqual(set(declared.values()), {"Crate.Bot", "Crate Bot"})
        built = registry.build(root, config=config)
        held = agents.taken(root, config)
        for agent_id, display in declared.items():
            self.assertEqual(held[agent_id], ["agent"])
            self.assertIn(agent_id, built.agents)
            self.assertEqual(built.subject(agent_id)["name"], display)
            tree = root / "02 ltm" / f"{agent_id}_LTM.md"
            index = root / "03 mtm" / agent_id / f"{agent_id}_MTM.md"
            self.assertTrue(tree.is_file())
            self.assertTrue(index.is_file())
            self.assertEqual(model.parse_page(tree.read_text(encoding="utf-8")).title, display)
            turns = [json.loads(line) for path in (root / "06 istm" / agent_id).glob("*.jsonl")
                     for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["text"], "a fictional crate was checked")

    def test_admission_waits_at_each_partial_tree_boundary_before_allocating_the_numbered_id(self):
        boundaries = ("01 xltm/XLTM.md", "02 ltm/crate-bot_LTM.md", "03 mtm/crate-bot/crate-bot_MTM.md")
        for number, boundary in enumerate(boundaries):
            with self.subTest(boundary=boundary):
                root = self._forest(f"boundary-{number}")
                marker = self.base / f"tree-complete-{number}"
                holder = self._start(root, "Crate.Bot", mode="holder", stop_after=boundary, marker=marker)
                waiter = self._start(root, "Crate Bot", mode="waiter", marker=marker)
                try:
                    self._go(holder)
                    self.assertEqual(_line_within(holder.stdout, 20), "tree-paused:" + boundary)
                    self.assertFalse(marker.exists())
                    # Even the partial tree belongs to the agent record, never to a non-agent area tree.
                    self.assertEqual(agents.taken(root, load_config(root))["crate-bot"], ["agent"])
                    self._go(waiter)
                    self.assertEqual(_line_within(waiter.stdout, 20), "allocation-waiting-for-writer")
                    self.assertIsNone(load_config(root).agents.declared("crate-bot-2"))
                    self._go(holder)
                    answers = [self._answer(holder), self._answer(waiter)]
                    self.assertTrue(marker.is_file())
                    self.assertEqual([answer["agent"] for answer in answers], ["crate-bot", "crate-bot-2"])
                    self._assert_forest(root, answers)
                finally:
                    _stop(holder)
                    _stop(waiter)

    def test_twenty_free_running_rounds_keep_both_names_trees_turns_and_the_numbered_suffix(self):
        for number in range(20):
            with self.subTest(round=number):
                root = self._forest(f"round-{number}")
                procs = [self._start(root, name) for name in ("Crate.Bot", "Crate Bot")]
                try:
                    for proc in procs:
                        self._go(proc)
                    answers = [self._answer(proc) for proc in procs]
                    self._assert_forest(root, answers)
                finally:
                    for proc in procs:
                        _stop(proc)

