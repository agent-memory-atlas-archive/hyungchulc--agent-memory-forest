"""Wait for a child or a fifo, and fail with what was expected and the child's stderr.

A wait has a deadline. A dead child ends the wait. Nothing here sleeps for a fixed interval as the
way of knowing the child arrived.
"""

from __future__ import annotations

import os
import select
import subprocess
import threading


def stderr_text(proc: subprocess.Popen[str]) -> str:
    if proc.stderr is None:
        return ""
    try:
        return proc.stderr.read()
    except Exception:
        return ""


def _fail(proc: subprocess.Popen[str], what: str, reason: str) -> None:
    err = stderr_text(proc)
    code = proc.poll()
    raise AssertionError(f"while waiting for {what}: {reason}. child exit {code}. stderr: {err}")


def read_fifo(proc: subprocess.Popen[str], path: str, *, what: str, seconds: float = 120) -> bytes:
    """Read the child's handshake. The open does not block for a writer that never comes."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        died_read, died_write = os.pipe()
        threading.Thread(target=_watch, args=(proc, died_write), daemon=True).start()
        ready, _, _ = select.select([descriptor, died_read], [], [], seconds)
        if died_read in ready or (proc.poll() is not None and descriptor not in ready):
            _fail(proc, what, "the child exited")
        if descriptor not in ready:
            _fail(proc, what, f"the deadline of {seconds:g}s passed")
        data = os.read(descriptor, 64)
        if not data:
            _fail(proc, what, "the handshake was empty")
        return data
    finally:
        os.close(descriptor)


def write_fifo(proc: subprocess.Popen[str], path: str, payload: bytes, *, what: str, seconds: float = 120) -> None:
    """Write the release line. Opening the write end waits only until the deadline or the child exits."""
    error: list[BaseException] = []
    finished_read, finished_write = os.pipe()

    def open_and_write() -> None:
        try:
            descriptor = os.open(path, os.O_WRONLY)
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
        except BaseException as exc:
            error.append(exc)
        finally:
            try:
                os.write(finished_write, b"x")
            except OSError:
                pass

    threading.Thread(target=open_and_write, daemon=True).start()
    died_read, died_write = os.pipe()
    threading.Thread(target=_watch, args=(proc, died_write), daemon=True).start()
    ready, _, _ = select.select([finished_read, died_read], [], [], seconds)
    if finished_read in ready:
        if error:
            _fail(proc, what, f"the write failed: {error[0]}")
        return
    if died_read in ready or proc.poll() is not None:
        _fail(proc, what, "the child exited")
    _fail(proc, what, f"the deadline of {seconds:g}s passed")


def _watch(proc: subprocess.Popen[str], write_end: int) -> None:
    try:
        proc.wait()
        os.write(write_end, b"x")
    except Exception:
        pass
