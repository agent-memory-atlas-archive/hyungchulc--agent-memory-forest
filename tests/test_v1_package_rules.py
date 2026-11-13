"""Rules that hold for every module of the package, read from the syntax tree.

Nothing here names a person or a forest.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import memory_forest
from memory_forest import cli, derived, errors, mcp, search

import codes_walk
from test_v1_door import agent_commands


PACKAGE = Path(memory_forest.__file__).resolve().parent


def modules() -> list[tuple[str, ast.Module]]:
    """Every module of the package, by its path under the package."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        found.append((str(path.relative_to(PACKAGE)), ast.parse(path.read_text(encoding="utf-8"))))
    return found


class PackageRuleTests(unittest.TestCase):
    def test_the_walk_reads_every_module(self) -> None:
        names = [name for name, _tree in modules()]
        self.assertIn("search.py", names)
        self.assertIn("adapters/jsonl.py", names)
        self.assertGreater(len(names), 40)

    def test_no_object_is_kept_beside_another_by_its_address(self) -> None:
        """A flag or a record keyed by ``id()`` outlives the object and is found again by a stranger."""
        calls = [
            f"{name}:{node.lineno}"
            for name, tree in modules()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "id"
        ]
        self.assertEqual(calls, [])

    def test_no_default_argument_names_the_first_index_file(self) -> None:
        """The current index file is read from the pointer every time. A default would freeze the first name."""
        first = {search.INDEX_FILENAME, search.INDEX_RELATIVE}
        found = []
        for name, tree in modules():
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                for default in [*node.args.defaults, *node.args.kw_defaults]:
                    if default is None:
                        continue
                    literal = isinstance(default, ast.Constant) and default.value in first
                    named = isinstance(default, (ast.Name, ast.Attribute)) and (
                        getattr(default, "id", None) in {"INDEX_FILENAME", "INDEX_RELATIVE"}
                        or getattr(default, "attr", None) in {"INDEX_FILENAME", "INDEX_RELATIVE"}
                    )
                    if literal or named:
                        found.append(f"{name}:{default.lineno}")
        self.assertEqual(found, [])


# ─────────────────────────── the codes that can leave ───────────────────────────


def _trees(**sources: str) -> dict[str, ast.Module]:
    """Made-up modules for the walk: ``{"name.py": source}``, with the refusal type defined in errors.py."""
    base = {"errors.py": "class MemoryForestError(Exception):\n    pass\n"}
    return {name: ast.parse(text) for name, text in {**base, **sources}.items()}


def document_rows() -> dict[str, tuple[str, str, str]]:
    """``docs/refusals.md``: code, next move, command and the "When" cell of each row."""
    rows: dict[str, tuple[str, str, str]] = {}
    text = (PACKAGE.parents[1] / "docs" / "refusals.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        rows[cells[0].strip("`")] = (cells[1].strip("`"), cells[2].strip("`"), cells[3])
    return rows


class RefusalCodeTests(unittest.TestCase):
    """The codes that can leave are one closed set: the walk (``codes_walk``), the table and the document agree."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.walk = codes_walk.walk()
        cls.codes = cls.walk.codes()

    def test_the_walk_sees_every_class_of_refusal_at_any_depth(self) -> None:
        self.assertIn("ArchiveProofError", self.walk.classes)
        trees = _trees(**{"deep.py": (
            "from .errors import MemoryForestError\n"
            "class First(MemoryForestError):\n    pass\n"
            "class Second(First):\n    pass\n"
            "def refuse():\n    raise Second('second_kind', 'A refusal two classes down.', next_step='report')\n"
        )})
        result = codes_walk.walk(trees, listed={})
        self.assertEqual(result.classes, {"MemoryForestError", "First", "Second"})
        self.assertEqual(set(result.codes()), {"second_kind"})

    def test_a_code_from_a_parameter_is_followed_to_every_caller_or_refused(self) -> None:
        followed = _trees(**{"follow.py": (
            "import functools\n"
            "from .errors import MemoryForestError\n"
            "def helper(code):\n    return MemoryForestError(code, 'made by a helper')\n"
            "class Shop:\n    def refuse(self, code):\n        raise MemoryForestError(code, 'made by a method')\n"
            "def callers(shop):\n"
            "    helper('by_name')\n"
            "    shop.refuse('by_method')\n"
            "    functools.partial(helper, 'by_partial')()\n"
            "def each():\n"
            "    for code in ('first_kind', 'second_kind'):\n"
            "        raise MemoryForestError(code, 'one of two')\n"
        )})
        result = codes_walk.walk(followed, listed={})
        self.assertEqual(result.unresolved, [])
        self.assertEqual(set(result.codes()), {"by_name", "by_method", "by_partial", "first_kind", "second_kind"})
        # Each case the walk cannot follow, with the reason it gives; no site is kept for it.
        refused = {
            "a parameter no caller fills": ("def helper(code):\n    return MemoryForestError(code, 'x')\n",
                                            "no caller fills helper's code"),
            "a callback": (
                "def helper(code):\n    return MemoryForestError(code, 'x')\n"
                "def later(callback):\n    return callback('from_a_callback')\n"
                "def caller():\n    helper('by_name')\n    later(helper)\n",
                "helper is used as a value",
            ),
            "a partial filled later": (
                "import functools\n"
                "def helper(words, code):\n    return MemoryForestError(code, words)\n"
                "def caller():\n    functools.partial(helper, 'the words')('filled_later')\n",
                "helper's code is filled after a partial application",
            ),
            "a code read from a table": (
                "TABLE = {'a': 'from_a_table'}\n"
                "def refuse(key):\n    raise MemoryForestError(TABLE[key], 'x')\n",
                "TABLE[key]",
            ),
            "a move that is not a literal": (
                "def refuse(command):\n"
                "    raise MemoryForestError('one_code', 'x', next_step='run' if command else 'report')\n",
                "the next move is not a literal",
            ),
            "a refusal made with no code": ("def refuse():\n    raise MemoryForestError()\n", "the code: nothing"),
            "a code named outside a function": ("CODE = 'at_the_top'\nREFUSAL = MemoryForestError(CODE, 'x')\n",
                                                "the code: CODE"),
            "a code passed round a loop of calls": (
                "def helper(code, again=False):\n"
                "    if again:\n        helper(code)\n"
                "    raise MemoryForestError(code, 'x')\n"
                "def caller():\n    helper('once')\n",
                "too deep",
            ),
            "a code unpacked from a pair": (
                "def refuse(pair):\n    code, words = pair\n    raise MemoryForestError(code, words)\n",
                "code unpacked",
            ),
            "a code from a loop over a name": (
                "def refuse(codes):\n    for code in codes:\n        raise MemoryForestError(code, 'x')\n",
                "code from a loop",
            ),
        }
        for label, (source, reason) in refused.items():
            with self.subTest(case=label):
                text = "from .errors import MemoryForestError\n" + source
                result = codes_walk.walk(_trees(**{"case.py": text}), listed={})
                self.assertEqual(len(result.unresolved), 1, result.unresolved)
                self.assertIn(reason, result.unresolved[0])
                self.assertEqual(result.sites, [])

    def test_a_call_from_another_module_is_followed_however_it_was_imported(self) -> None:
        trees = _trees(**{
            "shop.py": (
                "from .errors import MemoryForestError\n"
                "def helper(code):\n    return MemoryForestError(code, 'made by a helper')\n"
            ),
            "counter.py": (
                "from . import shop\n"
                "from . import shop as kept\n"
                "from .shop import helper\n"
                "from .shop import helper as made\n"
                "from memory_forest import shop as rooted\n"
                "def callers():\n"
                "    shop.helper('through_the_module')\n"
                "    kept.helper('through_an_alias')\n"
                "    helper('by_its_name')\n"
                "    made('by_another_name')\n"
                "    rooted.helper('through_the_package')\n"
                "def plain():\n"
                "    import memory_forest.shop\n"
                "    memory_forest.shop.helper('through_a_plain_import')\n"
            ),
        })
        result = codes_walk.walk(trees, listed={})
        self.assertEqual(result.unresolved, [])
        self.assertEqual(set(result.codes()), {"through_the_module", "through_an_alias", "by_its_name",
                                               "by_another_name", "through_the_package", "through_a_plain_import"})

    def test_a_method_and_a_function_of_one_name_are_told_apart(self) -> None:
        # The variable has the module's name: only an import makes a name a module.
        trees = _trees(**{"shop.py": (
            "from .errors import MemoryForestError\n"
            "def refuse(code):\n    raise MemoryForestError(code, 'made by the function')\n"
            "class Shop:\n    def refuse(self, code):\n        raise MemoryForestError(code, 'made by the method')\n"
            "def callers(shop):\n    refuse('by_the_function')\n    shop.refuse('by_the_method')\n"
        )})
        result = codes_walk.walk(trees, listed={})
        self.assertEqual(result.unresolved, [])
        self.assertEqual({message: site.codes for site in result.sites for message in site.messages},
                         {"made by the function": {"by_the_function"}, "made by the method": {"by_the_method"}})

    def test_a_message_is_read_in_every_form_the_walk_knows(self) -> None:
        trees = _trees(**{"words.py": (
            "from .errors import MemoryForestError\n"
            "WORD = 'tray'\n"
            "AT_THE_TOP = MemoryForestError('at_the_top', f'the {WORD} is gone')\n"
            "def no_words():\n    raise MemoryForestError('no_words')\n"
            "def branch(heavy):\n"
            "    raise MemoryForestError('branched', f\"the tray is {'full' if heavy else 'empty'}\")\n"
            "def cycle():\n"
            "    word = 'first'\n    other = word\n    word = other\n"
            "    raise MemoryForestError('cycled', f'the word is {word}')\n"
            "def helper(words, again=False):\n"
            "    if again:\n        helper(words)\n"
            "    raise MemoryForestError('looped', words)\n"
            "def caller():\n    helper('said once')\n"
            "SHELVES: dict[str, str] = {'up': 'the upper shelf', 'down': 'the lower shelf'}\n"
            "def shelved(key):\n    raise MemoryForestError('shelved', f'on {SHELVES[key]}')\n"
            "def named():\n    raise MemoryForestError(code='by_keyword', message='a keyword message', next_step=None)\n"
        )})
        result = codes_walk.walk(trees, listed={})
        self.assertEqual(result.unresolved, [])
        self.assertEqual({code: site.messages for site in result.sites for code in site.codes}, {
            "at_the_top": {"the … is gone"},  # a name outside a function is not followed
            "shelved": {"on the upper shelf", "on the lower shelf"},  # a table written in the module
            "by_keyword": {"a keyword message"},
            "no_words": set(),
            "branched": {"the tray is full", "the tray is empty"},
            "cycled": {"the word is …"},  # names assigned round a loop end at the depth
            "looped": {"said once", "…"},  # a message passed round a loop of calls ends at the depth
        })
        self.assertEqual({code: site.moves for site in result.sites for code in site.codes}["by_keyword"], {None})

    def test_a_construction_the_walk_cannot_name_is_refused(self) -> None:
        """An alias, type(), a class kept in a variable, a move passed through **, a dict with code, and an
        assignment to a refusal that already exists. Each is an error and no site is kept for it."""
        probes = {
            "an alias": (
                "from .errors import MemoryForestError as MFE\n"
                "def refuse():\n    raise MFE('zz', 'an alias')\n",
                "an alias",
            ),
            "type()": (
                "from .errors import MemoryForestError\n"
                "def refuse():\n"
                "    made = type('X', (MemoryForestError,), {})\n"
                "    raise made('zz', 'from type')\n",
                "type()",
            ),
            "a class kept in a variable": (
                "from .errors import MemoryForestError\n"
                "def refuse():\n"
                "    cls = MemoryForestError\n"
                "    raise cls('zz', 'from a variable')\n",
                "a class kept in a variable",
            ),
            "a move passed through **": (
                "from .errors import MemoryForestError\n"
                "def refuse():\n"
                "    raise MemoryForestError('zz', 'starred', **{'next_step': 'retry'})\n",
                "passed through **",
            ),
            "a dict with code": (
                "def refuse():\n    return {'code': 'zz', 'message': 'a dict'}\n",
                "a dict with code",
            ),
            "an assignment after construction": (
                "from .errors import MemoryForestError\n"
                "def refuse():\n"
                "    made = MemoryForestError('zz', 'built')\n"
                "    made.code = 'other'\n",
                "assigns the code",
            ),
        }
        for label, (source, reason) in probes.items():
            with self.subTest(case=label):
                result = codes_walk.walk(_trees(**{"case.py": source}), listed={})
                self.assertTrue(result.unresolved, result.unresolved)
                self.assertTrue(any(reason in line for line in result.unresolved), result.unresolved)
                if label != "an assignment after construction":
                    self.assertEqual(result.sites, [], result.sites)

    def test_no_site_has_a_code_or_a_move_the_walk_cannot_name(self) -> None:
        self.assertEqual(self.walk.unresolved, [], "resolve it, or list it in codes_walk with its values")
        self.assertEqual(self.walk.unused_listings, [], "a listing no site uses")

    def test_the_codes_made_and_the_table_are_the_same_set(self) -> None:
        made = set(self.codes) - set(search.INTERNAL_CODES)
        self.assertEqual(sorted(made - set(errors.REFUSALS)), [], "made but not in errors.REFUSALS")
        self.assertEqual(sorted(set(errors.REFUSALS) - made), [], "in errors.REFUSALS but never made")

    def test_every_code_has_a_next_move_and_a_site_never_says_another(self) -> None:
        for code, (nxt, _command) in errors.REFUSALS.items():
            self.assertIn(nxt, errors.NEXT_MOVES, code)
        for site in self.walk.sites:
            for code, move in site.pairs:
                if code in search.INTERNAL_CODES or move is None:
                    continue
                self.assertEqual(move, errors.REFUSALS[code][0], f"{site.module}:{site.line} {code}")
        for code in errors.REFUSALS:
            made = errors.MemoryForestError(code, "x")
            self.assertEqual(made.as_dict()["next"], errors.REFUSALS[code][0], code)

    def test_a_command_of_the_table_parses_and_names_no_folder(self) -> None:
        """A default command names nothing the caller did not give: only command words and options."""
        words_of = {word for command in cli.FOREST_COMMANDS for word in command.words}
        for code, (nxt, command) in errors.REFUSALS.items():
            if not command:
                continue
            with self.subTest(code=code):
                self.assertEqual(nxt, "run")
                words = shlex.split(command)
                self.assertEqual(words[0], "forest")
                cli.build_forest_parser().parse_args(words[1:])
                self.assertTrue(set(words[1:]) <= words_of, f"{command} names something the caller did not give")

    def test_a_run_code_with_no_command_in_the_table_gets_one_where_its_root_is_known(self) -> None:
        empty = {code for code, (nxt, command) in errors.REFUSALS.items() if nxt == "run" and not command}
        self.assertEqual(empty, set(errors.REBUILD_CODES))

        @errors.with_rebuild
        def refuse(root: str) -> None:
            raise errors.MemoryForestError("index_stale", "The local index no longer matches the forest.")

        with self.assertRaises(errors.MemoryForestError) as caught:
            refuse("/forests/the corner bakery")
        self.assertEqual(caught.exception.command, "memory-forest index '/forests/the corner bakery'")
        self.assertEqual(caught.exception.next_step, "run")

    def test_an_internal_code_is_not_in_the_table(self) -> None:
        self.assertIn("index_protocol", self.codes)
        self.assertTrue(set(search.INTERNAL_CODES).isdisjoint(errors.REFUSALS))

    def test_the_table_is_read_from_the_document_up_to_what_follows_it(self) -> None:
        row = "| `oven_cold` | fix | | The oven is cold. |\n"
        text = "# Refusals\n\n" + codes_walk.TABLE_HEAD + row + row + "\nWhat follows the table.\n"
        self.assertEqual(
            codes_walk.split_document(text),
            ("# Refusals\n\n", codes_walk.TABLE_HEAD + row + row, "\nWhat follows the table.\n"),
        )
        last = "# Refusals\n\n" + codes_walk.TABLE_HEAD + row.rstrip("\n")
        self.assertEqual(codes_walk.split_document(last), ("# Refusals\n\n", codes_walk.TABLE_HEAD + row.rstrip("\n"), ""))

    def test_the_document_is_the_generated_table(self) -> None:
        text = (PACKAGE.parents[1] / "docs" / "refusals.md").read_text(encoding="utf-8")
        _before, written, _after = codes_walk.split_document(text)
        self.assertEqual(written, codes_walk.table(self.walk), "regenerate it: python3 scripts/refusals_table.py")
        # Every code a decision of the state gives carries its claims, and nothing else does.
        self.assertEqual(set(codes_walk.claims_by_code()), set(derived.CODES))
        rows = document_rows()
        self.assertEqual(set(rows), set(errors.REFUSALS))
        for code, (nxt, command, said) in rows.items():
            self.assertEqual((nxt, command), errors.REFUSALS[code], code)
            self.assertTrue(said, f"{code}: a row with no When")

    def test_every_place_that_states_the_four_moves_uses_the_words_of_the_definitions(self) -> None:
        docs = PACKAGE.parents[1] / "docs"
        places = {
            name: " ".join((docs / name).read_text(encoding="utf-8").split())
            for name in ("refusals.md", "tools.md", "v1-design.md")
        }
        for name, text in places.items():
            for move, words in errors.MOVE_DEFINITIONS.items():
                with self.subTest(place=name, move=move):
                    self.assertTrue(f"`{move}`: {words}" in text or f"`{move}` ({words}" in text, words)
        # The MCP server's instructions: one line, one sentence, per move, in the order an agent meets them.
        lines = [line for line in mcp.INSTRUCTIONS.splitlines() if line.split(":")[0] in errors.MOVE_DEFINITIONS]
        self.assertEqual([line.split(":")[0] for line in lines], ["fix", "retry", "run", "report"])
        for line in lines:
            move = line.split(":")[0]
            with self.subTest(place="mcp.INSTRUCTIONS", move=move):
                self.assertTrue(line.startswith(f"{move}: {errors.MOVE_DEFINITIONS[move]}; "), line)
                self.assertEqual(line.count(". "), 0, line)
                self.assertTrue(line.endswith("."), line)
        self.assertLessEqual(len(mcp.GROUP_INSTRUCTIONS), 2048)

    def test_the_help_of_every_command_an_agent_runs_names_the_table(self) -> None:
        for words in [*agent_commands(), ("mcp",)]:
            out = io.StringIO()
            with self.subTest(command=" ".join(words)), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as stopped:
                    cli.forest_main([*words, "--help"])
                self.assertEqual(stopped.exception.code, 0)
                self.assertIn("docs/refusals.md", " ".join(out.getvalue().split()))

    def test_the_index_table_of_the_tools_document_agrees(self) -> None:
        for code, nxt, command in search.INDEX_REFUSALS:
            self.assertEqual(errors.REFUSALS[code], (nxt, command), code)
        text = (PACKAGE.parents[1] / "docs" / "tools.md").read_text(encoding="utf-8")
        table = text[text.index("What a state refuses with:"):].split("\n\n")[1]
        shown = []
        for line in table.splitlines()[2:]:
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            words = cells[2].split("`")
            shown.append((cells[0].strip("`"), words[1], words[3] if words[1] == "run" else ""))
        self.assertEqual(shown, list(search.INDEX_REFUSALS))


class WaitChildTests(unittest.TestCase):
    """A failed wait ends a child that is still alive before it reads the child's stderr."""

    def test_a_wait_on_a_live_child_that_writes_nothing_ends_at_its_deadline(self) -> None:
        from wait_child import read_fifo

        with tempfile.TemporaryDirectory() as scratch:
            fifo = Path(scratch) / "never-written"
            os.mkfifo(fifo)
            # The child blocks on its own stdin: it neither exits nor writes, until it is ended.
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            outcome: list[BaseException] = []
            done = threading.Event()

            def wait() -> None:
                try:
                    read_fifo(child, str(fifo), what="a handshake nobody sends", seconds=0.2)
                except BaseException as exc:
                    outcome.append(exc)
                finally:
                    done.set()

            threading.Thread(target=wait, daemon=True).start()
            try:
                self.assertTrue(done.wait(20), "the failed wait read a live child's stderr and did not end")
                self.assertIsInstance(outcome[0], AssertionError)
                self.assertIn("a handshake nobody sends", str(outcome[0]))
                self.assertIsNotNone(child.poll())
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=30)
                if child.stdin is not None:
                    child.stdin.close()
                if child.stderr is not None:
                    child.stderr.close()


if __name__ == "__main__":
    unittest.main()
