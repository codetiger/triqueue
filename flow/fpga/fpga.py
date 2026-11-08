# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Out-of-context FPGA build of tq_top on a Lattice ECP5 (open tools: Yosys + nextpnr-ecp5).

An Fmax sanity check and resource count, not a PPA result: FPGA numbers are never mixed with
the ASIC records in flow/results.md. The core is built out of context (no IO pads; its
memory ports are wider than any ECP5 package), with clock gating off (CLK_GATE = 0: FPGA
clocks are not gated with latches) and the reference queues from flow/ppa.py.

    python3 flow/fpga/fpga.py synth t8_cpa            # Yosys synth_ecp5 -> build/fpga/<cfg>/
    python3 flow/fpga/fpga.py pnr   t8_cpa --freq 50  # nextpnr-ecp5 (LFE5U-85F), timing report
    python3 flow/fpga/fpga.py all   t8_cpa

Tools come from PATH, or from OSS_CAD_SUITE/bin if that is set (YosysHQ oss-cad-suite).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "flow"))
from ppa import CONFIGS  # noqa: E402  (the ASIC configurations; FPGA builds reuse them)

DEVICE = ("--85k", "--package", "CABGA381", "--speed", "6")   # ECP5-85F, the ULX3S/OrangeCrab part


def tool(name):
    suite = os.environ.get("OSS_CAD_SUITE")
    path = shutil.which(name, path=f"{suite}/bin" if suite else None)
    if not path:
        sys.exit(f"[fpga] {name} not found (install it, or set OSS_CAD_SUITE)")
    return path


def sources():
    return [str(ROOT / f) for f in (ROOT / "rtl" / "files.f").read_text().split()]


def params(cfg):
    p = dict(CONFIGS[cfg])
    p["CLK_GATE"] = 0
    return p


def synth(cfg, out):
    out.mkdir(parents=True, exist_ok=True)
    chp = " ".join(f"-set {k} {v}" for k, v in params(cfg).items())
    script = (f"read_verilog -sv -DSYNTHESIS {' '.join(sources())}; "
              f"chparam {chp} tq_top; hierarchy -check -top tq_top; "
              f"synth_ecp5 -abc9 -top tq_top -json {out}/tq_top.json; "
              f"tee -o {out}/synth_stat.txt stat")
    subprocess.run([tool("yosys"), "-q", "-l", str(out / "synth.log"), "-p", script], check=True)
    print(summary_synth(out))


def summary_synth(out):
    text = (out / "synth_stat.txt").read_text()
    cells = {m.group(2): int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+(\w+)\s*$", text, re.M)}
    keys = ("LUT4", "TRELLIS_FF", "CCU2C", "MULT18X18D", "ALU54B", "DP16KD", "TRELLIS_DPR16X4")
    return "[fpga] synth: " + ", ".join(f"{k} {cells[k]}" for k in keys if k in cells)


def pnr(cfg, out, freq):
    if not (out / "tq_top.json").exists():
        synth(cfg, out)
    rpt = out / f"pnr_{freq}mhz.json"
    subprocess.run([tool("nextpnr-ecp5"), *DEVICE, "--out-of-context", "--json", str(out / "tq_top.json"),
                    "--freq", str(freq), "--report", str(rpt), "--log", str(out / f"pnr_{freq}mhz.log"),
                    "--seed", "1"], check=True)
    r = json.loads(rpt.read_text())
    fmax = {k: v["achieved"] for k, v in r.get("fmax", {}).items()}
    util = {k: f"{v['used']}/{v['available']}" for k, v in r.get("utilization", {}).items() if v["used"]}
    print(f"[fpga] {cfg} @ {freq} MHz: fmax {fmax}")
    print(f"[fpga] utilization: {util}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("synth", "pnr", "all"))
    ap.add_argument("config", choices=sorted(CONFIGS))
    ap.add_argument("--freq", type=float, default=50, help="target clock in MHz (default 50)")
    a = ap.parse_args()
    out = ROOT / "build" / "fpga" / a.config
    if a.step in ("synth", "all"):
        synth(a.config, out)
    if a.step in ("pnr", "all"):
        pnr(a.config, out, a.freq)


if __name__ == "__main__":
    main()
