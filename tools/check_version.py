#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Verify that the release version is consistent across all project files and git tags.

Checks:
  1. `model/tri_queue/__init__.py` (`__version__`)
  2. `pyproject.toml` (`dynamic = ["version"]` pointing to `tri_queue.__version__`)
  3. `CITATION.cff` (`version:`)
  4. `CHANGELOG.md` (top numbered release heading `## [<version>]` or `## [v<version>]`)
  5. `flow/LAYOUT_LOCK.json` (`release_version == "v" + __version__`)
  6. `GITHUB_REF_NAME` environment variable when it is a `v*` tag (`v<version>`)
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from tri_queue import __version__  # noqa: E402


def main() -> int:
    errors: list[str] = []
    ver = __version__
    v_tag = f"v{ver}"

    # 1. pyproject.toml
    pyproj = tomllib.loads((ROOT / "pyproject.toml").read_text())
    proj = pyproj.get("project", {})
    if "version" in proj and proj["version"] != ver:
        errors.append(f"pyproject.toml: project.version={proj['version']!r} != {ver!r}")
    elif "version" not in proj.get("dynamic", []):
        errors.append("pyproject.toml: 'version' must be in project.dynamic")

    # 2. CITATION.cff
    cff_text = (ROOT / "CITATION.cff").read_text()
    m_cff = re.search(r'^version:\s*"?([^"\s]+)"?\s*$', cff_text, re.MULTILINE)
    if not m_cff:
        errors.append("CITATION.cff: missing version field")
    elif m_cff.group(1) != ver:
        errors.append(f"CITATION.cff: version={m_cff.group(1)!r} != {ver!r}")

    # 3. CHANGELOG.md top numbered heading
    cl_text = (ROOT / "CHANGELOG.md").read_text()
    m_cl = re.search(r"^## \[v?(\d+\.\d+\.\d+)\]", cl_text, re.MULTILINE)
    if not m_cl:
        errors.append("CHANGELOG.md: no numbered release heading found")
    elif m_cl.group(1) != ver:
        errors.append(f"CHANGELOG.md: top release heading is {m_cl.group(1)!r} != {ver!r}")

    # 4. flow/LAYOUT_LOCK.json
    lock_path = ROOT / "flow" / "LAYOUT_LOCK.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if lock.get("release_version") != v_tag:
            errors.append(
                f"flow/LAYOUT_LOCK.json: release_version={lock.get('release_version')!r} != {v_tag!r}"
            )

    # 5. GITHUB_REF_NAME (if running on a release tag)
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    if ref_name.startswith("v") and ref_name != v_tag:
        errors.append(f"GITHUB_REF_NAME={ref_name!r} != {v_tag!r}")

    if errors:
        print("version-check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"version-check: {v_tag} consistent across __init__.py, pyproject.toml, CITATION.cff, CHANGELOG.md, LAYOUT_LOCK.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
