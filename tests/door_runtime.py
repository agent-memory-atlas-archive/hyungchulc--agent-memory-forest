"""The interpreter witnesses filesystem, process and SQLite access, including error paths.

The hook is installed once; the watch can be enabled for a command in any thread. Non-audited calls
are wrapped too. No permission is inferred from the receiver's variable name.
"""
from __future__ import annotations

import contextlib
import functools
import os
import sys
from unittest import mock

import door_rules
import memory_forest
from memory_forest import system
from pathlib import PurePath

_ACTIVE = None
_PREFIXES = {str(door_rules.PACKAGE) + os.sep, str(PurePath(memory_forest.__file__).parent) + os.sep}
# Python emits no event for these operations. The discovery test exercises the live inventory.
SILENT = frozenset({
    "stat", "lstat", "fstat", "access", "readlink", "pathconf", "fpathconf", "statvfs", "fstatvfs",
    "getcwd", "getcwdb", "read", "write", "pread", "pwrite", "close", "lseek", "fsync", "fdatasync",
    "dup", "pipe", "pipe2", "isatty", "ftruncate", "truncate", "fdopen", "sendfile", "copy_file_range",
    "get_blocking", "set_blocking", "device_encoding", "lockf",
})
EVENTS = ("open", "os.", "shutil.", "subprocess.", "sqlite3.", "mmap.", "fcntl.")


def record(event):
    active = _ACTIVE
    if active is None:
        return
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module in {"_frozen_importlib", "_frozen_importlib_external", "zipimport"} or module.startswith("importlib."):
            return
        filename = frame.f_code.co_filename
        prefix = next((prefix for prefix in _PREFIXES if filename.startswith(prefix)), None)
        if prefix is not None:
            name = filename[len(prefix):]
            active.append((name, event))
            if name != door_rules.DOOR and name not in door_rules.NOT_YET and name not in door_rules.INTAKE:
                raise AssertionError(f"outside the door: {name}: {event}")
            return
        frame = frame.f_back


def _audit(event, args):
    if event.startswith(EVENTS):
        record(event)


sys.addaudithook(_audit)


@contextlib.contextmanager
def watching():
    global _ACTIVE
    if _ACTIVE is not None:
        yield _ACTIVE
        return
    seen = []
    with contextlib.ExitStack() as stack:
        trace = system._WATCH
        if trace is None:
            trace = stack.enter_context(system.watching())
        call_start, event_start = len(trace.calls), len(trace.events)
        for name in SILENT:
            real = getattr(os, name, None)
            if real is None:
                continue

            @functools.wraps(real)
            def wrapped(*args, _real=real, _name=name, **kwargs):
                record("os." + _name)
                return _real(*args, **kwargs)

            stack.enter_context(mock.patch.object(os, name, wrapped))
        _ACTIVE = seen
        try:
            yield seen
        finally:
            _ACTIVE = None
            verify_trace(trace.calls[call_start:], trace.events[event_start:])


def verify_trace(calls, events):
    """Index connections compare after their final statement; every open has all three looks."""
    for index, (name, path) in enumerate(calls):
        if name != "open_index":
            continue
        earlier = calls[:index]
        for suffix in ("-journal", "-wal", "-shm"):
            assert ("look", path + suffix) in earlier, ("open without sidecar look", path, suffix)
        assert ("look", path) in earlier, ("open without file identity", path)
    calls_iter = iter(calls)
    expanded = []
    for kind, value in events:
        if kind == "call":
            name, path = next(calls_iter)
            assert name == value
            expanded.append((kind, name, path))
        else:
            expanded.append((kind, value, value))
    previous_close = {}
    for stop, (kind, _, path) in enumerate(expanded):
        if kind != "closed_index":
            continue
        start = previous_close.get(path, 0)
        statements = [i for i in range(start, stop) if expanded[i][0] == "statement" and expanded[i][2] == path]
        if statements:
            assert ("call", "same_index", path) in expanded[statements[-1] + 1:stop], ("connection closed without final compare", path, expanded[start:stop])
        previous_close[path] = stop + 1
    for path in {event[2] for event in expanded if event[0] == "statement"}:
        start = previous_close.get(path, 0)
        statements = [i for i in range(start, len(expanded)) if expanded[i][0] == "statement" and expanded[i][2] == path]
        if statements:
            assert ("call", "same_index", path) in expanded[statements[-1] + 1:], ("read without final compare", path, expanded[start:])
