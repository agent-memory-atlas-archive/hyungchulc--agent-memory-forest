"""Write the table of ``docs/refusals.md`` from the code: every code of ``errors.REFUSALS`` with its next move,
its command and, under "When", every message its places can give (a code of the state: the claims it carries).

    python3 scripts/refusals_table.py          # rewrite the table in docs/refusals.md
    python3 scripts/refusals_table.py --check  # exit 1 when the document is not the generated table

``tests/test_v1_package_rules.py`` proves the document equal to what this writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPOSITORY / "src"), str(REPOSITORY / "tests")]

import codes_walk  # noqa: E402

DOCUMENT = REPOSITORY / "docs" / "refusals.md"


def main(arguments: list[str]) -> int:
    text = DOCUMENT.read_text(encoding="utf-8")
    before, _table, after = codes_walk.split_document(text)
    generated = before + codes_walk.table() + after
    if "--check" in arguments:
        return 0 if generated == text else 1
    DOCUMENT.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
