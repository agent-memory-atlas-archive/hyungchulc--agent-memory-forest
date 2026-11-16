"""Discover path functions from the running interpreter, then try each on an absent decoy."""
from __future__ import annotations

import contextlib
import functools
import inspect
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bounds
import door_runtime

_EVENTS = None
_SILENT_EVENTS = None


def audit(event, args):
    if _EVENTS is not None:
        _EVENTS.append(event)


sys.addaudithook(audit)
# Pure path calculations have no filesystem act to observe. Every other path function must emit an
# event or be wrapped. These names are exercised too; they are not absent from the inventory.
PURE = frozenset({
    "basename", "commonpath", "commonprefix", "dirname", "expandvars", "isabs", "join", "normcase",
    "normpath", "relpath", "split", "splitdrive", "splitext", "splitroot", "isreserved",
    "fsdecode", "fsencode", "fspath", "expanduser", "samestat",
})
# These generators must be consumed to perform their work.
GENERATORS = {"walk", "fwalk", "scandir"}
# Each required argument gets the system's own accepted kind; no path points outside the decoy.
VALUES = {
    "mode": 0o600, "flags": os.O_RDONLY | os.O_NONBLOCK, "uid": -1, "gid": -1, "length": 0,
    "times": None, "ns": None, "attribute": "user.forest-test", "value": b"", "name": "PC_NAME_MAX",
    "command": 0, "arg": 0, "mask": os.R_OK, "follow_symlinks": False, "fd": -1,
}


# A parameter of one of these names is taken for a path.
PATH_NAMES = frozenset({"path", "filename", "src", "dst", "top", "file", "path1", "path2", "f1", "f2"})


@contextlib.contextmanager
def silent_watch():
    with contextlib.ExitStack() as stack:
        for name in door_runtime.SILENT:
            real = getattr(os, name, None)
            if real is None:
                continue
            @functools.wraps(real)
            def called(*args, _real=real, _name=name, **kwargs):
                if _SILENT_EVENTS is not None:
                    _SILENT_EVENTS.append(_name)
                return _real(*args, **kwargs)
            stack.enter_context(mock.patch.object(os, name, called))
        yield


def inventory():
    for owner in (os, os.path):
        for name in sorted(dir(owner)):
            function = getattr(owner, name)
            if name.startswith("_") or not callable(function) or inspect.isclass(function):
                continue
            signature = getattr(function, "__text_signature__", "") or ""
            try:
                signature = str(inspect.signature(function))
            except (TypeError, ValueError):
                pass
            names = set(re.findall(r"\b[a-z_]+\b", signature))
            explicit = names & PATH_NAMES
            described = names & {"name", "old", "new"} and re.search(
                r"\b(?:path|file|filename|directory|directories)\b", function.__doc__ or "", re.IGNORECASE)
            if owner is os.path or explicit or described:
                yield owner, name, function, signature


class InventoryTests(unittest.TestCase):
    def test_discovery_covers_path_aliases_and_the_whole_path_namespace(self):
        found = {(owner.__name__, name) for owner, name, _, _ in inventory()}
        for owner, name in ((os, "makedirs"), (os, "removedirs"), (os, "renames"),
                            (os.path, "basename"), (os.path, "join"), (os.path, "sameopenfile")):
            self.assertIn((owner.__name__, name), found)

    def test_every_path_function_audits_or_is_wrapped(self):
        global _EVENTS, _SILENT_EVENTS
        missing, invalid, called, descriptors = [], [], [], []
        with tempfile.TemporaryDirectory() as temporary, silent_watch():
            absent = str(Path(temporary) / "absent" / "decoy")
            existing = Path(temporary) / "existing-decoy"
            existing.write_bytes(b"synthetic")
            descriptor = os.open(existing, os.O_RDONLY)
            self.addCleanup(os.close, descriptor)
            info = os.fstat(descriptor)
            for owner, name, function, text in inventory():
                _EVENTS = []
                _SILENT_EVENTS = []
                error = None
                try:
                    if name.startswith("exec"):
                        if name in {"execv", "execvp"}:
                            result = function(absent, [absent])
                        elif name in {"execve", "execvpe"}:
                            result = function(absent, [absent], {})
                        elif name.endswith("e"):
                            result = function(absent, absent, {})
                        else:
                            result = function(absent, absent)
                    elif name.startswith("spawn"):
                        args = [os.P_WAIT, absent, [absent]]
                        if name.endswith("e"):
                            args.append({})
                        result = function(*args)
                    elif name in {"posix_spawn", "posix_spawnp"}:
                        result = function(absent, [absent], {})
                    elif name == "sameopenfile":
                        result = function(descriptor, descriptor)
                    elif name == "samestat":
                        result = function(info, info)
                    elif name == "abspath":
                        result = function("synthetic-decoy/absent")
                    elif name == "utime":
                        result = function(absent, None)
                    elif name in {"getxattr", "removexattr"}:
                        result = function(absent, "user.forest-test")
                    elif name == "setxattr":
                        result = function(absent, "user.forest-test", b"")
                    else:
                        sig = inspect.signature(function)
                        args = []
                        for p in sig.parameters.values():
                            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) or p.default is not p.empty:
                                continue
                            value = VALUES.get(p.name, absent)
                            if p.name == "name" and name not in {"pathconf", "fpathconf"}:
                                value = absent
                            if p.name in {"paths", "m"}:
                                value = [absent, absent]
                            args.append(value)
                        result = function(*args)
                    if name in GENERATORS:
                        list(result)
                    if hasattr(result, "close"):
                        result.close()
                except (OSError, ValueError) as exc:
                    error = exc
                except (TypeError, IndexError) as exc:
                    # A function that wants an integer where the decoy path stands takes descriptors there:
                    # copy_file_range and splice (Linux) name two descriptors ``src`` and ``dst``. No path
                    # goes in, so it is not a path function and has nothing to audit.
                    asked = set(re.findall(r"\b[a-z_]+\b", text)) & PATH_NAMES
                    wants_integer = isinstance(exc, TypeError) and "interpreted as an integer" in str(exc)
                    if wants_integer and asked <= {"src", "dst"}:
                        descriptors.append(name)
                        continue
                    invalid.append((name, text, str(exc)))
                finally:
                    events = _EVENTS
                    silent = _SILENT_EVENTS
                    _EVENTS = _SILENT_EVENTS = None
                called.append(name)
                if not events and name not in PURE and name not in door_runtime.SILENT:
                    # Delegation is proved by this invocation reaching a wrapped OS function.
                    if silent:
                        continue
                    missing.append((owner.__name__, name, text, type(error).__name__))
            self.assertGreater(len(called), 30)
            for name in descriptors:
                # Proof that it was left out for the right reason: the same call with two descriptors is
                # one the system judges (it may refuse it), not one the interpreter turns away.
                try:
                    getattr(os, name)(descriptor, descriptor, 0)
                except OSError:
                    pass
            self.assertEqual(invalid, [], "the inventory needs a valid invocation recipe")
            self.assertEqual(missing, [], "a new unaudited function must be wrapped")
