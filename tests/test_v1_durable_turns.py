"""A fictional bakery's durable turn, private caller key and arrival provenance.

Generated products use the parser/tool inventory and the filesystem door's own
fault outcomes. No real transcript, account or conversation is read.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import bounds
from memory_forest import cli, intake, mcp, search, sources, system, turn_keys
from memory_forest.config import load_config
from memory_forest.errors import MemoryForestError
from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS
from test_v1_masking_table import ROWS
from test_v1_sweep import Answer, SWEEP_FILES
from v1_support import ForestTestCase

AT = datetime(2051, 3, 2, 12, tzinfo=timezone.utc)
TEXT = "the fictional kiln cooled"


def log(root, *, key=None, at=AT, door="mcp", group="", session="shift", text=TEXT):
    return intake.append_owned(root, "till-bot", text, role="agent", origin="interactive", session=session,
                               at=at, door=door, group=group, turn_id=key)


def records(root, agent="till-bot"):
    return [json.loads(line) for path in sorted((root / "06 istm" / agent).glob("*.jsonl"))
            for line in path.read_text().split("\n") if line.strip()]


def command(root, *words):
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO((TEXT + "\n").encode()))
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin):
        code = cli.forest_main([*words, "--root", str(root), "--json"])
    return Answer(out.getvalue() + err.getvalue(), code != 0, json.loads(out.getvalue()))


class KeyTests(ForestTestCase):
    def forest(self):
        return self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS})

    def test_the_reviews_sign_in_key_masking_and_repeat_sequence(self):
        root = self.forest()
        key = "password=fictional-sesame"
        first = log(root, key=key)
        self.assertNotIn(key.encode(), (root / first["path"]).read_bytes())
        intake.lane_masking(root, fix=True)
        again = log(root, key=key, at=AT + timedelta(days=2))
        self.assertEqual((again["stored"], again["id"]), ("already stored", first["id"]))
        self.assertEqual(sum(row["id"] == first["id"] for row in records(root)), 1)
        self.assertEqual(first["turn_digest"], turn_keys.digest(root, key))

    def test_the_reviews_two_days_later_same_and_other_words_sequence(self):
        root = self.forest()
        first = log(root, key="stable-key")
        for days in (2, 4000):
            self.assertEqual(log(root, key="stable-key", at=AT + timedelta(days=days))["id"], first["id"])
            with self.assertRaises(MemoryForestError) as caught:
                log(root, key="stable-key", at=AT + timedelta(days=days), text="other fictional words")
            self.assertEqual(caught.exception.code, "turn_id_reused")

    def test_different_input_words_are_refused_even_when_masking_makes_the_stored_words_equal(self):
        root = self.forest()
        first_text = 'password=fictional-first-secret'
        other_text = 'password=fictional-other-secret'
        first = log(root, key='masked-words-key', text=first_text)
        self.assertEqual(first['turn_words_digest'], turn_keys.digest(root, 'turn-words\0' + first_text.encode('utf-8', 'surrogatepass').hex()))
        with self.assertRaises(MemoryForestError) as caught:
            log(root, key='masked-words-key', text=other_text, at=AT + timedelta(days=4000))
        self.assertEqual((caught.exception.code, caught.exception.details['id']), ('turn_id_reused', first['id']))
        for text in (first_text, '\n ' + first_text + ' \n'):
            self.assertEqual(log(root, key='masked-words-key', text=text)['id'], first['id'])
        intake.lane_masking(root, fix=True)
        self.assertEqual(log(root, key='masked-words-key', text=first_text)['id'], first['id'])
        self.assertEqual(sum(row['id'] == first['id'] for row in records(root)), 1)
        for path in root.rglob('*'):
            if path.is_file():
                self.assertNotIn(first_text.encode(), path.read_bytes())
                self.assertNotIn(other_text.encode(), path.read_bytes())

    def test_keyed_input_identity_survives_a_change_in_the_masking_rules(self):
        root = self.forest()
        original = log(root, key='changing-mask', text='the fictional kiln cooled')
        real_record = intake._record
        def changed(*args, **kwargs):
            record = real_record(*args, **kwargs)
            record['text'] = 'a later masked representation'
            return record
        with mock.patch.object(intake, '_record', changed):
            again = log(root, key='changing-mask', text='the fictional kiln cooled')
        self.assertEqual((again['stored'], again['id']), ('already stored', original['id']))

    def test_keyed_words_keep_the_existing_unicode_repair_and_their_original_identity(self):
        root = self.forest()
        first = log(root, key='repaired-input', text='fictional\ud800')
        self.assertEqual((first['text'], first['repaired']), ('fictional?', True))
        self.assertEqual(log(root, key='repaired-input', text='fictional\ud800')['id'], first['id'])
        with self.assertRaises(MemoryForestError) as caught:
            log(root, key='repaired-input', text='fictional\ud801')
        self.assertEqual(caught.exception.code, 'turn_id_reused')
        log(root, key='unicode-pair', text='fictional\U0001f600')
        with self.assertRaises(MemoryForestError) as caught:
            log(root, key='unicode-pair', text='fictional\ud83d\ude00')
        self.assertEqual(caught.exception.code, 'turn_id_reused')

    def test_both_real_doors_refuse_different_original_words_after_equal_masking(self):
        for kind in ('cli', 'mcp'):
            root = self.make_forest(FILES, name=kind)
            def send(text):
                if kind == 'mcp':
                    reply = mcp._call(root, 'till-bot', {'name': 'memory_log', 'arguments': {
                        'text': text, 'turn_id': 'public-words-key', 'session': 'public'}}, 'stdio')
                    return reply['isError'], json.loads(reply['content'][0]['text'])
                out, err = io.StringIO(), io.StringIO()
                stdin = io.TextIOWrapper(io.BytesIO((text + '\n').encode()))
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, 'stdin', stdin):
                    code = cli.forest_main(['log', '--as', 'till-bot', '--turn-id', 'public-words-key',
                                            '--session', 'public', '--root', str(root), '--json'])
                body = json.loads(out.getvalue())
                return code != 0, body.get('error', body)
            failed, first = send('password=fictional-first-secret')
            self.assertFalse(failed, first)
            failed, other = send('password=fictional-other-secret')
            self.assertTrue(failed, other)
            self.assertEqual((other['code'], other['details']['id']), ('turn_id_reused', first['id']))
            failed, again = send('password=fictional-first-secret')
            self.assertFalse(failed, again)
            self.assertEqual(again['id'], first['id'])
            self.assertEqual(sum(row['id'] == first['id'] for row in records(root)), 1)

    def test_every_masking_value_uses_a_digest_and_secret_values_leave_no_raw_byte(self):
        root = self.forest()
        # The masking table's value cases, including values needing a label in ordinary prose.
        values = list(dict.fromkeys(secret for _text, secret, _note in ROWS if secret is not None))
        accepted = [value for value in values if len(value) <= intake.TURN_ID_MAX and not intake.names.forbidden(value)]
        for key in accepted:
            first = log(root, key=key)
            self.assertEqual(log(root, key=key, at=AT + timedelta(days=7))["id"], first["id"])
        for path in root.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                for key in accepted:
                    # Very short generic words can occur in TOML or content; the lane identity must never
                    # contain the raw value, and whole secret-bearing keys cannot occur anywhere.
                    if len(key) >= 8 and not key.isdigit():
                        self.assertNotIn(key.encode(), data, str(path.relative_to(root)))
        for row in records(root):
            self.assertNotIn("turn", row)

    def test_deleted_torn_stale_and_falsified_key_records_answer_from_the_lane(self):
        root = self.forest()
        first = log(root, key="stable-key")
        path = Path(turn_keys.path(root, "till-bot"))
        for damage in ("deleted", "torn", "stale", "forged"):
            if damage == "deleted":
                path.unlink()
            elif damage == "torn":
                path.write_text('{"keys":')
            elif damage == "stale":
                path.write_text(json.dumps({"lanes": {}, "keys": {}}))
            else:
                entry = json.loads(path.read_text())
                entry["keys"] = {}
                entry["seal"] = sources._seal(entry["lanes"], entry["keys"])
                path.write_text(json.dumps(entry))
            self.assertEqual(log(root, key="stable-key", at=AT + timedelta(days=9))["id"], first["id"])
            with self.assertRaises(MemoryForestError) as caught:
                log(root, key="stable-key", text="different")
            self.assertEqual(caught.exception.code, "turn_id_reused")

    def test_legacy_raw_turn_is_read_and_one_masking_command_migrates_it(self):
        root = self.forest()
        legacy = {"id": "legacy-turn", "at": AT.isoformat(), "session": "shift", "role": "agent",
                  "origin": "interactive", "source": {"door": "mcp", "adapter": "mcp", "place": AT.isoformat(), "transcript": "mcp"},
                  "sources": ["door:mcp"], "text": TEXT, "turn": "password=fictional-sesame"}
        path = root / "06 istm/till-bot/2051-03-02.jsonl"
        path.write_text(json.dumps(legacy) + "\n")  # legacy encoding need not be today's sorted encoding
        self.assertEqual(log(root, key=legacy["turn"])["id"], "legacy-turn")
        rows = intake.lane_masking(root, fix=True)
        self.assertEqual(rows[0]["written"], 1)
        stored = json.loads(path.read_text())
        self.assertNotIn("turn", stored)
        self.assertEqual(stored["turn_digest"], turn_keys.digest(root, legacy["turn"]))
        self.assertEqual(log(root, key=legacy["turn"], at=AT + timedelta(days=10))["id"], "legacy-turn")

    def test_an_answer_lost_to_a_crash_is_safely_asked_again_with_its_key(self):
        root = self.forest()
        child = r'''
import sys, threading
from datetime import datetime, timezone
from memory_forest import intake
def committed(step):
    if step == "lane-committed":
        print("committed", flush=True)
        threading.Event().wait(60)
intake._CHECKPOINT = committed
intake.append_owned(sys.argv[1], "till-bot", "the fictional kiln cooled", role="agent", origin="interactive",
                    session="shift", turn_id="crash-key", at=datetime(2051,3,2,12,tzinfo=timezone.utc))
'''
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        proc = subprocess.Popen([sys.executable, "-u", "-c", child, str(root)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 10)
            self.assertTrue(ready, "the writer never acknowledged its commit checkpoint")
            self.assertEqual(proc.stdout.readline(), "committed\n")
            proc.kill()
            proc.communicate(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        first = next(row for row in records(root) if row["text"] == TEXT)
        again = log(root, key="crash-key")
        self.assertEqual((again["stored"], again["id"]), ("already stored", first["id"]))
        self.assertEqual(sum(row["id"] == first["id"] for row in records(root)), 1)


class ArrivalTests(ForestTestCase):
    def test_a_keyed_arrival_keeps_new_provenance_even_when_its_logical_source_is_unchanged(self):
        root = self.make_forest(FILES)
        first = log(root, key='arrival-key', group='night-shift')
        later = AT + timedelta(days=2)
        second = log(root, key='arrival-key', group='night-shift', at=later)
        self.assertEqual((second['stored'], second['id']), ('already stored', first['id']))
        row = records(root)[-1]
        self.assertEqual(row['sources'], ['group:night-shift'])
        self.assertEqual([item['at'] for item in row['arrivals']], [AT.isoformat(), later.isoformat()])
        self.assertEqual(row['arrivals'][1]['source'], {'adapter': 'mcp', 'door': 'mcp', 'group': 'night-shift',
                                                      'place': later.isoformat(), 'transcript': 'mcp'})
        before = (root / first['path']).read_bytes()
        self.assertEqual(log(root, key='arrival-key', group='night-shift', at=later)['id'], first['id'])
        self.assertEqual((root / first['path']).read_bytes(), before)

    def test_the_reviews_two_real_doors_retain_each_arrivals_full_provenance(self):
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS})
        args = {"name": "memory_log", "arguments": {"agent": "Till Bot", "text": TEXT, "session": "cli"}}
        result = mcp._call(root, "", args, "stdio", group="night-shift")
        self.assertFalse(result["isError"])
        second = command(root, "log", "--as-group", "bakery-bots", "--agent", "Till Bot")
        self.assertFalse(second.refused, second.raw)
        self.assertEqual(second.body["stored"], "joined")
        row = next(row for row in records(root) if row["text"] == TEXT)
        self.assertEqual([arrival["source"]["door"] for arrival in row["arrivals"]], ["mcp", "cli"])
        self.assertEqual([arrival["source"]["group"] for arrival in row["arrivals"]], ["night-shift", "bakery-bots"])
        for arrival in row["arrivals"]:
            self.assertTrue(all(key in arrival["source"] for key in ("door", "adapter", "place", "group")))
            self.assertTrue(arrival["at"])
        search.build(root)
        for source in row["sources"]:
            found = search.query(root, "fictional kiln", reader="till-bot", source=source)
            section = next(s for p in found["results"] for s in p["sections"] if s["kind"] == "turn")
            self.assertEqual(section["arrivals"], row["arrivals"])

    def test_generated_midnight_window_has_both_edges_and_both_orders(self):
        for zone in ("UTC", "America/Halifax", "Asia/Seoul"):
            root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS.replace('timezone = "UTC"', f'timezone = "{zone}"')},
                                    name="zone-" + zone.replace("/", "-"))
            config = load_config(root)
            midnight = datetime(2051, 3, 3, tzinfo=config.zone)
            for sign in (-1, 1):
                for distance in (120, 121):
                    for reverse in (False, True):
                        first = midnight + timedelta(seconds=sign * 30)
                        second = first - timedelta(seconds=sign * distance)
                        if reverse:
                            first, second = second, first
                        session = f"window-{sign}-{distance}-{reverse}"
                        a = log(root, at=first, door="cli", session=session)
                        b = log(root, at=second, door="mcp", session=session)
                        self.assertEqual(b["stored"], "joined" if distance == 120 else "written", (zone, session))
                        kept = [r for r in records(root) if r.get("session") == session]
                        self.assertEqual(len(kept), 1 if distance == 120 else 2)
                        if distance == 120:
                            self.assertEqual([x["source"]["door"] for x in kept[0]["arrivals"]], ["cli", "mcp"])
                            self.assertEqual(b["id"], a["id"])

    def test_an_old_line_is_one_arrival_and_joins_a_new_one(self):
        root = self.make_forest(FILES)
        old = {"at": AT.isoformat(), "id": "old-turn", "session": "shift", "role": "agent", "text": TEXT,
               "source": {"door": "cli", "adapter": "cli", "place": "old-place"}}
        (root / "06 istm/till-bot/2051-03-02.jsonl").write_text(json.dumps(old) + "\n")
        new = log(root, at=AT + timedelta(seconds=30))
        self.assertEqual(new["stored"], "joined")
        kept = records(root)[0]
        self.assertEqual(kept["arrivals"][0]["source"], old["source"])
        self.assertEqual(len(kept["arrivals"]), 2)


class SourceTruthTests(ForestTestCase):
    def forest(self, name="forest"):
        root = self.make_forest(FILES, name=name)
        log(root, group="night-shift")
        return root

    def test_review_list_replacement_wrong_element_and_same_length_rewrite(self):
        for damage in ("replaced", "wrong-element", "same-length"):
            root = self.forest(name=damage)
            path = root / sources.RECORD
            cached = json.loads(path.read_text())
            if damage == "same-length":
                lane = root / "06 istm/till-bot/2051-03-02.jsonl"
                data = lane.read_bytes()
                changed = data.replace(b"night-shift", b"light-shift")
                self.assertEqual(len(data), len(changed))
                lane.write_bytes(changed)
                real = "group:light-shift"
            else:
                cached["agents"]["till-bot"]["sources"] = [7] if damage == "wrong-element" else ["group:phantom"]
                path.write_text(json.dumps(cached))
                real = "group:night-shift"
            listed = sources.listed(root, ["till-bot"])["till-bot"]
            self.assertIn(real, listed)
            self.assertNotIn("group:phantom", listed)
            scope = search.for_reader(load_config(root), search.registry_mod.build(root), "till-bot", root=root)
            self.assertEqual(search.source_for(root, scope, real), real)
            with self.assertRaises(MemoryForestError) as caught:
                search.source_for(root, scope, "group:phantom")
            self.assertNotIn("group:phantom", caught.exception.details["sources"])

    def test_even_a_well_formed_forged_receipt_cannot_decide_no(self):
        root = self.forest()
        path = root / sources.RECORD
        cached = json.loads(path.read_text())
        entry = cached["agents"]["till-bot"]
        entry["sources"] = ["group:phantom"]
        entry["seal"] = sources._seal(entry["lanes"], entry["sources"])
        path.write_text(json.dumps(cached))
        scope = search.for_reader(load_config(root), search.registry_mod.build(root), "till-bot", root=root)
        self.assertEqual(search.source_for(root, scope, "group:night-shift"), "group:night-shift")
        with self.assertRaises(MemoryForestError) as caught:
            search.source_for(root, scope, "group:missing")
        self.assertNotIn("group:phantom", caught.exception.details["sources"])

    def test_read_only_stale_record_is_scanned_once_and_then_no_lane_reads(self):
        root = self.forest()
        path = root / sources.RECORD
        cached = json.loads(path.read_text())
        cached["agents"]["till-bot"]["sources"] = [7]
        path.write_text(json.dumps(cached))
        source_folder = path.parent
        source_folder.chmod(0o500)
        path.chmod(0o400)
        self.addCleanup(source_folder.chmod, 0o700)
        self.addCleanup(path.chmod, 0o600)
        sources._SCANNED.clear()
        with system.watching() as first:
            a = sources.listed(root, ["till-bot"])
        with system.watching() as second:
            b = sources.listed(root, ["till-bot"], verify=True)
        self.assertEqual(a, b)
        self.assertEqual(a, {"till-bot": ["adapter:jsonl", "group:night-shift"]})
        self.assertTrue([p for n, p in first.calls if n == "read" and "06 istm" in p])
        self.assertEqual([p for n, p in second.calls if n == "read" and "06 istm" in p], [])


    def test_a_valid_positive_source_needs_no_lane_scan(self):
        root = self.make_forest(FILES)
        log(root, group='night-shift')
        scope = search.for_reader(load_config(root), search.registry_mod.build(root), 'till-bot', root=root)
        sources._SCANNED.clear()
        with system.watching() as trace:
            self.assertEqual(search.source_for(root, scope, 'group:night-shift'), 'group:night-shift')
        self.assertEqual([p for name, p in trace.calls if name == 'read' and '06 istm' in p], [])


if __name__ == "__main__":
    unittest.main()
