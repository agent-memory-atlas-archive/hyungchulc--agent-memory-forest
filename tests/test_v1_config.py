from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import SYNTHETIC_FOREST, ForestTestCase  # noqa: E402

from memory_forest.config import (  # noqa: E402
    DEFAULT_CONFIG,
    find_root,
    load_config,
    parse_config,
    parse_identity_format,
    render_config,
)
from memory_forest.errors import MemoryForestError  # noqa: E402


class ConfigTests(ForestTestCase):
    def test_defaults_are_the_designs(self) -> None:
        config = DEFAULT_CONFIG
        self.assertEqual(config.timezone, "UTC")
        self.assertEqual(config.language, "en")
        self.assertEqual((config.owner.id, config.owner.name, config.owner.aliases), ("owner", "the owner", ()))
        self.assertEqual(config.limits.leaf_cap_bytes, 8192)
        self.assertEqual(config.limits.leaf_max_bytes, 20480)
        self.assertEqual(config.limits.record_keep_bytes, 2048)
        self.assertEqual(config.limits.category_max_leaves, 100)
        self.assertEqual(config.limits.about_max_ids, 8)
        self.assertEqual(config.limits.entry_max_bytes, 8192 - 1024)
        self.assertEqual((config.limits.repeat_min_lines, config.limits.repeat_min_chars), (3, 40))
        self.assertEqual(config.numbers.identity_formats, ())
        self.assertEqual(
            (config.levels.leaf_days, config.levels.branch_days, config.levels.tree_subtopics),
            (2, 5, 3),
        )
        self.assertEqual((config.levels.tree_subtopic_days, config.levels.tree_subtopic_leaves), (5, 3))
        self.assertEqual(config.levels.tree_types, ("project", "topic", "product", "device", "title", "agent"))
        self.assertEqual(
            (config.agents.policy, config.agents.orchestrators, config.agents.default_scope),
            ("subject-first", (), "own+shared"),
        )
        self.assertEqual(config.embeddings.provider, "none")
        self.assertIn("agent", config.subject_types)
        self.assertIn("collection", config.subject_types)
        self.assertEqual(config.about_excluded_ids, frozenset({"owner"}))

    def test_the_synthetic_forest_settings_load(self) -> None:
        config = load_config(SYNTHETIC_FOREST)
        self.assertEqual(config.timezone, "America/Halifax")
        self.assertEqual(config.owner.id, "juno-hale")
        self.assertEqual(config.owner.names, ("Juno", "Juno Hale", "J. Hale"))
        self.assertTrue(config.is_orchestrator("studio-hub"))
        self.assertFalse(config.is_orchestrator("kiln-bot"))

    def test_a_missing_file_means_the_defaults(self) -> None:
        self.assertIs(load_config(self.base), DEFAULT_CONFIG)

    def test_an_unknown_key_or_section_is_an_error_that_names_it(self) -> None:
        for text, key in (
            ("[limits]\nleaf_cap = 10\n", "limits.leaf_cap"),
            ("[colours]\nleaf = 1\n", "colours"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(MemoryForestError) as caught:
                    parse_config(text)
                self.assertEqual(caught.exception.code, "config_invalid")
                self.assertEqual(caught.exception.details["key"], key)
                self.assertIn(key, caught.exception.message)

    def test_values_are_checked(self) -> None:
        for text, key in (
            ('[forest]\ntimezone = "Nowhere/Atlantis"\n', "forest.timezone"),
            ("[limits]\nleaf_cap_bytes = -1\n", "limits.leaf_cap_bytes"),
            ('[limits]\nleaf_cap_bytes = "8 KB"\n', "limits.leaf_cap_bytes"),
            ("[limits]\nleaf_cap_bytes = 30000\n", "limits.leaf_max_bytes"),
            ('[agents]\npolicy = "everyone-first"\n', "agents.policy"),
            ('[agents]\ndefault_scope = "some"\n', "agents.default_scope"),
            ('[owner]\nid = "Ilse Varrow"\n', "owner.id"),
            ('[levels]\ntree_types = ["galaxy"]\n', "levels.tree_types"),
            ('[embeddings]\nprovider = "remote"\n', "embeddings.provider"),
            ('[owner]\naliases = ["a", "a"]\n', "owner.aliases"),
            ('[numbers]\nidentity_formats = ["NNNN"]\n', "numbers.identity_formats"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(MemoryForestError) as caught:
                    parse_config(text)
                self.assertEqual(caught.exception.details["key"], key)

    def test_agent_and_collection_are_always_types(self) -> None:
        config = parse_config('[subjects]\ntypes = ["person", "device"]\n[levels]\ntree_types = ["device"]\n')
        self.assertEqual(config.subjects.types, ("person", "device", "agent", "collection"))

    def test_identity_formats_mask_what_their_setting_says(self) -> None:
        after = parse_identity_format("NNNN-NNNN:after-hyphen")
        self.assertTrue(after.pattern().search("id 1234-5678 here"))
        self.assertIsNone(after.pattern().search("id 91234-5678 here"))
        self.assertEqual(after.mask("1234-5678"), "1234-****")
        last = parse_identity_format("AA NNNNNN:last-2")
        self.assertEqual(last.mask("QX 123456"), "QX 1234**")
        with self.assertRaises(MemoryForestError):
            parse_identity_format("NNNN:after-hyphen")

    def test_render_writes_every_setting_and_reads_back_the_same(self) -> None:
        config = parse_config(
            '[owner]\nid = "ilse-varrow"\nname = "Ilse"\naliases = ["Ilse \\"the baker\\" Varrow"]\n'
            '[numbers]\nidentity_formats = ["NN-NNN:last-2"]\n'
        )
        text = render_config(config)
        again = parse_config(text)
        self.assertEqual(again.owner, config.owner)
        self.assertEqual(again.numbers, config.numbers)
        self.assertEqual(render_config(again), text)
        for section in ("[forest]", "[owner]", "[limits]", "[numbers]", "[levels]", "[agents]", "[embeddings]"):
            self.assertIn(section, text)

    def test_find_root_walks_up_to_the_settings_file(self) -> None:
        root = self.base / "forest"
        (root / "04 stm" / "a").mkdir(parents=True)
        (root / "forest.toml").write_text("", encoding="utf-8")
        self.assertEqual(find_root(root / "04 stm" / "a"), root)
        self.assertIsNone(find_root(self.base))

    def test_a_symlinked_settings_file_is_refused(self) -> None:
        root = self.base / "forest"
        root.mkdir()
        (self.base / "elsewhere.toml").write_text("", encoding="utf-8")
        (root / "forest.toml").symlink_to(self.base / "elsewhere.toml")
        with self.assertRaises(MemoryForestError):
            load_config(root)


if __name__ == "__main__":
    unittest.main()
