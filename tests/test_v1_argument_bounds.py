"""Direct synthetic arguments and byte streams keep the shared door's refusals precise."""
from __future__ import annotations

import bounds

from io import BytesIO, StringIO
import sys
from unittest import mock

from memory_forest import intake
from memory_forest.errors import MemoryForestError
from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS
from v1_support import ForestTestCase


class ArgumentBoundaryTests(ForestTestCase):
    def test_a_text_stdin_without_a_byte_buffer_keeps_the_same_utf8_byte_bound(self):
        from memory_forest import cli_find, mcp
        with mock.patch.object(sys, 'stdin', StringIO('fictional trays\n')):
            self.assertEqual(cli_find._log_text(), 'fictional trays\n')
        text = '★' * (mcp.TURN_MAX_BYTES // 3 + 1)
        with mock.patch.object(sys, 'stdin', StringIO(text)), self.assertRaises(MemoryForestError) as caught:
            cli_find._log_text()
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('log_too_large', {'limit': mcp.TURN_MAX_BYTES}))

    def test_raw_stream_bounds_utf8_and_both_deadline_exits_keep_their_details(self):
        ended = mock.Mock()
        ended.read.side_effect = [b'', AssertionError('the reader asked again after EOF')]
        ended.fileno = None
        self.assertEqual(intake.read_bounded(ended, limit=3, seconds=None), '')
        ended.read.assert_called_once_with(4)
        self.assertEqual(intake.read_bounded(BytesIO('★'.encode()), limit=3, seconds=None), '★')
        self.assertEqual(intake.read_bounded(BytesIO(b''), limit=0, seconds=None), '')
        for raw, code, details in ((b'abcd', 'log_too_large', {'limit': 3}),
                                   (b'\xff', 'invalid_utf8', {})):
            with self.assertRaises(MemoryForestError) as caught:
                intake.read_bounded(BytesIO(raw), limit=3, seconds=None)
            self.assertEqual((caught.exception.code, caught.exception.details), (code, details))
        for clock, stand_in in ((lambda: 0.0, True), (iter((0.0, 2.0)).__next__, False)):
            with mock.patch.object(intake, '_read_some', return_value=None) as read:
                with self.assertRaises(MemoryForestError) as caught:
                    intake.read_bounded(BytesIO(), limit=3, seconds=1, clock=clock)
                self.assertEqual((caught.exception.code, caught.exception.details),
                                 ('log_read_timeout', {'seconds': 1}))
                self.assertEqual(read.called, stand_in)

    def test_invalid_direct_write_arguments_keep_the_exact_rejected_field(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        for changed, code, details in (({'agent': 'bad id'}, 'intake_agent_invalid', {'agent': 'bad id'}),
                                       ({'role': 'invalid'}, 'intake_role_invalid', {'role': 'invalid'}),
                                       ({'origin': 'invalid'}, 'intake_origin_invalid', {'origin': 'invalid'}),
                                       ({'text': ' '}, 'intake_text_empty', {})):
            args = {'agent': 'till-bot', 'text': 'The fictional trays were counted.',
                    'role': 'agent', 'origin': 'interactive', **changed}
            with self.assertRaises(MemoryForestError) as caught:
                intake.append_owned(root, **args)
            self.assertEqual((caught.exception.code, caught.exception.details), (code, details))


class ToolArgumentBoundaryTests(ForestTestCase):
    def test_each_schema_field_keeps_its_type_semantics_and_precise_refusal(self):
        from memory_forest import mcp
        import copy

        for tool in (*mcp._TOOLS, *mcp._GROUP_TOOLS):
            name = tool['name']
            baseline = {key: 'demo' for key in tool['inputSchema'].get('required', ())}
            if name == 'memory_timeline':
                baseline['phrase'] = 'demo'
            self.assertIsNone(mcp._check_arguments(name, baseline, tool))

            def refused(arguments, message):
                with self.assertRaises(mcp._BadArguments) as caught:
                    mcp._check_arguments(name, arguments, tool)
                self.assertIn(message, str(caught.exception), (name, arguments))

            refused({**baseline, 'surprise': 'demo'}, 'not part of this tool')
            for forbidden in ('role', 'origin', 'reader', 'as', 'named'):
                refused({**baseline, forbidden: 'demo'}, 'server sets the role')
            for key in tool['inputSchema'].get('required', ()):
                refused({field: value for field, value in baseline.items() if field != key}, 'required argument')
            for key, spec in tool['inputSchema']['properties'].items():
                if spec.get('type') == 'string':
                    for invalid in (None, 7, True, [], {}):
                        candidate = {**baseline, key: invalid}
                        if key == 'subject' and name == 'memory_timeline':
                            candidate.pop('phrase')
                        refused(candidate, {'agent': 'caller', 'text': 'turn needs',
                                                           'lines': 'Lines', 'phrase': 'phrase',
                                                           'subject': 'subject', 'turn_id': 'turn id'}.get(key, key))
                    good = {**baseline, key: '1-2' if key == 'lines' else 'demo'}
                    if key == 'subject' and name == 'memory_timeline':
                        good.pop('phrase')
                    self.assertIsNone(mcp._check_arguments(name, good, tool))
            if name == 'memory_search':
                for value in (True, False, 0, -1, 1.5, '1', None):
                    refused({**baseline, 'limit': value}, 'integer of at least 1')
                self.assertIsNone(mcp._check_arguments(name, {**baseline, 'limit': 1}, tool))
            if name == 'memory_read':
                for value in ('bad', '0-1', '1-x'):
                    refused({**baseline, 'lines': value}, 'Lines')
            if name == 'memory_timeline':
                refused({key: value for key, value in baseline.items() if key != 'phrase'}, 'one of them')
                refused({**baseline, 'subject': 'demo'}, 'one of them')
                for key in ('phrase', 'subject'):
                    good = {field: value for field, value in baseline.items() if field != 'phrase'}
                    for value in ('', ' '):
                        refused({**good, key: value}, key)
            for key, fragment in (('agent', 'caller'), ('text', 'turn needs')):
                if key in tool['inputSchema']['properties']:
                    for value in ('', ' '):
                        refused({**baseline, key: value}, fragment)
            # A schema cannot grant the caller a role/origin: the server owns both fields.
            for field in ('role', 'origin'):
                altered = copy.deepcopy(tool)
                altered['inputSchema']['properties'][field] = {'type': 'string'}
                with self.assertRaises(mcp._BadArguments) as caught:
                    mcp._check_arguments(name, {**baseline, field: 'demo'}, altered)
                self.assertIn('server sets the role', str(caught.exception))


class QueryBoundaryTests(ForestTestCase):
    def test_query_validation_and_missing_connection_keep_their_own_refusals(self):
        from memory_forest import search
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        for question, limit, code, details in ((' ', 1, 'search_empty', {}),
                                               ('trays', 0, 'search_limit', {'limit': 0})):
            with self.assertRaises(MemoryForestError) as caught:
                search.query(root, question, limit=limit)
            self.assertEqual((caught.exception.code, caught.exception.details), (code, details))
        with mock.patch.object(search, 'refresh_lanes_and_daily', return_value=0), \
                mock.patch.object(search, '_checked_read', side_effect=lambda root, fn: fn(None)):
            with self.assertRaises(MemoryForestError) as caught:
                search.query(root, 'trays')
            self.assertEqual((caught.exception.code, caught.exception.next_step), ('index_busy', 'retry'))
        refusal = MemoryForestError('search_empty', 'Search needs the words to look for.', next_step='fix')
        ticks = iter((0.0, 40.0))
        with mock.patch.object(search, 'refresh_lanes_and_daily', return_value=0), \
                mock.patch.object(search, '_checked_read', side_effect=refusal):
            with self.assertRaises(MemoryForestError) as caught:
                search.query(root, 'trays', clock=ticks.__next__)
            self.assertIs(caught.exception, refusal)
