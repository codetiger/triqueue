# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Toggle census: switching per functional block and per beat, from an activity SAIF.

    python3 flow/census.py t8_cpa gemm64 mbv1_pw8_16       # one column per workload
    python3 flow/census.py t8_cpa mbv1_pw8_16 --vs t8_x    # two configurations, same workload
    python3 flow/census.py t8_cpa mbv1_pw8_16 --nets drain # the busiest nets of one block

A pre-layout check of where an RTL change moves switching, in seconds instead of a flow run. It
runs flow/activity.py when the SAIF is missing or stale. Counts are zero-delay toggles of named RTL
nets (Verilator SAIF): no glitches, no capacitance weighting, combinational logic is visible only
through the nets the RTL names, and a wire that crosses a module port is counted once per name
(`u_array.rd_val_o` and `u_drain.rd_val_i`). Use it to compare runs, and flow/power.py on a routed
design for power.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flow"))

from blocks import BLOCK_NAMES, block_of  # noqa: E402


def saif_bits(config: str, workload: str) -> dict:
    from activity import TB_PERIOD_PS, saif_source_digest  # noqa: F401
    from power import _saif_fresh, flatten_saif
    saif = ROOT / "build" / "activity" / config / f"{workload}.saif"
    if not _saif_fresh(saif, config, workload):
        r = subprocess.run([sys.executable, str(ROOT / "flow" / "activity.py"), config, workload],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"[census] flow/activity.py {config} {workload} failed:\n{r.stdout[-3000:]}{r.stderr[-2000:]}")
        print(r.stdout.strip().splitlines()[-1], file=sys.stderr)
    _, bits = flatten_saif(saif.read_text())
    return bits


def census(config: str, workload: str):
    """(toggles per block, toggles per net, beats, cycles in the window)."""
    from activity import TB_PERIOD_PS
    bits = saif_bits(config, workload)
    blocks, nets = Counter(), Counter()
    for name, (_, _, tc) in bits.items():
        b = block_of(name)
        if b is None:
            continue
        blocks[b] += tc
        nets[(b, re.sub(r"\[\d+\]", "", name))] += tc
    beat = bits["u_core.beat"]
    beats = round(beat[1] / TB_PERIOD_PS)
    cycles = round((beat[0] + beat[1]) / TB_PERIOD_PS)
    return blocks, nets, beats, cycles


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("workloads", nargs="+")
    ap.add_argument("--vs", help="second configuration, compared on the first workload")
    ap.add_argument("--nets", help="list the busiest nets of this block")
    args = ap.parse_args()

    cols = [(args.config, w) for w in args.workloads]
    if args.vs:
        cols = [(args.config, args.workloads[0]), (args.vs, args.workloads[0])]
    data = [census(c, w) for c, w in cols]

    if args.nets:
        for (c, w), (_, nets, beats, _) in zip(cols, data):
            print(f"== {c} {w}: {args.nets}, toggles per beat")
            top = sorted(((v, n) for (b, n), v in nets.items() if b == args.nets), reverse=True)[:20]
            for v, n in top:
                print(f"  {v / beats:10.1f}  {n}")
        return 0

    head = [f"{c}/{w}" for c, w in cols]
    width = max(14, *(len(h) for h in head))
    print(f"{'toggles per beat':16s}" + "".join(f"{h:>{width + 2}s}" for h in head))
    totals = [0.0] * len(cols)
    for b in BLOCK_NAMES:
        vals = [d[0][b] / d[2] for d in data]
        if not any(vals):
            continue
        totals = [t + v for t, v in zip(totals, vals)]
        print(f"{b:16s}" + "".join(f"{v:>{width + 2}.1f}" for v in vals))
    print(f"{'total':16s}" + "".join(f"{v:>{width + 2}.1f}" for v in totals))
    print(f"{'beats / cycles':16s}" + "".join(f"{f'{d[2]} / {d[3]}':>{width + 2}s}" for d in data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
