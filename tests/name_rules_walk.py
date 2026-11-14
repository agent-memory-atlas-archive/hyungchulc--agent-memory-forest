"""Bounded static check of the agent-name identity boundary.

This is a regression walk, not a proof of arbitrary Python. It scans every module,
resolves imports and simple callable aliases, and follows scalar/container name
flow through assignments and statically named package functions. Explicit roots
describe the existing untyped API arguments; independently, ``agent_name`` fields
and arguments and names on KnownAgent objects seed new consumers in new modules.
Agent ids, canonical keys and encoded display text are separate from raw names.

Dynamic dispatch/reflection and arbitrary third-party predicates cannot be proved:
an unresolved predicate receiving a raw name fails closed. Ordinary subject-name
normalization is allowed until an agent-name flow reaches it. Mixed settings
fields are narrowed by their ``field_name`` branch. The few nonidentity operations
below are expression-level exceptions with a reason, never whole-function skips.
The tracked shapes are names, name sequences/mappings, public KnownAgent fields,
and explicit agent-type branches. Untyped external records, computed container
keys, import rebinding and monkeypatching need a new root/flow rule and regression
case; this walk does not claim to infer those semantics from arbitrary Python.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


# Parameters whose generic spelling conceals their public agent-name meaning.
ROOTS = {
    'move.ensure_agent': {'name', 'aliases'},
    'move.new_agent': {'name', 'aliases'},
    'move.fill_agent': {'fills.name'},
    'plan.Plan.agent_name_holders': {'names', 'ref.name'},
    'agents.to_fill': {'name', 'known.name'},
    'agents.settings_differ': {'names', 'known.name'},
    'groups.resolve_caller': {'given'},
    'groups._interpret': {'given', 'source_names'},
    'groups._on_source': {'given', 'source_names'},
    'groups._labels': {'reference'},
    'groups._call_source': {'item'},
    'config._known_agents[name]': {'value'},
    'config._agent_groups[members]': {'item'},
}

# Exact comparisons of representation, not a decision about agent identity.
EXACT_DISPLAY = {
    ('groups._means', 'declared.name != name'): 'settings write readback preserves spelling',
    ('move.rename_subject', 'line.name not in aliases'): 'do not duplicate the exact old display alias',
    ('move.rename_subject', 'line.name != ref.line.name'): 'retitle when display spelling changes, including one key',
    ('move._fill_root_line.filled', 'name_rules.decode(parts[0].strip()) == was_name'):
        'replace the old display text only when unchanged',
    ('groups._call_source', 'len(item) > names.NAME_MAX'): 'member-source transport size bound',
}
NOTICE_CALLS = {'groups._one_word', 'groups._one_edit', 'names.invisible', 'names.has_invisible'}
NOTICE_CALLERS = {'groups._remarks', 'agents.problems'}
CANONICAL = {'names.key', 'names.same', 'names.find', 'names.check', 'names.bad_reason'}
NAME_SERVICES = CANONICAL | {
    'names.encode', 'names.decode', 'names.new_id', 'names.make_id',
    'agents.new_id',
    'names.forbidden', 'names.reserved_id', 'names.invisible', 'names.has_invisible',
}
NAME_METHODS = {
    'lower', 'upper', 'casefold', 'capitalize', 'title', 'swapcase', 'strip', 'lstrip', 'rstrip',
    'split', 'rsplit', 'replace', 'translate', 'removeprefix', 'removesuffix',
    'isalnum', 'isalpha', 'isascii', 'isspace', 'isidentifier', 'isdigit', 'isprintable',
}
OTHER_RULES = {
    'normalize', 'normalize_name', 'loose_name', 'slugify', '_name_ok', 'name_problem',
    '_id_ok', 'is_slug', 'file_id_ok',
    'getattr', 'eval', 'exec',
    'match', 'fullmatch', 'search', 'sub', 'subn', 'findall',
}


def spelling(node: ast.AST) -> str:
    return ast.unparse(node)


def own_nodes(node: ast.AST, agent_tests=frozenset()):
    """Descend a function body without mistaking a nested scope for its parent."""
    yield node
    if isinstance(node, ast.IfExp) and spelling(node.test) in agent_tests:
        yield from own_nodes(node.test, agent_tests)
        yield from own_nodes(node.body, agent_tests)
        return
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from own_nodes(child, agent_tests)


@dataclass
class Function:
    name: str
    module: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    imports: dict[str, str]
    raw: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)
    generated: set[str] = field(default_factory=set)
    returns_raw: bool = False
    returns_generated: bool = False
    callees: dict[str, Function] = field(default_factory=dict, repr=False)
    agent_tests: set[str] = field(default_factory=set)

    def nodes(self):
        return own_nodes(self.node, self.agent_tests)

    def resolve(self, node: ast.AST) -> str:
        value = spelling(node)
        first, dot, rest = value.partition('.')
        if first in self.imports:
            return self.imports[first] + (dot + rest if dot else '')
        if first == 'self' and '.' in self.name[len(self.module) + 1:]:
            return self.name.rsplit('.', 1)[0] + dot + rest
        if first == 'plan':
            return 'plan.Plan' + dot + rest
        if isinstance(node, ast.Name):
            enclosing = self.name
            while enclosing != self.module:
                candidate = enclosing + '.' + value
                if candidate in self.callees:
                    return candidate
                enclosing = enclosing.rsplit('.', 1)[0]
        return self.module + '.' + value

    def is_raw(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        text = spelling(node)
        if text in self.raw or text.endswith('.agent_name') or text == 'agent_name':
            return True
        if isinstance(node, ast.Call):
            target = self.resolve(node.func)
            if target in CANONICAL:
                return target == 'names.check'
            if target in NAME_SERVICES - {'names.decode'} or target in NOTICE_CALLS:
                return False
            if target == 'config._toml_value':
                return False  # The TOML serializer returns syntax, never an identity key.
            if target in self.callees:
                return self.callees[target].returns_raw
            if target in {'json.dumps', 'json.loads', 'builtins.repr'} or spelling(node.func) == 'repr':
                return False  # Serialized representations are not identity strings.
            if target.rsplit('.', 1)[-1][:1].isupper():
                return False  # A record is not its name field; field roots handle records.
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
                key = node.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    return spelling(node.func.value) + '.' + key.value in self.raw
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                if spelling(node.value) + '.' + node.slice.value in self.raw:
                    return True
            return self.is_raw(node.value)
        if isinstance(node, ast.IfExp) and spelling(node.test) in self.agent_tests:
            return self.is_raw(node.body)
        if isinstance(node, (ast.JoinedStr, ast.Compare)):
            return False  # A message or boolean is no longer a raw name.
        return any(self.is_raw(child) for child in ast.iter_child_nodes(node))

    def is_id(self, node: ast.AST) -> bool:
        if _id(node) or spelling(node) in self.ids:
            return True
        if isinstance(node, ast.Call):
            return self.resolve(node.func) == 'names.find'
        return isinstance(node, ast.Subscript) and self.is_id(node.value)

    def is_generated(self, node: ast.AST | None) -> bool:
        """Fresh id output is not evidence that a supplied name selects an existing agent."""
        if node is None:
            return False
        if spelling(node) in self.generated:
            return True
        if isinstance(node, ast.Call):
            target = self.resolve(node.func)
            if target in {'names.make_id', 'names.new_id', 'agents.new_id'}:
                return any(self.is_raw(arg) for arg in node.args)
            if target in self.callees:
                return self.callees[target].returns_generated
            if target.rsplit('.', 1)[-1][:1].isupper():
                return False  # Records carry ids but are not fresh id strings themselves.
        if isinstance(node, (ast.Compare, ast.JoinedStr)):
            return False
        return any(self.is_generated(child) for child in ast.iter_child_nodes(node))


def _imports(tree: ast.AST, module: str) -> dict[str, str]:
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            origin = (node.module or '').removeprefix('memory_forest.')
            for alias in node.names:
                found[alias.asname or alias.name] = '.'.join(filter(None, (origin, alias.name)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name] = alias.name.removeprefix('memory_forest.')
    changed = True
    while changed:
        before = dict(found)
        for node in getattr(tree, 'body', []):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                first, dot, rest = spelling(node.value).partition('.')
                if first in found:
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id not in found:
                            found[target.id] = found[first] + (dot + rest if dot else '')
        changed = before != found
    return found


def _functions(tree: ast.AST, prefix: str):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield prefix + '.' + node.name, node
            yield from _functions(node, prefix + '.' + node.name)
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node, prefix + '.' + node.name)


def _id(node: ast.AST) -> bool:
    value = spelling(node)
    return value.endswith('.id') or value.endswith('_id') or value == 'besides'


def _sentinel(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in (None, '')


def _id_probe(function: Function, call: ast.Call) -> bool:
    """The settings parser may classify an unresolved member reference as a literal id."""
    if function.name not in {'config._agent_groups', 'config._agent_groups[members]'}:
        return False
    if function.resolve(call.func) != 'config._id_ok' or not call.args:
        return False
    argument = spelling(call.args[0])
    references = {'item'}
    for node in own_nodes(function.node):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            value = node.value
            if (isinstance(value, ast.Call) and function.resolve(value.func) == 'names.check'
                    and value.args):
                value = value.args[0]
            if spelling(value) in references:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                references.update(spelling(target) for target in targets)
    if argument not in references:
        return False
    for node in own_nodes(function.node):
        if isinstance(node, ast.BoolOp) and call in tuple(own_nodes(node)):
            terms = {spelling(value) for value in node.values}
            if 'not hits' in terms or f'{argument} != chosen' in terms:
                return True
    return False


def _agent_test(node: ast.AST) -> bool:
    return (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
            and spelling(node.left).endswith('.type')
            and spelling(node.comparators[0]).rsplit('.', 1)[-1] == 'AGENT_TYPE')


def _toml_escape(function: Function, call: ast.Call) -> bool:
    """Only the serializer's two lossless string-escaping substitutions are display work."""
    if function.name != 'config._toml_value' or not isinstance(call.func, ast.Attribute):
        return False
    return (call.func.attr == 'replace' and len(call.args) == 2
            and all(isinstance(arg, ast.Constant) for arg in call.args)
            and tuple(arg.value for arg in call.args) in {(chr(92), chr(92) * 2), ('"', chr(92) + '"')})


def violations(sources: Mapping[str, str]) -> list[str]:
    """Return location-bearing findings for relative Python paths and their source."""
    functions: dict[str, Function] = {}
    for path, source in sources.items():
        module = path.removesuffix('.py').replace('/', '.')
        if module == 'names':
            continue
        tree = ast.parse(source, filename=path)
        imports = _imports(tree, module)
        for name, node in _functions(tree, module):
            function = Function(name, module, node, dict(imports), set(ROOTS.get(name, ())))
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if 'agent_name' in arg.arg:
                    function.raw.add(arg.arg)
                if arg.arg == 'agent':
                    function.raw.add('agent.name')
                if arg.annotation and 'KnownAgent' in spelling(arg.annotation):
                    function.raw.add(arg.arg + '.name')
            # A new module iterating the public KnownAgent collection is a root too.
            for child in own_nodes(node):
                if _agent_test(child):
                    function.agent_tests.add(spelling(child))
                if isinstance(child, ast.Assign) and _agent_test(child.value):
                    function.agent_tests.update(spelling(target) for target in child.targets)
                if isinstance(child, (ast.For, ast.comprehension)):
                    if spelling(child.iter).endswith('.agents.known'):
                        function.raw.add(spelling(child.target) + '.name')
            if function.agent_tests:
                parameters = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
                for child in own_nodes(node):
                    if isinstance(child, ast.IfExp) and spelling(child.test) in function.agent_tests:
                        used = {part.id for part in ast.walk(child.body) if isinstance(part, ast.Name)}
                        function.raw.update(parameters & used & {'name', 'new_name', 'aliases'})
            functions[name] = function
            for child in own_nodes(node):
                if isinstance(child, ast.If) and _agent_test(child.test):
                    owner = spelling(child.test.left).removesuffix('.type')
                    guarded_name = name + f'[agent@{child.lineno}]'
                    guarded = ast.FunctionDef(name=node.name, args=node.args, body=child.body,
                                              decorator_list=[], returns=node.returns,
                                              lineno=child.lineno, col_offset=child.col_offset)
                    functions[guarded_name] = Function(guarded_name, module, guarded, dict(imports),
                                                       {owner + '.name', owner + '.line.name'})
            # Settings uses one ``value`` variable for names, roles and booleans.
            # Analyze the two name-bearing branches independently of those fields.
            branch = {'config._known_agents': 'name', 'config._agent_groups': 'members'}.get(name)
            if branch:
                for child in own_nodes(node):
                    if isinstance(child, ast.If) and spelling(child.test) == f"field_name == {branch!r}":
                        narrowed = ast.FunctionDef(name=node.name, args=node.args, body=child.body,
                                                   decorator_list=[], returns=node.returns,
                                                   lineno=child.lineno, col_offset=child.col_offset)
                        branch_name = name + '[' + branch + ']'
                        functions[branch_name] = Function(branch_name, module, narrowed, dict(imports),
                                                          set(ROOTS[branch_name]))

    for function in functions.values():
        function.callees = functions

    # A finite monotone data-flow worklist. The finite set consists of AST spellings
    # and formal arguments; no iteration budget can silently truncate the walk.
    changed = True
    while changed:
        changed = False
        for function in functions.values():
            old = (set(function.raw), set(function.ids), set(function.generated), dict(function.imports),
                   function.returns_raw, function.returns_generated)
            parent = functions.get(function.name.rsplit('.', 1)[0])
            if parent is not None:
                function.raw.update(parent.raw)
                function.generated.update(parent.generated)
            for node in function.nodes():
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if spelling(node.value) + '.name' in function.raw:
                            function.raw.add(spelling(target) + '.name')
                        if (isinstance(node.value, ast.Call) and
                                spelling(node.value.func).endswith('.agents.declared')):
                            function.raw.add(spelling(target) + '.name')
                        if function.is_raw(node.value):
                            function.raw.add(spelling(target))
                        if function.is_id(node.value):
                            function.ids.add(spelling(target))
                        if function.is_generated(node.value):
                            function.generated.add(spelling(target))
                        callable_value = node.value
                        if isinstance(callable_value, ast.IfExp) and spelling(callable_value.test) in function.agent_tests:
                            callable_value = callable_value.body
                        if isinstance(callable_value, (ast.Name, ast.Attribute)):
                            resolved = function.resolve(callable_value)
                            if (spelling(target) not in function.imports and
                                    (resolved in functions or resolved.rsplit('.', 1)[-1] in OTHER_RULES
                                     or resolved.startswith('names.'))):
                                function.imports[spelling(target)] = resolved
                if isinstance(node, (ast.For, ast.comprehension)) and function.is_raw(node.iter):
                    # An iterable of names has name elements, not agent-id tuples.
                    if isinstance(node.target, ast.Name):
                        function.raw.add(node.target.id)
                if isinstance(node, ast.Call):
                    target = function.resolve(node.func)
                    callee = functions.get(target)
                    if callee is None or target in NOTICE_CALLS or _id_probe(function, node):
                        continue
                    params = [arg.arg for arg in (*callee.node.args.posonlyargs, *callee.node.args.args)]
                    if params and params[0] in {'self', 'cls'}:
                        params = params[1:]
                    keyword_params = [*params, *(arg.arg for arg in callee.node.args.kwonlyargs)]
                    supplied = list(zip(params, node.args)) + [
                        (param, kw.value) for kw in node.keywords for param in keyword_params if kw.arg == param
                    ]
                    for param, value in supplied:
                        if function.is_raw(value) and param not in callee.raw:
                            callee.raw.add(param)
                            changed = True
                if isinstance(node, ast.Return) and function.is_raw(node.value):
                    function.returns_raw = True
                if isinstance(node, ast.Return) and function.is_generated(node.value):
                    function.returns_generated = True
            changed |= old != (function.raw, function.ids, function.generated, function.imports,
                               function.returns_raw, function.returns_generated)

    found = set()
    for function in functions.values():
        def report(node: ast.AST, reason: str) -> None:
            found.add(f'{function.name}:{node.lineno}: {reason}: {spelling(node)}')

        for node in function.nodes():
            if isinstance(node, ast.Call):
                target = function.resolve(node.func)
                method = target.rsplit('.', 1)[-1]
                raw_args = any(function.is_raw(arg) for arg in node.args)
                receiver = isinstance(node.func, ast.Attribute) and function.is_raw(node.func.value)
                if _id_probe(function, node) or _toml_escape(function, node):
                    continue
                parameters = {arg.arg for arg in (*function.node.args.posonlyargs, *function.node.args.args,
                                                   *function.node.args.kwonlyargs)}
                if isinstance(node.func, ast.Name) and node.func.id in parameters and raw_args:
                    report(node, 'unresolved callable receives an agent name')
                if target == 'names.reserved_id' and raw_args:
                    report(node, 'a reserved id predicate cannot validate an agent display name')
                if target in NOTICE_CALLS:
                    if raw_args and function.name not in NOTICE_CALLERS:
                        report(node, 'notice-only comparison reached an identity consumer')
                elif target.startswith('names.') and target not in NAME_SERVICES and raw_args:
                    report(node, 'names helper is not an identity or admission adapter')
                elif target not in NAME_SERVICES:
                    if (method in NAME_METHODS and receiver) or (method in OTHER_RULES and raw_args):
                        report(node, 'agent name transformed or validated outside names.key/check')
            if isinstance(node, ast.Compare):
                if (function.name, spelling(node)) in EXACT_DISPLAY:
                    continue
                values = [node.left, *node.comparators]
                for left, right in zip(values, values[1:]):
                    generated = function.is_generated(left) or function.is_generated(right)
                    if generated:
                        report(node, 'a generated id cannot replace the agent name key')
                    if not (function.is_raw(left) or function.is_raw(right)):
                        continue
                    if function.is_id(left) or function.is_id(right) or _sentinel(left) or _sentinel(right):
                        continue
                    report(node, 'raw agent name compared without names.key')
            if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.comprehension)):
                tests = node.ifs if isinstance(node, ast.comprehension) else [node.test]
                for test in tests:
                    bare = test.operand if isinstance(test, ast.UnaryOp) else test
                    parameters = {arg.arg for arg in (*function.node.args.posonlyargs, *function.node.args.args,
                                                       *function.node.args.kwonlyargs)}
                    if (isinstance(node, ast.If) and isinstance(bare, ast.Name)
                            and bare.id in parameters and function.is_raw(bare)):
                        refuses = any(isinstance(child, ast.Raise) or
                                      isinstance(child, ast.Call) and spelling(child.func).endswith('refuse')
                                      for statement in node.body for child in own_nodes(statement))
                        if refuses:
                            report(test, 'raw name admission predicate outside names.check')
                    for call in own_nodes(test):
                        if not isinstance(call, ast.Call) or not any(function.is_raw(arg) for arg in call.args):
                            continue
                        target = function.resolve(call.func)
                        if target in functions or target in CANONICAL or target in NOTICE_CALLS:
                            continue
                        if target in NAME_SERVICES or spelling(call.func) in {'isinstance', 'len', 'any', 'all', 'bool'}:
                            continue
                        report(call, 'unresolved predicate receives an agent name')
    return sorted(found)


def package_violations(root: Path) -> list[str]:
    return violations({path.relative_to(root).as_posix(): path.read_text(encoding='utf-8')
                       for path in root.rglob('*.py')})
