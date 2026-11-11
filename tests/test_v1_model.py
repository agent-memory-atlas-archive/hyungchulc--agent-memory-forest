from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import SYNTHETIC_FOREST  # noqa: E402

from memory_forest import layout  # noqa: E402
from memory_forest.model import (  # noqa: E402
    AgentLine,
    CurrentStateLine,
    EntryLine,
    LineShapeError,
    ListLine,
    RecordLine,
    RecordPointerLine,
    ReferenceLine,
    RegistryLine,
    TimelineLine,
    UpLink,
    code_span_paths,
    parse_page,
    read_page,
    rewrite_code_span_paths,
    wikilinks,
)

LINE_TYPES = (
    RegistryLine,
    EntryLine,
    TimelineLine,
    ListLine,
    AgentLine,
    UpLink,
    RecordLine,
    RecordPointerLine,
    ReferenceLine,
    CurrentStateLine,
)


class RoundTripTests(unittest.TestCase):
    def test_every_synthetic_page_round_trips_byte_for_byte(self) -> None:
        pages = layout.iter_pages(SYNTHETIC_FOREST, layout.ALL_LAYERS)
        self.assertGreater(len(pages), 30)
        for relative in pages:
            with self.subTest(page=relative):
                raw = (SYNTHETIC_FOREST / relative).read_bytes()
                self.assertEqual(read_page(SYNTHETIC_FOREST / relative).render().encode("utf-8"), raw)

    def test_every_parsed_list_line_of_the_synthetic_forest_renders_to_itself(self) -> None:
        counts = {kind.__name__: 0 for kind in LINE_TYPES}
        for relative in layout.iter_pages(SYNTHETIC_FOREST):
            page = read_page(SYNTHETIC_FOREST / relative)
            for section in page.sections:
                for _index, line in section.items():
                    for kind in LINE_TYPES:
                        parsed = kind.match(line)
                        if parsed is not None:
                            counts[kind.__name__] += 1
                            self.assertEqual(parsed.render(), line, (relative, kind.__name__))
        for name in ("RegistryLine", "EntryLine", "TimelineLine", "ListLine", "AgentLine", "UpLink", "RecordLine",
                     "ReferenceLine", "CurrentStateLine"):
            self.assertGreater(counts[name], 0, name)

    def test_odd_text_round_trips(self) -> None:
        for text in (
            "",
            "no newline at the end",
            "---\nentity: x\n",  # an unclosed frontmatter is body text
            "---\r\nentity: x\r\n---\r\n\r\n# T\r\n\r\n## A\r\n- a\r\n",
            "# T\n\n```\n## not a heading\n```\n\n## Real\n\ntext\n",
            "---\nkey:\n  - a\n  - 'b'\nflow: [c, d]\n# comment\n\n---\nbody\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_page(text).render(), text)


class PageTests(unittest.TestCase):
    TEXT = (
        "---\nentity: deck-oven\nsubtopic: repairs\nkind: event\nabout: millstone-flour, front-desk\n"
        "aliases:\n  - DO-3\n  - the old oven\nupdated_at: 2051-03-02\n---\n\n"
        "# Deck Oven repairs\n\nWhat broke and when.\n\n"
        "## 2051-03-01 The door hinge\n\nThe hinge cracked.\n\n### Parts\n\n- a hinge\n\n"
        "## Timeline\n\n- 2051-03-01 07:30 Pacific/Auckland — the hinge cracked.\n\n"
        "## Links\n\n- up: [[03 mtm/bakery/deck-oven|deck-oven MTM]]\n\n"
        "## Update record\n\n- 2051-03-02 — hinge: was \"sound\", now \"cracked\". Why: seen. Source: `05 daily/2051-03-02.md`.\n"
    )

    def test_sections_and_frontmatter(self) -> None:
        page = parse_page(self.TEXT)
        self.assertEqual(page.title, "Deck Oven repairs")
        self.assertEqual(
            page.section_names(), ["2051-03-01 The door hinge", "Timeline", "Links", "Update record"]
        )
        self.assertIn("### Parts\n", page.section("2051-03-01 the door hinge").body())
        self.assertEqual(page.frontmatter.keys(), ["entity", "subtopic", "kind", "about", "aliases", "updated_at"])
        self.assertEqual(page.field("entity"), "deck-oven")
        self.assertEqual(page.frontmatter.get_list("about"), ["millstone-flour", "front-desk"])
        self.assertEqual(page.frontmatter.get("aliases"), ["DO-3", "the old oven"])
        self.assertEqual(page.section("timeline").item_lines(), ["- 2051-03-01 07:30 Pacific/Auckland — the hinge cracked."])
        self.assertNotIn("Update record", page.content_text())
        self.assertTrue(page.record_text().startswith("## Update record"))
        self.assertEqual(page.content_text() + page.record_text(), self.TEXT)

    def test_an_edit_changes_only_its_own_lines(self) -> None:
        page = parse_page(self.TEXT)
        page.frontmatter.set("about", ["millstone-flour"])
        page.frontmatter.set("aliases", ["DO-3"])
        page.frontmatter.set("status", "superseded")
        page.frontmatter.remove("subtopic")
        text = page.render()
        self.assertIn("about: millstone-flour\n", text)
        self.assertIn("aliases:\n  - DO-3\nupdated_at", text)
        self.assertIn("updated_at: 2051-03-02\nstatus: superseded\n---\n", text)
        self.assertNotIn("subtopic", text)
        self.assertEqual(text.split("---\n", 2)[2], self.TEXT.split("---\n", 2)[2])

    def test_adding_list_lines_and_sections(self) -> None:
        page = parse_page(self.TEXT)
        page.section("Timeline").append_item("- 2051-03-02 09:00 Pacific/Auckland — the hinge was replaced.")
        page.add_section("Pointers", ["- `04 stm/bakery/suppliers/millstone-flour.md` — the hinge order"])
        text = page.render()
        self.assertIn(
            "- 2051-03-01 07:30 Pacific/Auckland — the hinge cracked.\n"
            "- 2051-03-02 09:00 Pacific/Auckland — the hinge was replaced.\n\n## Links",
            text,
        )
        self.assertIn("## Pointers\n\n- `04 stm/bakery/suppliers/millstone-flour.md` — the hinge order\n\n## Update record", text)
        self.assertEqual(page.section_names()[-1], "Update record")
        self.assertEqual(parse_page(text).render(), text)

    def test_appending_to_an_empty_last_section(self) -> None:
        page = parse_page("# T\n\n## Update record\n")
        page.section("Update record").append_item("- 2051-03-02 — created.")
        self.assertEqual(page.render(), "# T\n\n## Update record\n\n- 2051-03-02 — created.\n")
        page = parse_page("# T\n\n## Timeline\n\n## Links\n")
        page.section("Timeline").append_item("- 2051-03-02 — baked.")
        self.assertEqual(page.render(), "# T\n\n## Timeline\n\n- 2051-03-02 — baked.\n\n## Links\n")
        page = parse_page("# T\n\n## About\n\nAn oven.\n")
        page.section("About").append_item("- built 2040")
        self.assertEqual(page.render(), "# T\n\n## About\n\n- built 2040\n\nAn oven.\n")
        page = parse_page("# T\n\n## Leaves")
        page.add_section("Pointers")
        self.assertEqual(page.render(), "# T\n\n## Leaves\n\n## Pointers\n\n")


class LineShapeTests(unittest.TestCase):
    def test_timeline_lines(self) -> None:
        line = TimelineLine.parse("- 2051-03-01 07:30–08:10 Pacific/Auckland — the hinge cracked.")
        self.assertEqual((line.day, line.start, line.end, line.zone), ("2051-03-01", "07:30", "08:10", "Pacific/Auckland"))
        self.assertEqual(line.text, "the hinge cracked.")
        superseded = TimelineLine.parse("- [superseded 2051-03-02] 2051-01-05 UTC — set to 220 °C.")
        self.assertEqual((superseded.superseded, superseded.day, superseded.zone), ("2051-03-02", "2051-01-05", "UTC"))
        relative = TimelineLine.parse('- 2051-02 (said "last month" on 2051-03-02) — the flour price rose.')
        self.assertEqual((relative.date, relative.day, relative.note), ("2051-02", None, '(said "last month" on 2051-03-02)'))
        since = TimelineLine.parse('- since about 2041 (said "for ten years" on 2051-03-02) — bakes rye.')
        self.assertEqual((since.qualifier, since.date), ("since about", "2041"))
        self.assertEqual(
            TimelineLine.make("2051-03-02", "baked", start="06:00", zone="UTC").render(),
            "- 2051-03-02 06:00 UTC — baked",
        )
        for bad in ("- yesterday — baked", "- 2051-03-02 baked", "2051-03-02 — baked"):
            with self.assertRaises(LineShapeError):
                TimelineLine.parse(bad)

    def test_registry_lines(self) -> None:
        text = "- `deck-oven` — Deck Oven · device · branch · `03 mtm/bakery/deck-oven.md` · aliases: the old oven; DO-3 · ended"
        line = RegistryLine.parse(text)
        self.assertEqual(
            (line.id, line.name, line.type, line.level, line.home, line.aliases, line.status),
            ("deck-oven", "Deck Oven", "device", "branch", "03 mtm/bakery/deck-oven.md", ["the old oven", "DO-3"], "ended"),
        )
        self.assertEqual(line.render(), text)
        plain = RegistryLine.parse("- `bram` — Bram · person · leaf · `04 stm/people/regulars/bram.md`")
        self.assertEqual((plain.aliases, plain.status), ([], "active"))
        for bad in (
            "- `x` — X · person · leaf",
            "- `x` — X · person · leaf · 04 stm/a/b/x.md",
            "- `x` — X · person · leaf · `04 stm/a/b/x.md` · colour: red",
            "- x — X · person · leaf · `04 stm/a/b/x.md`",
        ):
            with self.subTest(line=bad):
                with self.assertRaises(LineShapeError):
                    RegistryLine.parse(bad)

    def test_entry_agent_list_and_link_lines(self) -> None:
        entry = EntryLine.parse("- entry: `tamsin-orrow` · person · aliases: Tam; T. Orrow")
        self.assertEqual((entry.id, entry.type, entry.aliases), ("tamsin-orrow", "person", ["Tam", "T. Orrow"]))
        agent = AgentLine.parse("- `front-desk` — Front Desk · orchestrator · ended")
        self.assertEqual((agent.name, agent.role, agent.ended), ("Front Desk", "orchestrator", True))
        listed = ListLine.parse("- `repairs.md` — repairs, newest first (updated 2051-03-02)")
        self.assertEqual((listed.target, listed.description, listed.updated), ("repairs.md", "repairs, newest first", "2051-03-02"))
        self.assertEqual(ListLine.parse("- `bakery`").description, None)
        up = UpLink.parse("- up: [[03 mtm/bakery/deck-oven|deck-oven MTM]]")
        self.assertEqual((up.path, up.label), ("03 mtm/bakery/deck-oven.md", "deck-oven MTM"))

    def test_record_lines(self) -> None:
        line = RecordLine.parse(
            '- 2051-03-02 — oven limit: was "Bake at 240 °C.", now "Bake at 230 °C.". Why: the crust burned. '
            "Source: `05 daily/2051-03-02.md`, `04 stm/bakery/deck-oven/repairs.md`."
        )
        self.assertEqual(line.what, "oven limit")
        self.assertEqual((line.was, line.now), ("Bake at 240 °C.", "Bake at 230 °C."))
        self.assertEqual(line.why, "the crust burned")
        self.assertEqual(line.sources, ["05 daily/2051-03-02.md", "04 stm/bakery/deck-oven/repairs.md"])
        made = RecordLine.make("2051-03-02", "oven limit", was="a", now="b", why="seen", sources=["05 daily/2051-03-02.md"])
        self.assertEqual(made.render(), '- 2051-03-02 — oven limit: was "a", now "b". Why: seen. Source: `05 daily/2051-03-02.md`.')
        self.assertEqual(RecordLine.parse(made.render()).now, "b")
        sourced = RecordLine.parse('- 2051-03-02 — oven limit: was "a", now "b". Source: `05 daily/2051-03-02.md`.')
        self.assertEqual((sourced.was, sourced.now, sourced.sources), ("a", "b", ["05 daily/2051-03-02.md"]))
        made = RecordLine.make("2051-03-02", "oven limit", was="a", now="b", sources=["05 daily/2051-03-02.md"])
        self.assertEqual((RecordLine.parse(made.render()).was, RecordLine.parse(made.render()).now), ("a", "b"))
        removal = RecordLine.make("2051-03-02", "the rye note", removed="Rye on Fridays.", why='Ilse said "drop it"')
        self.assertEqual(removal.render(), '- 2051-03-02 — the rye note: removed "Rye on Fridays.". Why: Ilse said "drop it".')
        parsed = RecordLine.parse(removal.render())
        self.assertEqual((parsed.what, parsed.removed, parsed.was), ("the rye note", "Rye on Fridays.", None))
        with self.assertRaises(LineShapeError):
            RecordLine.make("2051-03-02", "x", was="a", now="b", removed="c")
        exclusion = RecordLine.make("2051-03-02", "the shop chat", not_kept="a family matter",
                                    why='Ilse said "do not keep that"')
        self.assertEqual(exclusion.render(),
                         '- 2051-03-02 — the shop chat: not kept: a family matter. Why: Ilse said "do not keep that".')
        parsed = RecordLine.parse(exclusion.render())
        self.assertEqual((parsed.what, parsed.not_kept, parsed.removed), ("the shop chat", "a family matter", None))
        self.assertIsNone(RecordLine.parse('- 2051-03-02 — tidied. Why: the note said not kept: x.').not_kept)
        for forms in ({"removed": "c", "not_kept": "d"}, {"was": "a", "now": "b", "not_kept": "d"}):
            with self.assertRaises(LineShapeError):
                RecordLine.make("2051-03-02", "x", **forms)
        pointer = RecordPointerLine(
            "2050-01-02", "2050-12-30", 14, ".forest/archive/2051-03-02/04 stm/bakery/deck-oven/repairs.md.gz"
        )
        self.assertEqual(RecordPointerLine.parse(pointer.render()), pointer)

    def test_reference_and_current_state_lines(self) -> None:
        reference = ReferenceLine.parse("- 2051-03-02 — the hinge order: recorded in `04 stm/bakery/suppliers/millstone-flour.md` (archived).")
        self.assertEqual((reference.event, reference.archived), ("the hinge order", True))
        state = CurrentStateLine.parse("- Working again (as of 2051-03-02; source `04 stm/bakery/deck-oven/repairs.md`)")
        self.assertEqual((state.statement, state.date), ("Working again", "2051-03-02"))

    def test_spans_links_and_rewrites(self) -> None:
        line = "- see `04 stm/a/b/c.md`, `not a path` and [[03 mtm/a/b|b MTM]]; `04 stm/a/b` too"
        self.assertEqual([span.text for span in code_span_paths(line)], ["04 stm/a/b/c.md", "04 stm/a/b"])
        self.assertEqual([link.path for link in wikilinks(line)], ["03 mtm/a/b.md"])
        rewritten = rewrite_code_span_paths(line, {"04 stm/a/b": "04 stm/x/y"})
        self.assertEqual(rewritten, "- see `04 stm/x/y/c.md`, `not a path` and [[03 mtm/a/b|b MTM]]; `04 stm/x/y` too")
        self.assertEqual(rewrite_code_span_paths(line, {"04 stm/a/b/c.md": "04 stm/a/b/d.md"}).count("d.md"), 1)


if __name__ == "__main__":
    unittest.main()
