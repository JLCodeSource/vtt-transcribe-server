#!/usr/bin/env python3
"""Print the changelog section for a version (the GitHub release notes); fail if there is none.

Usage: python scripts/release_notes.py 0.1.0 [docs/CHANGELOG.md]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str | None:
    """The text under ``## <version>`` up to the next ``## `` heading, or ``None``."""
    pattern = re.compile(rf"^##\s+v?{re.escape(version)}\b.*?$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(changelog)
    return match.group(1).strip() if match else None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1] if len(argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "CHANGELOG.md")
    notes = section(path.read_text(encoding="utf-8"), argv[0].removeprefix("v"))
    if not notes:
        print(f"No changelog entry for {argv[0]} in {path}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
