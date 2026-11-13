"""``forest collections demote | admit | recut | list`` on made-up forests."""

from __future__ import annotations

import bounds  # every invocation has a deadline

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_move import TODAY, MoveTestCase, difference, snapshot  # noqa: E402

from memory_forest import collections, maps, movelog, registry  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402

COLLECTION = "04 stm/people/regulars/seen-once-2051-q1.md"
MILLSTONE = "04 stm/bakery/suppliers/millstone-flour.md"


def entry(name: str, entry_id: str, day: str, words: int) -> str:
    return (
        f"## {name}\n\n- entry: `{entry_id}` · person\n\n"
        + " ".join(["Bought bread."] * words)
        + f"\n\n- {day} 10:00 Pacific/Auckland — {name} came in.\n\n"
    )


class CollectionTestCase(MoveTestCase):
    def forest(self) -> Path:
        root = self.make_forest()
        (root / "03 mtm/people/regulars.md").write_text("# regulars\n\nPeople who come in.\n", encoding="utf-8")
        maps.apply(root)
        self.assertEqual(maps.check(root), [])
        self.assertEqual(registry.check(root), [])
        return root

    def act(self, root: Path, action: str, target: str) -> tuple[dict, dict[str, list[str]]]:
        before = snapshot(root)
        result = collections.execute(root, action, target, today=TODAY)
        changed = difference(before, snapshot(root))
        self.assertEqual(registry.check(root), [], action)
        self.assertEqual(maps.check(root), [], action)
        return result, changed


class AdmitAndDemoteTests(CollectionTestCase):
    def test_admit_moves_an_entry_out_into_a_leaf_and_the_entry_address_resolves(self) -> None:
        root = self.forest()
        _, changed = self.act(root, "admit", "tamsin-orrow")
        leaf = "04 stm/people/regulars/tamsin-orrow.md"
        self.assertEqual(changed["created"], [leaf])
        self.assertIn(COLLECTION, changed["changed"])
        self.assertIn("03 mtm/people/people_MTM.md", changed["changed"])
        page = self.text(root, COLLECTION)
        self.assertNotIn("## Tamsin Orrow", page)
        self.assertIn("## Bram Eskell", page)
        text = self.text(root, leaf)
        self.assertIn("entity: tamsin-orrow", text)
        self.assertIn("Asked for rye loaves on Fridays.", text)
        self.assertIn("- `tamsin-orrow` — Tamsin Orrow · person · leaf · `04 stm/people/regulars/tamsin-orrow.md`",
                      self.text(root, "03 mtm/people/people_MTM.md"))
        self.assertResolves(root, f"{COLLECTION}#tamsin-orrow", leaf)

    def test_demote_folds_a_leaf_subject_into_its_category_collection(self) -> None:
        root = self.forest()
        _, changed = self.act(root, "demote", "millstone-flour")
        page = "04 stm/bakery/suppliers/seen-once-2051-q1.md"
        self.assertEqual(changed["created"], [page])
        self.assertEqual(changed["removed"], [MILLSTONE])
        text = self.text(root, page)
        self.assertIn("## Millstone Flour", text)
        self.assertIn("- entry: `millstone-flour` · organization · aliases: Millstone", text)
        index = self.text(root, "03 mtm/bakery/bakery_MTM.md")
        self.assertNotIn("- `millstone-flour`", index)
        self.assertIn("- `suppliers-seen-once` — ", index)
        found = movelog.resolve(root, MILLSTONE)
        self.assertEqual((found.status, found.path), ("current", f"{page}#millstone-flour"))

    def test_demote_then_admit_brings_the_subject_back_to_a_leaf(self) -> None:
        root = self.forest()
        self.act(root, "demote", "millstone-flour")
        _, changed = self.act(root, "admit", "millstone-flour")
        self.assertEqual(changed["created"], [MILLSTONE])
        self.assertIn("Something happened.", self.text(root, MILLSTONE))

    def test_refusals_write_nothing(self) -> None:
        root = self.forest()
        before = snapshot(root)
        for action, target in (("admit", "nobody-here"), ("demote", "deck-oven"), ("demote", "front-desk"),
                               ("recut", MILLSTONE)):
            with self.assertRaises(MemoryForestError, msg=f"{action} {target}"):
                collections.execute(root, action, target, today=TODAY)
        self.assertEqual(snapshot(root), before)


class RecutAndListTests(CollectionTestCase):
    def test_recut_cuts_a_quarter_past_the_cap_into_months(self) -> None:
        root = self.forest()
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8")
                            + "\n[limits]\nleaf_cap_bytes = 1200\nentry_admit_margin_bytes = 100\n", encoding="utf-8")
        page = root / COLLECTION
        text = page.read_text(encoding="utf-8")
        extra = (entry("Corin Vale", "corin-vale", "2051-01-15", 20)
                 + entry("Odile Marsh", "odile-marsh", "2051-02-09", 20))
        page.write_text(text.replace("## Timeline", extra + "## Timeline"), encoding="utf-8")
        result, changed = self.act(root, "recut", COLLECTION)
        self.assertEqual(changed["removed"], [COLLECTION])
        made = changed["created"]
        self.assertTrue(made)
        self.assertTrue(all(path.startswith("04 stm/people/regulars/seen-once-2051-0") for path in made), made)
        joined = "".join(self.text(root, path) for path in made)
        for name in ("Tamsin Orrow", "Bram Eskell", "Corin Vale", "Odile Marsh"):
            self.assertEqual(joined.count(f"## {name}"), 1, name)
        self.assertIn(COLLECTION, result["copies_kept"] + result["archived"])
        found = movelog.resolve(root, f"{COLLECTION}#corin-vale")
        self.assertEqual(found.status, "current")
        self.assertIn(found.path.split("#", 1)[0], made)

    def test_list_names_every_page_with_its_entries_and_their_days(self) -> None:
        root = self.forest()
        found = collections.pages(root)
        self.assertEqual([page["path"] for page in found], [COLLECTION])
        entries = {item["id"]: item["days"] for item in found[0]["entries"]}
        self.assertEqual(entries, {"tamsin-orrow": ["2051-03-02"], "bram-eskell": []})
        self.assertEqual(found[0]["collection"], "regulars-seen-once")
