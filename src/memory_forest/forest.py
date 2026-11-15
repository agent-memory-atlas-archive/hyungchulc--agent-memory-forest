"""``python -m memory_forest.forest``: the ``forest`` command, for where its script is not on the path."""

from __future__ import annotations

from .cli import forest_main

if __name__ == "__main__":
    raise SystemExit(forest_main())
