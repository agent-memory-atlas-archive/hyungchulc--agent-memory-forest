"""The mutation report keeps an assertion, a bound, and a survivor distinct."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import bounds

_SPEC = importlib.util.spec_from_file_location("forest_mutations", Path(__file__).resolve().parents[1] / "scripts/mutate_guards.py")
tool = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = tool
_SPEC.loader.exec_module(tool)


class MutationToolTests(unittest.TestCase):
    def test_a_set_entry_is_removed_and_has_a_distinct_id(self):
        import ast
        source = b'NAMES = frozenset({"read", "write", "enter"})\n'
        node = ast.parse(source).body[0]
        mutations = tool.make_mutants("tests/probe.py:NAMES", source, tool.line_starts(source), node, 1)
        self.assertEqual(len(mutations), 3)
        self.assertEqual(len({m.ident for m in mutations}), 3)
        for mutation in mutations:
            scope = {}
            exec(source[:mutation.start] + mutation.text + source[mutation.end:], scope)
            self.assertEqual(len(scope["NAMES"]), 2)

    def test_a_multiline_change_is_one_markdown_table_row(self):
        record = {"target": "loop", "tests": ["probe"], "mutants": 1,
                  "results": {"m": {"kind": "loop", "line": 1, "excerpt": "if done:\n break",
                                    "change": "if done:\n pass", "result": "timeout", "seconds": 1}}}
        text = tool.report([record])
        self.assertNotIn("\n break", text)
        self.assertNotIn("\n pass", text)
        rows = [line for line in text.splitlines() if "if done:" in line]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].count("|"), 9)

    def test_timeout_is_never_an_assertion_kill(self):
        records = []
        for result in ("killed", "timeout", "survived"):
            records.append({"target": result, "tests": ["test_probe"], "mutants": 1,
                            "results": {"m": {"result": result, "line": 1, "kind": "raise",
                                             "excerpt": "raise", "change": "pass", "seconds": 0}}})
        text = tool.report(records)
        self.assertIn("assertion", text)
        self.assertIn("bound", text)
        self.assertIn("survived", text)
        totals = tool._totals(records[1], list(records[1]["results"].values()))
        self.assertIn("0/1", totals)

    def test_faulthandler_exit_is_a_bound_even_before_the_outer_timeout(self):
        group = tool.Group("tests/probe.py:f", "tests/probe.py", ("probe",), b"x=1\n", "key", ())
        mutant = tool.Mutant(group.target, "boolean", 1, 0, "1", "0", 2, 3, b"0")
        workspace = mock.Mock()
        workspace.run.return_value = ("failed", 0.1)
        workspace.output.return_value = "Timeout (0:00:01)!\nThread stacks\n"
        workspace.module.return_value = mock.Mock()
        answer = tool.try_mutant(workspace, group, mutant, 5)
        self.assertEqual(answer["result"], "timeout")
        self.assertEqual(workspace.run.call_count, 1)
