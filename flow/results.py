# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Physical design results: record finished runs, and write flow/results.md from the records.

    python3 flow/results.py collect sky130hd t8_cpa    # record a finished native run + its power
    python3 flow/results.py                            # rewrite flow/results.md from flow/records/

A full flow run takes from half an hour to hours, too long for CI. So each finished run is
condensed into a small JSON record in flow/records/<platform>/<config>.json, which is committed,
and flow/results.md is generated from the records alone (CI checks that it is up to date).

`collect` reads the native run in build/orfs_native/<platform>/<config>/ (flow/ppa.py) and the
power results of every workload (flow/power.py). It refuses a run whose RTL is older than the
working tree's rtl/ or whose rtl/ has uncommitted changes, unless --force (say why in the commit).
Metric definitions follow the ones in flow/README.md.
"""
import json
import math
import platform as host_platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "flow" / "records"
RESULTS = ROOT / "flow" / "results.md"

# Area of the PDK's smallest 2-input NAND (LEF SIZE), the gate equivalent for kGE.
NAND2_UM2 = {
    "sky130hd": ("sky130_fd_sc_hd__nand2_1", 1.38 * 2.72),
    "asap7": ("NAND2xp33_ASAP7_75t_R", 0.216 * 0.27),
    "ihp-sg13g2": ("sg13g2_nand2_1", 1.92 * 3.78),
}

# ORFS cell classes, grouped for the area table.
AREA_GROUPS = {
    "sequential": ["sequential_cell"],
    "logic": ["multi_input_combinational_cell", "inverter"],
    "clock tree": ["clock_buffer", "clock_inverter"],
    "timing buffers": ["timing_repair_buffer"],
    "other": ["tap_cell", "antenna_cell", "buffer", "tie_cell"],
}


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def tool_versions():
    from ppa import ORFS_HOME
    tools = ORFS_HOME / "tools" / "install"
    openroad = sh(str(tools / "OpenROAD" / "bin" / "openroad"), "-version")
    yosys = re.search(r"Yosys (\S+) \(git sha1 (\w+)", sh(str(tools / "yosys" / "bin" / "yosys"), "-V"))
    return openroad, f"{yosys.group(1)} {yosys.group(2)}"


def host():
    if sys.platform == "darwin":
        return f"macOS {host_platform.mac_ver()[0]} {host_platform.machine()}"
    return f"{host_platform.system()} {host_platform.release()} {host_platform.machine()}"


def power_groups(rpt: str):
    """Total power (W) per OpenSTA group (Sequential, Combinational, Clock, ...)."""
    out = {}
    for line in rpt.splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[0] in ("Sequential", "Combinational", "Clock", "Macro", "Pad"):
            out[cols[0].lower()] = float(cols[4])
    return out


def collect(platform, config, force=False):
    sys.path.insert(0, str(ROOT / "flow"))
    from ppa import CONFIGS, ORFS_COMMIT, PLATFORMS, work_dir
    from activity import WORKLOADS

    run = ROOT / work_dir(platform, config, native=True)
    base = f"{platform}/tq_{config}/base"
    report = run / "logs" / base / "6_report.json"
    if not report.exists():
        sys.exit(f"[results] {report.relative_to(ROOT)} missing: run flow/ppa.py run {platform} {config}")
    m = json.loads(report.read_text())
    route = json.loads((run / "logs" / base / "5_2_route.json").read_text())

    synth = run / "results" / base / "1_2_yosys.v"
    rtl = [ROOT / f for f in (ROOT / "rtl" / "files.f").read_text().split()]
    problems = []
    if max(f.stat().st_mtime for f in rtl) > synth.stat().st_mtime:
        problems.append("rtl/ changed after this run's synthesis")
    if sh("git", "-C", str(ROOT), "status", "--porcelain", "--", "rtl"):
        problems.append("rtl/ has uncommitted changes")
    if problems and not force:
        sys.exit(f"[results] {platform} {config}: " + "; ".join(problems) + " (rerun, or --force)")

    mk = (run / "config.mk").read_text()
    period = float(re.search(r"TQ_CLK_PERIOD\s*=\s*(\S+)", mk).group(1))
    util = int(re.search(r"CORE_UTILIZATION\s*=\s*(\S+)", mk).group(1))
    unit_ps = 1000 if PLATFORMS[platform]["unit"] == "ns" else 1
    cls = lambda c: m.get(f"finish__design__instance__area__class:{c}", 0.0)    # noqa: E731
    area_split = {g: round(sum(cls(c) for c in cs), 3) for g, cs in AREA_GROUPS.items()}
    power = {}
    for wl, w in WORKLOADS.items():
        f = run / "power" / f"{wl}.json"
        if not f.exists():
            print(f"[results] {platform} {config}: no power for {wl} (flow/power.py {platform} {config} {wl})")
            continue
        p = json.loads(f.read_text())
        power[wl] = dict(shape=[w["m"], w["n"], w["k"]], cycles=p["cycles"], useful_macs=p["useful_macs"],
                         utilization=p["utilization"], power_w=p["power_w"],
                         power_groups_w=power_groups(f.with_suffix(".rpt").read_text()), energy_j=p["energy_j"],
                         pj_per_mac=p["pj_per_mac"], tops_per_w=p["tops_per_w"])
    openroad, yosys = tool_versions()
    rec = dict(
        flow="native", orfs_commit=ORFS_COMMIT, openroad=openroad, yosys=yosys, host=host(),
        rtl_commit=sh("git", "-C", str(ROOT), "log", "-1", "--format=%h", "--", "rtl"),
        platform=platform, config=config, params=CONFIGS[config],
        clock_period_ps=period * unit_ps, core_utilization_pct=util,
        cell_area_um2=m["finish__design__instance__area__stdcell"], area_split_um2=area_split,
        core_area_um2=m["finish__design__core__area"],
        placement_density=m["finish__design__instance__utilization"],
        cells=m["finish__design__instance__count__stdcell"],               # without fill cells
        flops=m.get("finish__design__instance__count__class:sequential_cell", 0),
        nand2_cell=NAND2_UM2[platform][0], nand2_um2=NAND2_UM2[platform][1],
        setup_ws_ps=m["finish__timing__setup__ws"] * unit_ps,
        setup_tns_ps=m["finish__timing__setup__tns"] * unit_ps,
        hold_ws_ps=m["finish__timing__hold__ws"] * unit_ps,
        fmax_hz=m["finish__timing__fmax"],
        drc_errors=route["detailedroute__route__drc_errors"],
        power=power,
    )
    dst = RECORDS / platform / f"{config}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"[results] wrote {dst.relative_to(ROOT)}" + (f" (forced: {'; '.join(problems)})" if problems else ""))


# ---------------------------------------------------------------------------------------------
# flow/results.md


def num(x, digits=0):
    return f"{x:,.{digits}f}"


def area_str(um2):
    return f"{um2 / 1e6:.3f} mm²" if um2 >= 1e5 else f"{num(um2)} µm²"


def geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def derived(r):
    t = r["params"]["T"]
    kge = r["cell_area_um2"] / r["nand2_um2"] / 1e3
    peak_tops = 2 * t * t * r["fmax_hz"] / 1e12
    return dict(macs=t * t, kge=kge, macs_per_kge=t * t / kge, tops_mm2=peak_tops / (r["cell_area_um2"] / 1e6))


def platform_section(platform, recs):
    first = recs[0]
    out = [f"## {platform}", ""]
    out.append(f"Target clock {num(1e6 / first['clock_period_ps'])} MHz ({first['clock_period_ps']:g} ps), "
               f"core utilization {first['core_utilization_pct']}%, typical corner of the platform. "
               f"Gate equivalent: `{first['nand2_cell']}` ({first['nand2_um2']:.4g} µm²).")
    out += ["", "### Area and timing", "",
            "| Config | Parameters | Cell area | kGE | Flops | Flops per MAC | MACs/cycle per kGE "
            "| Fmax (post-route) | Worst setup slack | Peak TOPS/mm² at Fmax | DRC |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in recs:
        d = derived(r)
        params = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        out.append(f"| {r['config']} | {params} | {area_str(r['cell_area_um2'])} | {num(d['kge'], 1)} "
                   f"| {num(r['flops'])} | {num(r['flops'] / d['macs'])} | {d['macs_per_kge']:.3f} "
                   f"| {num(r['fmax_hz'] / 1e6, 1)} MHz | {num(r['setup_ws_ps'])} ps "
                   f"| {d['tops_mm2']:.3f} | {r['drc_errors']} |")
    out += ["", "### Cell area by class", "",
            "| Config | " + " | ".join(AREA_GROUPS) + " |",
            "| --- |" + " --- |" * len(AREA_GROUPS)]
    for r in recs:
        a = r["area_split_um2"]
        out.append(f"| {r['config']} | " + " | ".join(
            f"{a[g] / r['cell_area_um2'] * 100:.1f}%" for g in AREA_GROUPS) + " |")

    out += ["", f"### Energy, engine only, at {num(1e6 / first['clock_period_ps'])} MHz", "",
            "| Config | Workload | M×N×K | Utilization (simulated window) | Power | Clock tree | Registers "
            "| Logic | pJ/MAC | TOPS/W |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in recs:
        for wl, p in r["power"].items():
            out.append(f"| {r['config']} | {wl} | {'×'.join(map(str, p['shape']))} "
                       f"| {p['utilization'] * 100:.1f}% | {p['power_w']['total'] * 1e3:.2f} mW "
                       + "".join(f"| {p['power_groups_w'][g] / p['power_w']['total'] * 100:.1f}% "
                                 for g in ("clock", "sequential", "combinational"))
                       + f"| {p['pj_per_mac']:.3f} | {p['tops_per_w']:.3f} |")
        if len(r["power"]) > 1:
            pj = geomean([p["pj_per_mac"] for p in r["power"].values()])
            tw = geomean([p["tops_per_w"] for p in r["power"].values()])
            out.append(f"| {r['config']} | geometric mean | | | | | | | {pj:.3f} | {tw:.3f} |")

    pairs = [(c, k) for c in recs for k in recs
             if c["config"].endswith("_csa") and k["config"] == c["config"][:-4] + "_cpa"]
    if pairs:
        out += ["", "### Accumulator form: plain adder (cpa) against carry-save (csa)", "",
                "| T | Cell area | Flops | Fmax | Energy per MAC (geometric mean) |",
                "| --- | --- | --- | --- | --- |"]
        for c, k in pairs:
            pj = lambda r: geomean([p["pj_per_mac"] for p in r["power"].values()])    # noqa: E731
            rel = lambda a, b: f"{(a / b - 1) * 100:+.1f}%"                          # noqa: E731
            out.append(f"| {c['params']['T']} | {rel(k['cell_area_um2'], c['cell_area_um2'])} "
                       f"| {rel(k['flops'], c['flops'])} | {rel(k['fmax_hz'], c['fmax_hz'])} "
                       f"| {rel(pj(k), pj(c)) if c['power'] and k['power'] else '–'} |")
    return out + [""]


def write_results():
    recs = [json.loads(f.read_text()) for f in sorted(RECORDS.glob("*/*.json"))]
    order = {"sky130hd": 0, "ihp-sg13g2": 1, "asap7": 2}
    recs.sort(key=lambda r: (order.get(r["platform"], 9), r["params"]["T"], r["config"]))
    out = ["# Physical design results", "",
           "Generated by `flow/results.py` from the run records in `flow/records/`. Do not edit by hand.", "",
           "Post-route results of `tq_top` from OpenROAD-flow-scripts (`flow/ppa.py`), with power from "
           "simulated switching activity of each workload (`flow/power.py`). How each number is produced: "
           "`flow/README.md`.", "",
           "- **Engine only.** The operand SRAM is outside `tq_top`; its area and energy are not included.",
           "- **Cell area** is the placed standard-cell area (logic, flops, clock tree, buffers, taps), "
           "without fill. kGE = cell area ÷ the PDK's NAND2 area.",
           "- **Fmax** is from post-route STA at the target clock (1 ÷ (period − worst slack)). Peak TOPS/mm² "
           "= 2 × T² × Fmax ÷ cell area. Energy is measured at the target clock, "
           "including where post-route timing misses it by a few percent (negative slack).",
           "- **Workloads** run in Verilator against the cycle model; operands are uniform random int8, "
           "more switching than real data. Energy and utilization cover the whole simulated window "
           "(the run plus about 20 setup cycles), and energy is charged to the useful MACs (M × N × K).", ""]
    prov = sorted({(r["flow"], r["orfs_commit"], r["openroad"], r["yosys"], r["host"]) for r in recs})
    out += ["| Flow | ORFS | OpenROAD | Yosys | Host | Runs (RTL commit) |", "| --- | --- | --- | --- | --- | --- |"]
    for p in prov:
        runs = ", ".join(f"{r['platform']} {r['config']} ({r['rtl_commit']})" for r in recs
                         if (r["flow"], r["orfs_commit"], r["openroad"], r["yosys"], r["host"]) == p)
        out.append("| " + " | ".join(p) + f" | {runs} |")
    out.append("")
    for plat in dict.fromkeys(r["platform"] for r in recs):
        out += platform_section(plat, [r for r in recs if r["platform"] == plat])
    RESULTS.write_text("\n".join(out).rstrip() + "\n")
    print(f"[results] wrote {RESULTS.relative_to(ROOT)} ({len(recs)} runs)")


def main():
    args = sys.argv[1:]
    if not args:
        write_results()
        return 0
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if len(args) == 3 and args[0] == "collect":
        collect(args[1], args[2], force)
        write_results()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
