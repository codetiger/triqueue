#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Check that every tracked source/config file has an SPDX license and copyright header.

Hardware files under `rtl/`, `verif/`, and `flow/` must carry `Apache-2.0 WITH SHL-2.1`;
software and configuration files elsewhere must carry `Apache-2.0`. Non-commentable data
and documentation files (`rtl/files.f`, `flow/records/*.json`, `flow/LAYOUT_LOCK.json`,
`ui/data/*.js`, `bench/**/*.csv`, and Markdown docs) are covered by `REUSE.toml`.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXTENSIONS = {
    ".sv", ".v", ".py", ".c", ".h", ".scala", ".sh", ".sby", ".sdc", ".pcf",
    ".mk", ".yml", ".yaml", ".toml", ".html", ".css", ".js", ".ini", ".txt", ".cff",
}
NAMES = {"Makefile"}
SKIP_PREFIXES = ("ui/data/",)
SHL_PREFIXES = ("rtl/", "verif/", "flow/")


def tracked_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [ROOT / p for p in out.splitlines() if p]


def needs_header(rel: str, p: Path) -> bool:
    if rel.startswith(SKIP_PREFIXES):
        return False
    return p.name in NAMES or p.suffix in EXTENSIONS


def expected_license(rel: str) -> str:
    if rel.startswith(SHL_PREFIXES):
        return "SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1"
    return "SPDX-License-Identifier: Apache-2.0"


def check(rel: str, p: Path) -> str | None:
    head = "\n".join(p.read_text(errors="replace").splitlines()[:5])
    if "SPDX-FileCopyrightText:" not in head:
        return "missing SPDX-FileCopyrightText"
    want = expected_license(rel)
    if want not in head:
        return f"expected '{want}'"
    return None


def main() -> int:
    reuse = ROOT / "REUSE.toml"
    if not reuse.exists() or not reuse.read_text().strip():
        print("spdx-check: missing or empty REUSE.toml", file=sys.stderr)
        return 1

    files = tracked_files()
    bad = []
    checked = 0
    for f in files:
        if not f.exists():
            continue
        rel = str(f.relative_to(ROOT))
        if not needs_header(rel, f):
            continue
        checked += 1
        err = check(rel, f)
        if err:
            bad.append((rel, err))
    if bad:
        print("Files with invalid or missing SPDX headers:", file=sys.stderr)
        for rel, err in bad:
            print(f"  {rel}: {err}", file=sys.stderr)
        return 1
    print(f"spdx-check: {checked} files ok (+ REUSE.toml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
