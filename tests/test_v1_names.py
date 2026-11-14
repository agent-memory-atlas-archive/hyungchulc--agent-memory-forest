"""The name alone decides who an agent is, in any script (design, section 6; contract, section 2).

Every name here is invented: the bakery's bots, the studio's clerks, and names written in Hangul, kana, Han
characters, Cyrillic, Greek and Arabic letters that mean a scout, a copier or nothing at all.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import hashlib
import json
import random
import re
import unicodedata
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from memory_forest import agents, groups, mcp, move, names, registry, search
from memory_forest.cli import forest_main
from memory_forest.config import load_config, parse_config
from memory_forest.errors import MemoryForestError
from memory_forest.model import parse_page

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS, COOLING, _call, _stdin, _tool_body
from v1_support import ForestTestCase

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def _cli(root: Path, *args: str, stdin: str = "") -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err), _stdin(stdin):
        code = forest_main([*args, "--root", str(root), "--json"])
    return code, out.getvalue(), err.getvalue()


def _hash(name: str) -> str:
    return hashlib.sha256(names.key(name).encode("utf-8")).hexdigest()[:6]


class KeyTests(ForestTestCase):
    """The key is the only comparison: NFKC, case folding, and no white space, dashes or underscores."""

    def test_the_reviews_pairs_are_one_agent_or_two_by_the_key_alone(self) -> None:
        one = [
            ("Kiln Bot", "kiln_bot"), ("Kiln Bot", "KILN-BOT"), ("Kiln Bot", "Kiln–Bot"),
            ("Kiln Bot", "Ｋｉｌｎ Ｂｏｔ"), ("Kiln Bot", "Kiln\u3000Bot"), ("Straße", "STRASSE"),
            ("Bakéry", "Bake\u0301ry"), ("스카우트", "\u1109\u1173\u110f\u1161\u110b\u116e\u1110\u1173"),
            ("Front Desk", "front-desk"),
        ]
        two = [
            ("Crate.Bot", "Crate Bot"), ("Tea+Bot", "Tea/Bot"), ("Kiln!", "Kiln?"), ("Café Bot", "Cafe Bot"),
            ("스카우트 Bot", "카피 Bot"), ("名 7", "字 7"), ("Ωmega 7", "Ψmega 7"), ("Front.Desk", "front-desk"),
            ("front desk!", "front-desk"), ("Frönt Desk", "front-desk"), ("Bakery Agent", "Bakery"),
        ]
        for left, right in one:
            self.assertTrue(names.same(left, right), (left, right))
        for left, right in two:
            self.assertFalse(names.same(left, right), (left, right))
        # Whether something holds a slug changes nothing: the same pairs on a forest whose area tree holds them.
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="pairs")
        for left, right in two:
            first = groups.resolve_caller(root, "night-shift", left).id
            second = groups.resolve_caller(root, "night-shift", right).id
            self.assertNotEqual(first, second, (left, right))
            self.assertEqual(groups.resolve_caller(root, "night-shift", left).id, first)
        for left, right in one:
            self.assertEqual(groups.resolve_caller(root, "night-shift", left).id,
                             groups.resolve_caller(root, "night-shift", right).id, (left, right))

    def test_a_name_finds_an_agent_by_its_id_exactly_or_by_its_key_and_never_by_a_slug(self) -> None:
        rows = [("crate-bot", "Crate.Bot"), ("till-bot", "Till Bot"), ("lane-only", "")]
        self.assertEqual(names.find("crate-bot", rows), ["crate-bot"])
        self.assertEqual(names.find("CRATE.bot", rows), ["crate-bot"])
        self.assertEqual(names.find("Crate Bot", rows), [])
        self.assertEqual(names.find("Crate-Bot", rows), [])
        self.assertEqual(names.find("till_bot", rows), ["till-bot"])
        self.assertEqual(names.find("Lane Only", rows), ["lane-only"])
        self.assertEqual(names.find("Till.Bot", rows), [])
        self.assertEqual(names.find(7, rows), [])  # type: ignore[arg-type]
        self.assertEqual(names.find("Shop Bot", [("a", "Shop Bot"), ("b", "shop_bot")]), ["a", "b"])
        # An id that matches wins over another agent's name.
        self.assertEqual(names.find("tray", [("counter", "tray"), ("tray", "Tray Keeper")]), ["tray"])

    def test_the_same_name_in_case_composition_and_width_is_one_agent_and_a_mark_makes_another(self) -> None:
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="marks")
        base = groups.resolve_caller(root, "night-shift", "Bakéry").id
        self.assertEqual(base, "bakery-agent")  # the area tree `bakery` holds `bakery`
        for spelling in ("BAKÉRY", "Bake\u0301ry", "Ｂａｋéｒｙ", " bakéry "):
            self.assertEqual(groups.resolve_caller(root, "night-shift", spelling).id, base, spelling)
        marked = groups.resolve_caller(root, "night-shift", "Bakéry!").id
        plain = groups.resolve_caller(root, "night-shift", "Bakery").id
        self.assertEqual(marked, "bakery-agent-2")
        self.assertEqual(plain, "bakery-agent-3")
        config = load_config(root)
        self.assertEqual(config.agents.declared(base).name, "Bakéry")
        self.assertEqual(config.agents.declared(marked).name, "Bakéry!")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "BAKERY").id, plain)

    def test_a_name_one_mark_from_the_main_agents_is_not_the_main_agent(self) -> None:
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="main")
        search.build(root)
        for spelling in ("front-desk", "Front Desk", "FRONT_DESK"):
            self.assertEqual(groups.resolve_caller(root, "night-shift", spelling).id, "front-desk")
        for near in ("Front.Desk", "Frönt Desk", "Front Desk!"):
            caller = groups.resolve_caller(root, "night-shift", near).id
            self.assertNotEqual(caller, "front-desk")
            self.assertFalse(load_config(root).is_orchestrator(caller))
            reply, _version = mcp.handle_message(
                root, "", _call("memory_read", {"agent": near, "path": COOLING}), "stdio", "2025-11-25",
                group="night-shift",
            )
            assert isinstance(reply, dict)
            self.assertTrue(reply["result"]["isError"], near)
            self.assertEqual(_tool_body(reply)["code"], "out_of_scope")


class AmbiguityTests(ForestTestCase):
    """Ambiguity is forest-wide, before membership or the main agent's exemption."""

    def _refused(self, settings: str, group: str, given: str, name: str) -> MemoryForestError:
        root = self.make_forest({**FILES, "forest.toml": settings}, name=name)
        before = (root / "forest.toml").read_bytes()
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(root, group, given)
        self.assertEqual((root / "forest.toml").read_bytes(), before)
        return caught.exception

    def test_a_name_two_agents_share_is_refused_with_both_wherever_the_two_are(self) -> None:
        two = '\n[agents.known.desk-one]\nname = "Front Desk"\n\n[agents.known.desk-two]\nname = "front_desk"\n'
        members = BAKERY_GROUPS.replace('members = ["till-bot", "oven-bot"]', 'members = ["desk-one", "desk-two"]')
        mains = BAKERY_GROUPS.replace('orchestrators = ["front-desk"]', 'orchestrators = ["front-desk", "frontdesk"]')
        shelves = '\n[agents.known.shelf-one]\nname = "Shelf Bot"\n\n[agents.known.shelf-two]\nname = "shelf_bot"\n'
        cases = (
            ("members", members + two, "bakery-bots", "Front Desk", ["front-desk", "desk-one", "desk-two"]),
            ("main agents", mains, "night-shift", "Front Desk", ["front-desk", "frontdesk"]),
            ("agents of no group", BAKERY_GROUPS + shelves, "night-shift", "Shelf Bot", ["shelf-one", "shelf-two"]),
        )
        for label, settings, group, given, wanted in cases:
            with self.subTest(where=label):
                refused = self._refused(settings, group, given, label.replace(" ", "-"))
                self.assertEqual(refused.code, "agent_ambiguous")
                self.assertEqual([item["id"] for item in refused.details["members"]], wanted)
                expected_names = {"front-desk": "Front Desk", "frontdesk": "frontdesk", "desk-one": "Front Desk",
                                  "desk-two": "front_desk", "shelf-one": "Shelf Bot", "shelf-two": "shelf_bot"}
                self.assertEqual([item["name"] for item in refused.details["members"]],
                                 [expected_names[item] for item in wanted])


class BadArgumentTests(ForestTestCase):
    def test_only_empty_too_long_or_control_text_is_a_bad_argument(self) -> None:
        for given, code in (("", "agent_unset"), ("   ", "agent_unset"), ("y" * 201, "agent_name_invalid"),
                            ("Kiln\nBot", "agent_name_invalid"), ("Kiln\x07Bot", "agent_name_invalid"),
                            ("Kiln\u2028Bot", "agent_name_invalid"), ("Kiln\tBot", "agent_name_invalid"),
                            (None, "agent_name_invalid")):
            with self.assertRaises(MemoryForestError) as caught:
                names.check(given)
            self.assertEqual((caught.exception.code, caught.exception.next_step), (code, "fix"), repr(given))
        # Counted in characters, not bytes: two hundred Hangul syllables are a name.
        self.assertEqual(names.check("스" * 200), "스" * 200)
        self.assertEqual(names.check(" Kiln  Bot "), "Kiln  Bot")
        for kept in ("★", "!!!", "---", "\u0301", "Kiln\u200dBot"):
            self.assertEqual(names.check(kept), kept)


class IdTests(ForestTestCase):
    def test_each_script_has_its_table_and_its_letters(self) -> None:
        tables = {table.name: table for table in names.tables()}
        self.assertEqual(sorted(tables), ["cyrillic", "greek", "hangul", "kana", "latin"])
        prefixes = {"cyrillic": ("CYRILLIC",), "greek": ("GREEK",), "hangul": ("HANGUL",),
                    "kana": ("HIRAGANA", "KATAKANA"), "latin": ("LATIN",)}
        for name, table in tables.items():
            for key, value in table.letters.items():
                self.assertTrue(1 <= len(key) <= 2, (name, key))
                for character in key:
                    self.assertTrue(unicodedata.name(character).startswith(prefixes[name]), (name, key))
                self.assertRegex(value, r"\A[a-z]*\Z", (name, key))
        def letters(first: int, last: int, word: str) -> set[str]:
            return {chr(point) for point in range(first, last + 1)
                    if word in unicodedata.name(chr(point), "") and unicodedata.category(chr(point)).startswith("L")}
        wanted = {
            "latin": {chr(point) for point in range(ord("a"), ord("z") + 1)},
            "hangul": {part for point in range(0xAC00, 0xD7A4) for part in unicodedata.normalize("NFD", chr(point))},
            "kana": letters(0x3040, 0x309F, "LETTER") | letters(0x30A0, 0x30FF, "LETTER"),
            "greek": letters(0x03B1, 0x03C9, "GREEK SMALL LETTER"),
            "cyrillic": letters(0x0430, 0x045F, "CYRILLIC SMALL LETTER"),
        }
        for name, needed in wanted.items():
            self.assertTrue(needed, name)
            missing = sorted(needed - set(tables[name].letters))
            self.assertEqual(missing, [], name)
        # Every letter of Latin-1 and Latin Extended-A is carried: none ends in the hash.
        for point in range(0x00C0, 0x0180):
            character = chr(point)
            if unicodedata.category(character).startswith("L"):
                self.assertFalse(names.make_id(f"{character}x").startswith("agent-"), hex(point))

    def test_only_the_json_files_of_the_folder_are_tables(self) -> None:
        folder = self.base / "letters"
        (folder / "notes.json").mkdir(parents=True)
        (folder / "README").write_text("not a table\n", encoding="utf-8")
        (folder / "latin.json").write_bytes(Path(names.LETTERS_DIR / "latin.json").read_bytes())
        names.tables.cache_clear()
        self.addCleanup(names.tables.cache_clear)
        with mock.patch.object(names, "LETTERS_DIR", folder):
            self.assertEqual([table.name for table in names.tables()], ["latin"])

    def test_ids_are_made_letter_for_letter_and_a_name_no_table_carries_is_hashed(self) -> None:
        cases = {
            "Kiln Bot": "kiln-bot", "Crème Brûlée": "creme-brulee", "Søren Ålund": "soren-alund",
            "O'Neil": "oneil", "스카우트": "seukauteu", "박": "bak", "창틀": "changteul", "스카우트 Bot": "seukauteu-bot",
            "카피 Bot": "kapi-bot", "スカウト": "sukauto", "マッチャ": "matcha", "ガッツ": "gattsu", "きょう": "kyou",
            "ラーメン": "ramen", "Разведчик": "razvedchik", "Ёлка": "yolka", "Θάλεια Ρόδου": "thaleia-rodou",
            "Ωmega 7": "omega-7", "Ψmega 7": "psmega-7", "Crate.Bot": "crate-bot", "Kiln!": "kiln",
            "Kiln -- Bot!": "kiln-bot",
        }
        for given, wanted in cases.items():
            self.assertEqual(names.make_id(given), wanted, given)
        # A small tsu doubles the first letter of what follows, a Hangul syllable taken apart included.
        self.assertEqual(names.make_id("ガッ야"), "gayya")
        self.assertEqual(names.make_id("ガッや"), "gayya")
        for given in ("侦察兵", "名 7", "字 7", "كشاف", "★", "Tea+Bot", "✓✓", "---", "스카우트 ★"):
            self.assertEqual(names.make_id(given), f"agent-{_hash(given)}", given)
        self.assertNotEqual(names.make_id("名 7"), names.make_id("字 7"))
        long = names.make_id("y" * 199 + " Bot")
        self.assertEqual(len(long), names.NAME_MAX)
        self.assertTrue(long.endswith("-" + _hash("y" * 199 + " Bot")))

    def test_no_id_leaves_the_pattern_on_a_seeded_walk_over_letters_marks_and_symbols(self) -> None:
        points = [point for point in range(0x20, 0x30000)
                  if unicodedata.category(chr(point))[0] in "LMSNPZ" and unicodedata.category(chr(point)) != "Zl"]
        generator = random.Random(20510302)
        made = set()
        for _round in range(4000):
            given = "".join(chr(generator.choice(points)) for _letter in range(generator.randint(1, 14)))
            if names.bad_reason(given) is not None:
                continue
            made_id = names.make_id(given)
            self.assertRegex(made_id, ID_RE, repr(given))
            self.assertTrue(ID_RE.fullmatch(made_id) and len(made_id) <= names.NAME_MAX, repr(given))
            names.key(given)
            made.add(made_id)
        self.assertGreater(len(made), 3000)
        for given in ("ŉ", "ŀ", "\u0301", "ǅ", "ﬁ", "Ⅻ", "²", "ß" * 150, "Ŀ" * 210):
            self.assertRegex(names.make_id(given), ID_RE, repr(given))

    def test_a_taken_id_is_followed_by_agent_or_a_number_and_never_by_another_agent(self) -> None:
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="taken")
        search.build(root)
        self.assertEqual(groups.resolve_caller(root, "night-shift", "Crate.Bot").id, "crate-bot")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "Crate Bot").id, "crate-bot-2")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "Crate_Bot").id, "crate-bot-2")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "Crate+Bot").id, f"agent-{_hash('Crate+Bot')}")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "Bakery").id, "bakery-agent")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "Ilse").id, "ilse-agent")
        held = {"x": ["agent"], "x-2": ["subject:place"], "x-3": ["agent"]}
        self.assertEqual(names.new_id("X", lambda value: held.get(value, [])), "x-4")
        held = {"x": ["subject:place", "agent"], "x-agent": ["agent"]}
        self.assertEqual(names.new_id("X", lambda value: held.get(value, [])), "x-agent-2")

    def test_an_id_held_by_any_one_kind_of_agent_entry_alone_is_not_given_again(self) -> None:
        settings = BAKERY_GROUPS.replace('orchestrators = ["front-desk"]', 'orchestrators = ["front-desk", "proof-bot"]')
        settings += '\n[agents.known.sift-bot]\nrole = "sifts"\n'
        settings += '\n[agents.groups.flour]\nmembers = ["mill-bot"]\nopen = true\n'
        settings += '\n[intake.tools.jsonl]\nagent = "dough-bot"\n'
        lane = json.dumps({"id": "r1", "at": "2051-03-02T06:00:00+00:00", "role": "agent", "session": "s",
                           "text": "racked"}) + "\n"
        root = self.make_forest({**FILES, "forest.toml": settings, "06 istm/rack-bot/2051-03-02.jsonl": lane},
                                name="holders")
        # oven-bot is an agent with a tree as well as a table and a lane: an agent's id, so a number follows.
        for held in ("mill-bot", "sift-bot", "proof-bot", "dough-bot", "rack-bot", "oven-bot"):
            given = held.replace("-", ".").title()  # another key, the same letters
            self.assertEqual(names.make_id(given), held)
            self.assertEqual(groups.resolve_caller(root, "night-shift", given).id, f"{held}-2", given)

    def test_a_name_finds_an_agent_the_forest_already_has_wherever_only_it_is_written(self) -> None:
        settings = BAKERY_GROUPS + '\n[agents.known.sift-bot]\nrole = "sifts"\n'
        settings += '\n[agents.groups.flour]\nmembers = ["mill-bot"]\nopen = false\n'
        settings += '\n[intake.tools.jsonl]\nagent = "dough-bot"\n'
        lane = json.dumps({"id": "r1", "at": "2051-03-02T06:00:00+00:00", "role": "agent", "session": "s",
                           "text": "racked"}) + "\n"
        root = self.make_forest({
            **FILES, "forest.toml": settings, "06 istm/rack-bot/2051-03-02.jsonl": lane,
            "06 istm/kiln-keeper/2051-03-02.jsonl": lane, "06 istm/Shelf Bot/2051-03-02.jsonl": lane,
        }, name="existing")
        move.execute(root, "new-agent", id="kiln-keeper", name="Kiln Warden", role="keeps the kiln", today="2051-03-02")
        move.execute(root, "new-agent", id="tray-bot", name="Tray Carrier", role="carries trays", today="2051-03-02")
        found = {given: groups.resolve_caller(root, "night-shift", given).id
                 for given in ("Sift Bot", "Mill Bot", "Dough Bot", "Rack Bot", "Kiln Warden", "Tray Carrier",
                               "Oven Bot", "Shelf Bot")}
        self.assertEqual(found, {
            "Sift Bot": "sift-bot",  # a known table with no name: named by its id
            "Mill Bot": "mill-bot",  # a member of another group only
            "Dough Bot": "dough-bot",  # an adapter's agent only
            "Rack Bot": "rack-bot",  # a lane only
            "Kiln Warden": "kiln-keeper",  # a lane, and on the registry line a name that is not its id
            "Tray Carrier": "tray-bot",  # a registry line only
            "Oven Bot": "oven-bot",  # the settings' name
            "Shelf Bot": "shelf-bot",  # a lane folder that cannot be an id is no agent
        })

    def test_an_id_is_made_once_and_the_owners_own_id_wins(self) -> None:
        settings = BAKERY_GROUPS + '\n[agents.known.scout-one]\nname = "스카우트"\n'
        settings += '\n[agents.known.old-handle]\nname = "Kiln Keeper"\n'
        root = self.make_forest({**FILES, "forest.toml": settings}, name="once")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "스카우트").id, "scout-one")
        # An id today's tables would not give still resolves by the name stored beside it.
        self.assertEqual(groups.resolve_caller(root, "night-shift", "kiln keeper").id, "old-handle")
        self.assertEqual(groups.resolve_caller(root, "night-shift", "old-handle").id, "old-handle")
        self.assertNotEqual(names.make_id("Kiln Keeper"), "old-handle")


NAME_KINDS = {
    "latin": ("Kiln Bot", "KILN_BOT"),
    "accented": ("Crème Bot", "crème-bot"),
    "hangul": ("스카우트", " 스카우트 "),
    "kana": ("スカウト", "ｽｶｳﾄ"),
    "han": ("侦察兵", "侦察兵"),
    "cyrillic": ("Разведчик", "РАЗВЕДЧИК"),
    "greek": ("Ανιχνευτής", "ΑΝΙΧΝΕΥΤΉΣ"),
    "arabic": ("كشاف", "كشاف"),
    "symbol": ("★", "★"),
    "mixed": ("스카우트 Bot", "스카우트-BOT"),
    "mixed-rest": ("카피 Bot", "카피_bot"),
    "dot": ("Crate.Bot", "CRATE.BOT"),
    "space": ("Crate Bot", "crate_bot"),
    "display-spaces": ("Crumb  \u3000  Clerk", "CRUMB_CLERK"),
    "backtick": ("Crumb ` Bot", "CRUMB ` BOT"),
    "middle-dot": ("Crumb · Bot", "CRUMB · BOT"),
    "masked": ("Mask [REDACTED] Bot", "MASK [REDACTED] BOT"),
    "markup": ("<Sift>&* Bot;", "<SIFT>&* BOT;"),
    "subject": ("Deck Oven", "deck_oven"),
}


class DoorProductTests(ForestTestCase):
    """Every kind of name through every door: admitted, a lane and a tree, the name stored as first given."""

    def _settings(self) -> str:
        return BAKERY_GROUPS + '\n[agents.groups.listed]\nmembers = []\nopen = false\nsource = "listed"\n'

    def test_every_name_kind_through_every_door(self) -> None:
        groups.register_member_source("listed", lambda _group: [name for name, _again in NAME_KINDS.values()])
        self.addCleanup(groups.unregister_member_source, "listed")
        for door in ("connection", "command", "log", "source"):
            with self.subTest(door=door):
                root = self.make_forest({**FILES, "forest.toml": self._settings()}, name=f"door-{door}")
                ids: dict[str, str] = {}
                original_trees = set(registry.build(root).agents)
                for kind, (name, again) in NAME_KINDS.items():
                    agent_id = self._through(root, door, name)
                    self.assertRegex(agent_id, ID_RE, kind)
                    ids[kind] = agent_id
                    self.assertEqual(load_config(root).agents.declared(agent_id).name, name, kind)
                    self.assertTrue((root / "06 istm" / agent_id).is_dir(), kind)
                    self.assertTrue((root / "02 ltm" / f"{agent_id}_LTM.md").is_file(), kind)
                    self.assertEqual(parse_page((root / "02 ltm" / f"{agent_id}_LTM.md").read_text()).title, name)
                    compiled = registry.build(root)
                    self.assertEqual(compiled.subject(agent_id)["name"], name)
                    self.assertFalse([p for p in compiled.problems if p.code in
                                      {"registry_name_masked", "registry_duplicate_alias"}], compiled.problems)
                    self.assertEqual(self._through(root, door, again), agent_id, (kind, again))
                    self.assertEqual(set(registry.build(root).agents), original_trees | set(ids.values()))
                self.assertEqual(len(set(ids.values())), len(ids), ids)
                self.assertNotEqual(ids["mixed"], ids["mixed-rest"])

    def _through(self, root: Path, door: str, name: str) -> str:
        if door == "connection":
            reply, _version = mcp.handle_message(
                root, "", _call("memory_log", {"agent": name, "text": f"a turn of {name}"}), "stdio",
                "2025-11-25", group="night-shift",
            )
            assert isinstance(reply, dict)
            self.assertFalse(reply["result"]["isError"], reply)
            return Path(_tool_body(reply)["path"]).parent.name
        if door == "command":
            code, out, err = _cli(root, "navigate", "--as-group", "night-shift", "--agent", name)
            self.assertEqual(code, 0, out + err)
            agent_id = json.loads(out)["reader"]
            mcp.record_turn(root, agent_id, f"a turn of {name}", origin="interactive", session="cli", door="cli",
                            group="night-shift")
            return agent_id
        if door == "log":
            code, out, err = _cli(root, "log", "--as-group", "night-shift", "--agent", name, stdin=f"a turn of {name}\n")
            self.assertEqual(code, 0, out + err)
            return Path(json.loads(out)["path"]).parent.name
        reply, _version = mcp.handle_message(
            root, "", _call("memory_log", {"agent": name, "text": f"a turn of {name}"}), "stdio", "2025-11-25",
            group="listed",
        )
        assert isinstance(reply, dict)
        self.assertFalse(reply["result"]["isError"], reply)
        return Path(_tool_body(reply)["path"]).parent.name


class SettingsTests(ForestTestCase):
    """One bad value of the agents' tables never stops the forest: that entry is reported and left out."""

    def test_a_member_written_twice_is_counted_once_and_reported(self) -> None:
        text = BAKERY_GROUPS.replace('members = ["till-bot", "oven-bot"]', 'members = ["till-bot", "oven-bot", "Oven Bot", "till-bot"]')
        config = parse_config(text)
        self.assertEqual(config.agents.group("bakery-bots").members, ("till-bot", "oven-bot"))
        self.assertEqual(len([why for _where, why in config.agents.left_out if "twice" in why]), 2)
        root = self.make_forest({**FILES, "forest.toml": text}, name="twice")
        problems = agents.problems(root, load_config(root), agents.Subjects({}, frozenset()))
        self.assertEqual(len([item for item in problems if item.code == agents.SETTING_INVALID]), 2)

    def test_a_declared_id_that_cannot_be_an_agent_is_a_problem_and_the_rest_are_not(self) -> None:
        text = BAKERY_GROUPS + f'\n[agents.known.{"y" * 300}]\nname = "Too Long"\n'
        text += '\n[agents.known.none]\nname = "Nobody"\n\n[agents.known.shelf-bot]\nname = "Shelf Bot"\n'
        root = self.make_forest({**FILES, "forest.toml": text}, name="declared")
        subjects = agents.Subjects({"shelf-bot": "place", "till-bot": "agent"}, frozenset())
        problems = [item for item in agents.problems(root, load_config(root), subjects) if item.code == agents.INVALID]
        self.assertEqual(len(problems), 3, [item.detail for item in problems])
        self.assertTrue(problems[0].path.startswith("agents.known.y") and "cannot be placed" in problems[0].detail)
        self.assertIn("`[agents.known.none]`: `none` is a reserved name", problems[1].detail)
        self.assertIn("`[agents.known.shelf-bot]`: `shelf-bot` is registered as a `place`", problems[2].detail)
        # A declared agent with no registry line yet differs from nothing.
        self.assertEqual(agents.settings_differ(load_config(root), {"till-bot": "Till Bot"}, {}), [])

    def test_a_member_written_as_a_name_is_that_agents_id(self) -> None:
        text = BAKERY_GROUPS.replace('members = ["till-bot", "oven-bot"]', 'members = ["Till Bot", "ovenbot"]')
        self.assertEqual(parse_config(text).agents.group("bakery-bots").members, ("till-bot", "oven-bot"))

    def test_every_value_wrong_for_one_entry_is_left_out_and_the_rest_loads(self) -> None:
        tail = (
            '\n[agents.known.plain-bot]\nname = "Plain Bot"\n'
            '\n[agents.known.shelf-bot]\nname = "Shelf\\u0007Bot"\ncolour = "red"\nrole = 7\n'
            '\n[agents.groups.loose]\nmembers = "till-bot"\nopen = "yes"\nmax_members = -1\nsource = "Not A Slug"\n'
            'colour = "red"\n'
            '\n[agents.groups.listing]\nmembers = [7, "till-bot", "listing"]\nopen = true\n'
        )
        text = BAKERY_GROUPS + tail
        config = parse_config(text)
        self.assertEqual(config.agents.declared("plain-bot").name, "Plain Bot")
        self.assertEqual(config.agents.declared("shelf-bot").name, "")
        loose = config.agents.group("loose")
        self.assertEqual((loose.members, loose.open, loose.max_members, loose.source), ((), False, None, ""))
        self.assertEqual(config.agents.group("listing").members, ("till-bot",))
        where = sorted(where for where, _why in config.agents.left_out)
        self.assertEqual(where, sorted([
            "agents.known.shelf-bot.name", "agents.known.shelf-bot.colour", "agents.known.shelf-bot.role",
            "agents.groups.loose.members", "agents.groups.loose.open", "agents.groups.loose.max_members",
            "agents.groups.loose.source", "agents.groups.loose.colour", "agents.groups.listing.members",
            "agents.groups.listing.members",
        ]))
        for whole in ('[agents]\nknown = 7\n', '[agents]\ngroups = "x"\n', '[agents]\norchestrators = "desk"\n',
                      '[agents]\norchestrators = ["desk", 7, "desk"]\n', '[agents.known]\nbot-one = 7\n',
                      '[agents.groups]\nnight = 7\n'):
            parsed = parse_config(whole)
            self.assertTrue(parsed.agents.left_out, whole)
        self.assertEqual(parse_config('[agents]\norchestrators = ["desk", 7, "desk"]\n').agents.orchestrators, ("desk",))
        root = self.make_forest({**FILES, "forest.toml": text}, name="loose")
        code, out, err = _cli(root, "agents")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(groups.resolve_caller(root, "bakery-bots", "till-bot").id, "till-bot")
