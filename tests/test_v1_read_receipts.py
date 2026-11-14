"""Chronology, source selection and read refusals describe the invented lanes themselves."""
import json
from datetime import datetime, timezone
from unittest import mock

import bounds
from memory_forest import intake, search, timeline
from memory_forest.errors import MemoryForestError
from test_v1_find import FILES
from v1_support import ForestTestCase

LANE = '06 istm/oven-bot/2051-03-02.jsonl'
DAILY = '05 daily/2051-03-02.md'


class ReadTests(ForestTestCase):
    def forest(self):
        return self.make_forest({**FILES, DAILY: '# 2051-03-02\n\n<!-- block: oven-bot 2051-03-02T10:00 -->\n\n'
                                               '## 10:00 oven-bot\n\nA fictional oven note.\n'})

    def test_subject_uses_the_earliest_turn_orders_pages_and_obeys_source_scope_and_limit(self):
        root = self.forest()
        body = timeline.subject(root, 'oven-bot', reader='front-desk')
        self.assertEqual([page['path'] for page in body['results'][:2]], [LANE, DAILY])
        self.assertEqual(body['subject'], 'oven-bot')
        self.assertEqual(body['lanes'], ['oven-bot'])
        self.assertEqual(body['trees'], ['oven-bot'])
        self.assertEqual([page['path'] for page in timeline.subject(root, 'oven-bot', reader='till-bot')['results']], [DAILY])
        limited = timeline.subject(root, 'oven-bot', reader='front-desk', limit=1)
        self.assertEqual([page['path'] for page in limited['results']], [LANE])
        filtered = timeline.subject(root, 'oven-bot', reader='front-desk', source='adapter:jsonl')
        self.assertEqual([page['path'] for page in filtered['results']], [LANE])
        sections = filtered['results'][0]['sections']
        self.assertEqual([section['heading'] for section in sections], ['oven-bot:shift:cooling', 'oven-bot:shift:morning'])
        self.assertEqual([section['lines'] for section in sections], [[1, 1], [2, 2]])
        self.assertTrue(all(section['kind'] == 'turn' for section in sections))
        self.assertEqual([section['arrivals'][0]['source']['place'] for section in sections], ['1', '2'])

    def test_time_queries_keep_each_pages_earliest_time_and_exclude_the_other_days(self):
        root = self.forest()
        body = timeline.query(root, '2051-03-02', reader='front-desk')
        paths = [page['path'] for page in body['results']]
        self.assertLess(paths.index(LANE), paths.index(DAILY))
        self.assertNotIn('05 daily/2051-03-01.md', paths)
        self.assertEqual(body['phrase'], '2051-03-02')
        self.assertEqual([page['path'] for page in timeline.query(root, '2051-03-02', reader='till-bot')['results']],
                         ['04 stm/bakery/ovens/deck-oven.md', DAILY, '06 istm/till-bot/2051-03-02.jsonl'])

    def test_addressed_lane_reads_keep_the_complete_section_and_provenance(self):
        root = self.forest()
        body = search.read(root, LANE + '#oven-bot:shift:morning', reader='front-desk')
        section = body['results'][0]['sections'][0]
        wanted = {'address': LANE + '#oven-bot:shift:morning', 'heading': 'oven-bot:shift:morning', 'kind': 'read',
                  'lines': [2, 2], 'text': FILES[LANE].splitlines()[1], 'why': ['read'],
                  'arrivals': [{'at': '2051-03-02T08:00:00+00:00', 'origin': 'interactive',
                                'source': {'adapter': 'jsonl', 'place': '2', 'transcript': 'shift'},
                                'sources': ['adapter:jsonl']}]}
        self.assertEqual(section, wanted)
        self.assertEqual(body['results'][0]['tree'], 'oven-bot')
        self.assertEqual(body['results'][0]['why'], ['read'])
        self.assertEqual(body['lanes'], ['oven-bot'])

    def test_read_argument_refusals_keep_their_exact_details(self):
        root = self.forest()
        cases = ((lambda: search.query(root, '', reader='till-bot'), 'search_empty', {}),
                 (lambda: search.query(root, 'crumbs', limit=0), 'search_limit', {'limit': 0}),
                 (lambda: search.read(root, LANE, reader='front-desk', lines=(0, 1)), 'read_lines', {}),
                 (lambda: search.read(root, LANE, reader='front-desk', lines=(9, 10)), 'read_lines_empty',
                  {'lines': '9-10', 'path': LANE}))
        for call, code, details in cases:
            with self.assertRaises(MemoryForestError) as caught:
                call()
            self.assertEqual((caught.exception.code, caught.exception.details), (code, details))
        intake.append_owned(root, 'till-bot', 'a fictional CLI turn', role='agent', origin='interactive',
                            session='shift', door='cli', at=datetime(2051, 3, 2, tzinfo=timezone.utc))
        with self.assertRaises(MemoryForestError) as caught:
            search.read(root, LANE, reader='front-desk', source='door:cli')
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('read_lines_empty', {'path': LANE, 'source': 'door:cli'}))

    def test_reads_retain_read_only_and_stale_state(self):
        root = self.forest()
        search.build(root)
        with mock.patch.object(search, 'refresh_lanes_and_daily', return_value=[LANE]), \
                mock.patch.object(search, 'is_read_only', return_value=True):
            for call in (lambda: search.query(root, 'trays', reader='front-desk'),
                         lambda: timeline.query(root, '2051-03-02', reader='front-desk'),
                         lambda: timeline.subject(root, 'oven-bot', reader='front-desk')):
                body = call()
                self.assertTrue(body['read_only'])
                self.assertEqual(body['stale'], [LANE])

    def test_an_unavailable_index_is_busy_in_each_reader_and_other_refusals_are_never_retried(self):
        root = self.forest()
        def no_connection(root, read, **options):
            return read(None)
        with mock.patch.object(search, '_checked_read', no_connection), \
                mock.patch.object(search, 'refresh_lanes_and_daily', return_value=[]):
            for call in (lambda: search.query(root, 'trays', reader='front-desk'),
                         lambda: timeline.query(root, '2051-03-02', reader='front-desk'),
                         lambda: timeline.subject(root, 'oven-bot', reader='front-desk')):
                with self.assertRaises(MemoryForestError) as caught:
                    call()
                self.assertEqual(caught.exception.code, 'index_busy')
        clock = iter([0, search.READER_TIMEOUT_SECONDS + 1])
        with mock.patch.object(search, '_checked_read', side_effect=MemoryForestError('read_lines', 'invented')), \
                mock.patch.object(search, 'refresh_lanes_and_daily', return_value=[]):
            with self.assertRaises(MemoryForestError) as caught:
                search.query(root, 'trays', reader='front-desk', clock=lambda: next(clock))
        self.assertEqual(caught.exception.code, 'read_lines')
