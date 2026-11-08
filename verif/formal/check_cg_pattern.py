# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Every clock-gated register uses the template that tq_cg.sby proves equivalent to an
enable flop (rtl/README.md): a register clocked by anything but clk_i must sit in an
always_ff whose write condition ORs in the gate parameter (`|| GATE` or `|| CLK_GATE`).

    python3 verif/formal/check_cg_pattern.py      # part of make formal
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLOCKED = re.compile(r"always_ff\s*@\s*\(\s*posedge\s+(\w+)")


def blocks(text):
    """(clock, body) for each always_ff block (body up to its matching `end`)."""
    for m in CLOCKED.finditer(text):
        i, depth, body = m.end(), 0, []
        for tok in re.finditer(r"\bbegin\b|\bend\b|;", text[i:]):
            if tok.group() == "begin":
                depth += 1
            elif tok.group() == "end":
                depth -= 1
                if depth == 0:
                    body = text[i:i + tok.end()]
                    break
            elif depth == 0:                   # single statement without begin/end
                body = text[i:i + tok.end()]
                break
        yield m.group(1), body, text.count("\n", 0, m.start()) + 1


def main():
    bad, n = [], 0
    for f in (ROOT / "rtl" / "files.f").read_text().split():
        text = re.sub(r"//.*", "", (ROOT / f).read_text())
        for clk, body, line in blocks(text):
            if clk == "clk_i":
                continue
            n += 1
            if not re.search(r"\|\|\s*(CLK_)?GATE\b", body):
                bad.append(f"{f}:{line}: posedge {clk} without the gate term")
    for b in bad:
        print(b)
    print(f"[cg-pattern] {n} gated register blocks, {len(bad)} not following the template")
    return 1 if bad or n == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
