"""The walk over the refusal codes, read from the package's syntax trees (shared by the tests and the document).

It finds every class that derives from the refusal type, at any depth, and every construction of any of
them. At each site it reads the code, the next move and the message. A code or a message a site takes from
a parameter is followed to every caller: a call by name, through the module, as a method, and through a
partial application. What the walk cannot follow is an error unless ``LISTED`` names the site with the
values it can give and why: a parameter no caller fills, a function used as a value (a callback), a code
read from a table, and a next move that is not a literal.

Nothing here names a person or a forest.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field

import door_rules

REFUSAL_TYPE = "MemoryForestError"
DEPTH = 8
COMBINATIONS = 24  # a message with more forms than this shows ``…`` for the value that makes them


class Unresolved(Exception):
    """The walk cannot name the values: the site must be listed."""


def _listed() -> dict[tuple[str, str], dict[str, Callable[[], set]]]:
    """Sites whose values come from a table or a decision, each with the values it can give and why."""
    from memory_forest import derived, search

    return {
        ("derived.py", "refusal"): {
            "why": lambda: "the state's decision: its code and move are a row of derived.CODES",
            "codes": lambda: set(derived.CODES),
            "pairs": lambda: {(code, move) for code, moves in derived.CODES.items() for move in moves},
            "message": lambda: {"claims"},
        },
        ("search.py", "index_error"): {
            "why": lambda: "the SQLite class's row of search._PERSON_MESSAGES",
            "codes": lambda: {code for code, _message in search._PERSON_MESSAGES.values()},
            "message": lambda: {message for _code, message in search._PERSON_MESSAGES.values()},
        },
    }


@dataclass
class Site:
    module: str
    line: int
    function: str
    codes: set[str] = field(default_factory=set)
    moves: set[str | None] = field(default_factory=set)
    pairs: set[tuple[str, str | None]] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)
    listed: bool = False


@dataclass
class Walk:
    classes: set[str]
    sites: list[Site]
    unresolved: list[str]
    unused_listings: list[tuple[str, str]]

    def codes(self) -> dict[str, list[Site]]:
        found: dict[str, list[Site]] = {}
        for site in self.sites:
            for code in site.codes:
                found.setdefault(code, []).append(site)
        return found


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _Package:
    def __init__(self, trees: dict[str, ast.Module] | None = None) -> None:
        self.trees = dict(door_rules.modules()) if trees is None else trees
        self.owner: dict[tuple[str, int, int], ast.FunctionDef] = {}
        self.functions: dict[str, list[tuple[str, ast.FunctionDef, bool]]] = {}
        self.imported: dict[str, dict[str, tuple[str, str]]] = {}
        self.module_aliases: dict[str, dict[str, str]] = {}
        for module, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            item._walk_method = True  # type: ignore[attr-defined]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault(node.name, []).append(
                        (module, node, bool(getattr(node, "_walk_method", False)))
                    )
                    for child in ast.walk(node):
                        if child is not node:
                            self.owner[(module, *_where(child))] = node
            names: dict[str, tuple[str, str]] = {}
            aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.level > 0:
                        stem = node.module.split(".")[-1]
                        for alias in node.names:
                            names[alias.asname or alias.name] = (stem, alias.name)
                    elif node.module is None or node.module == "memory_forest":
                        for alias in node.names:
                            aliases[alias.asname or alias.name] = alias.name
            self.imported[module] = names
            self.module_aliases[module] = aliases

    def enclosing(self, module: str, node: ast.AST) -> ast.FunctionDef | None:
        return self.owner.get((module, *_where(node)))

    def _stem(self, module: str) -> str:
        return module.rsplit("/", 1)[-1].removesuffix(".py")

    def _calls_of(self, module: str, function: ast.FunctionDef, method: bool):
        """Every call of ``function`` as ``(caller module, call, bound)``, where ``bound`` counts the arguments a
        partial application binds; and every place it is used as a value (a callback the walk cannot follow)."""
        stem = self._stem(module)
        calls: list[tuple[str, ast.Call, int]] = []
        as_values: list[str] = []
        for caller, tree in self.trees.items():
            in_call: set[tuple[int, int, int, int]] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if self._is(caller, node.func, module, stem, function.name, method):
                    calls.append((caller, node, 0))
                    in_call.add(_where(node.func))
                elif _name(node.func) == "partial" and node.args and self._is(
                    caller, node.args[0], module, stem, function.name, method
                ):
                    calls.append((caller, node, len(node.args) - 1))
                    in_call.add(_where(node.args[0]))
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.Name, ast.Attribute))
                    and isinstance(node.ctx, ast.Load)
                    and _where(node) not in in_call
                    and self._is(caller, node, module, stem, function.name, method)
                ):
                    as_values.append(f"{caller}:{node.lineno}")
        return calls, as_values

    def _is(self, caller: str, node: ast.AST, module: str, stem: str, name: str, method: bool) -> bool:
        if isinstance(node, ast.Name):
            if method:
                return False
            if caller == module:
                return node.id == name
            return self.imported[caller].get(node.id) == (stem, name)
        if isinstance(node, ast.Attribute):
            if node.attr != name:
                return False
            if method:
                return True
            base = node.value  # a module is reached through the name an import gave it
            return isinstance(base, ast.Name) and self.module_aliases[caller].get(base.id) == stem
        return False

    def values(self, module: str, node: ast.expr | None, depth: int = 0) -> set[str]:
        """The strings an expression can be (a code)."""
        if node is None:
            raise Unresolved("nothing")
        if depth > DEPTH:
            raise Unresolved("too deep")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return self.values(module, node.body, depth + 1) | self.values(module, node.orelse, depth + 1)
        if isinstance(node, ast.Name):
            return self._name_values(module, node, depth, self.values)
        raise Unresolved(ast.unparse(node))

    def texts(self, module: str, node: ast.expr | None, depth: int = 0) -> set[str]:
        """The sentences an expression can be (a message), with ``…`` for a value."""
        if node is None:
            return set()
        if depth > DEPTH:
            return {"…"}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.JoinedStr):
            texts = {""}
            for part in node.values:
                options = {part.value} if isinstance(part, ast.Constant) else self._choices(module, part.value, depth)
                if len(texts) * len(options) > COMBINATIONS:
                    options = {"…"}
                texts = {text + option for text in texts for option in options}
            return texts
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return {left + right for left in self.texts(module, node.left, depth + 1)
                    for right in self.texts(module, node.right, depth + 1)}
        if isinstance(node, ast.IfExp):
            return self.texts(module, node.body, depth + 1) | self.texts(module, node.orelse, depth + 1)
        if isinstance(node, ast.Name):
            try:
                return self._name_values(module, node, depth, self.texts)
            except Unresolved:
                return {"…"}
        return {"…"}

    def _choices(self, module: str, node: ast.expr, depth: int) -> set[str]:
        """The words a value inside a message can be when the code fixes them (a literal, a branch, an entry of
        a table written in the code); ``{"…"}`` for a value the code does not fix."""
        try:
            return self._fixed(module, node, depth + 1)
        except Unresolved:
            return {"…"}

    def _fixed(self, module: str, node: ast.expr, depth: int) -> set[str]:
        if depth > DEPTH:
            raise Unresolved("too deep")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return self._fixed(module, node.body, depth + 1) | self._fixed(module, node.orelse, depth + 1)
        if isinstance(node, ast.Subscript):
            table = node.value
            if isinstance(table, ast.Name):
                table = self._module_value(module, table.id)
            if isinstance(table, ast.Dict) and all(
                isinstance(value, ast.Constant) and isinstance(value.value, str) for value in table.values
            ):
                return {value.value for value in table.values}  # type: ignore[union-attr]
            raise Unresolved(ast.unparse(node))
        if isinstance(node, ast.Name):
            function = self.enclosing(module, node)
            if function is None:
                raise Unresolved(node.id)
            parameters = {item.arg for item in [*function.args.posonlyargs, *function.args.args,
                                                  *function.args.kwonlyargs]}
            if node.id in parameters:
                raise Unresolved(node.id)
            return self._name_values(module, node, depth, self._fixed)
        raise Unresolved(ast.unparse(node))

    def _module_value(self, module: str, name: str) -> ast.expr | None:
        for node in self.trees[module].body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
        return None

    def _name_values(self, module: str, node: ast.Name, depth: int, follow) -> set[str]:
        function = self.enclosing(module, node)
        if function is None:
            raise Unresolved(node.id)
        parameters = [item.arg for item in [*function.args.posonlyargs, *function.args.args]]
        keyword_only = [item.arg for item in function.args.kwonlyargs]
        if node.id in parameters or node.id in keyword_only:
            return self._from_callers(module, function, node.id, depth, follow)
        found: set[str] = set()
        assigned = False
        for child in ast.walk(function):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id == node.id:
                        found |= follow(module, child.value, depth + 1)
                        assigned = True
                    elif isinstance(target, ast.Tuple) and any(
                        isinstance(item, ast.Name) and item.id == node.id for item in target.elts
                    ):
                        raise Unresolved(f"{node.id} unpacked")
            elif isinstance(child, ast.For) and isinstance(child.target, ast.Name) and child.target.id == node.id:
                if isinstance(child.iter, (ast.Tuple, ast.List)):
                    for item in child.iter.elts:
                        found |= follow(module, item, depth + 1)
                    assigned = True
                else:
                    raise Unresolved(f"{node.id} from a loop")
        if not assigned:
            raise Unresolved(node.id)
        return found

    def _from_callers(self, module: str, function: ast.FunctionDef, parameter: str, depth: int, follow) -> set[str]:
        parameters = [item.arg for item in [*function.args.posonlyargs, *function.args.args]]
        method = any(node is function and is_method for _m, node, is_method in self.functions.get(function.name, ()))
        if method and parameters and parameters[0] in ("self", "cls"):
            parameters = parameters[1:]
        defaults = dict(zip(parameters[len(parameters) - len(function.args.defaults):], function.args.defaults))
        defaults.update({item.arg: value for item, value in zip(function.args.kwonlyargs, function.args.kw_defaults)
                         if value is not None})
        found: set[str] = set()
        if parameter in defaults:
            found |= follow(module, defaults[parameter], depth + 1)
        calls, as_values = self._calls_of(module, function, method)
        if as_values:
            raise Unresolved(f"{function.name} is used as a value at {', '.join(as_values[:3])}")
        reached = parameter in defaults
        for caller, call, bound in calls:
            arguments = call.args[1:] if bound else call.args
            argument = next((item.value for item in call.keywords if item.arg == parameter), None)
            if argument is None and parameter in parameters:
                index = parameters.index(parameter)
                if index < len(arguments):
                    argument = arguments[index]
                elif bound:
                    raise Unresolved(f"{function.name}'s {parameter} is filled after a partial application")
            if argument is None:
                continue
            reached = True
            found |= follow(caller, argument, depth + 1)
        if not reached:
            raise Unresolved(f"no caller fills {function.name}'s {parameter}")
        return found


def _where(node: ast.AST) -> tuple[int, int, int, int]:
    """A node by where it stands, so no table is keyed by an object's address."""
    return (
        getattr(node, "lineno", -1), getattr(node, "col_offset", -1),
        getattr(node, "end_lineno", -1), getattr(node, "end_col_offset", -1),
    )


def refusal_classes(package: _Package) -> set[str]:
    """Every class that derives from the refusal type, at any depth."""
    found = {REFUSAL_TYPE}
    while True:
        more = {
            node.name
            for tree in package.trees.values()
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and any(_name(base) in found for base in node.bases)
        } - found
        if not more:
            return found
        found |= more


def walk(trees: dict[str, ast.Module] | None = None,
         listed: dict[tuple[str, str], dict[str, Callable[[], set]]] | None = None) -> Walk:
    """The walk over the package, or over ``trees`` (``{module path: syntax tree}``) with ``listed``."""
    package = _Package(trees)
    classes = refusal_classes(package)
    listed = _listed() if listed is None else listed
    used: set[tuple[str, str]] = set()
    sites: list[Site] = []
    unresolved: list[str] = []
    for module, tree in package.trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _name(node.func) not in classes:
                continue
            function = package.enclosing(module, node)
            name = getattr(function, "name", "")
            site = Site(module, node.lineno, name)
            entry = listed.get((module, name))
            code = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "code"), None)
            message = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "message"), None
            )
            step = next((k.value for k in node.keywords if k.arg == "next_step"), None)
            try:
                site.codes = package.values(module, code)
            except Unresolved as exc:
                if entry is None or "codes" not in entry:
                    unresolved.append(f"{module}:{node.lineno} ({name}): the code: {exc}")
                    continue
                site.codes = entry["codes"]()
                site.listed = True
                used.add((module, name))
            if step is None or (isinstance(step, ast.Constant) and (step.value is None or isinstance(step.value, str))):
                site.moves = {None if step is None else step.value}
            elif entry is not None and "pairs" in entry:
                site.pairs = entry["pairs"]()
                site.moves = {move for _code, move in site.pairs}
                site.listed = True
                used.add((module, name))
            else:
                unresolved.append(f"{module}:{node.lineno} ({name}): the next move is not a literal: "
                                  f"{ast.unparse(step)}")
                continue
            if not site.pairs:
                site.pairs = {(code, move) for code in site.codes for move in site.moves}
            if entry is not None and "message" in entry and site.listed:
                site.messages = entry["message"]()
            else:
                site.messages = package.texts(module, message)
            sites.append(site)
    unused = sorted(set(listed) - used)
    return Walk(classes, sites, unresolved, unused)


# ─────────────────────────── the document ───────────────────────────


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def claims_by_code() -> dict[str, set[str]]:
    """For each code a decision of the state gives, every sentence of the claims it can carry (``…`` for a value)."""
    from memory_forest import derived

    found: dict[str, set[str]] = {}
    for facts in derived.combinations():
        for command in derived.COMMANDS:
            decision = derived.decide(facts, command)
            if decision.code is None:
                continue
            for key in decision.claims:
                sentence = derived.CLAIMS[key].sentence.replace("{count}", "…").replace("{error}", "…")
                found.setdefault(decision.code, set()).add(sentence)
    return found


def when(result: Walk) -> dict[str, list[str]]:
    """Every message each code's places can give; for a code of the state, the claims it can carry."""
    claims = claims_by_code()
    found: dict[str, set[str]] = {}
    for site in result.sites:
        for code in site.codes:
            messages = claims.get(code, set()) if site.messages == {"claims"} else site.messages
            found.setdefault(code, set()).update(_cell(message) for message in messages)
    return {code: sorted(messages) for code, messages in found.items()}


TABLE_HEAD = "| Code | Next | Command | When |\n| --- | --- | --- | --- |\n"


def split_document(text: str) -> tuple[str, str, str]:
    """``docs/refusals.md`` in three parts: what comes before the table, the table (its head and its rows, each
    ``| `code` | ...``), and what follows it."""
    start = text.index(TABLE_HEAD)
    end = start + len(TABLE_HEAD)
    while text.startswith("| `", end):
        newline = text.find("\n", end)
        end = len(text) if newline < 0 else newline + 1
    return text[:start], text[start:end], text[end:]


def table(result: Walk | None = None) -> str:
    """The table of ``docs/refusals.md``, generated: one row per code of ``errors.REFUSALS``."""
    from memory_forest import errors

    result = result or walk()
    messages = when(result)
    rows = []
    for code, (move, command) in sorted(errors.REFUSALS.items()):
        said = "<br>".join(messages.get(code, ()))
        shown = f"`{command}`" if command else ""
        rows.append(f"| `{code}` | `{move}` | {shown} | {said} |\n")
    return TABLE_HEAD + "".join(rows)
