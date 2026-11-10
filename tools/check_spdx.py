#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Check that every checked-in source/config file carries an SPDX license identifier.

    python3 tools/check_spdx.py

Generated bundles under ui/data/ are rebuilt by the layout release, so they are excluded here; the
generators add the header when they run. Markdown and data files are not treated as source.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXTENSIONS = {
    ".sv", ".svh", ".v", ".vh", ".c", ".h", ".py", ".js", ".css", ".sh",
    ".yml", ".yaml", ".tcl", ".sdc", ".html", ".scala", ".toml", ".sby", ".mk",
}
NAMES = {"Makefile"}
EXCLUDE_PREFIXES = ("ui/data/", "build/", ".git/")
EXCLUDE_SUFFIXES = (".pyc",)


def tracked_files() -> list[Path]:
    """Files to check: the git index when available, otherwise a tree walk (source tarball)."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [ROOT / line for line in out.splitlines() if line.strip()]


def wanted(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith(EXCLUDE_PREFIXES) or rel.endswith(EXCLUDE_SUFFIXES):
        return False
    return path.suffix in EXTENSIONS or path.name in NAMES


def has_spdx(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
    except OSError:
        return False
    return any("SPDX-License-Identifier:" in line for line in head)


def main() -> int:
    missing = [p for p in tracked_files() if wanted(p) and not has_spdx(p)]
    if missing:
        print("Missing SPDX-License-Identifier in the first 8 lines:", file=sys.stderr)
        for path in missing:
            print(f"  {path.relative_to(ROOT)}", file=sys.stderr)
        print("Add the project header (Apache-2.0, or Apache-2.0 WITH SHL-2.1 under rtl/, verif/, flow/).",
              file=sys.stderr)
        return 1
    print(f"SPDX headers OK ({sum(1 for p in tracked_files() if wanted(p))} files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
