"""The fictional adapter and lane maintenance acknowledge their actual commit."""
import json
import contextlib
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

import bounds
from memory_forest import intake, mcp, sources, system
from memory_forest.adapters.common import Part, ReadBatch, Transcript, Turn
from memory_forest.config import load_config
from memory_forest.errors import MemoryForestError
from test_v1_durable_turns import AT, TEXT, log, records
from test_v1_find import FILES
from test_v1_intake import IntakeTestCase, record


class ReceiptTests(IntakeTestCase):
    def fake_batch(self, name, *, carry=None, reread=True, faults=(), internal=False, offset=None):
        from types import SimpleNamespace
        root = self.prepared(name)
        path = self.base / (name + '-exports') / 'kiln.jsonl'
        path.write_bytes(b'one\nnext\n')
        transcript = Transcript('kiln.jsonl', path, 'shift', '/studio/kiln', 'interactive', described=True)
        config = load_config(root)
        settings = replace(config.intake.tool('jsonl'), import_earlier=False)
        cursor = {'agent': 'glaze-bot', 'owner': 'ordinary', 'carry': carry, 'turn_faults': 7,
                  'excluded': True, 'seeded': True}
        if reread:
            cursor['reread'] = {'faults': 0}
        intake._store_cursor(root, 'jsonl', transcript, cursor, offset=0, internal=False)
        turn = Turn('first', AT, 'shift', 'owner', TEXT, 'line 1', 'interactive')
        batch = ReadBatch((turn, turn, replace(turn, role='invalid'), replace(turn, at='invalid')),
                          path.stat().st_size if offset is None else offset, 2, internal=internal,
                          faults=faults, carry={'remaining': 'fictional'})
        adapter = SimpleNamespace(name='jsonl', read=mock.Mock(return_value=batch))
        return root, config, settings, transcript, adapter

    def test_each_batch_drops_bad_or_duplicate_turns_and_preserves_carry_faults_and_the_reread_floor(self):
        for bounded in (False, True):
            for carry in ({'pending': 'fictional'}, 'invalid'):
                name = 'batch-' + str(bounded) + '-' + str(isinstance(carry, dict))
                root, config, settings, transcript, adapter = self.fake_batch(
                    name, carry=carry, faults=(('fictional_fault', 'line 2'),))
                receipts = []
                self.assertEqual(intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                                              dry_run=False, bounded=bounded, id_cache={}, receipts=receipts), (1, 2, ''))
                self.assertEqual(adapter.read.call_args.kwargs,
                                 {'carry': carry if isinstance(carry, dict) else None,
                                  'max_bytes': intake.HOOK_READ_BYTES if bounded else None})
                self.assertEqual(len(self.rows(root)), 1)
                self.assertEqual(receipts[0]['id'], 'shift:first')
                cursor = intake._load_cursor(root, 'jsonl', 'kiln.jsonl')
                self.assertEqual(cursor['turn_faults'], 8)
                self.assertEqual(cursor['carry'], {'remaining': 'fictional'})
                self.assertFalse(cursor['excluded'])
                self.assertEqual(cursor['floor'], 0)
                self.assertNotIn('reread', cursor)
                failures = [json.loads(line)['class'] for line in (root / intake.FAILURES).read_text().splitlines()]
                self.assertEqual(failures, ['fictional_fault', 'AttributeError'])
                # A plugin replaying the same ids cannot append them twice, even past offset zero.
                again = []
                self.assertEqual(intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                                              dry_run=False, bounded=False, id_cache={}, receipts=again)[0], 0)
                self.assertEqual(again, [])
                self.assertEqual(len(self.rows(root)), 1)

    def test_a_complete_faultless_reread_clears_faults_but_a_partial_one_keeps_its_marker(self):
        for complete in (False, True):
            root, config, settings, transcript, adapter = self.fake_batch('reread-' + str(complete), offset=9 if complete else 4)
            good = adapter.read.return_value.turns[:1]
            adapter.read.return_value = replace(adapter.read.return_value, turns=good)
            intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                        dry_run=False, bounded=False, id_cache={}, receipts=[])
            cursor = intake._load_cursor(root, 'jsonl', 'kiln.jsonl')
            self.assertEqual(cursor['turn_faults'], 0 if complete else 7)
            self.assertEqual('reread' in cursor, not complete)

    def test_internal_batch_and_session_decisions_are_kept_without_storing_a_turn(self):
        for dry in (False, True):
            for session in (False, True):
                root, config, settings, transcript, adapter = self.fake_batch(
                    'internal-' + str(dry) + '-' + str(session), internal=True)
                if session:
                    transcript = replace(transcript, internal=True)
                before = intake.cursor_file(root, 'jsonl', 'kiln.jsonl').read_bytes()
                self.assertEqual(intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                                              dry_run=dry, bounded=False, id_cache={}, receipts=[]),
                                 (0, 0 if session else 2, 'session carries MEMORY_FOREST_INTERNAL'))
                self.assertEqual(adapter.read.call_count, 0 if session else 1)
                self.assertEqual(self.rows(root), [])
                if dry:
                    self.assertEqual(intake.cursor_file(root, 'jsonl', 'kiln.jsonl').read_bytes(), before)
                else:
                    self.assertEqual(intake._load_cursor(root, 'jsonl', 'kiln.jsonl')['owner'], 'internal')

    def test_a_transcript_that_disappears_is_refused_before_a_turn_is_stored(self):
        root, config, settings, transcript, adapter = self.fake_batch('disappeared')
        transcript.path.unlink()
        with self.assertRaises(MemoryForestError) as caught:
            intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                        dry_run=False, bounded=False, id_cache={}, receipts=[])
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('transcript_missing', {'adapter': 'jsonl', 'path': 'kiln.jsonl'}))
        self.assertEqual(self.rows(root), [])

    def test_a_stored_session_decision_does_not_consult_stage_state_or_reclassify_later_content(self):
        for decision in ('ordinary', 'internal'):
            root, config, settings, transcript, adapter = self.fake_batch('decided-' + decision)
            cursor = intake._load_cursor(root, 'jsonl', 'kiln.jsonl')
            cursor.update(owner=decision, seeded=False)
            intake._save_cursor(root, 'jsonl', 'kiln.jsonl', cursor)
            turn = replace(adapter.read.return_value.turns[0], text='MEMORY_FOREST_INTERNAL\nfictional later words')
            adapter.read.return_value = replace(adapter.read.return_value, turns=(turn,))
            with mock.patch.object(intake, '_stage_session', side_effect=AssertionError('the decision was already made')):
                result = intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                                     dry_run=False, bounded=False, id_cache={}, receipts=[])
            self.assertEqual(result[0], 1 if decision == 'ordinary' else 0)

    def test_lifting_an_exclusion_never_rewinds_the_bytes_it_already_covered(self):
        root, config, settings, transcript, adapter = self.fake_batch('excluded-floor', carry={'pending': 'fictional'})
        cursor = intake._load_cursor(root, 'jsonl', 'kiln.jsonl')
        intake._store_cursor(root, 'jsonl', transcript, cursor, offset=4, internal=False)
        settings = replace(settings, import_earlier=True)
        adapter.read.return_value = replace(adapter.read.return_value, turns=adapter.read.return_value.turns[:1])
        intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                    dry_run=False, bounded=False, id_cache={}, receipts=[])
        self.assertEqual(adapter.read.call_args.args[1], 4)
        self.assertEqual(adapter.read.call_args.kwargs['carry'], {'pending': 'fictional'})
        self.assertEqual(intake._load_cursor(root, 'jsonl', 'kiln.jsonl')['floor'], 4)

    def test_adapter_records_preserve_provenance_role_parts_and_repair_information(self):
        root = self.forest()
        config = load_config(root)
        transcript = Transcript('kiln.jsonl', Path('kiln.jsonl'), 'shift', '/studio/kiln', 'headless',
                                origin_raw='batch', parent='parent-shift')
        turn = Turn('turn-1', AT.replace(tzinfo=None), 'shift', 'owner', TEXT, 'line 1', 'interactive',
                    parts=(Part(0, 3, 'quote'),))
        source = {'adapter': 'jsonl', 'place': 'line 1', 'transcript': 'kiln.jsonl',
                  'origin_raw': 'batch', 'parent': 'parent-shift'}
        wanted = {'at': AT.isoformat(), 'id': 'shift:turn-1', 'origin': 'interactive', 'role': 'owner',
                  'session': 'shift', 'text': TEXT, 'source': source,
                  'parts': [{'start': 0, 'end': 3, 'kind': 'quote'}],
                  'arrivals': [{'at': AT.isoformat(), 'origin': 'interactive', 'source': source,
                                'sources': ['adapter:jsonl']}]}
        self.assertEqual(intake._record(config, 'glaze-bot', 'jsonl', transcript, turn), wanted)
        self.assertEqual(intake._record(config, 'glaze-bot', 'jsonl', transcript,
                                        replace(turn, origin='missing'))['origin'], 'headless')
        self.assertEqual(intake._record(config, 'glaze-bot', 'jsonl', replace(transcript, origin='missing'),
                                        replace(turn, origin='missing'))['origin'], 'unknown')
        self.assertIsNone(intake._record(config, 'glaze-bot', 'jsonl', transcript, replace(turn, role='invalid')))
        self.assertEqual(intake._record(config, 'glaze-bot', 'jsonl', transcript,
                                        replace(turn, role='other:Guest'))['role'], 'other:Guest')
        repaired = intake._record(config, 'glaze-bot', 'jsonl', transcript, replace(turn, text='kiln\ud800', parts=()))
        self.assertEqual((repaired['text'], repaired['repaired']), ('kiln?', True))
        kept = replace(config, intake=replace(config.intake, tool_output='keep'))
        tool = replace(turn, role='tool', name='read_file', arguments='{}', parts=())
        for settings, item, text, name, argument in ((config, tool, 'ran read_file', 'read_file', None),
                                                    (kept, tool, TEXT, 'read_file', '{}'),
                                                    (kept, replace(tool, name=None), TEXT, 'tool', '{}'),
                                                    (kept, replace(tool, text=''), 'ran read_file', 'read_file', None)):
            row = intake._record(settings, 'glaze-bot', 'jsonl', transcript, item)
            self.assertEqual((row['text'], row['name'], row.get('arguments')), (text, name, argument))
        self.assertNotIn('arguments', intake._record(kept, 'glaze-bot', 'jsonl', transcript,
                                                    replace(turn, arguments='{}')))

    def prepared(self, name='oven'):
        folder = self.base / (name + '-exports')
        folder.mkdir()
        (folder / 'kiln.jsonl').write_text(record(id='turn-1', text=TEXT))
        return self.forest(self.tool(folder), name=name)

    def test_run_argument_refusals_keep_their_exact_code_and_details(self):
        root = self.forest()
        path = self.base / 'single.jsonl'
        path.write_text(record(id='single', text=TEXT))
        for arguments, code, details in (({'adapter_name': 'jsonl', 'transcript': str(path), 'hook': {}}, 'intake_arguments', {}),
                                         ({'adapter_name': 'bad/id'}, 'intake_adapter_unknown', {'adapter': 'bad/id'}),
                                         ({'adapter_name': 'jsonl', 'once': True, 'agent': 'glaze-bot'},
                                          'intake_path_unset', {'adapter': 'jsonl'}),
                                         ({'adapter_name': 'jsonl', 'once': True, 'transcript': str(path)},
                                          'intake_agent_unset', {'adapter': 'jsonl'}),
                                         ({'adapter_name': 'jsonl', 'once': True, 'transcript': str(path), 'agent': 'bad/id'},
                                          'intake_agent_invalid', {'agent': 'bad/id'})):
            with self.assertRaises(MemoryForestError) as caught:
                intake.run(root, **arguments)
            self.assertEqual((caught.exception.code, caught.exception.details), (code, details))
        disabled = intake.run(root, 'jsonl')
        message = 'intake for jsonl is off; set [intake.tools.jsonl] enabled = true, or pass --once'
        self.assertEqual(disabled.data, {'adapter': 'jsonl', 'enabled': False, 'message': message, 'ok': True, 'wrote': 0})
        self.assertEqual(intake.run(root, 'jsonl', once=True, transcript=str(path), agent='glaze-bot').data['wrote'], 1)

    def test_a_hook_before_turn_on_is_a_complete_result_with_no_path_requirement(self):
        for dry in (False, True):
            root = self.forest(name='before-' + str(dry))
            result = intake.run(root, 'jsonl', once=True, hook={}, agent='glaze-bot', dry_run=dry)
            message = 'jsonl: intake is not on yet; run `forest intake --from jsonl` to turn it on'
            self.assertEqual(result.data, {'adapter': 'jsonl', 'agent': 'glaze-bot', 'dry_run': dry,
                                           'message': message, 'ok': True, 'on': False, 'wrote': 0})
            self.assertEqual(intake._counted(root, 'jsonl', 'before-on'), 0 if dry else 1)

    def test_only_jsonl_requires_a_configured_path_and_deferred_hooks_keep_their_reason(self):
        from types import SimpleNamespace
        root = self.forest()
        adapter = SimpleNamespace(name='fictional', discover=lambda settings: [])
        with mock.patch.object(intake.adapter_loader, 'get_adapter', return_value=adapter):
            self.assertEqual(intake.run(root, 'fictional', once=True, agent='glaze-bot').data['wrote'], 0)
        ready = self.prepared('ready')
        self.turn_on(ready)
        real = intake.adapter_loader.get_adapter('jsonl')
        for dry in (False, True):
            with mock.patch.object(real, 'hook', side_effect=MemoryForestError('transcript_deferred', 'fictional delay')):
                body = intake.run(ready, 'jsonl', once=True, hook={}, agent='glaze-bot', dry_run=dry).data
            self.assertEqual(body, {'adapter': 'jsonl', 'agent': 'glaze-bot', 'dry_run': dry, 'ok': True,
                                    'placed': True, 'transcripts': ['jsonl: waiting for a sweep'], 'unknown': 0, 'wrote': 0})
        self.assertEqual(intake._counted(ready, 'jsonl', 'deferred'), 1)
        with mock.patch.object(real, 'hook', side_effect=MemoryForestError('hook_incomplete', 'fictional bad payload')):
            with self.assertRaises(MemoryForestError) as caught:
                intake.run(ready, 'jsonl', once=True, hook={}, agent='glaze-bot')
        self.assertEqual(caught.exception.code, 'hook_incomplete')

    def test_a_busy_run_has_no_stored_turn_and_only_a_hook_leaves_a_marker(self):
        @contextlib.contextmanager
        def busy(root):
            yield None
        for hook in (False, True):
            root = self.prepared('busy-' + str(hook))
            self.turn_on(root)
            payload = {'transcript_path': str(self.base / ('busy-' + str(hook) + '-exports') / 'kiln.jsonl')}
            with mock.patch.object(intake, '_intake_lock', busy):
                result = intake.run(root, 'jsonl', once=True, hook=payload if hook else None)
            self.assertEqual(result.data, {'adapter': 'jsonl', 'lock': 'busy',
                                           'message': 'another intake holds the lock; this one left its cursor',
                                           'ok': True, 'wrote': 0})
            self.assertEqual(self.rows(root), [])
            self.assertEqual(intake._busy_count(root, 'jsonl'), 1 if hook else 0)

    def test_cursor_inventory_ignores_other_files_and_malformed_or_nonobject_records(self):
        root = self.forest()
        self.assertEqual(intake._tool_names(root), [])
        folder = root / '.forest/intake/tools/jsonl/cursors'
        folder.mkdir(parents=True)
        wanted = {'agent': 'glaze-bot', 'source': 'kiln.jsonl'}
        (folder / 'a.json').write_text(json.dumps(wanted))
        for name, value in (('b.json', '{torn'), ('c.json', '[]'), ('_d.json', json.dumps(wanted)),
                            ('e.txt', json.dumps(wanted))):
            (folder / name).write_text(value)
        (folder / 'directory.json').mkdir()
        (folder.parent.parent / 'a-file').write_text('fictional note')
        self.assertEqual(intake._tool_names(root), ['jsonl'])
        self.assertEqual(intake._each_cursor(root, 'jsonl'), [(folder / 'a.json', wanted)])

    def test_only_committed_valid_rename_intents_can_relabel_a_cursor(self):
        root = self.prepared()
        intake.append_owned(root, 'glaze-bot', TEXT, role='agent', origin='interactive', session='shift', at=AT)
        intake._save_cursor(root, 'jsonl', 'kiln.jsonl', {'agent': 'glaze-bot'})
        folder = root / '.forest/intake/renames'
        folder.mkdir()
        (folder / 'a.json').mkdir()
        (folder / 'b.txt').write_text(json.dumps({'old': 'past-bot', 'new': 'glaze-bot'}))
        (folder / 'c.json').write_text(json.dumps({'old': 'bad/name', 'new': 'glaze-bot'}))
        (root / '06 istm/bad name').mkdir()
        (folder / 'd.json').write_text(json.dumps({'old': 'past-bot', 'new': 'bad name'}))
        (folder / 'e.json').write_text(json.dumps({'old': 'past-bot', 'new': 'missing-bot'}))
        # A preparation left before the rename is not a committed rename, even if another lane appears later.
        (root / '06 istm/new-bot').mkdir()
        (folder / 'new-bot.json').write_text(json.dumps({'old': 'glaze-bot', 'new': 'new-bot'}))
        self.assertEqual(intake._finish_renames(root), {})
        self.assertEqual(intake._load_cursor(root, 'jsonl', 'kiln.jsonl')['agent'], 'glaze-bot')

    def test_the_adapter_receipt_contains_the_id_path_and_every_derived_state(self):
        root = self.prepared()
        code, out, err = self.invoke(root, '--from', 'jsonl', '--once', '--json')
        self.assertEqual((code, err), (0, ''))
        expected_derived = {name: {'state': 'updated'} for name in ('ids', 'turns', 'sources')}
        self.assertEqual(json.loads(out), {'adapter': 'jsonl', 'agent': 'glaze-bot', 'dry_run': False,
                                          'ok': True, 'placed': True, 'transcripts': ['kiln.jsonl: 1 turn'],
                                          'files': [{'note': '', 'read_to_end': True, 'transcript': 'kiln.jsonl',
                                                     'turns': 1, 'unknown': 0}],
                                          'unknown': 0, 'wrote': 1, 'ids': ['ses-kiln-1:turn-1'],
                                          'stored': [{'id': 'ses-kiln-1:turn-1', 'stored': 'written',
                                                      'path': '06 istm/glaze-bot/2051-03-02.jsonl',
                                                      'derived': expected_derived}], 'derived': expected_derived})
        self.assertFalse((root / intake.FAILURES).exists())

    def test_a_postcommit_id_failure_is_pending_until_the_next_run_repairs_it(self):
        root = self.prepared()
        committed = []
        original = intake._write_ids
        def ids(*args, **options):
            if committed:
                raise system.Denied(root, access='write')
            return original(*args, **options)
        def checkpoint(step):
            if step == 'lane-committed':
                committed.append(True)
        with mock.patch.object(intake, '_write_ids', ids), mock.patch.object(intake, '_CHECKPOINT', checkpoint):
            code, out, err = self.invoke(root, '--from', 'jsonl', '--once', '--json')
        self.assertEqual((code, err), (0, ''))
        body = json.loads(out)
        self.assertEqual(body['wrote'], 1)
        self.assertEqual(body['ids'], ['ses-kiln-1:turn-1'])
        self.assertEqual(body['derived']['ids'], {'state': 'pending', 'reason': 'access_denied'})
        failure = json.loads((root / intake.FAILURES).read_text().splitlines()[0])
        self.assertEqual(failure, {'class': 'derived_pending', 'place': 'jsonl', 'wrote': 1})
        code, out, err = self.invoke(root, '--from', 'jsonl', '--once', '--json')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['wrote'], 0)
        self.assertTrue(all(item['state'] == 'updated' for item in json.loads(out)['derived'].values()))
        self.assertEqual(len(self.rows(root)), 1)

    def test_cursor_bookkeeping_failure_keeps_each_committed_receipt_and_replays_without_duplicates(self):
        root = self.prepared()
        def checkpoint(step):
            if step == 'cursor':
                raise system.Denied(root, access='write')
        with mock.patch.object(intake, '_CHECKPOINT', checkpoint):
            code, out, err = self.invoke(root, '--from', 'jsonl', '--once', '--json')
        self.assertEqual((code, err), (0, ''))
        body = json.loads(out)
        self.assertEqual(body['wrote'], 1)
        self.assertEqual(body['stored'][0]['id'], 'ses-kiln-1:turn-1')
        self.assertEqual(body['stored'][0]['derived']['bookkeeping'], {'state': 'pending', 'reason': 'access_denied'})
        self.assertEqual(self.invoke(root, '--from', 'jsonl', '--once')[0], 0)
        self.assertEqual(len(self.rows(root)), 1)

    def test_a_failure_before_the_adapter_append_refuses_and_keeps_the_lane_empty(self):
        root = self.prepared()
        def checkpoint(step):
            if step == 'append':
                raise system.Denied(root, access='write')
        with mock.patch.object(intake, '_CHECKPOINT', checkpoint):
            code, out, err = self.invoke(root, '--from', 'jsonl', '--once', '--json')
        self.assertEqual((code, err), (2, ''))
        self.assertEqual(json.loads(out)['error']['code'], 'access_denied')
        self.assertNotIn('stored', json.loads(out))
        self.assertEqual(self.rows(root), [])
        self.assertEqual(self.invoke(root, '--from', 'jsonl', '--once')[0], 0)
        self.assertEqual(len(self.rows(root)), 1)

    def test_a_lock_release_failure_after_the_adapter_commit_cannot_reverse_its_answer(self):
        root = self.prepared()
        original = system.LockFile.release
        def release(handle):
            original(handle)
            if handle.path.name == 'intake.lock':
                raise system.Denied(handle.path, access='write')
        with mock.patch.object(system.LockFile, 'release', release):
            code, out, err = self.invoke(root, '--from', 'jsonl', '--once', '--json')
        self.assertEqual((code, err), (0, ''))
        body = json.loads(out)
        self.assertEqual(body['wrote'], 1)
        self.assertEqual(body['stored'][0]['id'], 'ses-kiln-1:turn-1')
        self.assertEqual(body['derived']['lock'], {'state': 'pending', 'reason': 'access_denied'})
        self.assertEqual(len(self.rows(root)), 1)
        self.assertEqual(self.invoke(root, '--from', 'jsonl', '--once')[0], 0)

    def test_a_pending_lane_rename_is_visible_and_repaired_by_the_next_actual_write(self):
        root = self.prepared()
        first = intake.append_owned(root, 'glaze-bot', TEXT, role='agent', origin='interactive', session='shift',
                                    at=AT, turn_id='rename-key')
        intake._save_cursor(root, 'jsonl', 'kiln.jsonl', {'agent': 'glaze-bot'})
        with mock.patch.object(intake, '_save_cursor', side_effect=system.Denied(root, access='write')):
            renamed = intake.rename_agent(root, 'glaze-bot', 'new-bot').data
            self.assertEqual(renamed['stored'], 'renamed')
            self.assertEqual(renamed['derived']['rename'], {'state': 'pending', 'reason': 'access_denied'})
            reply = mcp._call(root, 'new-bot', {'name': 'memory_log', 'arguments': {'text': 'a second fictional turn'}}, 'stdio')
        self.assertFalse(reply['isError'])
        self.assertEqual(json.loads(reply['content'][0]['text'])['derived']['renames'],
                         {'state': 'pending', 'reason': 'access_denied'})
        repaired = intake.append_owned(root, 'new-bot', TEXT, role='agent', origin='interactive', session='shift',
                                       at=AT, turn_id='rename-key')
        self.assertEqual(repaired['id'], first['id'])
        self.assertEqual(repaired['derived']['renames'], {'state': 'updated'})
        self.assertEqual(intake._load_cursor(root, 'jsonl', 'kiln.jsonl')['agent'], 'new-bot')
        self.assertEqual(list((root / '.forest/intake/renames').iterdir()), [])

    def test_all_wrong_kind_disposable_records_are_cleared_for_the_next_write(self):
        root = self.prepared()
        paths = {'ids': root / '.forest/intake/ids/glaze-bot',
                 'turns': root / '.forest/intake/turns/glaze-bot.json', 'sources': root / sources.RECORD}
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == 'ids':
                path.write_text('fictional disposable obstruction')
            else:
                path.mkdir()
            intake._clear_derived_obstruction(root, 'glaze-bot', name)
            self.assertFalse(path.exists(), name)
        # Ordinary files at the file names and ordinary ids folders are left alone.
        for name, path in paths.items():
            if name == 'ids':
                path.mkdir()
            else:
                path.write_text('{}')
            intake._clear_derived_obstruction(root, 'glaze-bot', name)
            self.assertTrue(path.exists(), name)

    def test_a_join_keeps_its_original_id_and_pending_reason_after_publication(self):
        root = self.make_forest(FILES)
        first = log(root, door='mcp')
        original = system.sync_folder
        def sync(path):
            if path == root / '06 istm/till-bot':
                raise system.Denied(path, access='write')
            original(path)
        with mock.patch.object(system, 'sync_folder', sync):
            joined = log(root, door='cli')
        self.assertEqual((joined['stored'], joined['id']), ('joined', first['id']))
        self.assertEqual(joined['derived']['bookkeeping'], {'state': 'pending', 'reason': 'access_denied'})
        self.assertEqual(sum(row['id'] == first['id'] for row in records(root)), 1)

    def execute(self, root, settings, *, dry=False, sweep=True, discovered=()):
        return intake._execute(root, load_config(root), SimpleNamespace(name='jsonl'), settings,
                               'glaze-bot', list(discovered), sweep=sweep, bounded=False, dry_run=dry)

    def test_preview_and_first_placement_keep_their_complete_result_and_existing_cursors(self):
        root, config, settings, transcript, adapter = self.fake_batch('place-preview')
        settings = replace(settings, import_earlier=False)
        before = intake.cursor_file(root, 'jsonl', transcript.key).read_bytes()
        with mock.patch.object(intake, '_one', side_effect=AssertionError('placement cannot read turns')):
            result = self.execute(root, settings, dry=True, discovered=[transcript])
        preview = 'kiln.jsonl: 0 turns (the cursor would be placed at the end)'
        self.assertEqual(result.lines, ('glaze-bot: 0 turns would be taken in', '  ' + preview))
        self.assertEqual(result.data, {'adapter': 'jsonl', 'agent': 'glaze-bot', 'dry_run': True,
                                       'ok': True, 'placed': False, 'transcripts': [preview],
                                       'unknown': 0, 'wrote': 0})
        with mock.patch.object(intake, '_follow_end', side_effect=AssertionError('existing cursor must stay')):
            result = self.execute(root, settings, discovered=[transcript])
        self.assertEqual(result.lines, ('glaze-bot: 0 turns taken in',))
        self.assertEqual(result.data, {'adapter': 'jsonl', 'agent': 'glaze-bot', 'dry_run': False,
                                       'ok': True, 'placed': True, 'transcripts': [], 'unknown': 0, 'wrote': 0})
        self.assertEqual(intake.cursor_file(root, 'jsonl', transcript.key).read_bytes(), before)

    def test_preview_never_recovers_renames_or_marks_placement_and_explicit_reads_do_not_mark_it(self):
        for dry, sweep, placed in ((True, True, False), (False, False, False), (False, True, True)):
            root = self.prepared('marks-' + str(dry) + str(sweep))
            settings = replace(load_config(root).intake.tool('jsonl'), import_earlier=True)
            with mock.patch.object(intake, '_finish_renames', return_value={}) as recover:
                self.execute(root, settings, dry=dry, sweep=sweep)
            self.assertEqual(recover.call_count, 0 if dry else 1)
            self.assertEqual(bool(intake._read_meta(root, 'jsonl', dry_run=True).get('placed')), placed)
            if placed:
                with mock.patch.object(intake, '_write_meta', side_effect=AssertionError('already placed')):
                    self.execute(root, settings)

    def test_failed_preview_is_read_only_and_partial_write_reports_each_failed_transcript(self):
        for dry in (False, True):
            for known in (False, True):
                root, config, settings, transcript, adapter = self.fake_batch('failed-' + str(dry) + str(known))
                error = MemoryForestError('intake_text_empty', 'fictional empty turn') if known else ValueError('fictional')
                def one(*args, receipts, **kwargs):
                    if args[5].key == 'bad':
                        raise error
                    if not dry:
                        receipts.append({'id': 'fictional-id', 'stored': 'written', 'path': 'fictional',
                                         'derived': {'ids': {'state': 'updated'}}})
                    return 1, 0, ''
                with mock.patch.object(intake, '_one', side_effect=one), mock.patch.object(intake, 'log_failure') as failure:
                    result = self.execute(root, settings, dry=dry, sweep=False,
                                          discovered=[replace(transcript, key='bad'), replace(transcript, key='good')])
                self.assertEqual(result.data['transcripts'], [f'bad: failed ({type(error).__name__})', 'good: 1 turn'])
                self.assertEqual(result.data['wrote'], 1)
                self.assertEqual(failure.call_args_list, [] if dry else [mock.call(
                    root, error.code if known else 'ValueError', 'jsonl')])
                if not dry:
                    self.assertIn('stored: written; id: fictional-id', result.lines)
                    self.assertEqual(result.lines[-1], 'ids: updated')

    def test_invalid_adapter_is_rejected_before_the_loader_and_preview_passes_hook_boundedness(self):
        root = self.prepared('bounded')
        with mock.patch.object(intake.adapter_loader, 'get_adapter', side_effect=AssertionError('invalid loader call')):
            with self.assertRaises(MemoryForestError) as caught:
                intake.run(root, 'bad/id', once=True)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('intake_adapter_unknown', {'adapter': 'bad/id'}))
        intake._write_meta(root, 'jsonl', {'placed': True})
        path = self.base / 'bounded-exports/kiln.jsonl'
        for hook in (False, True):
            with mock.patch.object(intake, '_execute', return_value='preview') as execute:
                self.assertEqual(intake.run(root, 'jsonl', once=True, dry_run=True,
                                            hook={'transcript_path': str(path)} if hook else None), 'preview')
            self.assertEqual(execute.call_args.kwargs['bounded'], hook)

    def test_rename_collision_and_prepublication_failures_are_exact_refusals(self):
        root = self.prepared('rename-collision')
        for agent in ('glaze-bot', 'other-bot'):
            intake.append_owned(root, agent, 'fictional note', role='agent', origin='interactive')
        with self.assertRaises(MemoryForestError) as caught:
            intake.rename_agent(root, 'glaze-bot', 'other-bot')
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('intake_rename_exists', {'agent': 'other-bot'}))
        with mock.patch.object(system, 'rename_folder', side_effect=system.Denied('fictional', access='rename')):
            with self.assertRaises(system.Denied):
                intake.rename_agent(root, 'glaze-bot', 'new-bot')
        self.assertTrue((root / '06 istm/glaze-bot').is_dir())
        self.assertFalse((root / '06 istm/new-bot').exists())

    def test_rewinds_and_missing_description_refresh_metadata_but_an_unchanged_cursor_keeps_it(self):
        for mode in ('rewind', 'undescribed', 'cached'):
            root, config, settings, transcript, adapter = self.fake_batch('describe-' + mode)
            cursor = intake._load_cursor(root, 'jsonl', transcript.key)
            cursor.update(described=mode != 'undescribed', parent='cached-parent')
            intake._save_cursor(root, 'jsonl', transcript.key, cursor)
            if mode == 'rewind':
                old = transcript.path.with_suffix('.old')
                transcript.path.rename(old)
                transcript.path.write_bytes(old.read_bytes())
            adapter.describe = mock.Mock(return_value=replace(transcript, parent='fresh-parent'))
            adapter.read.return_value = replace(adapter.read.return_value, turns=adapter.read.return_value.turns[:1])
            intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                        dry_run=False, bounded=False, id_cache={}, receipts=[])
            self.assertEqual(adapter.describe.call_count, 0 if mode == 'cached' else 1)
            self.assertEqual(self.rows(root)[0]['source']['parent'],
                             'cached-parent' if mode == 'cached' else 'fresh-parent')

    def test_a_full_reread_checks_the_lane_even_when_the_ids_record_claims_it_is_empty(self):
        root, config, settings, transcript, adapter = self.fake_batch('full-ids')
        adapter.read.return_value = replace(adapter.read.return_value, turns=adapter.read.return_value.turns[:1])
        for damaged in (False, True):
            if damaged:
                intake._ids_path(root, 'glaze-bot', '2051-03-02').write_text('')
                cursor = intake._load_cursor(root, 'jsonl', transcript.key)
                cursor.update(offset=0, prefix=intake._EMPTY_PREFIX)
                intake._save_cursor(root, 'jsonl', transcript.key, cursor)
            intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                        dry_run=False, bounded=False, id_cache={}, receipts=[])
        self.assertEqual(len(self.rows(root)), 1)

    def test_excluded_and_long_line_previews_leave_cursors_and_failure_logs_unchanged(self):
        for mode in ('excluded', 'long', 'owner'):
            root, config, settings, transcript, adapter = self.fake_batch('dry-' + mode)
            if mode == 'excluded':
                settings = replace(settings, exclude=('/studio',))
            elif mode == 'long':
                adapter.read.return_value = replace(adapter.read.return_value, turns=(), offset=0)
            else:
                cursor = intake._load_cursor(root, 'jsonl', transcript.key)
                cursor.update(owner='', seeded=False)
                intake._save_cursor(root, 'jsonl', transcript.key, cursor)
                adapter.read.return_value = replace(adapter.read.return_value, turns=())
            before = intake.cursor_file(root, 'jsonl', transcript.key).read_bytes()
            with mock.patch.object(intake, '_clear_busy', side_effect=AssertionError('preview is read only')):
                result = intake._one(root, config, adapter, settings, 'glaze-bot', transcript,
                                     dry_run=True, bounded=mode == 'long', id_cache={}, receipts=[])
            self.assertEqual(result, (0, 2 if mode == 'owner' else 0,
                                     {'excluded': 'excluded', 'long': 'a line longer than a hook reads waits for a sweep',
                                      'owner': 'waiting for the owner turn'}[mode]))
            self.assertEqual(intake.cursor_file(root, 'jsonl', transcript.key).read_bytes(), before)
