"""Boundary and representation guards for the fictional bakery's durable writes."""
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from unittest import mock

import bounds
from memory_forest import cli, cli_find, cli_upkeep, credentials, intake, levels, mcp, search, sources, system, turn_keys
from memory_forest.config import load_config
from memory_forest.errors import MemoryForestError
from test_v1_durable_turns import AT, TEXT, log, records
from test_v1_find import FILES
from v1_support import ForestTestCase


class BoundaryTests(ForestTestCase):
    def test_append_rejects_each_bad_argument_before_any_lane_write(self):
        root = self.make_forest(FILES)
        cases = (({'agent': 'bad/id'}, 'intake_agent_invalid', {'agent': 'bad/id'}),
                 ({'role': 'invented'}, 'intake_role_invalid', {'role': 'invented'}),
                 ({'origin': 'invented'}, 'intake_origin_invalid', {'origin': 'invented'}),
                 ({'text': '  '}, 'intake_text_empty', {}))
        before = records(root)
        for options, code, details in cases:
            arguments = dict(agent='till-bot', text=TEXT, role='agent', origin='interactive', session='shift')
            arguments.update(options)
            with self.assertRaises(MemoryForestError) as caught:
                intake.append_owned(root, **arguments)
            self.assertEqual((caught.exception.code, caught.exception.details), (code, details))
            self.assertEqual(records(root), before)
        for key in (7, '', ' ', 'a' * (intake.TURN_ID_MAX + 1), '\u0085', '\ud800'):
            with self.assertRaises(MemoryForestError) as caught:
                log(root, key=key)
            self.assertEqual(caught.exception.code, 'arguments_invalid')
        self.assertEqual(records(root), before)

    def test_command_fields_and_missing_reader_are_refused_without_reading_stdin(self):
        root = self.make_forest(FILES)
        for flags, code in ((['words'], 'log_text_from_stdin'), (['--text', 'words'], 'log_text_from_stdin'),
                            (['--role', 'owner'], 'log_caller_fields'),
                            (['--origin', 'interactive'], 'log_caller_fields'), ([], 'log_agent_unset')):
            argv = ['log', *flags, '--root', str(root)]
            if flags:
                argv += ['--as', 'till-bot']
            args = cli.build_forest_parser().parse_args(argv)
            with mock.patch.object(cli_find, '_log_text', side_effect=AssertionError('stdin must stay unread')):
                with self.assertRaises(MemoryForestError) as caught:
                    cli_find.cmd_log(args)
            self.assertEqual(caught.exception.code, code)

    def test_the_plain_write_answer_contains_every_state_and_reason_with_or_without_a_path(self):
        root = self.make_forest(FILES)
        args = cli.build_forest_parser().parse_args(['log', '--as', 'till-bot', '--root', str(root)])
        body = {'id': 'fictional-id', 'stored': 'written', 'path': '06 istm/till-bot/day.jsonl',
                'wrote': True, 'indexed': False, 'tree': 'pending', 'index': 'pending', 'reason': 'index_busy',
                'derived': {'ids': {'state': 'pending', 'reason': 'access_denied'}, 'sources': {'state': 'updated'}}}
        for withheld in (False, True):
            out = io.StringIO()
            with mock.patch.object(cli_find, '_log_text', return_value=TEXT), \
                    mock.patch.object(mcp, 'record_turn', return_value=body.copy()), \
                    mock.patch.object(cli_find, '_shown', side_effect=ValueError('private') if withheld else None,
                                      return_value=body.copy()), contextlib.redirect_stdout(out):
                self.assertEqual(cli_find.cmd_log(args), 0)
            text = out.getvalue()
            for line in ('id: fictional-id', 'wrote: true', 'indexed: false', 'stored: written',
                         'tree: pending', 'index: pending', 'reason: index_busy',
                         'ids: pending (access_denied)', 'sources: updated'):
                self.assertIn(line + '\n', text)
            self.assertEqual('path: ' in text, not withheld)
            self.assertEqual('withheld: internal_error\n' in text, withheld)

    def test_conflict_retains_the_first_id_and_only_echoes_the_callers_own_key(self):
        root = self.make_forest(FILES)
        first = log(root, key='caller-key')
        with self.assertRaises(MemoryForestError) as caught:
            log(root, key='caller-key', text='other words')
        self.assertEqual(caught.exception.details, {'id': first['id'], 'turn': 'caller-key'})
        self.assertEqual(first['turn_digest'], turn_keys.digest(root, 'caller-key'))
        self.assertNotIn('turn', first)

    def test_the_stored_record_has_every_field_and_an_exact_arrival(self):
        root = self.make_forest(FILES)
        result = log(root, key='record-key', door='cli', group='night-shift')
        wanted = {'at': AT.isoformat(), 'id': result['id'], 'origin': 'interactive', 'role': 'agent',
                  'session': 'shift', 'text': TEXT, 'turn_digest': turn_keys.digest(root, 'record-key'),
                  'source': {'adapter': 'cli', 'place': AT.isoformat(), 'transcript': 'cli',
                             'door': 'cli', 'group': 'night-shift'}, 'sources': ['group:night-shift']}
        wanted['arrivals'] = [{'at': wanted['at'], 'origin': wanted['origin'], 'source': wanted['source'],
                               'sources': wanted['sources']}]
        self.assertEqual(records(root)[-1], wanted)
        self.assertEqual({key: result[key] for key in wanted}, wanted)
        self.assertEqual(result['derived'], {name: {'state': 'updated'} for name in ('ids', 'turns', 'sources')})
        self.assertEqual(result['stored'], 'written')
        self.assertTrue(result['wrote'])
        self.assertEqual(result['path'], '06 istm/till-bot/2051-03-02.jsonl')
        self.assertEqual(set(result), set(wanted) | {'path', 'wrote', 'stored', 'derived'})

    def test_every_join_guard_keeps_distinct_turns_distinct_and_tolerates_old_times(self):
        for field, value in (('session', 'other-shift'), ('role', 'owner'), ('text', 'other words'),
                             ('at', 'not a time')):
            root = self.make_forest(FILES, name=field)
            first = log(root, door='cli')
            path = root / first['path']
            record = json.loads(path.read_text().splitlines()[-1])
            record[field] = value
            lines = path.read_text().splitlines()
            lines[-1] = intake._encode(record).rstrip('\n')
            path.write_text('\n'.join(lines) + '\n')
            self.assertNotEqual(log(root, door='mcp')['id'], first['id'], field)
        root = self.make_forest(FILES, name='old-time')
        first = log(root, door='cli')
        path = root / first['path']
        record = json.loads(path.read_text().splitlines()[-1])
        record['at'] = AT.replace(tzinfo=None).isoformat()
        lines = path.read_text().splitlines()
        lines[-1] = intake._encode(record).rstrip('\n')
        path.write_text('\n'.join(lines) + '\n')
        self.assertEqual(log(root, door='mcp')['id'], first['id'])
        # A same-source repeat has no caller key and is a fresh turn.
        self.assertNotEqual(log(root, door='mcp')['id'], first['id'])

    def test_rename_reports_its_entire_committed_result_and_rejects_before_the_move(self):
        root = self.make_forest(FILES)
        first = log(root, key='rename-key')
        for old, new in (('bad/id', 'new-bot'), ('till-bot', 'bad/id')):
            with self.assertRaises(MemoryForestError) as caught:
                intake.rename_agent(root, old, new)
            self.assertEqual(caught.exception.code, 'intake_agent_invalid')
            self.assertEqual(caught.exception.details, {'agent': old})
        self.assertEqual(intake.rename_agent(root, 'till-bot', 'till-bot').data,
                         {'agent': 'till-bot', 'ok': True, 'renamed': False})
        all_ids = sorted(row['id'] for row in records(root))
        renamed = intake.rename_agent(root, 'till-bot', 'renamed-bot')
        self.assertEqual(renamed.data, {'agent': 'renamed-bot', 'from': 'till-bot', 'ok': True,
                                       'renamed': True, 'stored': 'renamed', 'ids': all_ids, 'id': all_ids[0],
                                       'cursors': 0, 'derived': {name: {'state': 'updated'}
                                                               for name in ('ids', 'turns', 'sources', 'cursors')}})
        self.assertEqual(intake.append_owned(root, 'renamed-bot', TEXT, turn_id='rename-key', session='shift',
                                             role='agent', origin='interactive', at=AT)['id'], first['id'])

    def test_plain_and_json_masking_answers_include_pages_lanes_and_derived_state(self):
        page = credentials.PageCredentials(path='04 stm/bakery/ovens/floor.md', lines=[3])
        lanes = [{'lane': '06 istm/till-bot', 'lines': 3, 'written': 2, 'kept': 1,
                  'stored': 'rewritten', 'ids': ['turn-a'],
                  'derived': {'ids': {'state': 'pending', 'reason': 'access_denied'},
                              'turns': {'state': 'updated'}}}]
        for written in (False, True):
            page.written = written
            page.archive_path = 'archive/copy.md' if written else None
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli_upkeep._print_credentials([page], json_out=False, extra={'ok': True}, lanes=lanes)
            text = out.getvalue()
            for phrase in ('04 stm/bakery/ovens/floor.md', '1 pages', '3 lines', '2 rewritten', '1 kept',
                           'stored: rewritten; ids: turn-a', 'ids: pending (access_denied)', 'turns: updated'):
                self.assertIn(phrase, text)
            self.assertEqual('masked, copy archive/copy.md' in text, written)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_upkeep._print_credentials([page], json_out=True, extra={'ok': True}, lanes=lanes)
        self.assertEqual(json.loads(out.getvalue()), {'ok': True, 'lanes': lanes, 'pages': [page.as_dict()]})

    def test_identity_metadata_survives_even_a_masker_that_recognizes_every_string(self):
        root = self.make_forest(FILES)
        first = log(root, key='mask-key')
        raw = intake._encode({key: first[key] for key in ('id', 'at', 'turn_digest', 'text', 'source')}).rstrip('\n').encode()
        with mock.patch.object(credentials, 'mask', return_value=credentials.REDACTED):
            masked = intake._masked_lane_line(raw, load_config(root), None, root=root, fix=True)
            self.assertIsNotNone(masked)
            row = json.loads(masked)
            for key in ('id', 'at', 'turn_digest'):
                self.assertEqual(row[key], first[key])
            self.assertEqual(row['text'], credentials.REDACTED)
            self.assertEqual(row['source'], {key: credentials.REDACTED for key in first['source']})
            self.assertEqual(intake._mask_lane_value({'turn_digest': first['turn_digest']}, load_config(root), None),
                             {'turn_digest': first['turn_digest']})
            self.assertEqual(intake._mask_lane_value(['fictional', 7], load_config(root), None),
                             [credentials.REDACTED, 7])

    def test_masking_ignores_nonrecords_and_folders_that_are_not_agent_lanes(self):
        root = self.make_forest({'forest.toml': FILES['forest.toml']})
        self.assertEqual(intake._lane_files(root), [])
        for raw in (b'', b'not json', b'[]', b'null', b'false'):
            self.assertIsNone(intake._masked_lane_line(raw, load_config(root), None, root=root, fix=True))
        invalid = root / '06 istm/bad name'
        invalid.mkdir(parents=True)
        (invalid / '2051-03-02.jsonl').write_text('{}\n')
        self.assertEqual(intake._lane_files(root), [])

    def test_the_tool_record_reports_the_whole_commit_and_each_kind_of_derived_failure(self):
        root = self.make_forest(FILES)
        for failure, reason in ((None, None), (MemoryForestError('index_busy', 'invented'), 'index_busy'),
                                (system.Denied(root), 'access_denied'), (ValueError('private'), 'internal')):
            with mock.patch.object(levels, 'agent_tree', side_effect=failure, return_value='already there'), \
                    mock.patch.object(search, 'update_files', side_effect=failure, return_value={'indexed': True}):
                body = mcp.record_turn(root, 'till-bot', TEXT, origin='interactive', session='shift', at=AT)
            expected = {'id': records(root)[-1]['id'], 'path': '06 istm/till-bot/2051-03-02.jsonl',
                        'stored': 'written', 'wrote': True, 'sources': ['door:mcp'],
                        'derived': {name: {'state': 'updated'} for name in ('ids', 'turns', 'sources')},
                        'tree': 'pending' if failure else 'already there',
                        'index': 'pending' if failure else 'updated', 'indexed': not failure}
            if failure:
                expected['tree_reason'] = 'tree_failed' if reason == 'internal' else reason
                expected['reason'] = 'index_failed' if reason == 'internal' else reason
            self.assertEqual(body, expected)
            self.assertNotIn('private', json.dumps(body))

    def test_connection_bookkeeping_and_the_mcp_envelope_keep_the_committed_answer(self):
        root = self.make_forest(FILES)
        with mock.patch.object(mcp, '_remember', side_effect=system.Denied(root, access='write')):
            reply = mcp._call(root, 'till-bot', {'name': 'memory_log', 'arguments': {'text': TEXT}}, 'stdio')
        self.assertEqual(set(reply), {'content', 'isError'})
        self.assertFalse(reply['isError'])
        self.assertEqual(set(reply['content'][0]), {'type', 'text'})
        self.assertEqual(reply['content'][0]['type'], 'text')
        self.assertEqual(json.loads(reply['content'][0]['text'])['derived']['seen'],
                         {'state': 'pending', 'reason': 'access_denied'})

    def test_a_turns_size_guard_and_tree_wait_are_independent_of_the_transport(self):
        root = self.make_forest(FILES)
        with self.assertRaises(MemoryForestError) as caught:
            mcp.record_turn(root, 'till-bot', 'x' * (mcp.TURN_MAX_BYTES + 1), origin='interactive', session='shift')
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('log_too_large', {'limit': mcp.TURN_MAX_BYTES}))
        for seconds in (None, 0.25):
            with mock.patch.object(levels, 'agent_tree', return_value='already there') as tree:
                mcp.record_turn(root, 'till-bot', TEXT, origin='interactive', session='shift', tree_wait=seconds)
            self.assertEqual(tree.call_args.kwargs['wait_seconds'], levels.TREE_WAIT_SECONDS if seconds is None else seconds)

    def test_masking_before_publication_refuses_and_a_later_lane_failure_preserves_earlier_receipts(self):
        for late in (False, True):
            root = self.make_forest(FILES, name='mask-late-' + str(late))
            targets = []
            for agent in ('till-bot', 'crumb-bot'):
                turn = intake.append_owned(root, agent, 'fictional note', role='agent', origin='interactive')
                path = root / turn['path']
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                rows[-1]['text'] = 'password=fictional-sesame'
                path.write_text(''.join(intake._encode(row) for row in rows))
                targets.append(path)
            targets.sort()
            original = system.write_whole
            def write(path, *args, **kwargs):
                if path == targets[1 if late else 0]:
                    raise system.Denied(path, access='write')
                return original(path, *args, **kwargs)
            with mock.patch.object(system, 'write_whole', write):
                if not late:
                    with self.assertRaises(system.Denied):
                        intake.lane_masking(root, fix=True)
                    continue
                rows = intake.lane_masking(root, fix=True)
            self.assertEqual([row['written'] for row in rows], [1, 0])
            self.assertEqual(rows[0]['stored'], 'rewritten')
            self.assertEqual(rows[0]['derived']['bookkeeping'], {'state': 'pending', 'reason': 'access_denied'})
            self.assertNotIn('stored', rows[1])
        self.assertNotIn('derived', rows[1])

    def test_a_real_seen_record_failure_is_pending_on_the_stored_turn_and_the_next_call_repairs_it(self):
        root = self.make_forest(FILES)
        path = root / mcp.SEEN_FILE
        given = {'name': 'memory_log', 'arguments': {'text': TEXT, 'turn_id': 'seen-key'}}
        with system.watching(fail_under={path: 'denied'}) as trace:
            reply = mcp._call(root, 'till-bot', given, 'stdio')
        self.assertTrue(trace.injected)
        self.assertFalse(reply['isError'])
        body = json.loads(reply['content'][0]['text'])
        self.assertEqual(body['derived']['seen'], {'state': 'pending', 'reason': 'access_denied'})
        self.assertEqual(sum(row['id'] == body['id'] for row in records(root)), 1)
        again = mcp._call(root, 'till-bot', given, 'stdio')
        answer = json.loads(again['content'][0]['text'])
        self.assertFalse(again['isError'])
        self.assertEqual(answer['id'], body['id'])
        self.assertNotIn('seen', answer['derived'])
        self.assertTrue(path.is_file())

    def test_seen_refresh_preserves_first_observation_and_fresh_or_raced_records_do_not_write(self):
        root = self.make_forest(FILES)
        path = root / mcp.SEEN_FILE
        path.parent.mkdir(parents=True)
        for previous in (7, {}, {'at': '2051-03-02T12:00:00Z', 'last': '2051-03-02T12:00:00Z',
                                'tool': 'first-tool', 'transport': 'first-door', 'extra': 'kept'}):
            path.write_text(json.dumps({'till-bot': previous}))
            with mock.patch.object(mcp, '_seen_fresh', return_value=False):
                self.assertIsNone(mcp._remember(root, 'till-bot', 'memory_log', 'stdio'))
            current = json.loads(path.read_text())['till-bot']
            self.assertEqual(set(current), {'at', 'last', 'tool', 'transport'} |
                             ({'extra'} if isinstance(previous, dict) and 'extra' in previous else set()))
            self.assertTrue(current['at'])
            self.assertTrue(current['last'])
            self.assertEqual(current['tool'], previous.get('tool', 'memory_log') if isinstance(previous, dict) else 'memory_log')
            self.assertEqual(current['transport'], previous.get('transport', 'stdio') if isinstance(previous, dict) else 'stdio')
            if isinstance(previous, dict) and previous.get('at'):
                self.assertEqual(current['at'], previous['at'])
                self.assertEqual(current['extra'], 'kept')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        before = path.read_bytes()
        with mock.patch.object(mcp, '_seen_fresh', return_value=True), \
                mock.patch.object(mcp.groups, 'exclusive', side_effect=MemoryForestError('lock_busy', 'fictional busy')), \
                system.watching() as trace:
            self.assertIsNone(mcp._remember(root, 'till-bot', 'memory_log', 'stdio'))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(any(name in {'write_whole', 'set_mode'} for name, _ in trace.calls))
        fresh = {'till-bot': {'last': '2051-03-02T12:00:00Z'}}
        with mock.patch.object(mcp, '_load_seen', side_effect=[{}, fresh]), \
                mock.patch.object(mcp, '_seen_fresh', return_value=True), \
                mock.patch.object(mcp, 'atomic_write', side_effect=AssertionError('the other caller refreshed it')):
            self.assertIsNone(mcp._remember(root, 'till-bot', 'memory_log', 'stdio'))

    def test_seen_write_and_lock_failures_return_pending_instead_of_disappearing(self):
        root = self.make_forest(FILES)
        path = root / mcp.SEEN_FILE
        with mock.patch.object(mcp, '_load_seen', return_value={}), \
                mock.patch.object(system, 'write_whole', side_effect=system.Denied(path)):
            self.assertEqual(mcp._remember(root, 'till-bot', 'memory_log', 'stdio'),
                             {'state': 'pending', 'reason': 'access_denied'})
        with mock.patch.object(mcp, '_load_seen', return_value={}), \
                mock.patch.object(mcp.groups, 'exclusive', side_effect=MemoryForestError('lock_busy', 'fictional busy')):
            self.assertEqual(mcp._remember(root, 'till-bot', 'memory_log', 'stdio'),
                             {'state': 'pending', 'reason': 'lock_busy'})
