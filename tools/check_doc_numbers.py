#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Check that hand-written docs quote the current physical-design results.

    python3 tools/check_doc_numbers.py

`flow/results.md` is generated from `flow/records/` (CI checks that), so it is the source of truth.
This script fails when a hand-maintained file quotes a value that is no longer in it:

  1. Every flip-flop count ("N flip-flops", "N FFs", a "Flip-flops" table row) in the public docs
     and the diagram generator is one of the recorded flop counts.
  2. The accumulator-form and clock-gating paragraphs of the spec (§10.2, §10.8) and
     `docs/benchmarks.md` §5 quote the comparison rows of `flow/results.md` for both platforms.

Historical values (CHANGELOG, a note that names the release it was measured at) are not checked.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "flow" / "results.md"
FLOP_FILES = ["README.md", "docs/specification.md", "docs/benchmarks.md", "docs/integration.md",
              "docs/verification.md", "rtl/README.md", "tools/gen_diagrams.py",
              "docs/figures/triqueue_architecture.svg"]
PLATFORMS = ("sky130hd", "asap7")


def tables(text: str) -> dict[tuple[str, str], list[dict[str, str]]]:
    """{(platform, '### heading'): rows} for every markdown table in flow/results.md."""
    out: dict[tuple[str, str], list[dict[str, str]]] = {}
    platform = heading = ""
    header: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            platform, heading, header = line[3:].strip(), "", None
        elif line.startswith("### "):
            heading, header = line[4:].strip(), None
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not set("".join(cells)) <= set("-: "):
                out.setdefault((platform, heading), []).append(dict(zip(header, cells)))
        else:
            header = None
    return out


def pct(cell: str) -> float:
    return abs(float(cell.rstrip("%").replace("+", "")))


def paragraph(text: str, anchor: str) -> str:
    """The paragraph or list item that contains `anchor` (up to the next blank line or item)."""
    i = text.find(anchor)
    if i < 0:
        return ""
    start = max(text.rfind("\n\n", 0, i), text.rfind("\n- ", 0, i), text.rfind("\n2. ", 0, i))
    ends = [j for j in (text.find("\n\n", i), text.find("\n- ", i)) if j >= 0]
    return text[start:min(ends) if ends else len(text)]


def main() -> int:
    res = tables(RESULTS.read_text())
    errors: list[str] = []

    # 1. flip-flop counts
    flops = {row["Flops"] for (p, h), rows in res.items() if h == "Area and timing" for row in rows}
    num = r"(\d{1,3}(?:,\d{3})+)"
    for rel in FLOP_FILES:
        text = (ROOT / rel).read_text()
        found = re.findall(num + r"\**\s+(?:flip-flops|FFs)\b", text)
        for row in re.findall(r"^\|\s*Flip-flops[^|]*\|(.*)$", text, flags=re.M):
            found += re.findall(num, row)
        for n in found:
            if n not in flops:
                errors.append(f"{rel}: {n} flip-flops is not a recorded flop count "
                              f"({', '.join(sorted(flops))}, flow/results.md)")

    # 2. comparison paragraphs
    spec = (ROOT / "docs" / "specification.md").read_text()
    bench = (ROOT / "docs" / "benchmarks.md").read_text()
    want: dict[str, list[str]] = {"acc": [], "cg": []}
    for p in PLATFORMS:
        acc = res.get((p, "Accumulator form: plain adder (cpa) against carry-save (csa)"), [])
        cg = res.get((p, "Clock gating: gated against ungated (CLK_GATE = 0)"), [])
        acc8 = next((r for r in acc if r.get("T") == "8"), None)
        cg8 = next((r for r in cg if r.get("Config") == "t8_cpa"), None)
        if not acc8 or not cg8:
            errors.append(f"flow/results.md: no T = 8 comparison rows for {p}")
            continue
        want["acc"] += [f"{pct(acc8[k]):.1f}%" for k in ("Cell area", "Flops", "Energy per MAC (geometric mean)")]
        powers = [pct(v) for k, v in cg8.items() if k.startswith("Power, ")]
        lo, hi = min(powers), max(powers)
        want["cg"].append({f"{a}–{b}%" for a in (math.floor(lo), round(lo)) for b in (math.floor(hi), round(hi))})
        want["cg"] += [f"{pct(cg8[k]):.1f}%" for k in ("Cell area", "Energy per MAC (geometric mean)")]
    fmax = {r["Config"]: r["Fmax (post-route)"] for r in res.get(("asap7", "Area and timing"), [])}
    want["acc_spec"] = [fmax.get("t8_cpa", "?"), fmax.get("t8_csa", "?")]

    checks = [
        ("docs/specification.md §10.2", paragraph(spec, "**Accumulator form (settled"), want["acc"] + want["acc_spec"]),
        ("docs/specification.md §10.8", paragraph(spec, "**Clock gating (settled"), want["cg"]),
        ("docs/benchmarks.md §5 accumulator form", paragraph(bench, "**Accumulator form:**"), want["acc"]),
        ("docs/benchmarks.md §5 clock gating", paragraph(bench, "**Clock gating**"), want["cg"]),
    ]
    for where, para, needles in checks:
        if not para:
            errors.append(f"{where}: paragraph not found (update tools/check_doc_numbers.py)")
            continue
        for n in needles:
            options = n if isinstance(n, set) else {n}
            if not any(o in para for o in options):
                errors.append(f"{where}: does not quote {' or '.join(sorted(options))} (flow/results.md)")

    for e in errors:
        print(f"doc-check: {e}", file=sys.stderr)
    if not errors:
        print(f"doc-check: flop counts and comparison figures match flow/results.md")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
