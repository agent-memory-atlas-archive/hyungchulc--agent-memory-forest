"""Synthetic counterexamples to the bounded agent identity AST walk."""

import bounds  # noqa: F401

from pathlib import Path
import textwrap
import unittest

from memory_forest import names
from name_rules_walk import package_violations, violations


class NameRuleWalkTests(unittest.TestCase):
    def check_source(self, source, *, rejected=True, module='future.py', companions=None):
        sources = {module: textwrap.dedent(source), **(companions or {})}
        found = violations(sources)
        self.assertEqual(bool(found), rejected, '\n'.join(found) or source)
        return found

    def test_package_uses_the_agent_identity_boundary(self):
        self.assertEqual(package_violations(Path(names.__file__).parent), [])

    def test_new_consumer_and_direct_raw_comparison_are_discovered(self):
        for expression in ('agent_name.casefold()', 'agent_name == other',
                           'agent_name in remembered', 'len(agent_name) > 12',
                           'agent_name.isascii()'):
            with self.subTest(expression=expression):
                self.check_source(f'def newly_added(agent_name, other, remembered):\n    return {expression}\n')
        self.check_source('from . import names\ndef future(agent_name):\n    return names.reserved_id(agent_name)\n')

    def test_imported_and_assigned_normalizer_aliases_are_discovered(self):
        self.check_source('''
            from .registry import normalize_name as fold
            normalize = fold
            def future(agent_name):
                alias = fold
                return alias(agent_name)
        ''')
        self.check_source('''
            import unicodedata as unicode
            def future(agent_name):
                return unicode.normalize('NFC', agent_name)
        ''')

    def test_name_flow_crosses_new_helpers_and_modules(self):
        self.check_source('''
            from .helper import compare
            def future(agent_name):
                copied = agent_name
                return compare(copied)
        ''', companions={'helper.py': 'def compare(value):\n    return value.strip().lower()\n'})
        self.check_source('''
            from .helper import preserve
            def future(agent_name):
                return preserve(agent_name).lower()
        ''', companions={'helper.py': 'def preserve(value):\n    return value\n'})

    def test_names_flow_through_nested_and_bound_helpers(self):
        self.check_source('''
            def future(agent_name):
                def compare(value):
                    return value.lower()
                return compare(agent_name)
        ''')
        self.check_source('''
            class Clerk:
                def compare(self, value):
                    return value.lower()
                def future(self, agent_name):
                    return self.compare(agent_name)
        ''')
        self.check_source('''
            from . import names
            def record(agent_name):
                def inner():
                    return names.make_id(agent_name)
                return Agent(agent_name)
            def future(agent_name, agent_id):
                return record(agent_name) == agent_id
        ''', rejected=False)

    def test_empty_returns_and_record_fields_do_not_become_raw_names(self):
        self.check_source('''
            def nothing(agent_name):
                return
            def future(agent_name):
                value = nothing(agent_name)
                if value is not None:
                    return value.lower()
        ''', rejected=False)
        self.check_source('''
            def future(agent_name, other):
                return Agent(agent_name) == other
        ''', rejected=False)

    def test_id_construction_and_decoding_have_different_name_flow(self):
        self.check_source('''
            from . import names
            def future(agent_name, held):
                return names.new_id(agent_name, held).isascii(), names.encode(agent_name).strip()
        ''', rejected=False)
        self.check_source('''
            from . import names
            def future(agent_name):
                return names.decode(agent_name).lower()
        ''')
        for route in ('names.make_id', 'names.new_id', 'agents.new_id'):
            self.check_source('from . import names, agents\n'
                              f'def future(agent_name, held):\n    return {route}(agent_name, held) == "id"\n')

    def test_plain_helpers_container_fields_and_messages_have_distinct_flow(self):
        self.check_source('def future(agent_name):\n    return preserve(agent_name).lower()\n')
        self.check_source('def future(agent_name):\n    return agent_name.encode().lower()\n')
        self.check_source('def fill_agent(plan, agent_id, fills):\n    return fills["name"].lower()\n', module='move.py')
        self.check_source('def fill_agent(plan, agent_id, fills):\n    return fills["role"].lower()\n',
                          module='move.py', rejected=False)
        self.check_source('class Plan:\n    def agent_name_holders(self, names, index):\n        return names[index].lower()\n',
                          module='plan.py')
        self.check_source('def future(agent_name, captions):\n    return captions[agent_name].lower()\n',
                          rejected=False)
        self.check_source('''
            def future(agent_name, agent_id):
                matched = agent_name == agent_id
                caption = f'Agent {agent_name}'
                return str(matched).lower(), caption.lower()
        ''', rejected=False)
        self.check_source('''
            from . import names
            def caption(agent_name):
                return f'Handle {names.make_id(agent_name)}'
            def record(agent_name):
                return Agent(names.make_id(agent_name))
            def nothing(agent_name):
                return
            def future(agent_name, other):
                return caption(agent_name) == other, record(agent_name) == other, nothing(agent_name) == other
        ''', rejected=False)

    def test_stored_ids_can_flow_from_lookup_but_new_handles_are_not_identity(self):
        self.check_source('''
            from . import names
            def future(agent_name, other, rows, besides):
                found = names.find(other, rows)
                selected = found[0]
                return agent_name == selected, agent_name == besides
        ''', rejected=False)
        self.check_source('''
            from . import names
            def future(agent_name, other, rows):
                selected = names.find(other, rows)[0]
                return agent_name == selected
        ''', rejected=False)
        self.check_source('def future(agent_name, other):\n    copied = agent_name\n    return copied == other\n')

    def test_keyword_binding_and_aliases_preserve_the_callees_name_contract(self):
        self.check_source('''
            from . import names
            def identity(value, *, role):
                return names.key(value), role.lower()
            def future(agent_name):
                return identity(value=agent_name, role='caption')
        ''', rejected=False)
        self.check_source('''
            def compare(*, value):
                return value.lower()
            def future(agent_name):
                return compare(value=agent_name)
        ''')
        self.check_source('''
            from .helper import identity
            def future(agent_name, other):
                chosen = identity
                return chosen(agent_name) == chosen(other)
        ''', companions={'helper.py': 'from . import names\ndef identity(value):\n    return names.key(value)\n'},
                          rejected=False)

    def test_module_aliases_preserve_canonical_keys_and_other_rules(self):
        self.check_source('''
            from .names import key as identity
            canonical = identity
            again = canonical
            def future(agent_name, other):
                return again(agent_name) == again(other)
        ''', rejected=False)
        self.check_source('''
            from . import names
            rules = names
            def future(agent_name, other):
                return rules.key(agent_name) == rules.key(other)
        ''', rejected=False)
        self.check_source('''
            from . import names
            identity = names.key
            def future(agent_name, other):
                return identity(agent_name) == identity(other)
        ''', rejected=False)

    def test_agent_conditional_keys_do_not_taint_the_subject_arm(self):
        for parameter, value in (('name', 'name'), ('new_name', 'new_name'), ('aliases', 'aliases[0]')):
            self.check_source('from . import names, registry\n'
                              f'def future(ref, {parameter}, other):\n'
                              '    agent = ref.type == registry.AGENT_TYPE\n'
                              f'    selected = names.key({value}) if agent else {value}\n'
                              '    return selected == other\n', rejected=False)
            self.check_source('from . import registry\n'
                              f'def future(ref, {parameter}):\n'
                              '    agent = ref.type == registry.AGENT_TYPE\n'
                              f'    return {value}.lower() if agent else "ordinary"\n')
        self.check_source('''
            from .registry import AGENT_TYPE
            def future(ref, new_name):
                return new_name.lower() if ref.type == AGENT_TYPE else 'ordinary'
        ''')

    def test_known_agent_type_and_collection_are_independent_roots(self):
        self.check_source('''
            def future(worker: KnownAgent):
                return worker.name.lower()
        ''')
        self.check_source('''
            def future(config):
                return [worker.name.lower() for worker in config.agents.known]
        ''')
        self.check_source('''
            def future(config, identifier):
                selected = config.agents.declared(identifier)
                return selected.name.casefold()
        ''')
        self.check_source('def future(agent):\n    return agent.name.lower()\n')
        self.check_source('''
            def future(worker: KnownAgent):
                copied = worker
                return copied.name.lower()
        ''')

    def test_named_callable_aliases_and_classmethods_preserve_name_flow(self):
        self.check_source('''
            from .helper import preserve
            def future(agent_name):
                chosen = preserve
                return chosen(agent_name).lower()
        ''', companions={'helper.py': 'def preserve(value):\n    return value\n'})
        self.check_source('''
            from .helper import Clerk
            def future(agent_name):
                return Clerk.compare(agent_name)
        ''', companions={'helper.py': 'class Clerk:\n    @classmethod\n    def compare(cls, value):\n        return value.lower()\n'})

    def test_only_the_names_module_owns_raw_normalization(self):
        self.check_source('def key(agent_name):\n    return agent_name.lower()\n',
                          module='names.py', rejected=False)

    def test_observations_and_generic_argument_checks_are_not_alternate_name_admission(self):
        for predicate in ('isinstance(agent_name, str)', 'len(agent_name)', 'any([agent_name])',
                          'all([agent_name])', 'bool(agent_name)', 'names.forbidden(agent_name)'):
            self.check_source('from . import names\n'
                              f'def future(agent_name):\n    if {predicate}:\n        return "a caption"\n',
                              rejected=False)

    def test_the_reviews_tree_settings_and_membership_consumers_are_roots(self):
        cases = (
            ('move.py', 'def ensure_agent(plan, agent_id, name=None):\n    return name.lower()\n'),
            ('move.py', 'def fill_agent(plan, agent_id, fills):\n    return fills.get("name").lower()\n'),
            ('plan.py', 'class Plan:\n    def agent_name_holders(self, names):\n        return [name.lower() for name in names]\n'),
            ('agents.py', 'def to_fill(known, agent_id, name, role):\n    return name == known.name\n'),
            ('agents.py', 'def settings_differ(config, names, roles):\n    return names["fictional-worker"].lower()\n'),
            ('groups.py', 'def _labels(config, group, identities):\n    return reference.lower()\n'),
            ('groups.py', 'def _on_source(given, source_names):\n    return given in source_names\n'),
        )
        for module, source in cases:
            with self.subTest(module=module, source=source):
                self.check_source(source, module=module)

    def test_reverting_to_subject_holders_propagates_the_name(self):
        self.check_source('''
            def ensure_agent(plan, agent_id, name=None):
                return plan.name_holders([name])
        ''', module='move.py', companions={'plan.py': textwrap.dedent('''
            from .registry import loose_name
            class Plan:
                def name_holders(self, names):
                    return [loose_name(value) for value in names]
        ''')})

    def test_subject_names_headings_and_role_heuristics_are_not_agent_identity(self):
        self.check_source('''
            from .registry import normalize_name
            def ordinary(subject_name, heading, role):
                return normalize_name(subject_name), heading.strip(), role.casefold()
        ''', rejected=False)

    def test_serializers_records_and_notices_keep_their_nonidentity_meaning(self):
        self.check_source('''
            def _means(agent_name, declared):
                name = agent_name
                return declared.name != name
        ''', module='groups.py', rejected=False)
        self.check_source('''
            from . import names
            def problems(agent_name, agent_id):
                return names.invisible(agent_name), names.has_invisible(agent_name), Agent(agent_id, agent_name)
        ''', module='agents.py', rejected=False)
        self.check_source('''
            import json
            import builtins
            from .config import _toml_value
            def future(agent_name):
                return (json.dumps(agent_name) == 'encoded', json.loads(agent_name) == 'decoded',
                        repr(agent_name) == 'display', builtins.repr(agent_name) == 'display',
                        _toml_value(agent_name) == 'syntax')
        ''', rejected=False)

    def test_lossless_toml_escaping_is_allowed_only_inside_the_serializer(self):
        source = '''
            def _toml_value(value):
                return value.replace(chr(92), chr(92) * 2)
            def future(agent_name):
                return _toml_value(agent_name)
        '''
        # Literal replacements are the serializer's contract. Arbitrary substitutions are another rule.
        literal = source.replace('chr(92), chr(92) * 2', repr('\\') + ', ' + repr('\\\\'))
        self.check_source(literal, module='config.py', rejected=False)
        self.check_source(literal.replace(repr('\\\\'), repr('')), module='config.py')

    def test_canonical_adapters_exact_ids_and_display_sentinels_are_allowed(self):
        self.check_source('''
            from . import names as rule
            from .names import key as identity
            def future(agent_name, other, agent_id, rows):
                clean = rule.check(agent_name)
                return (identity(clean) == identity(other), rule.same(clean, other),
                        rule.find(clean, rows), rule.bad_reason(clean),
                        clean == agent_id, clean != '', clean is not None,
                        rule.encode(clean))
        ''', rejected=False)

    def test_unknown_validation_predicate_fails_closed(self):
        self.check_source('''
            from outside import approved
            def future(agent_name):
                if approved(agent_name):
                    return agent_name
        ''')
        self.check_source('''
            def future(agent_name):
                if not agent_name:
                    raise ValueError('another admission rule')
        ''')
        self.check_source('''
            def future(agent_name, operation):
                return operation(agent_name)
        ''')

    def test_settings_name_and_member_branches_are_checked_without_tainting_roles(self):
        self.check_source('''
            def _known_agents(table):
                for field_name, value in table.items():
                    if field_name == 'name':
                        return value.lower()
        ''', module='config.py')
        self.check_source('''
            def _agent_groups(table):
                for field_name, value in table.items():
                    if field_name == 'members':
                        return [item.lower() for item in value]
        ''', module='config.py')

    def test_a_literal_id_probe_is_scoped_to_an_unresolved_member_reference(self):
        source = '''
            from . import names
            def _id_ok(value):
                return isinstance(value, str) and value.isascii()
            def _agent_groups(table, rows):
                for field_name, value in table.items():
                    if field_name == 'members':
                        for item in value:
                            reference = names.check(item)
                            hits = names.find(reference, rows)
                            if not hits and _id_ok(reference):
                                return reference
        '''
        self.check_source(source, module='config.py', rejected=False)
        self.check_source(source.replace('not hits and ', ''), module='config.py')
        self.check_source(source.replace('table, rows)', 'table, rows, agent_name)')
                          .replace('_id_ok(reference)', '_id_ok(agent_name)'), module='config.py')
        selected = source.replace('hits = names.find(reference, rows)',
                                  'hits = names.find(reference, rows)\n                            chosen = hits[0]')
        self.check_source(selected.replace('not hits and ', 'reference != chosen and '),
                          module='config.py', rejected=False)
        self.check_source('''
            def _id_ok(value):
                return value.isascii()
            def future(agent_name, hits):
                item = agent_name
                if not hits and _id_ok(item):
                    return item
        ''', module='config.py')

    def test_notice_helpers_cannot_silently_become_identity_predicates(self):
        self.check_source('''
            from .groups import _one_word
            def future(agent_name, other):
                return _one_word(agent_name, other)
        ''')

    def test_other_names_helpers_are_not_identity_authorities(self):
        for expression in ('names.folded(agent_name)', 'names.transliterated(agent_name)',
                           'names.make_id(agent_name) == names.make_id(other)'):
            with self.subTest(expression=expression):
                self.check_source('from . import names\n'
                                  f'def future(agent_name, other):\n    return {expression}\n')
        self.check_source('''
            from . import names
            def future(agent_name):
                alias = names.folded
                return alias(agent_name)
        ''')

    def test_a_generated_id_cannot_become_identity_through_assignments_or_helpers(self):
        self.check_source('''
            from . import names
            def future(agent_name, agent_id):
                generated = names.make_id(agent_name)
                copied = generated
                return copied == agent_id
        ''')
        self.check_source('''
            from .helper import create
            def future(agent_name, agent_id):
                return create(agent_name) == agent_id
        ''', companions={'helper.py': 'from . import names\ndef create(value):\n    return names.make_id(value)\n'})
        self.check_source('''
            from . import names
            def future(agent_name, agent_id):
                candidates = [names.make_id(agent_name)]
                return candidates[0] == agent_id
        ''')

    def test_agent_type_guards_seed_new_mixed_subject_consumers(self):
        self.check_source('''
            from .registry import AGENT_TYPE, normalize_name
            def future(subject):
                if subject.type == AGENT_TYPE:
                    return normalize_name(subject.name)
        ''')

    def test_agent_arm_uses_keys_while_subject_arm_keeps_subject_rules(self):
        source = '''
            from . import names, registry
            def rename_subject(ref, new_name):
                agent = ref.type == registry.AGENT_TYPE
                name = names.check(new_name) if agent else registry.normalize_name(new_name)
                normal = names.key if agent else registry.normalize_name
                return normal(name)
        '''
        self.check_source(source, rejected=False)
        self.check_source(source.replace('names.check(new_name)', 'registry.normalize_name(new_name)'))
        self.check_source(source.replace('normal = names.key', 'normal = registry.normalize_name'))

    def test_toml_escaping_does_not_authorize_other_name_transforms(self):
        source = '''
            def _toml_value(value):
                escaped = value.replace(chr(92), chr(92) * 2)
                return f'"{escaped}"'
            def store(agent_name):
                return _toml_value(agent_name)
        '''
        # The production serializer uses literal lossless substitutions. The walk
        # deliberately does not accept arbitrary expressions as equivalent escapes.
        self.check_source(source, module='config.py')
        literal = f'value.replace({chr(92)!r}, {chr(92) * 2!r})'
        self.check_source(source.replace('value.replace(chr(92), chr(92) * 2)', literal),
                          module='config.py', rejected=False)
        source = source.replace('value.replace(chr(92), chr(92) * 2)', 'value.lower()')
        self.check_source(source, module='config.py')


if __name__ == '__main__':
    unittest.main()
