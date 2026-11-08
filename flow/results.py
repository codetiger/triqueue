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

# Operand SRAM for the engine + SRAM line (metrics.md): a macro from the ORFS platform, one per
# 128-bit slice of the W-byte port, with a read-write port (C writes) and a read port (A/B reads),
# like tq_top's separate ports. Only sky130hd has a characterized open macro (OpenRAM); ASAP7 has
# fakeram, whose generator calls its values not realistic, so ASAP7 gets no SRAM line.
SRAM_MACROS = {
    "sky130hd": ("sky130ram", "sky130_sram_1rw1r_128x256_8", "sky130_sram_1rw1r_128x256_8_TT_1p8V_25C.lib"),
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


def sram_macro(platform):
    """Area, energy per access and leakage of the platform's SRAM macro, from its liberty file.

    OpenRAM's liberty gives one internal energy per clock edge, the same whether the macro reads,
    writes or is deselected. An access is charged one rise and one fall of its clock (the SRAM
    clock is assumed gated while the port is idle), and leakage runs for the whole window.
    """
    if platform not in SRAM_MACROS:
        return None
    from ppa import ORFS_HOME
    pdir, cell, lib = SRAM_MACROS[platform]
    text = (ORFS_HOME / "flow" / "platforms" / pdir / cell / lib).read_text()
    assert '"1V"' in text and "capacitive_load_unit(1, pF)" in text, "expected pJ energy units"
    word = int(re.search(r"word_width\s*:\s*(\d+)", text).group(1))
    words = 2 ** int(re.search(r"address_width\s*:\s*(\d+)", text).group(1))
    clk = text[text.index("pin(clk1)"):]                       # the read-only port's clock
    rise = float(re.search(r"rise_power\(scalar\)\{\s*values\(\"([^\"]+)\"", clk).group(1))
    fall = float(re.search(r"fall_power\(scalar\)\{\s*values\(\"([^\"]+)\"", clk).group(1))
    return dict(cell=cell, source=f"OpenRAM liberty ({lib})", word_bits=word, kbytes=word * words / 8 / 1024,
                area_um2=float(re.search(r"area\s*:\s*([\d.]+)", text).group(1)),
                access_pj=rise + fall,
                leakage_w=float(re.search(r"(?m)^\s*cell_leakage_power\s*:\s*([\d.eE+-]+)", text).group(1)) * 1e-3)


def sram_traffic(params, shape):
    """Read and write transactions of one workload, from the cycle model (the activity run checks
    the RTL's transactions against it)."""
    import numpy as np
    sys.path.insert(0, str(ROOT / "model"))
    from activity import MODEL_FIELDS, model_params
    from tri_queue.core import Params, run_gemm
    m, n, k = shape
    mp = model_params({k_: v for k_, v in params.items() if k_ in MODEL_FIELDS})
    _, st = run_gemm(np.zeros((m, k), np.int8), np.zeros((k, n), np.int8), Params(**mp))
    return st.read_vectors, st.write_vectors


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

    synth = run / "logs" / base / "1_2_yosys.log"          # end of synthesis (results may be pruned)
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
    sram = sram_macro(platform)
    if sram:
        w_bits = 2 * CONFIGS[config]["T"] * 8
        assert w_bits % sram["word_bits"] == 0
        sram["macros_per_port_word"] = w_bits // sram["word_bits"]
        for wl, p in power.items():
            reads, writes = sram_traffic(CONFIGS[config], p["shape"])
            time_s = p["cycles"] * period * unit_ps * 1e-12
            e = ((reads + writes) * sram["access_pj"] * 1e-12 + sram["leakage_w"] * time_s) * sram["macros_per_port_word"]
            p["sram"] = dict(reads=reads, writes=writes, energy_j=e)
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
        sram=sram,
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


def suite(power):
    """The workloads that enter geometric means: one per shape. *_relu repeats a shape with other
    operand data and is shown on its own row."""
    return {wl: p for wl, p in power.items() if not wl.endswith("_relu")}


def derived(r):
    t = r["params"]["T"]
    kge = r["cell_area_um2"] / r["nand2_um2"] / 1e3
    peak_tops = 2 * t * t * r["fmax_hz"] / 1e12
    return dict(macs=t * t, kge=kge, macs_per_kge=t * t / kge, tops_mm2=peak_tops / (r["cell_area_um2"] / 1e6))


def platform_section(platform, recs):
    first = recs[0]
    out = [f"## {platform}", ""]
    clocks = sorted({r["clock_period_ps"] for r in recs})
    clk = (f"Target clock {num(1e6 / clocks[0])} MHz ({clocks[0]:g} ps)" if len(clocks) == 1 else
           "Target clock per config (column *Clock*; a config that cannot close the platform's clock is "
           "run at one it closes)")
    out.append(f"{clk}, "
               f"core utilization {first['core_utilization_pct']}%, typical corner of the platform. "
               f"Gate equivalent: `{first['nand2_cell']}` ({first['nand2_um2']:.4g} µm²).")
    mixed = len(clocks) > 1
    out += ["", "### Area and timing", "",
            "| Config | Parameters | " + ("Clock | " if mixed else "") + "Cell area | kGE | Flops | Flops per MAC "
            "| MACs/cycle per kGE | Fmax (post-route) | Worst setup slack | Peak TOPS/mm² at Fmax | DRC |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |" + (" --- |" if mixed else "")]
    for r in recs:
        d = derived(r)
        params = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        clock = f"{num(1e6 / r['clock_period_ps'])} MHz | " if mixed else ""
        out.append(f"| {r['config']} | {params} | {clock}{area_str(r['cell_area_um2'])} | {num(d['kge'], 1)} "
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

    at = f"at {num(1e6 / clocks[0])} MHz" if len(clocks) == 1 else "at each config's target clock"
    out += ["", f"### Energy, engine only, {at}", "",
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
        if len(suite(r["power"])) > 1:
            pj = geomean([p["pj_per_mac"] for p in suite(r["power"]).values()])
            tw = geomean([p["tops_per_w"] for p in suite(r["power"]).values()])
            out.append(f"| {r['config']} | geometric mean | | | | | | | {pj:.3f} | {tw:.3f} |")

    sr = [r for r in recs if r.get("sram") and any("sram" in p for p in r["power"].values())]
    if sr:
        m = sr[0]["sram"]
        out += ["", f"### Energy, engine + operand SRAM, {at}", "",
                f"SRAM: `{m['cell']}` ({m['word_bits']}-bit word, {m['kbytes']:g} KB, one read-write and one read "
                f"port), {m['macros_per_port_word']} per port word, {area_str(m['area_um2'] * m['macros_per_port_word'])} "
                f"of macro area, not included in the cell area above. Energy per access "
                f"{m['access_pj']:.1f} pJ, from the {m['source']}: OpenRAM gives the same energy for every clock "
                f"edge whether the macro reads, writes or is idle, so each access is charged one clock cycle "
                f"(SRAM clock gated when idle) plus leakage. Access counts are the workload's read and write "
                f"transactions. Capacity does not fit the workloads; this is the energy of the port's traffic.", "",
                "| Config | Workload | SRAM reads | SRAM writes | SRAM pJ/MAC | Engine pJ/MAC | Engine + SRAM pJ/MAC | TOPS/W |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in sr:
            tot = []
            for wl, p in r["power"].items():
                if "sram" not in p:
                    continue
                s_pj = p["sram"]["energy_j"] / p["useful_macs"] * 1e12
                t_pj = p["pj_per_mac"] + s_pj
                if not wl.endswith("_relu"):
                    tot.append(t_pj)
                out.append(f"| {r['config']} | {wl} | {num(p['sram']['reads'])} | {num(p['sram']['writes'])} "
                           f"| {s_pj:.3f} | {p['pj_per_mac']:.3f} | {t_pj:.3f} | {2 / t_pj:.3f} |")
            if len(tot) > 1:
                g = geomean(tot)
                out.append(f"| {r['config']} | geometric mean | | | | | {g:.3f} | {2 / g:.3f} |")

    gated = [(c, k) for c in recs for k in recs
             if c["config"].endswith("_nocg") and k["config"] == c["config"][:-5]]
    if gated:
        out += ["", "### Clock gating: gated against ungated (CLK_GATE = 0)", "",
                "| Config | Cell area | Flops | Fmax | " + " | ".join(
                    f"Power, {wl}" for wl in gated[0][1]["power"]) + " | Energy per MAC (geometric mean) |",
                "| --- | --- | --- | --- |" + " --- |" * (len(gated[0][1]["power"]) + 1)]
        rel = lambda a, b: f"{(a / b - 1) * 100:+.1f}%"                              # noqa: E731
        pj = lambda r: geomean([p["pj_per_mac"] for p in suite(r["power"]).values()])      # noqa: E731
        for c, k in gated:
            out.append(f"| {k['config']} | {rel(k['cell_area_um2'], c['cell_area_um2'])} "
                       f"| {rel(k['flops'], c['flops'])} | {rel(k['fmax_hz'], c['fmax_hz'])} | "
                       + " | ".join(rel(k["power"][wl]["power_w"]["total"], c["power"][wl]["power_w"]["total"])
                                    if wl in c["power"] else "–" for wl in k["power"])
                       + f" | {rel(pj(k), pj(c)) if c['power'] and k['power'] else '–'} |")

    pairs = [(c, k) for c in recs for k in recs
             if c["config"].endswith("_csa") and k["config"] == c["config"][:-4] + "_cpa"]
    if pairs:
        out += ["", "### Accumulator form: plain adder (cpa) against carry-save (csa)", "",
                "| T | Cell area | Flops | Fmax | Energy per MAC (geometric mean) |",
                "| --- | --- | --- | --- | --- |"]
        for c, k in pairs:
            pj = lambda r: geomean([p["pj_per_mac"] for p in suite(r["power"]).values()])    # noqa: E731
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
           "- **Engine only** unless a table says otherwise. The operand SRAM is outside `tq_top`; where an "
           "open macro with characterized power exists (sky130hd), a separate table adds its access energy.",
           "- **Cell area** is the placed standard-cell area (logic, flops, clock tree, buffers, taps), "
           "without fill. kGE = cell area ÷ the PDK's NAND2 area.",
           "- **Fmax** is from post-route STA at the target clock (1 ÷ (period − worst slack)). Peak TOPS/mm² "
           "= 2 × T² × Fmax ÷ cell area. Energy is measured at the target clock, "
           "including where post-route timing misses it by a few percent (negative slack).",
           "- **Workloads** run in Verilator against the cycle model; operands are uniform random int8, "
           "more switching than real data. Energy and utilization cover the whole simulated window "
           "(the run plus about 20 setup cycles), and energy is charged to the useful MACs (M × N × K). "
           "`*_relu` workloads repeat a shape with synthetic inference-like operands (post-ReLU uint8 "
           "activations, about half zeros, and Gaussian int8 weights; not data from a real network) and "
           "are left out of the geometric means.", ""]
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
