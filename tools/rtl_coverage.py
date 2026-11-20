#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Merge the Verilator coverage of the RTL regression and summarize it per source file.

    TQ_COVERAGE=1 python3 -m pytest verif -q    # coverage builds in build/rtl-cov/<config>/
    python3 tools/rtl_coverage.py               # or: make rtl-coverage (both steps)

Writes build/rtl-cov/merged.dat, coverage.info (lcov), annotated/ (sources with uncovered points
marked) and summary.md. A point is covered when any instance in any configuration hit it, so a
parameter-dependent branch counts as covered if one configuration reaches it. This is a report, not
a gate: toggle points include bits that cannot move in a given configuration (constant address
bits, unused widths).
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COV = ROOT / "build" / "rtl-cov"


def points(dat: Path):
    """Yield (fields, count) for every point in a SystemC::Coverage-3 file."""
    for line in dat.read_text(errors="replace").splitlines():
        m = re.match(r"C '(.*)' (\d+)$", line)
        if not m:
            continue
        fields = dict(kv.split("\x02", 1) for kv in m.group(1).split("\x01") if "\x02" in kv)
        yield fields, int(m.group(2))


def main() -> int:
    inputs = sorted(COV.glob("*/coverage.dat"))
    if not inputs:
        print("rtl-coverage: no build/rtl-cov/*/coverage.dat; run TQ_COVERAGE=1 python3 -m pytest verif",
              file=sys.stderr)
        return 1
    merged = COV / "merged.dat"
    subprocess.run(["verilator_coverage", "--write", str(merged), "--write-info", str(COV / "coverage.info"),
                    *map(str, inputs)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["verilator_coverage", "--annotate", str(COV / "annotated"), "--annotate-min", "1",
                    str(merged)], check=True, stdout=subprocess.DEVNULL)

    hit: dict[tuple, bool] = {}
    for f, n in points(merged):
        path = Path(f.get("f", ""))
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if not rel.startswith("rtl/"):
            continue
        kind = f.get("t") or f.get("page", "").split("/")[0].removeprefix("v_")
        key = (rel, kind, f.get("l"), f.get("n"), f.get("o"))
        hit[key] = hit.get(key, False) or n > 0

    per = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for (rel, kind, *_), h in hit.items():
        per[rel][kind][0] += h
        per[rel][kind][1] += 1
    kinds = sorted({k for d in per.values() for k in d})

    def cell(c):
        return f"{100 * c[0] / c[1]:.1f}% ({c[0]:,}/{c[1]:,})" if c[1] else "–"

    lines = [f"# RTL coverage ({len(inputs)} regression configurations, merged)", "",
             "| File | " + " | ".join(kinds) + " |", "| --- |" + " --- |" * len(kinds)]
    total = defaultdict(lambda: [0, 0])
    for rel in sorted(per):
        lines.append(f"| `{rel}` | " + " | ".join(cell(per[rel].get(k, [0, 0])) for k in kinds) + " |")
        for k in kinds:
            total[k][0] += per[rel].get(k, [0, 0])[0]
            total[k][1] += per[rel].get(k, [0, 0])[1]
    lines.append("| **total** | " + " | ".join(f"**{cell(total[k])}**" for k in kinds) + " |")
    lines += ["", "Uncovered points are marked in `build/rtl-cov/annotated/`; lcov data is in "
              "`build/rtl-cov/coverage.info`."]
    (COV / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
