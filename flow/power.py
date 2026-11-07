# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Activity-based power and energy per workload on a routed tq_top.

    python3 flow/power.py sky130hd t8_csa gemm64

Needs a finished flow (flow/ppa.py run <platform> <config>). Runs flow/activity.py for the
workload if its SAIF is missing, rescales the SAIF from the testbench clock to the flow clock,
runs OpenSTA (flow/power.tcl) on the routed design with its extracted parasitics, and writes
build/orfs/<platform>/<config>/power/<workload>.json.

Energy covers the whole SAIF window (see flow/activity.py) and is charged to the workload's
useful MACs (M x N x K), as defined in metrics.md. Engine only: the SRAM is outside tq_top.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flow"))
from activity import WORKLOADS  # noqa: E402
from ppa import CONFIGS, PLATFORMS, WORK, orfs_make, work_dir  # noqa: E402

TIME_FIELD = re.compile(rb"\((DURATION|T0|T1|TZ|TX|TB) (\d+)\)")


def flow_period_ps(platform, config):
    mk = (ROOT / work_dir(platform, config) / "config.mk").read_text()
    period = float(re.search(r"TQ_CLK_PERIOD\s*=\s*(\S+)", mk).group(1))
    return period * (1000 if PLATFORMS[platform]["unit"] == "ns" else 1)


def rescale_saif(src: Path, dst: Path, period_ps: float):
    """Scale all times so that the testbench clock period becomes period_ps. Returns cycles."""
    data = src.read_bytes()
    assert b"(TIMESCALE 1ps)" in data, "expected a 1 ps SAIF timescale"
    duration = int(re.search(rb"\(DURATION (\d+)\)", data).group(1))
    clk_tc = int(re.search(rb"\(clk_i \(T0 \d+\) \(T1 \d+\).*?\(TC (\d+)\)", data).group(1))
    tb_period = 2 * duration / clk_tc
    cycles = round(duration / tb_period)
    f = period_ps / tb_period
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(TIME_FIELD.sub(lambda m: b"(%s %d)" % (m.group(1), round(int(m.group(2)) * f)), data))
    return cycles


def parse_total_power(rpt: str):
    """Total power (W) and its split from OpenSTA's report_power 'Total' row."""
    for line in rpt.splitlines():
        cols = line.split()
        if cols and cols[0] == "Total" and len(cols) >= 5:
            internal, switching, leakage, total = map(float, cols[1:5])
            return dict(internal=internal, switching=switching, leakage=leakage, total=total)
    raise ValueError("no Total row in the power report")


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in PLATFORMS or sys.argv[2] not in CONFIGS \
            or sys.argv[3] not in WORKLOADS:
        print(__doc__)
        return 2
    platform, config, workload = sys.argv[1:]
    rel = work_dir(platform, config)
    saif = ROOT / "build" / "activity" / config / f"{workload}.saif"
    if not saif.exists():
        rc = subprocess.run([sys.executable, str(ROOT / "flow" / "activity.py"), config, workload]).returncode
        if rc:
            return rc
    period_ps = flow_period_ps(platform, config)
    scaled = ROOT / rel / "power" / f"{workload}.saif"
    cycles = rescale_saif(saif, scaled, period_ps)
    rpt = ROOT / rel / "power" / f"{workload}.rpt"
    rc = orfs_make(platform, config, "run", 4, f"power/{workload}.log", env=dict(
        RUN_SCRIPT=f"{WORK}/flow/power.tcl", RUN_LOG_NAME_STEM=f"power_{workload}",
        TQ_SAIF=f"{WORK}/{rel}/power/{workload}.saif", TQ_POWER_RPT=f"{WORK}/{rel}/power/{workload}.rpt"))
    if rc or not rpt.exists():
        print(f"[power] {platform} {config} {workload}: failed, see {rel}/power/{workload}.log")
        return rc or 1
    p = parse_total_power(rpt.read_text())
    w = WORKLOADS[workload]
    macs = w["m"] * w["n"] * w["k"]
    time_s = cycles * period_ps * 1e-12
    energy = p["total"] * time_s
    t = CONFIGS[config]["T"]
    out = dict(platform=platform, config=config, workload=workload, period_ps=period_ps,
               cycles=cycles, useful_macs=macs, utilization=macs / (t * t * cycles),
               power_w=p, energy_j=energy, pj_per_mac=energy / macs * 1e12,
               tops_per_w=2 * macs / energy / 1e12)
    (ROOT / rel / "power" / f"{workload}.json").write_text(json.dumps(out, indent=2) + "\n")
    print(f"[power] {platform} {config} {workload}: {p['total'] * 1e3:.2f} mW at "
          f"{1e6 / period_ps:.0f} MHz, {cycles} cycles, {out['pj_per_mac']:.3f} pJ/MAC, "
          f"{out['tops_per_w']:.2f} TOPS/W (engine only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
