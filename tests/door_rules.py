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

# Functions of these modules touch the disk, or the process's place on it. ``os.path`` names those that ask
# the disk (``abspath`` asks for the working folder); the pure ones (``join``, ``dirname``) are not here.
FORBIDDEN_MODULE_FUNCTIONS: dict[str, frozenset[str]] = {
    "os": frozenset({
        "open", "fdopen", "stat", "lstat", "fstat", "scandir", "listdir", "walk", "fwalk", "mkdir", "makedirs",
        "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace", "chmod", "lchmod", "fchmod",
        "chown", "lchown", "fchown", "access", "utime", "readlink", "symlink", "link", "truncate", "ftruncate",
        "read", "write", "pread", "pwrite", "close", "lseek", "fsync", "fdatasync", "mkfifo", "mknod",
        "statvfs", "fstatvfs", "getcwd", "getcwdb", "chdir", "fchdir", "chroot", "sendfile", "copy_file_range",
        "dup", "dup2", "pipe", "openpty",
    }),
    "os.path": frozenset({
        "exists", "lexists", "isfile", "isdir", "islink", "ismount", "isjunction", "getsize", "getmtime",
        "getatime", "getctime", "realpath", "samefile", "sameopenfile", "abspath",
    }),
    "io": frozenset({"open", "open_code", "FileIO"}),
    "codecs": frozenset({"open"}),
    "fcntl": frozenset({"flock", "lockf", "fcntl", "ioctl"}),
}
# Every function of these modules touches the disk.
FORBIDDEN_MODULES = frozenset({"shutil", "tempfile", "glob"})
# Methods of a path object that touch the disk, with the number of positional arguments the path method
# takes: a method of the same name with another count (``plan.exists(page)``, ``text.replace(a, b)``) is
# another object's. ``group`` is left out: a path's group and a match's group cannot be told apart, and no
# module asks a path for its group.
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
# Receivers whose methods of those names are the package's own (a plan, a view, the door itself).
OWN_RECEIVERS = frozenset({"plan", "Plan", "self", "view", "ForestView", "forest", "system"})


def modules() -> list[tuple[str, ast.Module]]:
    """Every module of the package, by its path under the package."""
    return [
        (str(path.relative_to(PACKAGE)), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    ]


def _imports(tree: ast.Module) -> dict[str, str]:
    """Local names bound to the modules the rules name, and to their functions: ``{local: dotted}``."""
    bound: dict[str, str] = {}
    watched = set(FORBIDDEN_MODULE_FUNCTIONS) | FORBIDDEN_MODULES | {"builtins", "sqlite3", "os.path"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in watched:
                    bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module in watched:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _module_names(tree: ast.Module) -> set[str]:
    """Local names bound to a module (``import x``, ``from . import x``): a call on one is a module's function."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and (node.module is None or node.level > 0):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def forbidden_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Every call in ``tree`` that touches the disk without the door: ``(line, what)``."""
    bound = _imports(tree)
    module_names = _module_names(tree) - set(bound)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open" and "open" not in bound:
            found.append((node.lineno, "open()"))
            continue
        dotted = _dotted(func)
        if dotted is not None:
            head, _, rest = dotted.partition(".")
            full = f"{bound[head]}.{rest}" if head in bound and rest else bound.get(head, dotted)
            module, _, name = full.rpartition(".")
            if module == "builtins" and name == "open":
                found.append((node.lineno, full))
                continue
            if module in FORBIDDEN_MODULES or full.split(".")[0] in FORBIDDEN_MODULES:
                found.append((node.lineno, full))
                continue
            if name in FORBIDDEN_MODULE_FUNCTIONS.get(module, ()):
                found.append((node.lineno, full))
                continue
            if full == "sqlite3.connect":
                first = node.args[0] if node.args else None
                if not (isinstance(first, ast.Constant) and first.value == ":memory:"):
                    found.append((node.lineno, full))
                continue
        if isinstance(func, ast.Attribute) and func.attr in PATH_METHODS:
            receiver = func.value
            name = receiver.id if isinstance(receiver, ast.Name) else (
                receiver.attr if isinstance(receiver, ast.Attribute) else None
            )
            if name in OWN_RECEIVERS or (name is not None and (name in bound or name in module_names)):
                continue
            if isinstance(receiver, ast.Constant) and isinstance(receiver.value, str):
                continue
            if len(node.args) in PATH_METHODS[func.attr]:
                found.append((node.lineno, f".{func.attr}()"))
    return sorted(found)
