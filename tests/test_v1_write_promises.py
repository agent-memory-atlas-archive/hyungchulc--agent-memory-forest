"""The public promises name the behavior the durable-turn regressions exercise."""
import importlib
import unittest
from pathlib import Path

import bounds
from memory_forest import cli, mcp

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'The lane commit fixes `stored` and the turn id; ids, caller-key and source records, the tree, the index and connection bookkeeping are derived, with `pending` and a reason when unfinished.'
CRASH = 'If a crash loses the answer, the caller cannot know whether the turn was stored; repeating the call with its key is safe.'
BOUND = "For unchanged lane and record stamps, a reader scans an agent's lanes at most once per process, and persists that scan when it can."


class PromiseTests(unittest.TestCase):
    def test_each_write_and_crash_promise_names_its_guard(self):
        for document in ('README.md', 'docs/tools.md', 'docs/multi-agent.md', 'CHANGELOG.md'):
            self.assertIn(COMMIT, (ROOT / document).read_text(), document)
        for document in ('README.md', 'docs/tools.md', 'docs/multi-agent.md'):
            self.assertIn(CRASH, (ROOT / document).read_text(), document)
        for document in ('docs/tools.md', 'docs/multi-agent.md'):
            self.assertIn(BOUND, (ROOT / document).read_text(), document)
        # Link each promise to a real, discovered unittest, not a historical report's claim.
        names = ('test_v1_commit_sweep.CommitSweepTests.test_generated_writers_keep_the_answer_at_the_commit_and_repair_their_records',
                 'test_v1_durable_turns.KeyTests.test_an_answer_lost_to_a_crash_is_safely_asked_again_with_its_key',
                 'test_v1_durable_turns.SourceTruthTests.test_read_only_stale_record_is_scanned_once_and_then_no_lane_reads')
        for name in names:
            self.assertEqual(unittest.defaultTestLoader.loadTestsFromName(name).countTestCases(), 1)

    def test_key_schema_help_and_docs_have_the_same_lifetime(self):
        tool = next(tool for tool in mcp._TOOLS if tool['name'] == 'memory_log')
        self.assertIn('no lifetime', tool['inputSchema']['properties']['turn_id']['description'])
        parser = cli.build_forest_parser()
        sub = next(a for a in parser._actions if hasattr(a, 'choices') and isinstance(a.choices, dict))
        self.assertIn('no lifetime', sub.choices['log'].format_help())
        for document in ('docs/tools.md', 'docs/multi-agent.md'):
            text = (ROOT / document).read_text()
            self.assertIn('no lifetime', text)
            self.assertIn('HMAC-SHA256', text)
            self.assertNotIn('the day before', text)
        tools = (ROOT / 'docs/tools.md').read_text()
        self.assertIn('Masking repair converts legacy raw', tools)
        self.assertIn('counts them as rewritten, and preserves their identity.', tools)
        # These prove keys survive maintenance and days, with conflict refusals still grounded in the lane.
        for name in ('test_the_reviews_sign_in_key_masking_and_repeat_sequence',
                     'test_the_reviews_two_days_later_same_and_other_words_sequence'):
            self.assertTrue(hasattr(importlib.import_module('test_v1_durable_turns').KeyTests, name))

    def test_navigation_and_settings_attributions_are_precise(self):
        text = (ROOT / 'docs/tools.md').read_text()
        self.assertIn('`memory_navigate` uses `lines: [first, last]` in place of its former `line_start` and `line_end` fields.', text)
        self.assertIn('Settings entry problems are reported by `forest check`, not by `forest agents`.', text)
        multi = (ROOT / 'docs/multi-agent.md').read_text()
        self.assertIn('returns, or answers `pending`', multi)
        self.assertIn('with a reason when tree work cannot finish. The next write retries it', multi)
        contract = (ROOT / 'docs/contract.md').read_text()
        self.assertIn('Except for an exact stored-id selection, two agents are one agent', contract)
        self.assertIn('An exact stored id intentionally selects its stored agent even when', contract)
        for sentence in ('source label,', 'door, adapter, group, place and time', 'both sides'):
            self.assertIn(sentence, multi)


if __name__ == '__main__':
    unittest.main()
