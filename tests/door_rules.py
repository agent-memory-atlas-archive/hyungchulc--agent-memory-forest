"""What the door rules forbid outside ``system.py``, read from the syntax tree (shared by the tests).

Nothing here names a person or a forest.
"""

from __future__ import annotations

import ast
from pathlib import Path

import memory_forest

PACKAGE = Path(memory_forest.__file__).resolve().parent

# Modules that do not go through the door yet, one per line, each with the round that owns it. Only the
# intake modules (another branch replaces them) and modules no command or tool an agent can call reaches
# may stand here; ``DoorRuntimeTests`` proves the second for each command and tool of an agent.
NOT_YET: dict[str, str] = {
    "intake.py": "the intake branch (v1-intake), merged by its own round",
    "cli_intake.py": "the intake branch (v1-intake), merged by its own round",
    "adapters/codex.py": "the intake branch (v1-intake), merged by its own round",
    "adapters/common.py": "the intake branch (v1-intake), merged by its own round",
    "adapters/grok.py": "the intake branch (v1-intake), merged by its own round",
    "adapters/jsonl.py": "the intake branch (v1-intake), merged by its own round",
    "stage.py": "the stages branch (v1-stages), merged by its own round",
    "archive.py": "round 8: the owner's commands through the door",
    "audit.py": "round 8: the owner's commands through the door",
    "core.py": "round 8: the owner's commands through the door",
    "index.py": "round 8: the owner's commands through the door",
    "retrieval.py": "round 8: the owner's commands through the door",
    "writer.py": "round 8: the owner's commands through the door",
}
# The intake modules: they may run while an agent's command runs, and do not go through the door yet.
INTAKE = frozenset(name for name, owner in NOT_YET.items() if "intake branch" in owner) | {
    "adapters/__init__.py", "adapters/claude_code.py",
}
DOOR = "system.py"

# Imports are closed. These modules compute in memory; the narrow socket/http imports implement the
# transport, not a file reader. sqlite3 is types, constants and errors only. Path is lexical only.
ALLOWED_IMPORTS = frozenset({
    "__future__", "argparse", "bisect", "calendar", "collections", "collections.abc", "contextlib",
    "dataclasses", "datetime", "enum", "errno", "functools", "hashlib", "hmac", "http.server",
    "itertools", "json", "os", "pathlib", "re", "secrets", "shlex", "socket", "sqlite3",
    "sys", "threading", "time", "tomllib", "typing", "unicodedata", "urllib.parse", "zoneinfo",
})
# No path lookup: PathLike is a protocol, fspath converts, getpid reads this process's identifier.
# path is checked separately.
ALLOWED_OS = {
    "PathLike": "typing protocol", "fspath": "text conversion", "getpid": "own process identifier",
    "path": "lexical functions checked separately",
}
ALLOWED_OS_PATH = frozenset({"join", "dirname", "basename", "split", "splitext", "splitdrive", "normpath",
                             "normcase", "isabs", "commonprefix", "commonpath"})
PATH_METHODS: dict[str, tuple[int, ...]] = {
    "exists": (0,), "is_file": (0,), "is_dir": (0,), "is_symlink": (0,), "is_fifo": (0,), "is_socket": (0,),
    "is_block_device": (0,), "is_char_device": (0,), "is_mount": (0,), "is_junction": (0,), "stat": (0,),
    "lstat": (0,), "open": (0, 1, 2, 3, 4), "read_text": (0, 1, 2), "read_bytes": (0,),
    "write_text": (1, 2, 3, 4), "write_bytes": (1,), "iterdir": (0,), "glob": (1,), "rglob": (1,),
    "walk": (0,), "mkdir": (0, 1, 2, 3), "rmdir": (0,), "unlink": (0, 1), "rename": (1,), "replace": (1,),
    "touch": (0, 1, 2), "chmod": (1,), "lchmod": (1,), "symlink_to": (1, 2), "hardlink_to": (1,),
    "readlink": (0,), "resolve": (0, 1), "absolute": (0,), "samefile": (1,), "owner": (0,),
    "cwd": (0,), "home": (0,), "copy": (1,), "copy_into": (1,), "move": (1,), "move_into": (1,),
}
def modules() -> list[tuple[str, ast.Module]]:
    """Every module of the package, by its path under the package."""
    return [
        (str(path.relative_to(PACKAGE)), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    ]



def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else base + "." + node.attr
    return None


def forbidden_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Fail closed on imports, filesystem capabilities and dynamic lookup, even passed as a value.

    A receiver is a package class only through its import, annotation, constructor or enclosing class.
    Relative imports of symbols are not assumed to be modules.
    """
    bound = {}
    own = set()
    classes = {}
    found = set()
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                    if alias.name not in ALLOWED_IMPORTS and not alias.name.startswith("memory_forest"):
                        found.add((node.lineno, "import " + alias.name))
            else:
                module = node.module or ""
                if module == "os" and any(a.name not in ALLOWED_OS for a in node.names):
                    found.add((node.lineno, "non-pure os import"))
                if module == "os.path" and any(a.name not in ALLOWED_OS_PATH for a in node.names):
                    found.add((node.lineno, "non-pure os.path import"))
                if module == "sqlite3" and any(a.name in {"connect", "Connection"} for a in node.names):
                    found.add((node.lineno, "SQLite constructor import"))
                if module == "pathlib" and any(a.name in {"Path", "PosixPath", "WindowsPath"} for a in node.names):
                    found.add((node.lineno, "concrete path"))
                for alias in node.names:
                    local = alias.asname or alias.name
                    bound[local] = module + "." + alias.name
                    if node.level or module.startswith("memory_forest"):
                        if module:
                            own.add(module)
                        # Resolve actual modules/classes, never the receiver's spelling.
                        source = PACKAGE / (module.replace(".", "/") + ".py") if module else None
                        if source is not None and source.is_file():
                            symbols = {item.name for item in ast.parse(source.read_text()).body if isinstance(item, ast.ClassDef)}
                            if alias.name in symbols:
                                own.add(local)
                        if not module and (PACKAGE / (alias.name + ".py")).is_file():
                            own.add(local)
                if not node.level and module not in ALLOWED_IMPORTS and not module.startswith("memory_forest"):
                    found.add((node.lineno, "import " + module))
        if isinstance(node, ast.ClassDef):
            classes[node.name] = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            own.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Name) and node.annotation.id in own:
            own.add(node.arg)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Call):
                base = (_dotted(value.func) or "").split(".")[0]
                if base in own:
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        if isinstance(target, ast.Name):
                            own.add(target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            full = bound.get(node.id, node.id)
            if full in {"open", "builtins.open", "eval", "exec", "compile", "__import__"}:
                found.add((node.lineno, full))
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted:
                head, _, rest = dotted.partition(".")
                full = bound.get(head, head) + ("." + rest if rest else "")
                if full.startswith("os.path.") and full.split(".")[2] not in ALLOWED_OS_PATH:
                    found.add((node.lineno, full))
                elif full.startswith("os.") and not full.startswith("os.path.") and full.split(".")[1] not in ALLOWED_OS:
                    found.add((node.lineno, full))
                if full in {"sqlite3.connect", "sqlite3.Connection", "builtins.open"}:
                    current = node
                    annotation = False
                    while current in parents:
                        parent = parents[current]
                        if isinstance(parent, (ast.arg, ast.AnnAssign)) and parent.annotation is current:
                            annotation = True
                            break
                        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.returns is current:
                            annotation = True
                            break
                        if isinstance(parent, ast.stmt):
                            break
                        current = parent
                    if not annotation:
                        found.add((node.lineno, full))
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant) or not isinstance(node.args[1].value, str):
                found.add((node.lineno, "dynamic getattr"))
            elif node.args[1].value in PATH_METHODS or node.args[1].value in {"connect", "Connection", "open"}:
                found.add((node.lineno, "getattr filesystem capability"))
        if isinstance(node.func, ast.Attribute) and node.func.attr in PATH_METHODS:
            receiver = node.func.value
            head = (_dotted(receiver) or "").split(".")[0]
            allowed = head in own or (head in bound and bound[head] in ALLOWED_IMPORTS and bound[head] != "pathlib")
            if head in {"self", "cls"}:
                parent = node
                while parent in parents and not isinstance(parent, ast.ClassDef):
                    parent = parents[parent]
                allowed = isinstance(parent, ast.ClassDef) and node.func.attr in classes[parent.name]
            if isinstance(receiver, ast.Constant) and isinstance(receiver.value, str):
                allowed = True
            if not allowed and len(node.args) in PATH_METHODS[node.func.attr]:
                found.add((node.lineno, "." + node.func.attr + "()"))
        if isinstance(node.func, ast.Attribute) and node.func.attr == "backup":
            found.add((node.lineno, "SQLite backup"))
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sql = arg.value.upper().strip()
                if sql.startswith("ATTACH ") or sql.startswith("VACUUM INTO "):
                    found.add((node.lineno, "SQLite file statement"))
    return sorted(found)
