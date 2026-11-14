"""Discover writers by watching clean calls, then fail every door call around their lane commit.

The parser and tool table supply the agent calls. Owner intake/maintenance recipes
supply valid arguments to the same parser. The writer/rewrite sets are measured,
not a second list of which operations we think write. All data is fictional.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import bounds
import door_runtime
import test_v1_door as door
from memory_forest import cli, intake, mcp, search, sources, system, turn_keys
from memory_forest.errors import REFUSALS
from test_v1_durable_turns import TEXT, command, log, records
from test_v1_sweep import Answer, SWEEP_FILES


class CommitSweepTests(unittest.TestCase):
    bound_seconds = 900

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "forest"
        self.clean = self.base / "clean"
        for name, text in SWEEP_FILES.items():
            path = self.clean / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        search.build(self.clean)
        export = self.clean / "exports"
        export.mkdir()
        exported = {"id": "export-turn", "at": "2051-03-02T12:00:00+00:00", "session": "export-session",
                    "role": "owner", "origin": "interactive", "text": TEXT}
        (export / "synthetic.jsonl").write_text(json.dumps(exported) + "\n")
        with (self.clean / "forest.toml").open("a") as stream:
            stream.write('\n[intake.tools.jsonl]\nenabled = true\nagent = "till-bot"\nimport_earlier = true\n')
        self.made = []

    def restore(self, variant):
        if self.root.exists():
            shutil.rmtree(self.root)
        shutil.copytree(self.clean, self.root)
        with (self.root / "forest.toml").open("a") as stream:
            stream.write("path = " + json.dumps(str(self.root / "exports")) + "\n")
        sources._SCANNED.clear()
        if variant in ("join", "rename", "mask"):
            log(self.root, key="stable-key", at=datetime.now(timezone.utc), door="seed", session="cli")
        if variant == "mask":
            path = next((self.root / "06 istm/till-bot").glob("*.jsonl"))
            row = {"at": "2051-03-02T12:00:00+00:00", "id": "old-keyed", "session": "legacy",
                   "role": "agent", "origin": "interactive", "text": "password=fictional-sesame",
                   "turn": "old-caller", "source": {"door": "cli"}}
            with path.open("a") as stream:
                stream.write(intake._encode(row))

    def cases(self):
        # The caller inventory comes from the package, including a newly added tool/command.
        for words in door.agent_commands():
            yield ("command", words, "ordinary")
            if words == ("log",):
                yield ("command", words, "join")
        for name in door.agent_tools():
            yield ("tool", name, "ordinary")
        parser = cli.build_forest_parser()
        # These are argument recipes, not a writer inventory. Watching determines whether they write.
        for argv, variant in ((("intake", "--from", "jsonl"), "export"),
                              (("intake", "rename-agent", "till-bot", "renamed-bot"), "rename"),
                              (("credentials", "fix", "--all"), "mask"),
                              (("agents",), "sources")):
            parser.parse_args([*argv, "--root", str(self.root), "--json"])
            yield ("owner", argv, variant)

    def call(self, case):
        kind, name, variant = case
        if kind == "owner":
            return command(self.root, *name)
        if kind == "command":
            if name == ("log",):
                return command(self.root, "log", "--as", door.READER, "--turn-id", "stable-key")
            code, out, err = door.run_command(self.root, name)
            return Answer(out + err, code != 0, json.loads(out))
        args = dict(door.TOOL_ARGUMENTS[name])
        if name == "memory_log":
            args.update(text=TEXT, turn_id="stable-key", session="cli")
        reply = mcp._call(self.root, door.READER, {"name": name, "arguments": args}, "stdio")
        raw = reply["content"][0]["text"]
        return Answer(raw, reply["isError"], json.loads(raw))

    def trace(self, case, failure=None):
        points = []
        def canonical():
            found = {}
            for path in (self.root / '06 istm').rglob('*.jsonl'):
                data = path.read_bytes()
                complete = data[:data.rfind(b'\n') + 1]
                if complete:
                    found[str(path.relative_to(self.root))] = complete
            return found
        before = canonical()
        def observing(real):
            def call(path, *args, committed=None, **options):
                if '/06 istm/' not in str(path):
                    return real(path, *args, committed=committed, **options)
                def published():
                    points.append(len(watch.calls))
                    if committed is not None:
                        committed()
                return real(path, *args, committed=published, **options)
            return call
        with system.watching(fail_calls=failure) as watch, contextlib.ExitStack() as stack:
            for name in ('append', 'write_whole', 'rename_folder'):
                stack.enter_context(mock.patch.object(system, name, observing(getattr(system, name))))
            stack.enter_context(door_runtime.watching())
            answer = self.call(case)
        if canonical() != before:
            self.assertTrue(points, 'canonical bytes or their lane changed without a publication observation')
        return answer, watch, points

    def test_generated_writers_keep_the_answer_at_the_commit_and_repair_their_records(self):
        counts, problems = [], []
        for case in self.cases():
            self.restore(case[2])
            clean, traced, points = self.trace(case)
            self.assertFalse(clean.refused, (case, clean.raw))
            writes = [name for name, _ in traced.calls if name in {"append", "write_whole", "rename", "create_new"}]
            if not writes:
                continue
            counts.append((str(case), len(traced.calls), len(points)))
            sys.stderr.write(f"\nwriter {case}: {len(traced.calls)} calls, {len(points)} lane commits\n")
            # Cache-only writers have no lane point; every injected failure must leave the turns alone.
            for number, (name, path) in enumerate(traced.calls, start=1):
                for outcome in system.CAN[name]:
                    self.restore(case[2])
                    before = records(self.root)
                    answer, watched, landed = self.trace(case, {number: outcome})
                    where = (case, number, name, outcome)
                    if not watched.injected:
                        problems.append((where, "unreached"))
                        continue
                    agent = "renamed-bot" if (self.root / "06 istm/renamed-bot").is_dir() else "till-bot"
                    after = records(self.root, agent)
                    if landed:
                        if answer.refused:
                            problems.append((where, answer.raw[:300]))
                        # A write keeps every committed id, even if its answer's words must be withheld.
                        ids = [row["id"] for row in after]
                        self.assertEqual(len(ids), len(set(ids)), where)
                        self.assertTrue("stored" in answer.body or "lanes" in answer.body, (where, answer.raw))
                        def pending(value):
                            if isinstance(value, dict):
                                return ([value] if value.get('state') == 'pending' else []) + [
                                    row for child in value.values() for row in pending(child)]
                            if isinstance(value, list):
                                return [row for child in value for row in pending(child)]
                            return []
                        unfinished = pending(answer.body)
                        for row in unfinished:
                            self.assertIn(row.get('reason'), REFUSALS, (where, row))
                        if (number >= min(landed) and name == 'write_whole'
                                and any(part in path for part in ('/.forest/intake/', '/.forest/mcp/'))):
                            self.assertTrue(unfinished, (where, 'a failed derived write needs a pending reason', answer.raw))
                        acknowledged = set(answer.body.get('ids', []))
                        if answer.body.get('id'):
                            acknowledged.add(answer.body['id'])
                        for row in answer.body.get('lanes', []):
                            acknowledged.update(row.get('ids', []))
                        if isinstance(answer.body.get('stored'), list):
                            acknowledged.update(row['id'] for row in answer.body['stored'])
                        prior = {row['id']: row for row in before}
                        affected = set(ids) if case[2] == 'rename' else {
                            row['id'] for row in after if row != prior.get(row['id'])}
                        self.assertLessEqual(affected, acknowledged, (where, answer.raw))
                        self.assertLessEqual(acknowledged, set(ids), (where, answer.raw))
                        if case[2] not in {"mask", "rename", "export"}:
                            self.assertTrue(answer.body.get("id"), (where, answer.raw))
                            self.assertEqual(ids.count(answer.body['id']), 1, where)
                        # The next actual write repairs the lane's disposable records, without a repair command.
                        repaired = intake.append_owned(self.root, agent, "a fresh fictional turn", role="agent",
                                                       origin="interactive", session="repair")
                        self.assertTrue(all(item["state"] == "updated" for item in repaired["derived"].values()), where)
                        self.assertIn(agent, sources._read(self.root))
                        self.assertTrue(Path(turn_keys.path(self.root, agent)).is_file())
                    elif answer.refused:
                        self.assertEqual(after, before, where)
                    else:
                        # An optional missing cache may be repaired before the commit, or a zero-turn
                        # adapter run may answer with a failed transcript. No unacknowledged turn is allowed.
                        self.assertEqual(after, before, (where, answer.raw))
        sys.stderr.write("\nmeasured writers: " + json.dumps(counts) + "\n")
        self.assertEqual(problems[:30], [], f"{len(problems)} sweep failures")

    def test_named_obstructions_after_append_through_both_real_doors(self):
        for kind in ("ids-folder-is-file", "sources-record-is-folder"):
            for door_kind in ("command", "tool"):
                case = (door_kind, ("log",) if door_kind == "command" else "memory_log", "ordinary")
                self.restore("ordinary")
                if kind == "ids-folder-is-file":
                    path = self.root / ".forest/intake/ids/till-bot"
                    path.parent.mkdir(parents=True)
                    path.write_text("derived obstruction")
                else:
                    path = self.root / sources.RECORD
                    path.mkdir(parents=True)
                answer = self.call(case)
                self.assertFalse(answer.refused, (kind, door_kind, answer.raw))
                self.assertEqual(answer.body["stored"], "written")
                self.assertTrue(any(row["state"] == "pending" and row["reason"]
                                    for row in answer.body["derived"].values()))
                self.assertEqual(sum(r["id"] == answer.body["id"] for r in records(self.root)), 1)
                self.assertFalse(path.exists(), "a wrong-kind disposable record is cleared for the next write")
                again = self.call(case)
                self.assertEqual((again.body["stored"], again.body["id"]), ("already stored", answer.body["id"]))
                self.assertTrue(all(row["state"] == "updated" for row in again.body["derived"].values()))

    def test_every_measured_lane_rewrite_preserves_a_callers_key(self):
        rewrites = []
        for case in self.cases():
            self.restore(case[2])
            first = log(self.root, key="lasting-key", session="lasting-session")
            answer, trace, _ = self.trace(case)
            self.assertFalse(answer.refused, (case, answer.raw))
            if not any(name in {"write_whole", "rename"} and "/06 istm/" in path
                       for name, path in trace.calls):
                continue
            rewrites.append(case)
            agent = "renamed-bot" if (self.root / "06 istm/renamed-bot").is_dir() else "till-bot"
            again = intake.append_owned(self.root, agent, TEXT, turn_id="lasting-key", session="lasting-session",
                                        role="agent", origin="interactive", at=datetime(2062, 3, 2, tzinfo=timezone.utc))
            self.assertEqual((again["stored"], again["id"]), ("already stored", first["id"]), case)
        self.assertTrue(rewrites)
        sys.stderr.write("\nmeasured lane rewrites: " + repr(rewrites) + "\n")


if __name__ == "__main__":
    unittest.main()
