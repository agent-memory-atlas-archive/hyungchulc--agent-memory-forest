"""Find patches of the shared time module; local injected clock bindings are allowed."""

from __future__ import annotations

import ast


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return base + '.' + node.attr if base else None
    return None


def patches(text):
    """Line numbers of standard mock patches through module imports, aliases and literal target paths.

    This is a syntax rule, not a proof about arbitrary reflection. It includes module aliases assigned
    from another module's time attribute; importing a function into a local binding is not a shared patch.
    """
    tree = ast.parse(text)
    shared = set()
    patchers = set()
    object_patchers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            shared.update(alias.asname or alias.name for alias in node.names if alias.name == 'time')
        elif isinstance(node, ast.ImportFrom):
            shared.update(alias.asname or alias.name for alias in node.names
                          if alias.name == 'time' and node.module != 'datetime')
            if node.module == 'unittest.mock':
                patchers.update(alias.asname or alias.name for alias in node.names if alias.name == 'patch')
    changed = True
    while changed:
        before = (set(shared), set(patchers), set(object_patchers))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                value = _dotted(node.value)
                if value in shared or value and value.endswith('.time'):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    shared.update(target.id for target in targets if isinstance(target, ast.Name))
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if value in patchers or value and value.endswith('.patch'):
                    patchers.update(target.id for target in targets if isinstance(target, ast.Name))
                if value in object_patchers or value and (value.endswith('.patch.object') or
                        value.endswith('.object') and value.removesuffix('.object') in patchers):
                    object_patchers.update(target.id for target in targets if isinstance(target, ast.Name))
        changed = before != (shared, patchers, object_patchers)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    module = _dotted(target.value) or ''
                    if module in shared or module.endswith('.time'):
                        found.append(node.lineno)
                elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute)
                      and target.value.attr == '__dict__'):
                    module = _dotted(target.value.value) or ''
                    if module in shared or module.endswith('.time'):
                        found.append(node.lineno)
        if not isinstance(node, ast.Call):
            continue
        function = _dotted(node.func) or ''
        if (function.endswith('patch.object') or function in object_patchers or function == 'setattr'
                or function.endswith('.setattr')) and len(node.args) >= 2:
            target = _dotted(node.args[0]) or ''
            if target in shared or target.endswith('.time'):
                found.append(node.lineno)
        elif (function.endswith('patch') or function in patchers) and node.args and isinstance(node.args[0], ast.Constant):
            target = node.args[0].value
            if isinstance(target, str) and '.' in target:
                module = target.rsplit('.', 1)[0]
                if module in shared or module == 'time' or module.endswith('.time'):
                    found.append(node.lineno)
        elif function.endswith('patch.dict') and node.args:
            target = node.args[0]
            if isinstance(target, ast.Attribute) and target.attr == '__dict__':
                module = _dotted(target.value) or ''
                if module in shared or module.endswith('.time'):
                    found.append(node.lineno)
    return found
