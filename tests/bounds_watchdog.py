"""The test runner's independent deadline, using only its owned parent and a pipe protocol."""
from __future__ import annotations

import os
import queue
import signal
import sys
import threading
import time


def watch(parent: int) -> None:
    commands = queue.Queue()
    def read_commands():
        for line in sys.stdin:
            commands.put(line.strip())
        commands.put(None)
    threading.Thread(target=read_commands, daemon=True).start()
    deadline = None
    while os.getppid() == parent:
        left = 1.0 if deadline is None else max(0.0, min(1.0, deadline - time.monotonic()))
        try:
            command = commands.get(timeout=left)
        except queue.Empty:
            if deadline is not None and time.monotonic() >= deadline:
                if os.getppid() == parent:
                    try:
                        os.set_blocking(2, False)
                        os.write(2, b"Timeout (test bound backstop)!\n")
                    except OSError:
                        pass
                    os.kill(parent, getattr(signal, "SIGKILL", signal.SIGTERM))
                break
            continue
        if command is None:
            break
        deadline = None if command == "clear" else float(command)
    os._exit(0)


if __name__ == "__main__":
    watch(int(sys.argv[1]))
