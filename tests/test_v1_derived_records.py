"""Complete disposable records and canonical lane truth, using invented bakery turns."""
import copy
import json

import bounds
from memory_forest import credential_ledger, intake, search, sources, system, turn_keys
from memory_forest.config import load_config
from test_v1_durable_turns import AT, TEXT, log, records
from test_v1_find import FILES
from v1_support import ForestTestCase


class RecordTests(ForestTestCase):
    def test_key_digests_use_the_existing_ledger_mechanism_and_private_key(self):
        root = self.make_forest(FILES)
        token = turn_keys.digest(root, 'a fictional caller key')
        self.assertEqual(token, credential_ledger.load(root).digest('a fictional caller key'))
        self.assertEqual(len(token), 64)
    def test_a_valid_key_lookup_checks_only_its_selected_day_before_the_write(self):
        root = self.make_forest(FILES)
        first = log(root, key='short-lookup')
        from unittest import mock
        record = {'at': AT.isoformat(), 'session': 'shift', 'text': TEXT, 'turn_digest': first['turn_digest']}
        with mock.patch.object(turn_keys, 'scan', side_effect=AssertionError('a valid positive needs no full scan')):
            self.assertEqual(intake._same_turn(root, 'till-bot', record, load_config(root))[2]['id'], first['id'])
    def test_source_shape_is_checked_even_when_a_bad_entry_has_a_matching_seal(self):
        valid = {'sources': ['door:mcp'], 'lanes': {'day.jsonl': [1, 2, 3, 4, 5]}}
        for lanes in ({'day.jsonl': [1, 2]}, {'day.jsonl': [-1]*5}, {'day.jsonl': [True]*5},
                      {'day.jsonl': 'stamp'}, {'../day.jsonl': [1]*5}, {'day.txt': [1]*5}, {7: [1]*5}):
            entry = {**valid, 'lanes': lanes, 'seal': sources._seal(lanes, valid['sources'])}
            self.assertIsNone(sources._entry_sources(entry), entry)
        valid['seal'] = sources._seal(valid['lanes'], valid['sources'])
        self.assertEqual(sources._entry_sources(valid), ['door:mcp'])
        for labels in (7, [7], ['door:mcp', 'door:mcp'], ['door:bad name'], ['door:\u0080'], ['door:\ud800']):
            entry = {**valid, 'sources': labels, 'seal': sources._seal(valid['lanes'], labels)}
            self.assertIsNone(sources._entry_sources(entry), entry)

    def test_arrivals_and_legacy_labels_keep_the_complete_ordered_metadata(self):
        first = {'at': AT.isoformat(), 'origin': 'interactive',
                 'source': {'door': 'mcp', 'group': 'night-shift', 'adapter': 'mcp', 'place': 'first'},
                 'sources': ['group:night-shift']}
        second = {'at': AT.isoformat(), 'origin': 'interactive',
                  'source': {'door': 'cli', 'group': 'bakery-bots', 'adapter': 'cli', 'place': 'second'},
                  'sources': ['group:bakery-bots']}
        self.assertEqual(sources.arrivals(first), [first])
        joined = {'source': {'door': 'unused'}, 'sources': ['door:unused'], 'arrivals': [first, second, first]}
        self.assertEqual(sources.arrivals(joined), [first, second, first])
        self.assertEqual(sources.of_record(joined), ['group:night-shift', 'group:bakery-bots'])
        for value in (None, [], [7]):
            old = {'source': {'adapter': 'jsonl'}, 'arrivals': value}
            self.assertEqual(sources.of_record(old), ['adapter:jsonl'])
            self.assertEqual(sources.arrivals(old), [{'at': None, 'origin': 'unknown',
                                                     'source': {'adapter': 'jsonl'}, 'sources': ['adapter:jsonl']}])
        for record, labels in (({}, []), ({'source': 7}, []), ({'source': {'door': 7}}, []),
                               ({'source': {'door': ''}}, []), ({'source': {'door': 'bad name'}}, []),
                               ({'sources': [7, 'door:mcp', 'door:mcp']}, ['door:mcp']),
                               ({'sources': [7], 'source': {'door': 'cli'}}, ['door:cli'])):
            self.assertEqual(sources.of_record(record), labels, record)

    def test_a_writer_rebuilds_a_stale_source_entry_and_does_not_rewrite_an_unchanged_one(self):
        root = self.make_forest(FILES)
        first = log(root, group='night-shift')
        lane = root / first['path']
        lane.write_bytes(lane.read_bytes().replace(b'night-shift', b'light-shift'))
        before = sources.lanes_before(root, 'till-bot')
        sources.note(root, 'till-bot', ['door:phantom'], before)
        self.assertEqual(sources._read(root)['till-bot']['sources'], ['adapter:jsonl', 'group:light-shift'])
        with system.watching() as trace:
            sources.note(root, 'till-bot', ['bad label'], before)
        self.assertEqual([p for name, p in trace.calls if name == 'write_whole'], [])

    def test_valid_lines_survive_an_unrelated_undecodable_line_in_the_same_lane(self):
        root = self.make_forest(FILES)
        first = log(root, group='night-shift')
        with (root / first['path']).open('ab') as stream:
            stream.write(b'\xff\n')
        (root / sources.RECORD).unlink()
        self.assertIn('group:night-shift', sources.listed(root, ['till-bot'])['till-bot'])

    def test_a_lane_file_that_disappears_during_a_source_scan_does_not_hide_the_other_files(self):
        from unittest import mock
        root = self.make_forest(FILES)
        gone = root / '06 istm/till-bot/2051-03-01.jsonl'
        gone.write_text('{}\n')
        original = system.read_text
        def read(path, **options):
            if path == gone:
                raise system.Absent(path)
            return original(path, **options)
        with mock.patch.object(system, 'read_text', read):
            self.assertEqual(sources._from_lanes(root, 'till-bot'), ['adapter:jsonl'])

    def test_a_valid_positive_source_uses_the_record_without_a_lane_scan(self):
        root = self.make_forest(FILES)
        log(root, group='night-shift')
        scope = search.for_reader(load_config(root), search.registry_mod.build(root), 'till-bot', root=root)
        sources._SCANNED.clear()
        with system.watching() as trace:
            self.assertEqual(search.source_for(root, scope, 'group:night-shift'), 'group:night-shift')
        self.assertEqual([p for name, p in trace.calls if name == 'read' and '06 istm' in p], [])

    def test_the_key_record_is_an_exact_rebuild_of_complete_canonical_lines(self):
        root = self.make_forest(FILES)
        first = log(root, key='canonical-key')
        token = first['turn_digest']
        lane = root / first['path']
        lane.write_bytes(b'not json\n[]\n' + lane.read_bytes())
        with lane.open('ab') as stream:
            stream.write(intake._encode({'id': 'later-copy', 'session': 'shift',
                                                          'turn_digest': token}).encode())
            stream.write(b'{"turn_digest":"torn')
        self.assertEqual(turn_keys.scan(root, 'till-bot'), {'shift': {token: ['2051-03-02', 3, first['id']]}})
        turn_keys.save(root, 'till-bot')
        saved = json.loads(turn_keys.path(root, 'till-bot').read_text())
        expected = {'lanes': sources.lanes_before(root, 'till-bot'),
                    'keys': {'shift': {token: ['2051-03-02', 3, first['id']]}}}
        expected['seal'] = sources._seal(expected['lanes'], expected['keys'])
        self.assertEqual(saved, expected)
        with system.watching() as trace:
            self.assertEqual(turn_keys.load(root, 'till-bot'), expected['keys'])
        self.assertEqual([p for name, p in trace.calls if name == 'read' and '06 istm' in p], [])
        for field in ('lanes', 'seal', 'keys'):
            damaged = copy.deepcopy(saved)
            damaged[field] = {} if field != 'seal' else 'damaged'
            turn_keys.path(root, 'till-bot').write_text(json.dumps(damaged))
            with system.watching() as trace:
                self.assertEqual(turn_keys.load(root, 'till-bot'), expected['keys'])
            self.assertTrue([p for name, p in trace.calls if name == 'read' and '06 istm' in p])

    def test_a_join_never_persists_the_answer_envelope_in_the_lane(self):
        root = self.make_forest(FILES)
        first = log(root, door='mcp')
        second = log(root, door='cli')
        self.assertEqual(second['id'], first['id'])
        stored = records(root)[-1]
        expected_keys = {'at', 'id', 'origin', 'role', 'session', 'text', 'source', 'sources', 'arrivals'}
        self.assertEqual(set(stored), expected_keys)
        self.assertEqual(stored['sources'], ['door:mcp', 'door:cli'])
        self.assertEqual([row['source']['door'] for row in stored['arrivals']], ['mcp', 'cli'])

    def test_conflicting_words_are_refused_only_after_the_whole_lane_was_read(self):
        root = self.make_forest(FILES)
        first = log(root, key='conflict-key')
        # A second day without this key proves the decision inspected all canonical days.
        path = root / '06 istm/till-bot/2051-03-03.jsonl'
        path.write_text(intake._encode({'id': 'other-day', 'text': 'fictional crumb'}))
        turn_keys.save(root, 'till-bot')
        from memory_forest.errors import MemoryForestError
        with system.watching() as trace, self.assertRaises(MemoryForestError) as caught:
            log(root, key='conflict-key', text='different words')
        self.assertEqual(caught.exception.code, 'turn_id_reused')
        self.assertEqual(caught.exception.details['id'], first['id'])
        self.assertIn(('read', str(path)), trace.calls)


class ReadReceiptTests(ForestTestCase):
    def test_an_envelope_preserves_the_complete_read_contract(self):
        scope = search.Scope('till-bot', ('bakery',), ('till-bot',))
        section = {'address': 'a#turn-a', 'heading': 'Turn', 'kind': 'turn', 'line_start': 2,
                   'line_end': 3, 'text': 'fictional crumbs', 'why': ['word:crumbs'], 'agent': 'till-bot'}
        pages = [{'path': 'a', 'tree': 'bakery', 'sections': [section], 'why': ['word:crumbs']},
                 {'path': 'b', 'tree': '', 'sections': [{**section, 'agent': ''}], 'why': []}]
        wanted_section = {'address': 'a#turn-a', 'heading': 'Turn', 'kind': 'turn', 'lines': [2, 3],
                          'text': 'fictional crumbs', 'why': ['word:crumbs']}
        wanted = {'consulted': ['bakery'], 'lanes': ['till-bot'], 'ok': True, 'reader': 'till-bot',
                  'results': [{'path': 'a', 'sections': [wanted_section], 'tree': 'bakery', 'why': ['word:crumbs']},
                              {'path': 'b', 'sections': [wanted_section], 'tree': '', 'why': []}],
                  'trees': ['bakery'], 'vectors': 'off'}
        self.assertEqual(search._envelope(scope, pages), wanted)
        self.assertEqual(search._envelope(scope, pages, question='crumbs'), {**wanted, 'question': 'crumbs'})

    def test_provenance_reads_only_selected_lane_turns_and_keeps_every_arrival(self):
        root = self.make_forest(FILES)
        first = log(root, door='mcp')
        log(root, door='cli')
        row = records(root)[-1]
        path = root / first['path']
        path.write_text('not json\n[]\n' + intake._encode(row))
        payload = {'results': [{'path': first['path'], 'sections': [{'lines': [1, 3]}]},
                               {'path': '06 istm/oven-bot/2051-03-02.jsonl', 'sections': [{'lines': [1, 2]}]},
                               {'path': '04 stm/bakery/ovens/floor.md', 'sections': [{'lines': [1, 3]}]}]}
        from unittest import mock
        with mock.patch.object(search, 'load_config', wraps=search.load_config) as settings:
            answer = search._provenance(root, payload)
        self.assertEqual(settings.call_count, 1)
        self.assertEqual(answer['results'][0]['sections'][0]['arrivals'], row['arrivals'])
        self.assertNotIn('arrivals', answer['results'][2]['sections'][0])
        payload['results'][0]['sections'][0]['kept_lines'] = [2]
        self.assertEqual(search._provenance(root, payload)['results'][0]['sections'][0]['arrivals'], [])
