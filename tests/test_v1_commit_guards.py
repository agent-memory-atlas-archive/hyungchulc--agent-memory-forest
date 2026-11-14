"""Failures before publication keep the old lane; later failures keep a stored receipt.

All values and files belong to the repository's invented bakery.
"""
import copy
import errno
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import bounds
from memory_forest import intake, mcp, sources, system, turn_keys
from memory_forest.errors import MemoryForestError
from test_v1_durable_turns import log, records
from test_v1_find import FILES
from v1_support import ForestTestCase


class CommitGuardTests(ForestTestCase):
    def test_an_empty_append_has_no_commit_or_new_file(self):
        root = self.make_forest(FILES)
        path = root / 'empty.jsonl'
        marked = []
        system.append(path, b'', committed=lambda: marked.append(True))
        self.assertFalse(path.exists())
        self.assertEqual(marked, [])

    def test_a_torn_tail_is_removed_without_touching_the_complete_prefix(self):
        root = self.make_forest(FILES)
        path = root / '06 istm/till-bot/tail.jsonl'
        for prefix, tail in ((b'kept\n', b'torn'), (b'kept\n', b'torn'*4000), (b'', b'torn'), (b'', b'')):
            path.write_bytes(prefix + tail)
            system.append(path, b'new\n', trim_torn=True)
            self.assertEqual(path.read_bytes(), prefix + b'new\n')

    def test_a_partial_append_or_failed_sync_before_commit_is_rolled_back(self):
        root = self.make_forest(FILES)
        path = root / '06 istm/till-bot/2051-03-02.jsonl'
        before = path.read_bytes()
        marked = []
        for failed in ('write', 'fsync'):
            with mock.patch.object(system.os, failed, side_effect=OSError(errno.EIO, 'fictional failure')):
                with self.assertRaises(system.Other):
                    system.append(path, b'{"id":"new"}\n', committed=lambda: marked.append(True))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(marked, [])
        with mock.patch.object(system.os, 'write', return_value=0), self.assertRaises(system.Other):
            system.append(path, b'new\n', committed=lambda: marked.append(True))
        self.assertEqual(path.read_bytes(), before)

    def test_publication_precedes_the_directory_sync_for_rewrites_and_renames(self):
        root = self.make_forest(FILES)
        path = root / '06 istm/till-bot/2051-03-02.jsonl'
        marked = []
        with mock.patch.object(system, 'sync_folder', side_effect=system.Other(root)), self.assertRaises(system.Other):
            system.write_whole(path, b'new\n', committed=lambda: marked.append(path.read_bytes()))
        self.assertEqual(marked, [b'new\n'])
        source = root / '06 istm/till-bot'
        target = source.with_name('new-bot')
        marked.clear()
        with mock.patch.object(system, 'sync_folder', side_effect=system.Other(root)), self.assertRaises(system.Other):
            system.rename_folder(source, target, committed=lambda: marked.append(target.is_dir()))
        self.assertEqual(marked, [True])
        self.assertFalse(source.exists())
        self.assertTrue(target.is_dir())

    def test_rename_refuses_links_files_and_existing_targets_before_committing(self):
        root = self.make_forest(FILES)
        source = root / '06 istm/till-bot'
        target = source.with_name('new-bot')
        link = root / 'lane-link'
        link.symlink_to(source, target_is_directory=True)
        for old, new, kind in ((link, target, system.Link), (root / 'forest.toml', target, system.NotAFolder),
                               (source, root / '06 istm/oven-bot', system.Other)):
            marked = []
            with self.assertRaises(kind):
                system.rename_folder(old, new, committed=lambda: marked.append(True))
            self.assertEqual(marked, [])
            self.assertTrue(source.is_dir())
        with mock.patch.object(system.os, 'rename', side_effect=OSError(errno.EXDEV, 'fictional devices')):
            with self.assertRaises(system.Other) as caught:
                system.rename_folder(source, target)
        self.assertEqual(caught.exception.error, 'EXDEV')
        empty = root / 'empty-target'
        empty.mkdir()
        with self.assertRaises(system.Other) as caught:
            system.rename_folder(source, empty)
        self.assertEqual(caught.exception.error, 'EEXIST')
        self.assertTrue(source.is_dir())

    def test_a_rewrite_does_not_try_bytes_before_a_failed_look_or_into_a_nonfile(self):
        root = self.make_forest(FILES)
        with mock.patch.object(system.os, 'lstat', side_effect=PermissionError(errno.EACCES, 'fictional denial')):
            with self.assertRaises(system.Denied):
                system.write_whole(root / 'forest.toml', b'new')
        with mock.patch.object(system.os, 'write', side_effect=AssertionError('a directory is refused before writing')):
            with self.assertRaises(system.NotAFile):
                system.write_whole(root / '06 istm', b'new')

    def test_every_exception_class_after_commit_is_pending_and_never_exposes_its_text(self):
        root = self.make_forest(FILES)
        private = 'this-exception-text-must-not-leave'
        for exc, reason in ((MemoryForestError('write_readback_failed', private), 'write_readback_failed'),
                            (MemoryForestError(private, private), 'internal_error'),
                            (system.Denied(root / '.forest'), 'access_denied'), (ValueError(private), 'internal_error')):
            with mock.patch.object(intake, '_write_ids', side_effect=exc):
                written = log(root)
            self.assertEqual(written['stored'], 'written')
            self.assertEqual(written['derived']['ids'], {'state': 'pending', 'reason': reason})
            self.assertNotIn(private, json.dumps(written))
            self.assertEqual(sum(row['id'] == written['id'] for row in records(root)), 1)
        self.assertTrue(all(item['state'] == 'updated' for item in log(root)['derived'].values()))

    def test_a_directory_sync_failure_after_the_append_keeps_the_turn_and_its_id(self):
        root = self.make_forest(FILES)
        original = system.sync_folder
        def failed(path):
            if Path(path).name == 'till-bot':
                raise system.Denied(path, access='write')
            original(path)
        with mock.patch.object(system, 'sync_folder', failed):
            written = log(root, key='sync-key')
        self.assertEqual(written['stored'], 'written')
        self.assertTrue(written['id'])
        self.assertEqual(written['derived']['bookkeeping']['state'], 'pending')
        self.assertEqual(log(root, key='sync-key')['id'], written['id'])

    def test_rename_and_masking_keep_their_receipt_if_the_first_directory_sync_fails(self):
        for operation in ('rename', 'mask'):
            root = self.make_forest(FILES, name=operation)
            original = system.sync_folder
            def failed(path):
                if Path(path) == root / ('06 istm' if operation == 'rename' else '06 istm/till-bot'):
                    raise system.Denied(path, access='write')
                original(path)
            if operation == 'rename':
                expected_ids = sorted(row['id'] for row in records(root))
                with mock.patch.object(system, 'sync_folder', failed):
                    body = intake.rename_agent(root, 'till-bot', 'new-bot').data
                self.assertEqual((body['stored'], body['ids']), ('renamed', expected_ids))
            else:
                path = root / '06 istm/till-bot/2051-03-02.jsonl'
                row = records(root)[0]
                row['text'] = 'password=fictional-sesame'
                path.write_text(intake._encode(row))
                with mock.patch.object(system, 'sync_folder', failed):
                    body = intake.lane_masking(root, fix=True)[0]
                self.assertEqual((body['stored'], body['written']), ('rewritten', 1))
                self.assertNotIn('fictional-sesame', path.read_text())
            self.assertEqual(body['derived']['bookkeeping'], {'state': 'pending', 'reason': 'access_denied'})
            self.assertTrue(all(body['derived'][name]['state'] == 'pending' for name in ('ids', 'turns', 'sources')))

    def test_an_unknown_answer_formatting_failure_cannot_reverse_a_stored_turn(self):
        root = self.make_forest(FILES)
        with mock.patch.object(mcp, '_tool_text', side_effect=ValueError('fictional private exception')):
            reply = mcp._call(root, 'till-bot', {'name': 'memory_log', 'arguments': {'text': 'formatting turn'}}, 'stdio')
        self.assertFalse(reply['isError'])
        body = json.loads(reply['content'][0]['text'])
        self.assertEqual(body['stored'], 'written')
        self.assertTrue(body['id'])
        self.assertEqual(body['withheld'], 'internal_error')
        self.assertNotIn('private exception', reply['content'][0]['text'])

    def test_a_valid_source_record_shortens_a_yes_without_a_lane_scan(self):
        root = self.make_forest(FILES)
        log(root, group='night-shift')
        sources._SCANNED.clear()
        for _ in range(2):
            with system.watching() as trace:
                self.assertIn('group:night-shift', sources.listed(root, ['till-bot'])['till-bot'])
            self.assertEqual([p for name, p in trace.calls if name == 'read' and '06 istm' in p], [])
            self.assertEqual([p for name, p in trace.calls if name == 'write_whole'], [])

    def test_a_derived_scan_is_retained_for_each_lane_and_respects_keep_false(self):
        root = self.make_forest(FILES)
        log(root, group='night-shift')
        intake.append_owned(root, 'crumb-bot', 'fictional crumbs', role='agent', origin='interactive', session='crumbs')
        path = root / sources.RECORD
        path.write_text('{"agents":')
        before = path.read_bytes()
        sources._SCANNED.clear()
        for keep in (False, True):
            with system.watching() as trace:
                answer = sources.listed(root, ['till-bot', 'crumb-bot'], keep=keep, verify=True)
            self.assertEqual(answer, {'till-bot': ['adapter:jsonl', 'group:night-shift'], 'crumb-bot': ['door:mcp']})
            if not keep:
                self.assertEqual(path.read_bytes(), before)
            else:
                self.assertEqual(set(json.loads(path.read_text())['agents']), {'till-bot', 'crumb-bot'})
        with system.watching() as trace:
            sources.listed(root, ['till-bot', 'crumb-bot'], verify=True)
        self.assertEqual([p for name, p in trace.calls if name == 'read' and '06 istm' in p], [])

    def test_no_record_is_written_for_a_lane_that_has_never_existed(self):
        root = self.make_forest(FILES)
        with system.watching() as trace:
            self.assertEqual(sources.listed(root, ['never-bot']), {'never-bot': []})
        self.assertEqual([p for name, p in trace.calls if name == 'write_whole'], [])

    def test_every_malformed_source_record_is_rebuilt_whole(self):
        root = self.make_forest(FILES)
        log(root, group='night-shift')
        path = root / sources.RECORD
        good = json.loads(path.read_text())
        mutations = [lambda e: e.update(sources=7), lambda e: e.update(sources=['group:phantom', 7]),
                     lambda e: e.update(sources=['door:mcp', 'door:mcp']), lambda e: e.update(sources=['door:bad name']),
                     lambda e: e.update(lanes=[]), lambda e: e.update(lanes={'../x.jsonl': [0]*5}),
                     lambda e: e.update(lanes={'x.txt': [0]*5}), lambda e: e.update(lanes={'x.jsonl': [0]*4}),
                     lambda e: e.update(lanes={'x.jsonl': [True]*5}), lambda e: e.update(lanes={'x.jsonl': [-1]*5}),
                     lambda e: e.update(lanes={'x.jsonl': 'stamp'}), lambda e: e.update(extra=1),
                     lambda e: e.update(seal='torn')]
        for mutate in mutations:
            bad = copy.deepcopy(good)
            mutate(bad['agents']['till-bot'])
            path.write_text(json.dumps(bad))
            with system.watching() as trace:
                actual = sources.listed(root, ['till-bot'])
            self.assertIn('group:night-shift', actual['till-bot'])
            self.assertNotIn('group:phantom', actual['till-bot'])
            self.assertTrue([p for name, p in trace.calls if name == 'read' and '06 istm' in p])
        for bad in ([], {'agents': []}, {'agents': good['agents'], 'extra': 1},
                    {'agents': {'bad/id': good['agents']['till-bot']}}):
            path.write_text(json.dumps(bad))
            self.assertIn('group:night-shift', sources.listed(root, ['till-bot'])['till-bot'])

    def test_a_malformed_key_lookup_never_changes_the_turn(self):
        root = self.make_forest(FILES)
        first = log(root, key='stable-key')
        path = Path(turn_keys.path(root, 'till-bot'))
        bad_values = [[], {'shift': []}, {'shift': {'wrong': ['2051-03-02', 0, 'id']}},
                      {'shift': {first['turn_digest']: None}},
                      {'shift': {first['turn_digest']: ['../escape', 0, 'id']}},
                      {'shift': {first['turn_digest']: ['2051-03-02', True, 'id']}},
                      {'shift': {first['turn_digest']: ['2051-03-02', -1, 'id']}},
                      {'shift': {first['turn_digest']: ['2051-03-02', 0, 7]}}]
        for keys in bad_values:
            entry = json.loads(path.read_text())
            entry['keys'] = keys
            entry['seal'] = sources._seal(entry['lanes'], keys)
            path.write_text(json.dumps(entry))
            self.assertEqual(log(root, key='stable-key')['id'], first['id'])

    def test_the_key_record_validator_checks_the_whole_shape(self):
        token = 'a' * 64
        good = {'shift': {token: ['2051-03-02', 0, 'turn-id']}}
        self.assertTrue(turn_keys._valid(good))
        for value in ([], {7: {}}, {'shift': []}, {'shift': {7: ['2051-03-02', 0, 'turn-id']}},
                      {'shift': {'torn': ['2051-03-02', 0, 'turn-id']}},
                      {'shift': {token: None}}, {'shift': {token: ['2051-03-02', 0]}},
                      {'shift': {token: [7, 0, 'turn-id']}}, {'shift': {token: ['../escape', 0, 'turn-id']}},
                      {'shift': {token: ['2051-03-02', True, 'turn-id']}},
                      {'shift': {token: ['2051-03-02', -1, 'turn-id']}},
                      {'shift': {token: ['2051-03-02', 0, 7]}}):
            self.assertFalse(turn_keys._valid(value), value)

    def test_a_valid_key_record_shortens_the_lookup_without_reading_a_lane(self):
        root = self.make_forest(FILES)
        first = log(root, key='lookup-key')
        with system.watching() as trace:
            keys = turn_keys.load(root, 'till-bot')
        self.assertEqual(keys, {'shift': {first['turn_digest']: ['2051-03-02', 1, first['id']]}})
        self.assertEqual([p for name, p in trace.calls if name == 'read' and '06 istm' in p], [])


if __name__ == '__main__':
    unittest.main()
