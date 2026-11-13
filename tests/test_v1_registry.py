from __future__ import annotations

import json
import sys
import unittest

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import BAKERY_FILES, SYNTHETIC_FOREST, ForestTestCase, index_text, leaf_text  # noqa: E402

from memory_forest import registry  # noqa: E402
from memory_forest.config import parse_config  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402


class SyntheticForestTests(ForestTestCase):
    def test_the_synthetic_forest_has_no_registry_problem(self) -> None:
        self.assertEqual([problem.render() for problem in registry.check(SYNTHETIC_FOREST)], [])

    def test_the_synthetic_registry_holds_its_subjects_agents_and_trees(self) -> None:
        built = registry.build(SYNTHETIC_FOREST)
        self.assertEqual(built.agents, ["kiln-bot", "ledger-bot", "studio-hub"])
        self.assertEqual(
            built.tree_kinds,
            {
                "admin": "area",
                "kiln-bot": "agent",
                "ledger-bot": "agent",
                "people": "area",
                "studio": "area",
                "studio-hub": "agent",
            },
        )
        self.assertEqual(built.about_index, {"kiln-two": ["04 stm/admin/shops/cinderwell-supply.md"]})
        kiln = built.subject("kiln-two")
        self.assertEqual(kiln["pointers"], ["04 stm/admin/shops/cinderwell-supply.md"])
        self.assertEqual(len(kiln["leaves"]), 5)
        collection = built.subject("acquaintances-seen-once")
        self.assertEqual([entry["id"] for entry in collection["entries"]], ["pell-arrowsmith", "idris-mallow", "wren-castellane"])
        self.assertEqual(built.lookup("W. Castellane")["id"], "wren-castellane")
        self.assertEqual(built.lookup("the big kiln")["id"], "kiln-two")
        self.assertEqual(built.lookup("juno")["id"], "juno-hale")

    def test_the_same_forest_compiles_to_the_same_bytes(self) -> None:
        root = self.copy_synthetic()
        first = registry.build_and_write(root)
        written = (root / ".forest" / "registry.json").read_bytes()
        self.assertEqual(written, first.to_bytes())
        self.assertEqual(registry.build(root).to_bytes(), written)
        self.assertEqual(registry.load(root).data, json.loads(written))
        self.assertNotIn(str(root), written.decode("utf-8"))


class BuildTests(ForestTestCase):
    def test_a_made_up_forest_compiles(self) -> None:
        root = self.make_forest()
        built = registry.build(root)
        self.assertEqual(built.problems, [])
        self.assertEqual(built.agents, ["front-desk"])
        self.assertEqual(built.tree_kind("front-desk"), "agent")
        self.assertTrue(built.is_agent_tree("front-desk"))
        self.assertEqual(built.agent_trees(), ["front-desk"])
        # the owner and an unknown id are never about ids
        self.assertEqual(built.about_index, {"deck-oven": ["04 stm/bakery/suppliers/millstone-flour.md"]})
        self.assertEqual(built.subject("deck-oven")["about_leaves"], ["04 stm/bakery/suppliers/millstone-flour.md"])
        self.assertEqual(built.subject("deck-oven")["pointers"], ["04 stm/bakery/suppliers/millstone-flour.md"])
        self.assertEqual(built.lookup("Ilse Varrow")["id"], "ilse-varrow")
        self.assertEqual(built.tree_of("tamsin-orrow"), "people")
        self.assertEqual(built.tree_of("deck-oven"), "bakery")

    def test_lookups_fold_case_width_and_spacing(self) -> None:
        built = registry.build(self.make_forest())
        for name in ("deck oven", "DECK  OVEN", "ＤＯ-３", "do-3", "deck-oven"):
            with self.subTest(name=name):
                self.assertEqual(built.lookup(name)["id"], "deck-oven")
        self.assertIsNone(built.lookup("oven"))

    def test_find_lists_exact_names_then_substrings_subjects_before_entries(self) -> None:
        built = registry.build(self.make_forest())
        found = built.find("Tam")
        self.assertEqual([(match.id, match.level, match.match) for match in found][:1], [("tamsin-orrow", "entry", "exact")])
        self.assertEqual(found[0].collection, "regulars-seen-once")
        self.assertEqual(found[0].home, "04 stm/people/regulars/seen-once-2051-q1.md")
        found = built.find("oven")
        self.assertEqual(found[0].id, "deck-oven")
        self.assertEqual(found[0].match, "substring")
        self.assertEqual(built.find("Тамсин Орров")[0].id, "tamsin-orrow")
        self.assertEqual(built.find(""), [])

    def test_lookup_by_path(self) -> None:
        built = registry.build(self.make_forest())
        self.assertEqual(built.subject_for_path("04 stm/bakery/deck-oven/repairs.md")["id"], "deck-oven")
        self.assertEqual(built.subject_for_path("03 mtm/bakery/deck-oven.md")["id"], "deck-oven")
        self.assertEqual(built.subject_for_path("04 stm/bakery/deck-oven/new-leaf.md")["id"], "deck-oven")
        self.assertEqual(built.subject_for_path("02 ltm/front-desk_LTM.md")["id"], "front-desk")
        self.assertEqual(
            built.subject_for_path("04 stm/people/regulars/seen-once-2051-q1.md#bram-eskell")["id"], "bram-eskell"
        )
        self.assertEqual(
            built.subject_for_path("04 stm/people/regulars/seen-once-2051-q1.md")["id"], "regulars-seen-once"
        )
        self.assertIsNone(built.subject_for_path("04 stm/bakery/suppliers/unknown.md"))

    def test_a_standing_line_and_entry_details_compile(self) -> None:
        files = dict(BAKERY_FILES)
        files["04 stm/people/self/ilse-varrow.md"] = leaf_text(
            "ilse-varrow", kind="profile", extra='standing: own — "her own bakery"'
        )
        page = "04 stm/people/regulars/seen-once-2051-q1.md"
        files[page] = files[page].replace(
            "- entry: `bram-eskell` · person\n",
            '- entry: `bram-eskell` · person\n- standing: said — "Bram brings our apples every week" (2051-03-02)\n',
        )
        built = registry.build(self.make_forest(files))
        self.assertEqual(built.subject("ilse-varrow")["standing"], 'own — "her own bakery"')
        self.assertEqual(built.subject("deck-oven")["standing"], "")
        tamsin = built.entry("tamsin-orrow")
        self.assertEqual(tamsin["aliases"], ["Тамсин Орров", "Tam"])
        self.assertEqual(tamsin["kind"], "event")
        self.assertEqual(tamsin["collection"], "regulars-seen-once")
        self.assertEqual(tamsin["standing"], "")
        self.assertTrue(built.entry("bram-eskell")["standing"].startswith("said — "))


class ProblemTests(ForestTestCase):
    def problems(self, files: dict[str, str], config_text: str | None = None, *, name: str = "forest") -> list[tuple[str, str]]:
        config = parse_config(config_text) if config_text is not None else None
        built = registry.build(self.make_forest(files, name=name), config=config)
        return [(problem.code, problem.detail) for problem in built.problems]

    def with_bakery_subjects(self, extra: list[str], *, leaves: tuple[str, ...] = ()) -> dict[str, str]:
        files = dict(BAKERY_FILES)
        files["03 mtm/bakery/bakery_MTM.md"] = index_text(
            "bakery",
            [
                "- `deck-oven` — Deck Oven · device · branch · `03 mtm/bakery/deck-oven.md` · aliases: the old oven; DO-3",
                "- `millstone-flour` — Millstone Flour · organization · leaf · "
                "`04 stm/bakery/suppliers/millstone-flour.md` · aliases: Millstone",
                *extra,
            ],
        )
        for leaf in leaves:
            files[f"04 stm/bakery/recipes/{leaf}.md"] = leaf_text(leaf)
        return files

    def test_each_kind_of_bad_line_has_its_code(self) -> None:
        cases = [
            ("- `deck-oven` — Second Oven · device · branch · `03 mtm/bakery/deck-oven.md`", "registry_duplicate_id"),
            ("- `rye-starter` — Rye Starter · topic · leaf · `04 stm/bakery/recipes/rye-starter.md` · aliases: DO-3",
             "registry_duplicate_alias"),
            ("- `do3` — Do3 · topic · leaf · `04 stm/bakery/recipes/do3.md`", "registry_duplicate_alias"),
            ("- `sourdough` — Sourdough · topic · leaf · `04 stm/bakery/recipes/sourdough.md`",
             "registry_home_missing"),
            ("- `rye-starter` — Rye Starter · spaceship · leaf · `04 stm/bakery/recipes/rye-starter.md`",
             "registry_line_invalid"),
            ("- `rye-starter` — Rye Starter · topic · twig · `04 stm/bakery/recipes/rye-starter.md`",
             "registry_line_invalid"),
            ("- `rye-starter` — Rye Starter · topic · branch · `03 mtm/bakery/rye.md`", "registry_line_invalid"),
            ("- `Rye Starter` — Rye Starter · topic · leaf · `04 stm/bakery/recipes/rye-starter.md`",
             "registry_line_invalid"),
            ("- `none` — None · topic · leaf · `04 stm/bakery/recipes/none.md`", "registry_line_invalid"),
            ("- `till-bot` — Till Bot · agent · branch · `03 mtm/bakery/till-bot.md`", "registry_line_invalid"),
            ("- `rye-starter` — Rye Starter · topic · tree · `02 ltm/rye-starter_LTM.md`", "registry_line_invalid"),
            ("- rye starter, a sourdough culture", "registry_line_invalid"),
            ("- `rye-starter` — Rye verification code 4821 · topic · leaf · `04 stm/bakery/recipes/rye-starter.md`",
             "registry_name_masked"),
            ("- `rye-starter` — Rye Starter · topic · leaf · `04 stm/bakery/recipes/rye-starter.md` · "
             "aliases: door code 7395", "registry_name_masked"),
            ("- `rye-starter` — Rye verification code [REDACTED] · topic · leaf · "
             "`04 stm/bakery/recipes/rye-starter.md`", "registry_name_masked"),
        ]
        for number, (line, code) in enumerate(cases):
            with self.subTest(line=line):
                files = self.with_bakery_subjects([line], leaves=("rye-starter", "do3", "none"))
                found = self.problems(files, name=f"forest-{number}")
                self.assertEqual([item[0] for item in found], [code], found)

    def test_an_entry_a_subject_holds_or_another_entry_holds_is_an_error(self) -> None:
        files = dict(BAKERY_FILES)
        page = files["04 stm/people/regulars/seen-once-2051-q1.md"]
        files["04 stm/people/regulars/seen-once-2051-q1.md"] = page.replace(
            "- entry: `bram-eskell` · person", "- entry: `bram-eskell` · person · aliases: Millstone; Tam"
        )
        found = self.problems(files)
        self.assertEqual(sorted(code for code, _detail in found), ["registry_duplicate_alias", "registry_entry_is_subject"])
        built = registry.build(self.base / "forest")
        self.assertEqual(built.lookup("Millstone")["id"], "millstone-flour")
        self.assertEqual(built.lookup("Tam")["id"], "tamsin-orrow")

    def test_a_name_that_reads_as_a_sign_in_value_is_reported_and_stays_registered(self) -> None:
        files = self.with_bakery_subjects(
            ["- `rye-starter` — Rye verification code 4821 · topic · leaf · `04 stm/bakery/recipes/rye-starter.md`"],
            leaves=("rye-starter",),
        )
        page = files["04 stm/people/regulars/seen-once-2051-q1.md"]
        files["04 stm/people/regulars/seen-once-2051-q1.md"] = page.replace("## Bram Eskell", "## Bram passcode 7395")
        found = self.problems(files)
        self.assertEqual([code for code, _detail in found], ["registry_name_masked", "registry_name_masked"])
        for _code, detail in found:  # the reason shows the masked form, never the name
            self.assertNotIn("4821", detail)
            self.assertNotIn("7395", detail)
            self.assertIn("[REDACTED]", detail)
        built = registry.build(self.base / "forest")
        self.assertEqual(built.subject("rye-starter")["home"], "04 stm/bakery/recipes/rye-starter.md")
        entries = [entry["id"] for entry in built.subject("regulars-seen-once")["entries"]]
        self.assertIn("bram-eskell", entries)

    def test_a_malformed_entry_is_reported(self) -> None:
        files = dict(BAKERY_FILES)
        page = files["04 stm/people/regulars/seen-once-2051-q1.md"]
        files["04 stm/people/regulars/seen-once-2051-q1.md"] = page.replace(
            "- entry: `bram-eskell` · person\n", "Bram had no entry line.\n"
        ).replace("· person · aliases: Tam", "· starship · aliases: Tam")
        found = self.problems(files)
        self.assertEqual([code for code, _detail in found], ["collection_entry_malformed", "collection_entry_malformed"])

    def test_a_collection_lives_on_its_categorys_period_pages(self) -> None:
        files = dict(BAKERY_FILES)
        files["03 mtm/people/people_MTM.md"] = index_text(
            "people",
            [
                "- `ilse-varrow` — Ilse Varrow · person · leaf · `04 stm/people/self/ilse-varrow.md` · aliases: Ilse",
                "- `regulars-seen-once` — Regulars seen once · collection · branch · "
                "`04 stm/people/regulars/seen-once-2051-q1.md`",
            ],
        )
        self.assertEqual([code for code, _ in self.problems(files)], ["registry_line_invalid"])

    def test_the_owner_name_on_another_subject_is_a_duplicate(self) -> None:
        files = self.with_bakery_subjects(
            ["- `ilse-brandt` — Ilse Brandt · person · leaf · `04 stm/bakery/staff/ilse-brandt.md` · aliases: Ilse"]
        )
        files["04 stm/bakery/staff/ilse-brandt.md"] = leaf_text("ilse-brandt")
        found = self.problems(files)
        self.assertEqual([code for code, _ in found], ["registry_duplicate_alias"])

    def test_types_are_the_settings(self) -> None:
        files = self.with_bakery_subjects(["- `rye` — Rye · grain · leaf · `04 stm/bakery/recipes/rye.md`"])
        files["04 stm/bakery/recipes/rye.md"] = leaf_text("rye")
        self.assertEqual([code for code, _ in self.problems(files)], ["registry_line_invalid"])
        types = '["person", "organization", "device", "grain", "agent", "collection"]'
        settings = f"[subjects]\ntypes = {types}\n[levels]\ntree_types = [\"device\"]\n"
        self.assertEqual(self.problems(files, settings, name="typed"), [])

    def test_the_system_id_is_never_an_about_id(self) -> None:
        files = dict(BAKERY_FILES)
        files["04 stm/bakery/suppliers/millstone-flour.md"] = leaf_text("millstone-flour", about="deck-oven, front-desk")
        root = self.make_forest(files)
        self.assertIn("front-desk", registry.build(root).about_index)
        config = parse_config('[owner]\nid = "ilse-varrow"\n[subjects]\nsystem_id = "front-desk"\n')
        self.assertNotIn("front-desk", registry.build(root, config=config).about_index)

    def test_about_values(self) -> None:
        self.assertEqual(registry.about_ids("a, b, a, `c`"), ["a", "b", "c"])
        self.assertEqual(registry.about_ids("none"), [])
        self.assertEqual(registry.about_ids(["a", "none"]), ["a"])
        self.assertEqual(registry.about_state(None), "untagged")
        self.assertEqual(registry.about_state("none"), "none")
        self.assertEqual(registry.about_state("a"), "tagged")


class FileTests(ForestTestCase):
    def test_load_compiles_when_there_is_no_file_unless_told_not_to(self) -> None:
        root = self.make_forest()
        self.assertEqual(registry.load(root).agents, ["front-desk"])
        with self.assertRaises(MemoryForestError):
            registry.load(root, build_if_missing=False)

    def test_a_file_of_another_kind_is_refused(self) -> None:
        root = self.make_forest()
        (root / ".forest").mkdir()
        (root / ".forest" / "registry.json").write_text('{"schema": "something-else"}', encoding="utf-8")
        with self.assertRaises(MemoryForestError) as caught:
            registry.load(root)
        self.assertEqual(caught.exception.code, "registry_invalid")


if __name__ == "__main__":
    unittest.main()
