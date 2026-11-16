"""Write the front page as a package index shows it.

An index renders ``README.md`` as the project's page, away from the repository. There a relative link leads
nowhere, a diagram block is shown as its source, and an alert's marker is shown as text. This rewrites those
three things and nothing else, for the version the tree holds:

- a relative link becomes the address of that file at this version's tag in the repository;
- the alert marker ``> [!IMPORTANT]`` becomes a bold word;
- the diagram's block becomes one line that leads to the picture on the repository's front page.

    python3 scripts/pypi_readme.py            # print the page for the index
    python3 scripts/pypi_readme.py --write    # rewrite README.md in place: the publish workflow, in its checkout

``tests/test_v1_pypi_readme.py`` proves that every address it writes names a file of this tree, and that
nothing else on the page changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPOSITORY / "src")]

from memory_forest import __version__  # noqa: E402

HOME = "https://github.com/hyungchulc/agent-memory-forest"
PAGE = REPOSITORY / "README.md"

_TARGET = re.compile(r"(?<=\]\()[^)\s]+(?=\))")
_WHOLE = re.compile(r"[a-z][a-z0-9+.-]*:|#")
_ALERT = re.compile(r"(?m)^> \[!([A-Z]+)\]$")
_DIAGRAM = re.compile(r"(?ms)^```mermaid\n.*?^```\n")


def address(target: str, version: str) -> str:
    """One link target of the front page, as a page outside the repository must carry it."""
    if _WHOLE.match(target):
        return target
    path, mark, fragment = target.partition("#")
    kind = "tree" if (REPOSITORY / path).is_dir() else "blob"
    return f"{HOME}/{kind}/v{version}/{path.rstrip('/')}{mark}{fragment}"


def for_index(text: str, version: str = __version__) -> str:
    """The front page for an index: whole addresses, no alert marker, a pointer where the diagram was."""
    picture = f"{HOME}/blob/v{version}/README.md#the-forest-in-one-picture"
    text = _DIAGRAM.sub(f"The picture is drawn on [the repository's front page]({picture}).\n", text)
    text = _ALERT.sub(lambda found: f"> **{found.group(1).capitalize()}**\n>", text)
    return _TARGET.sub(lambda found: address(found.group(0), version), text)


def main(arguments: list[str]) -> int:
    written = for_index(PAGE.read_text(encoding="utf-8"))
    if "--write" in arguments:
        PAGE.write_text(written, encoding="utf-8")
    else:
        sys.stdout.write(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
