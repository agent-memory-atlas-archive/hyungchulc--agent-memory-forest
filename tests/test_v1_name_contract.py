"""Invented names exercise the same identity from the argument to its tree."""

from __future__ import annotations

import bounds
import clock_rules_walk

import argparse
import ast
import json
import os
import select
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from unittest import mock

from memory_forest import agents, cli, credentials, groups, mcp, model, move, names, registry
from memory_forest.config import load_config
from memory_forest.errors import MemoryForestError
from memory_forest.plan import Plan

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS, COOLING, _call, _tool_body
from test_v1_names import _cli
from v1_support import ForestTestCase


class RepresentationTests(ForestTestCase):
    def test_malformed_name_lines_remain_shape_errors(self):
        with self.assertRaises(model.LineShapeError):
            model.RegistryLine.parse('- `clerk` —  · agent · tree · `02 ltm/clerk_LTM.md`')
        for line in ('- `clerk`', '- `clerk` — Clerk · role (updated 2051-03-02)'):
            with self.assertRaises(model.LineShapeError):
                model.AgentLine.parse(line)

    def test_root_lists_keep_agent_status_section_order_and_absent_root_unchanged(self):
        from memory_forest import maps
        empty = self.make_forest({'forest.toml': ''}, name='no-root-index')
        plan = Plan.open(empty)
        changes = []
        maps.sync_root(plan, changes)
        move._fill_root_line(plan, 'clerk', 'Old Clerk', {'name': 'New Clerk'})
        self.assertEqual((plan.after, changes), ({}, []))
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS}, name='root-lists')
        move.execute(root, 'new-agent', id='clerk', name='Crumb · Clerk', today='2051-03-02')
        plan = Plan.open(root)
        plan.write('01 xltm/XLTM.md', '# Forest\n\n## Identity\n\n## Agents\n\n## Update record\n')
        for status, ended in (('ended', True), ('active', False)):
            ref = plan.subject('clerk')
            plan.replace_subject(ref, replace(ref.line, status=status))
            maps.sync_root(plan, [])
            text = plan.read('01 xltm/XLTM.md')
            self.assertLess(text.index('## Trees'), text.index('## Agents'))
            self.assertLess(text.index('## Agents'), text.index('## Update record'))
            row = next(model.AgentLine.parse(line) for line in model.parse_page(text).section('Agents').lines
                       if line.startswith('- `clerk`'))
            self.assertEqual((row.name, row.ended), ('Crumb · Clerk', ended))

    def test_filling_root_names_and_roles_preserves_other_lines_and_owner_captions(self):
        root = self.make_forest()
        plan = Plan.open(root)
        original = ('# Forest\n\n## Agents\n- `clerk` — Old Clerk · ' + move.ROLE_UNSET
                    + '\n- `other` — Old Clerk · ' + move.ROLE_UNSET + '\n- `bare`\n- not a list\n')
        plan.write('01 xltm/XLTM.md', original)
        move._fill_root_line(plan, 'clerk', 'Old Clerk', {'name': 'New · Clerk', 'role': 'counts trays'})
        text = plan.read('01 xltm/XLTM.md')
        row = model.AgentLine.parse(next(line for line in text.splitlines() if line.startswith('- `clerk`')))
        self.assertEqual((row.name, row.role), ('New · Clerk', 'counts trays'))
        self.assertIn('- `other` — Old Clerk · ' + move.ROLE_UNSET, text)
        self.assertIn('- `bare`\n- not a list', text)
        custom = '# Forest\n\n## Agents\n- `clerk` — Custom caption · custom role\n'
        plan.write('01 xltm/XLTM.md', custom)
        move._fill_root_line(plan, 'clerk', 'Old Clerk', {'name': 'New Clerk', 'role': 'counts trays'})
        self.assertEqual(plan.read('01 xltm/XLTM.md'), custom)
        plan.write('01 xltm/XLTM.md', '# Forest\n\n## Agents\n- `clerk` — Old Clerk\n')
        move._fill_root_line(plan, 'clerk', 'Old Clerk', {'role': 'counts trays'})
        self.assertIn('- `clerk` — Old Clerk\n', plan.read('01 xltm/XLTM.md'))

    def test_fill_only_supplies_an_unset_role_and_requires_an_agent_tree(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        move.execute(root, 'new-agent', id='clerk', name='Crumb Clerk', today='2051-03-02')
        plan = Plan.open(root)
        self.assertEqual(move.fill_agent(plan, 'clerk', {'role': 'counts trays'}), ['role'])
        self.assertIn('## Role\n\ncounts trays\n', plan.read('02 ltm/clerk_LTM.md'))
        self.assertEqual(move.fill_agent(plan, 'clerk', {}), [])
        for text in ('---\nentity: clerk\n---\n# Crumb Clerk\n',
                     '---\nentity: clerk\n---\n# Crumb Clerk\n\n## Role\n\ncustom role\n'):
            plan.write('02 ltm/clerk_LTM.md', text)
            with self.assertRaises(MemoryForestError) as caught:
                move.fill_agent(plan, 'clerk', {'role': 'counts trays'})
            self.assertEqual(caught.exception.code, 'role_set')
            self.assertEqual(plan.read('02 ltm/clerk_LTM.md'), text)
        with self.assertRaises(MemoryForestError) as caught:
            move.fill_agent(plan, 'deck-oven', {'name': 'New Oven'})
        self.assertEqual(caught.exception.code, 'not_an_agent_tree')

    def test_retitle_preserves_unrelated_descriptions_and_custom_titles(self):
        root = self.make_forest()
        plan = Plan.open(root)
        page = '02 ltm/bakery_LTM.md'
        text = ('# Bakery\n\n## Branches\n- `deck-oven` — Deck Oven\n- `other` — Deck Oven\n'
                '- `deck-oven.md` — Custom description\n- `bare`\n- not a list\n')
        plan.write(page, text)
        plan.write('02 ltm/deck-oven_LTM.md', '---\nentity: other\n---\n# Deck Oven\n')
        plan.hints['branch:bakery/deck-oven'] = 'Deck Oven'
        move._retitle(plan, 'deck-oven', 'bakery', 'Deck Oven', 'New Oven')
        self.assertIn('- `deck-oven` — New Oven', plan.read(page))
        for line in ('- `other` — Deck Oven', '- `deck-oven.md` — Custom description', '- `bare`', '- not a list'):
            self.assertIn(line, plan.read(page))
        self.assertEqual(plan.hints['branch:bakery/deck-oven'], 'New Oven')
        self.assertEqual(model.parse_page(plan.read('02 ltm/deck-oven_LTM.md')).title, 'Deck Oven')
        board = '03 mtm/bakery/deck-oven.md'
        self.assertEqual(model.parse_page(plan.read(board)).title, 'New Oven')
        move._retitle(plan, 'deck-oven', 'bakery', 'Deck Oven', 'Another Oven')
        self.assertEqual(model.parse_page(plan.read(board)).title, 'New Oven')
        self.assertEqual(plan.hints['branch:bakery/deck-oven'], 'New Oven')

    def test_a_staged_branch_rename_carries_its_existing_caption_for_materialization(self):
        root = self.make_forest()
        plan = Plan.open(root)
        plan.write('02 ltm/bakery_LTM.md', '# Bakery\n\n## Branches\n- `deck-oven` — Repairs in working order\n')
        # A staged destination hint must not override the source board's existing caption.
        plan.hints['branch:bakery/hearth-oven'] = 'An earlier proposed caption'
        move.rename_subject(plan, 'deck-oven', new_id='hearth-oven', new_name='Hearth Oven')
        self.assertEqual(plan.hints['branch:bakery/hearth-oven'], 'Repairs in working order')
        self.assertIn('rename deck-oven -> hearth-oven (Hearth Oven)', plan.operations)

    def test_legacy_literal_references_and_invalid_marked_values_remain_literal(self):
        values = ('Literal &#65;', 'Literal &#1114112;', 'Literal &#55296;',
                  'Literal &#' + '9' * 5000 + ';', names.ENCODED_NAME + '&#1114112;',
                  names.ENCODED_NAME + '&#55296;', names.ENCODED_NAME + '&#65;tail',
                  names.ENCODED_NAME + '&#' + '9' * 5000 + ';')
        for value in values:
            with self.subTest(value=value[:80]):
                self.assertEqual(names.decode(value), value)
                self.assertEqual(model.parse_page('# ' + value + '\n').title, value)
                line = '- `sifter` — ' + value + ' · agent · tree · `02 ltm/sifter_LTM.md`'
                self.assertEqual(model.RegistryLine.parse(line).name, value)
                self.assertEqual(model.AgentLine.parse('- `sifter` — ' + value + ' · sifts').name, value)
        # An ordinary subject's title and name are not implicitly an agent representation.
        ordinary = '- `sifter` — Literal &#65; · device · tree · `02 ltm/sifter_LTM.md`'
        self.assertEqual(model.RegistryLine.parse(ordinary).name, 'Literal &#65;')

    def test_a_name_that_reads_as_a_credential_keeps_its_identity_through_masking_upkeep(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        display = 'Crumb password 739154'
        config = load_config(root)
        self.assertNotEqual(credentials.mask(display, config=config, ledger=None), display)
        resolved = groups.resolve_caller(root, 'night-shift', display)
        body = mcp.record_turn(root, resolved.id, 'the fictional crate was checked', origin='interactive',
                               session='fictional-name-upkeep', group='night-shift',
                               at=datetime(2051, 3, 2, 12, tzinfo=timezone.utc))
        self.assertEqual(body['tree'], 'made')
        tree = root / '02 ltm' / (resolved.id + '_LTM.md')
        index = root / '03 mtm' / resolved.id / (resolved.id + '_MTM.md')
        before = {path: path.read_bytes() for path in (tree, index)}
        self.assertEqual(model.parse_page(tree.read_text()).title, display)
        self.assertEqual(credentials.mask(tree.read_text(), config=config, ledger=None), tree.read_text())
        credentials.fix(root, all_pages=True)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(registry.build(root).subject(resolved.id)['name'], display)
        self.assertEqual(load_config(root).agents.declared(resolved.id).name, display)
        self.assertEqual(groups.resolve_caller(root, 'night-shift', display).id, resolved.id)

    def test_one_encoding_round_trips_literal_entities_delimiters_and_markdown(self):
        for name in ('Plain Bot', 'Crumb ` Bot', 'Crumb · Bot', 'Mask [REDACTED]',
                     '<Sift>&* _Bot_;', '&#91;literal&#93;', 'كشاف', '★', 'Literal \\ Bot',
                     names.ENCODED_NAME + '&#65;'):
            with self.subTest(name=name):
                encoded = names.encode(name)
                self.assertEqual(names.decode(encoded), name)
                line = model.RegistryLine('sifter', name, 'agent', 'tree', '02 ltm/sifter_LTM.md', [name, 'Other; Alias'])
                parsed = model.RegistryLine.parse(line.render())
                self.assertEqual((parsed.name, parsed.aliases), (name, [name, 'Other; Alias']))
                root_line = model.AgentLine('sifter', name, 'sifts')
                self.assertEqual(model.AgentLine.parse(root_line.render()).name, name)
                self.assertEqual(model.parse_page('# ' + encoded + '\n').title, name)
        legacy = '- `sifter` — Sifter · agent · tree · `02 ltm/sifter_LTM.md` · aliases: `Old Sifter`; Old Sifter'
        self.assertEqual(model.RegistryLine.parse(legacy).aliases, ['Old Sifter'])

    def test_fill_and_rename_use_agent_keys_and_the_same_encoding(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        move.execute(root, 'new-agent', id='sifter', today='2051-03-02')
        with (root / 'forest.toml').open('a') as stream:
            stream.write('\n[agents.known.sifter]\nname = "Crumb · ` [REDACTED]"\n')
        from memory_forest import levels
        levels.apply(root, subjects=['sifter'], today='2051-03-02')
        path = root / '02 ltm/sifter_LTM.md'
        self.assertEqual(model.parse_page(path.read_text()).title, 'Crumb · ` [REDACTED]')
        move.execute(root, 'rename', target='sifter', name='Crate.Bot', today='2051-03-02')
        move.execute(root, 'new-agent', id='sifter-two', name='Crate Bot', today='2051-03-02')
        move.execute(root, 'rename', target='sifter', name='New · Name', today='2051-03-02')
        self.assertEqual(model.parse_page(path.read_text()).title, 'New · Name')
        self.assertEqual(registry.build(root).subject('sifter')['name'], 'New · Name')
        self.assertEqual(registry.build(root).lookup('New · Name')['id'], 'sifter')

    def test_agent_rename_ignores_subject_confusables_and_preserves_key_aliases_and_legacy_names(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        move.execute(root, 'new-agent', id='clerk', name='Crumb Clerk', today='2051-03-02')
        move.execute(root, 'rename', target='clerk', name='Deck Oven', today='2051-03-02')
        move.execute(root, 'rename', target='clerk', name='DECK_OVEN', today='2051-03-02')
        subject = registry.build(root).subject('clerk')
        self.assertEqual(subject['name'], 'DECK_OVEN')
        self.assertEqual(subject['aliases'], ['Crumb Clerk'])
        move.execute(root, 'rename', target='front-desk', name='Desk · ` [REDACTED]', today='2051-03-02')
        self.assertEqual(registry.build(root).subject('front-desk')['name'], 'Desk · ` [REDACTED]')
        self.assertEqual(model.parse_page((root / '02 ltm/front-desk_LTM.md').read_text()).title,
                         'Desk · ` [REDACTED]')

    def test_holders_and_fill_compare_only_agent_keys(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        self.assertEqual(registry.build(root).lookup('the desk')['id'], 'front-desk')
        move.execute(root, 'new-agent', id='crate-dot', name='Crate.Bot', today='2051-03-02')
        plan = Plan.open(root)
        self.assertEqual(plan.agent_name_holders(['Crate Bot', 'Deck Oven']), {})
        self.assertEqual(plan.agent_name_holders(['CRATE.BOT']), {'CRATE.BOT': 'crate-dot'})
        self.assertEqual(plan.agent_name_holders(['CRATE.BOT'], besides='crate-dot'), {})


class IdentityTests(ForestTestCase):
    def test_forest_identity_precedes_every_groups_membership_including_main(self):
        # Match kind x membership x open/closed, with an id shadow as a separate agent.
        for opened in (False, True):
            for membership in ('member', 'absent', 'main'):
                settings = BAKERY_GROUPS + '\n[agents.known.shadow]\nname = "front-desk"\n'
                settings += '\n[agents.known.tray]\nname = "Tray Keeper"\n'
                settings += '\n[agents.groups.local]\nmembers = ["shadow"' + (', "tray"' if membership == 'member' else '') + ']\n'
                settings += 'open = ' + str(opened).lower() + '\n'
                if membership == 'main':
                    settings = settings.replace('orchestrators = ["front-desk"]', 'orchestrators = ["front-desk", "tray"]')
                root = self.make_forest({**FILES, 'forest.toml': settings}, name=f'{opened}-{membership}')
                for given, wanted in [('tray', 'tray'), ('TRAY_KEEPER', 'tray'), ('shadow', 'shadow'),
                                      ('front-desk', 'front-desk'), ('Fresh Clerk', None)]:
                    with self.subTest(open=opened, membership=membership, given=given):
                        allowed = wanted in {'shadow', 'front-desk'} or membership != 'absent' and wanted == 'tray' or opened
                        if allowed:
                            chosen = groups.resolve_caller(root, 'local', given).id
                            if wanted is not None:
                                self.assertEqual(chosen, wanted)
                        else:
                            with self.assertRaises(MemoryForestError) as caught:
                                groups.resolve_caller(root, 'local', given)
                            self.assertEqual(caught.exception.code, 'agent_not_in_group')
                reply, _ = mcp.handle_message(root, '', _call('memory_read', {'agent': 'front-desk', 'path': COOLING}),
                                               'stdio', '2025-11-25', group='local')
                self.assertFalse(reply['result']['isError'], _tool_body(reply))
                self.assertIn('cool', json.dumps(_tool_body(reply)).lower())

    def test_check_names_ambiguous_declarations_id_shadows_and_invisible_pairs_with_edits(self):
        settings = BAKERY_GROUPS + '\n[agents.known.first]\nname = "Sift Clerk"\n'
        settings += '\n[agents.known.second]\nname = "SIFT_CLERK"\n'
        settings += '\n[agents.known.shadow]\nname = "front-desk"\n'
        settings += '\n[agents.known.front-desk]\nname = "Front Desk"\n'
        settings += '\n[agents.known.hidden]\nname = "Sift\\u200b Clerk"\n'
        settings += '\n[agents.known.isolated]\nname = "Opaque\\u200d Clerk"\n'
        settings += '\n[agents.known.soft]\nname = "Soft\\u00ad Clerk"\n'
        settings += '\n[agents.known.aaa]\nname = "zzz"\n[agents.known.zzz]\nname = "Last Clerk"\n'
        root = self.make_forest({**FILES, 'forest.toml': settings})
        problems = agents.problems(root, load_config(root), agents.Subjects({}, frozenset()))
        text = '\n'.join(p.detail for p in problems)
        self.assertIn('one name key', text)
        self.assertIn('Change name in [agents.known.second]', text)
        self.assertIn('another agent\'s exact id', text)
        self.assertIn('Change name in [agents.known.shadow]', text)
        self.assertIn('Change name in [agents.known.aaa]', text)
        self.assertIn('`first` (Sift Clerk) and `hidden`', text)
        held = [p.detail for p in problems if p.code == 'agent_name_invisible' and 'holds an invisible' in p.detail]
        self.assertEqual(len(held), 3)
        self.assertTrue(any('`isolated`' in line for line in held))
        self.assertTrue(any('`soft`' in line for line in held))
        self.assertNotEqual(names.key('Sift\u200b Clerk'), names.key('Sift Clerk'))

    def test_bad_member_references_are_reported_and_runtime_duplicate_diagnostics_stay_scoped(self):
        text = BAKERY_GROUPS + '\n[agents.known.scout]\nname = "Clerk"\n'
        text += '\n[agents.groups.current]\nmembers = ["clerk", "scout", 7, "Unmapped ★"]\n'
        text += '\n[agents.groups.other]\nmembers = ["scout", "scout"]\n'
        root = self.make_forest({**FILES, 'forest.toml': text})
        config = load_config(root)
        problems = agents.problems(root, config, agents.Subjects({'till-bot': 'device'}, frozenset()))
        lines = [p.detail for p in problems]
        self.assertTrue(any('cannot be placed' in line for line in lines))
        self.assertTrue(any('holds 7' in line for line in lines))
        self.assertTrue(any('registered as a `device`' in line for line in lines))
        self.assertEqual(len([line for line in lines if 'remove the duplicate reference' in line]), 1)
        self.assertIn('agents.groups.current.members', next(line for line in lines if 'remove the duplicate' in line))
        self.assertEqual(agents.settings_differ(config, {}, {}), [])
        # An already-invalid area id retains its own true reason instead of being overwritten by type.
        issues = agents.problems(root, config, agents.Subjects({'till-bot': 'device'}, frozenset({'till-bot'})))
        till = [p.detail for p in issues if '[agents.known.till-bot]' in p.detail]
        self.assertEqual(len(till), 1)
        self.assertIn('shared tree', till[0])

        ambiguous = text + '\n[agents.known.first]\nname = "Sift Clerk"\n[agents.known.second]\nname = "sift_clerk"\n'
        ambiguous += '\n[agents.groups.ambiguous]\nmembers = ["Sift Clerk", "first"]\n'
        (root / 'forest.toml').write_text(ambiguous)
        issues = agents.problems(root, load_config(root), agents.Subjects({}, frozenset()))
        self.assertFalse(any('groups.ambiguous.members' in p.detail and 'remove the duplicate' in p.detail for p in issues))


class IdContractTests(ForestTestCase):
    def test_allocator_asks_only_the_smallest_candidates_until_the_first_free_id(self):
        cases = (
            ('Crate Bot', {}, ['crate-bot'], 'crate-bot'),
            ('Crate Bot', {'crate-bot': ['agent'], 'crate-bot-2': ['agent']},
             ['crate-bot', 'crate-bot-2', 'crate-bot-3'], 'crate-bot-3'),
            ('Crate Bot', {'crate-bot': ['subject:device'], 'crate-bot-agent': ['agent']},
             ['crate-bot', 'crate-bot-agent', 'crate-bot-agent-2'], 'crate-bot-agent-2'),
            ('con', {}, ['con-agent'], 'con-agent'),
        )
        for name, holders, candidates, expected in cases:
            remaining = list(candidates)
            def held(candidate):
                self.assertTrue(remaining, ('unexpected extra id probe', candidate))
                self.assertEqual(candidate, remaining.pop(0))
                return holders.get(candidate, [])
            self.assertEqual(names.new_id(name, held), expected)
            self.assertEqual(remaining, [])

    def test_namespace_holders_keep_each_non_agent_reason_and_agent_record_precedence(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        base = load_config(root)
        config = replace(base, owner=replace(base.owner, aliases=('Shelf Keeper',)),
                         subjects=replace(base.subjects, system_id='clockwork'))
        fake = mock.Mock(entities={'widget': {'type': 'device'}, 'untyped': {},
                                   'registered-clerk': {'type': 'agent'}},
                         tree_kinds={'area-only': 'area', 'registered-clerk': 'agent'})
        with mock.patch.object(agents, '_registry', return_value=fake), \
                mock.patch.object(agents, 'lanes', return_value=['raw-clerk']):
            held = agents.taken(root, config)
        self.assertIn('owner-alias', held['shelf-keeper'])
        self.assertEqual(held['clockwork'], ['system'])
        self.assertIn('group', held['night-shift'])
        self.assertEqual(held['widget'], ['subject:device'])
        self.assertEqual(held['registered-clerk'], ['agent'])
        self.assertEqual(held['raw-clerk'], ['agent'])
        self.assertEqual(held['area-only'], ['area-tree'])
        self.assertNotIn('untyped', held)
        with mock.patch.object(agents, '_registry', return_value=None), \
                mock.patch.object(agents, 'lanes', side_effect=OSError):
            held = agents.taken(root, replace(config, subjects=replace(config.subjects, system_id='')))
        self.assertNotIn('', held)
        self.assertNotIn('widget', held)

    def test_device_table_is_a_path_contract_separate_from_the_slug_pattern(self):
        expected = {'con', 'prn', 'aux', 'nul', 'conin$', 'conout$',
                    *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10)),
                    'com¹', 'com²', 'com³', 'lpt¹', 'lpt²', 'lpt³'}
        self.assertEqual(set(names.DEVICE_NAMES), expected)
        for base in names.DEVICE_NAMES:
            for spelling in (base, base.upper(), base + '.txt', base.upper() + '.part'):
                self.assertTrue(names.reserved_id(spelling), spelling)
        self.assertTrue(names.reserved_id('tray.'))
        self.assertTrue(names.reserved_id('tray '))
        for base in ('con', 'nul', 'aux', 'prn', 'com1', 'lpt9'):
            self.assertEqual(names.new_id(base, lambda _id: []), base + '-agent')
        self.assertFalse(names.reserved_id('console'))
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        for name in ('con', 'nul', 'aux', 'prn', 'com1', 'lpt9'):
            agent = groups.resolve_caller(root, 'night-shift', name).id
            self.assertFalse(names.reserved_id(agent))
            self.assertEqual(agent, name + '-agent')
        with (root / 'forest.toml').open('a') as stream:
            stream.write('\n[agents.known.con]\nname = "Console Clerk"\n')
        self.assertEqual(groups.resolve_caller(root, 'night-shift', 'Console Clerk').id, 'con')
        if os.name != 'nt':
            body = mcp.record_turn(root, 'con', 'A fictional console was checked.', origin='interactive',
                                   session='fictional-reserved-handle', at=datetime(2051, 3, 2, 12, tzinfo=timezone.utc))
            self.assertEqual((body['stored'], body['tree']), ('written', 'made'))
            self.assertEqual(model.parse_page((root / '02 ltm/con_LTM.md').read_text()).title, 'Console Clerk')
        issues = agents.problems(root, load_config(root), agents.Subjects({}, frozenset()))
        self.assertTrue(any('reserved device' in p.detail and 'Rename the table' in p.detail for p in issues))

    def test_agent_id_record_wins_over_every_partial_tree_classification(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        first = groups.resolve_caller(root, 'night-shift', 'Crate.Bot').id
        # A partial tree is deliberately classified as an area by a registry snapshot.
        fake = mock.Mock(entities={}, tree_kinds={first: 'area'})
        with mock.patch.object(agents, '_registry', return_value=fake):
            held = agents.taken(root, load_config(root))
            self.assertEqual(held[first], ['agent'])
            self.assertEqual(agents.new_id('Crate Bot', held), 'crate-bot-2')

    def test_hangul_rows_are_the_standard_end_of_word_values_not_cluster_letters(self):
        table = next(table for table in names.tables() if table.name == 'hangul').letters
        # Revised Romanization, each syllable alone. No sound changes cross syllables.
        initials = 'g kk n d tt r m b pp s ss EMPTY j jj ch k t p h'.split()
        vowels = 'a ae ya yae eo e yeo ye o wa wae oe yo u wo we wi yu eu ui i'.split()
        finals = 'k k k n n n t l k m l l l p l m p p t t ng t t k t p t'.split()
        for start, expected in ((0x1100, initials), (0x1161, vowels), (0x11a8, finals)):
            for offset, value in enumerate(expected):
                self.assertEqual(table[chr(start + offset)], '' if value == 'EMPTY' else value)
        for syllables, value in [('각갂갃갉갘', 'k'), ('간갅갆', 'n'), ('갇갓갔갖갗같갛', 't'),
                                 ('갈갋갌갍갏', 'l'), ('감갊', 'm'), ('갑값갚갎', 'p'), ('강', 'ng')]:
            for syllable in syllables:
                self.assertEqual(names.make_id(syllable), 'ga' + value)

    def test_changed_tables_never_reallocate_existing_names(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        before = {name: groups.resolve_caller(root, 'night-shift', name).id for name in ('갂', '갓', '갖', '갛')}
        self.assertEqual(set(before.values()), {'gak', 'gat', 'gat-2', 'gat-3'})
        with mock.patch.object(names, 'make_id', side_effect=AssertionError('stored id recomputed')):
            for name, agent in before.items():
                self.assertEqual(groups.resolve_caller(root, 'night-shift', name).id, agent)
        # Make a forest with the earlier final rows, then put the current table back underneath it.
        letters, doubling = names._merged()
        earlier = {**letters, '\u11a9': 'kk', '\u11ba': 's', '\u11bd': 'j', '\u11c2': 'h'}
        old_root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS}, name='earlier-table')
        with mock.patch.object(names, '_merged', return_value=(earlier, doubling)):
            old_ids = {name: groups.resolve_caller(old_root, 'night-shift', name).id
                       for name in ('갂', '갓', '갖', '갛')}
        self.assertEqual(old_ids, {'갂': 'gakk', '갓': 'gas', '갖': 'gaj', '갛': 'gah'})
        for name, agent in old_ids.items():
            self.assertNotEqual(names.make_id(name), agent)
            self.assertEqual(groups.resolve_caller(old_root, 'night-shift', name).id, agent)


class ArgumentContractTests(ForestTestCase):
    def test_every_string_in_every_tool_schema_is_checked_before_trimming(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        for tool in mcp._GROUP_TOOLS:
            for key, schema in tool['inputSchema']['properties'].items():
                if schema.get('type') != 'string':
                    continue
                for character in ('\x00', '\x85', '\u2028', '\u2029', '\ud800'):
                    for position in range(3):
                        arguments = {'agent': 'Till Bot', **{name: 'demo' for name in tool['inputSchema'].get('required', [])}}
                        arguments[key] = ('demo'[:position] + character + 'demo'[position:]) if position == 1 else (character + 'demo' if position == 0 else 'demo' + character)
                        reply, _ = mcp.handle_message(root, '', _call(tool['name'], arguments), 'stdio', '2025-11-25', group='night-shift')
                        body = _tool_body(reply)
                        self.assertEqual(body.get('next'), 'fix', (tool['name'], key, position, body))
                        self.assertEqual(body.get('code'), 'arguments_invalid', (tool['name'], key, body))

    def test_every_text_argument_of_every_command_is_checked_as_supplied(self):
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        def walk(parser, prefix=()):
            children = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
            if children:
                for action in children:
                    for name, child in action.choices.items():
                        yield from walk(child, (*prefix, name))
            else:
                yield prefix, parser
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        tree = cli.build_forest_parser()
        leaves = list(walk(tree))
        invoked = []
        for _command, parser in leaves:
            parser.set_defaults(_run=lambda arguments: invoked.append(arguments))
        for command, parser in leaves:
            actions = [action for action in parser._actions if not action.dest.startswith('_')
                       and action.nargs != 0]
            for selected in actions:
                if selected.type not in (None, str):
                    continue
                for character in ('\x00', '\x85', '\u2028', '\u2029', '\ud800'):
                    for value in (character + 'demo', 'de' + character + 'mo', 'demo' + character):
                        argv = list(command)
                        for action in actions:
                            positional = not action.option_strings
                            if not positional and not action.required and action is not selected and action.dest != 'root':
                                continue
                            normal = (str(root) if action.dest == 'root' else
                                      str(next(iter(action.choices))) if action.choices else
                                      '1' if action.type in (int, float) else 'demo')
                            token = value if action is selected else normal
                            if not positional:
                                argv.append(action.option_strings[-1])
                            argv.extend([token] * (action.nargs if isinstance(action.nargs, int) else 1))
                        if any(action.dest == 'json' for action in parser._actions):
                            argv.append('--json')
                        out, err = StringIO(), StringIO()
                        with mock.patch.object(cli, 'build_forest_parser', return_value=tree), \
                                redirect_stdout(out), redirect_stderr(err):
                            if selected.choices:
                                with self.assertRaises(SystemExit) as stopped:
                                    cli.forest_main(argv)
                                self.assertEqual(stopped.exception.code, 2)
                                continue
                            code = cli.forest_main(argv)
                        self.assertEqual(code, 2, (command, selected.dest, out.getvalue(), err.getvalue()))
                        answer = json.loads(out.getvalue())['error']
                        self.assertEqual((answer['code'], answer['next']), ('arguments_invalid', 'fix'),
                                         (command, selected.dest, answer))
        self.assertEqual(invoked, [])

    def test_name_and_turn_key_reject_raw_controls_at_each_position(self):
        from memory_forest import intake
        root = self.make_forest({**FILES, 'forest.toml': BAKERY_GROUPS})
        for character in ('\n', '\t', '\x85', '\u2028', '\u2029', '\ud800'):
            for text in (character + 'Clerk', 'Cl' + character + 'erk', 'Clerk' + character):
                with self.assertRaises(MemoryForestError) as caught:
                    names.check(text)
                self.assertEqual((caught.exception.code, caught.exception.next_step), ('agent_name_invalid', 'fix'))
                with self.assertRaises(MemoryForestError) as caught:
                    intake.append_owned(root, 'till-bot', 'trays counted', role='agent', origin='interactive', turn_id=text)
                self.assertEqual((caught.exception.code, caught.exception.next_step), ('arguments_invalid', 'fix'))
        long_name = 'Clerk' * 50
        with self.assertRaises(MemoryForestError) as caught:
            names.check(long_name)
        self.assertEqual(caught.exception.details, {'name': long_name[:80]})

    def test_no_test_replaces_the_shared_time_modules_clock(self):
        for path in Path(__file__).parent.glob('*.py'):
            self.assertEqual(clock_rules_walk.patches(path.read_text()), [], path.name)
        bad = ["import time as wall\nmock.patch.object(wall, 'monotonic', fake)",
               "mock.patch.object(intake.time, 'time', fake)", "mock.patch('time.perf_counter', fake)",
               "from memory_forest.search import time as shared\nmock.patch.object(shared, 'sleep', fake)",
               "clock = search.time\nother = clock\nmock.patch.object(other, 'monotonic', fake)",
               "mock.patch('memory_forest.search.time.monotonic', fake)",
               "from unittest.mock import patch as replace\nreplace('time.monotonic', fake)",
               "replace = mock.patch.object\nreplace(search.time, 'monotonic', fake)",
               "import time as shared\nshared.monotonic = fake", "setattr(search.time, 'monotonic', fake)",
               "mock.patch.dict(search.time.__dict__, {'monotonic': fake})"]
        bad.append("import time as shared\nshared.__dict__['monotonic'] = fake")
        bad.append("search.time.__dict__['monotonic'] = fake")
        bad.append("import time as shared\nmock.patch.dict(shared.__dict__, {'monotonic': fake})")
        bad += ["replace = mock.patch\nagain = replace\nagain('time.monotonic', fake)",
                "base = mock.patch\nreplace = base.object\nrepeat = replace\nrepeat(search.time, 'monotonic', fake)"]
        for source in bad:
            self.assertTrue(clock_rules_walk.patches(source), source)
        for source in ("mock.patch.object(search, '_clock', fake)",
                       "from time import monotonic as local\nmock.patch.object(helper, 'local', fake)",
                       "from datetime import time\nmock.patch.object(time, 'isoformat', fake)"):
            self.assertEqual(clock_rules_walk.patches(source), [], source)
        for source in ("local = helper.clock\nmock.patch.object(local, 'tick', fake)",
                       "hook = module.replace\nhook('time.monotonic', fake)",
                       "one = mock.patch.object\nsome = helper.other\nsome(search.time, 'tick', fake)",
                       "patch_object = module.object\npatch_object(search.time, 'tick', fake)"):
            self.assertEqual(clock_rules_walk.patches(source), [], source)


class DocumentationTests(ForestTestCase):
    def test_documents_hold_each_changed_name_contract_sentence(self):
        root = Path(__file__).resolve().parents[1]
        expected = {
            'docs/contract.md': ['an exact stored id wins', 'before membership', 'Cc (controls)',
                                 'same admitted text as callers', 'edits using stable ids preserve both members',
                                 'Cs (surrogates)', 'Zl (line separators)', 'Zp (paragraph separators)',
                                 'at most 200 Unicode', '40,000,000 UTF-8 bytes',
                                 'at the end of a word', 'no sound change carried',
                                 'names.encode', 'names.decode', 'No admitted name has a permanent pending reason'],
            'docs/multi-agent.md': ['or answers `pending` with a reason when tree work cannot finish',
                                    'The next write retries it', 'after completion',
                                    'Concurrent misses share one refresh', 'deadline, cancelled',
                                    'set_value', 'before group membership'],
            'docs/tools.md': ['whole forest, exact stored id first', 'never refused later by a subject-name rule',
                              'starting at completion', 'set_value'],
            'README.md': ['exact stored id wins before the name key', 'admitted names are never refused'],
            'CHANGELOG.md': ['whole forest, exact stored id before name key', 'single', 'stored ids stay unchanged'],
        }
        for file, sentences in expected.items():
            text = ' '.join((root / file).read_text().split())
            for sentence in sentences:
                self.assertIn(sentence, text, (file, sentence))

    def test_identity_consumers_never_use_subject_normalizers_or_another_validator(self):
        root = Path(names.__file__).parent
        consumers = {'agents.py': {'to_fill', 'settings_differ'},
                     'groups.py': {'resolve_caller', '_interpret', '_labels', '_on_source'},
                     'move.py': {'ensure_agent', 'fill_agent'},
                     'plan.py': {'agent_name_holders'}}
        for path in root.glob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name not in consumers.get(path.name, set()):
                    continue
                for call in ast.walk(node):
                    if isinstance(call, ast.Call):
                        method = ast.unparse(call.func).split('.')[-1]
                        self.assertNotIn(method, {'normalize_name', 'loose_name', 'slugify', '_name_ok'},
                                         (path.name, node.name, call.lineno))


class RegistryBoundaryTests(ForestTestCase):
    def test_plan_registry_diagnostics_isolate_bad_lines_and_keep_subject_and_entry_rules(self):
        root = self.make_forest()
        index = '03 mtm/bakery/bakery_MTM.md'
        cases = (
            ('- broken', 'registry_line_invalid'),
            ('- `Bad Id` — Bad · device · twig · `04 stm/bakery/notes/missing.md`', 'registry_line_invalid'),
            ('- `bad` — Bad · spaceship · leaf · `04 stm/bakery/notes/missing.md`', 'registry_line_invalid'),
            ('- `bad` — Bad · device · twig · `04 stm/bakery/notes/missing.md`', 'registry_line_invalid'),
            ('- `deck-oven` — Duplicate · device · branch · `03 mtm/bakery/missing.md`', 'registry_duplicate_id'),
            ('- `bad` — Deck Oven · device · leaf · `04 stm/bakery/notes/bad.md`', 'registry_duplicate_alias'),
        )
        self.assertEqual(Plan.open(root).registry_problems(), set())
        for raw, expected in cases:
            plan = Plan.open(root)
            if expected == 'registry_duplicate_alias':
                plan.write('04 stm/bakery/notes/bad.md', '# Bad\n')
            plan.write(index, plan.read(index).replace('## Branches', raw + '\n\n## Branches'))
            problems = plan.registry_problems()
            self.assertEqual([code for code, _path, _detail in problems], [expected], (raw, problems))
        plan = Plan.open(root)
        ref = plan.subject('millstone-flour')
        plan.replace_subject(ref, replace(ref.line, name='Millstone password 739154'))
        self.assertEqual([code for code, _path, _detail in plan.registry_problems()], ['registry_name_masked'])
        page = '04 stm/people/regulars/seen-once-2051-q1.md'
        for old, new, expected in (
                ('## Bram Eskell', '## Bram password 739154', 'registry_name_masked'),
                ('`bram-eskell` · person', '`bram-eskell` · person · aliases: Millstone', 'registry_entry_is_subject'),
                ('`bram-eskell` · person', '`millstone-flour` · person', 'registry_entry_is_subject')):
            plan = Plan.open(root)
            plan.write(page, plan.read(page).replace(old, new))
            problems = plan.registry_problems()
            self.assertEqual([code for code, _path, _detail in problems], [expected], problems)

    def test_staged_and_disk_agent_names_use_only_agent_keys_with_their_own_namespace(self):
        root = self.make_forest()
        plan = Plan.open(root)
        for agent_id, name in (('crate-dot', 'Crate.Bot'), ('crate-space', 'Crate Bot'), ('oven-clerk', 'Deck Oven')):
            move.ensure_agent(plan, agent_id, name=name)
            self.assertIn(move.ROLE_UNSET, plan.read(f'02 ltm/{agent_id}_LTM.md'))
        before = dict(plan.after)
        self.assertIs(move.ensure_agent(plan, 'crate-dot', name='Ignored Clerk'), False)
        self.assertEqual(plan.after, before)
        self.assertEqual(plan.registry_problems(), set())
        ref = plan.subject('crate-space')
        plan.replace_subject(ref, replace(ref.line, name='CRATE.BOT'))
        problems = plan.registry_problems()
        self.assertEqual([code for code, _path, _detail in problems], ['registry_duplicate_alias'])
        for relative, text in plan.after.items():
            self.assertIsNotNone(text)
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        built = registry.build(root)
        self.assertEqual([problem.code for problem in built.problems], ['registry_duplicate_alias'])
        self.assertIn('have one agent key', next(iter(problems))[2])
        self.assertIn('has the name key', built.problems[0].detail)

    def test_registry_entry_metadata_and_bad_entries_preserve_their_first_valid_holders(self):
        from v1_support import leaf_text
        root = self.make_forest()
        page = root / '04 stm/people/regulars/seen-once-2051-q1.md'
        original = page.read_text()
        page.write_text(original.replace('`bram-eskell` · person', '`bram-eskell` · person · aliases: BRAM ESKELL'))
        mention = '04 stm/bakery/deck-oven/visitor.md'
        (root / mention).write_text(leaf_text('deck-oven', about='bram-eskell'))
        (root / '04 stm/people/regulars/notes.md').write_text(leaf_text('regulars-seen-once', body='## Ordinary note\n\nAn observation.'))
        built = registry.build(root)
        self.assertEqual(built.problems, [])
        self.assertNotIn('entries', built.subject('deck-oven'))
        self.assertNotIn('entries', built.subject('front-desk'))
        self.assertEqual(built.entry('bram-eskell')['about_leaves'], [mention])
        self.assertEqual(built.entry_index['bram-eskell']['page'], '04 stm/people/regulars/seen-once-2051-q1.md')
        self.assertEqual(built.alias_index['bram eskell'], 'regulars-seen-once')
        page.write_text(original.replace('`tamsin-orrow` · person', '`bad id` · person'))
        built = registry.build(root)
        self.assertEqual([problem.code for problem in built.problems], ['collection_entry_malformed'])
        self.assertEqual([entry['id'] for entry in built.subject('regulars-seen-once')['entries']], ['bram-eskell'])
