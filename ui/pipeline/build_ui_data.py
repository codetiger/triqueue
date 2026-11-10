#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""RTL-to-UI Deterministic Cycle Trace & PPA Record Bundler.

Generates deterministic JS bundles from committed sources:
  1. Cycle-exact microarchitectural snapshots from `model/tri_queue` (which matches `rtl/` cycle
     for cycle) across curated GEMM scenarios (`ui/data/traces_data.js`).
  2. Committed PPA records from `flow/records/*.json` (`ui/data/ppa_records.js`).

Silicon 3D layout bundles (`ui/data/chip_asap7.js`, `ui/data/chip_sky130hd.js`) are strictly
read-only here: they are extracted 1:1 from `6_final.def` ONLY by `flow/release_layout.py`
(`ui/pipeline/extract_layout.py`) and cryptographically locked via `flow/LAYOUT_LOCK.json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

from tri_queue import isa
from tri_queue.core import Params, TriQueueCore, build_gemm, read_c, reference
from tri_queue.packing import kp

OUT_DIR = ROOT / "ui" / "data"


# ---------------------------------------------------------------------------
# 1. Cycle-Exact Microarchitectural State Recorder
# ---------------------------------------------------------------------------

def capture_scenario(
    sc_id: str,
    title: str,
    subtitle: str,
    badge: str,
    description: str,
    m: int,
    n: int,
    k: int,
    params: Params,
    seed: int = 42,
) -> dict:
    """Run one GEMM scenario on TriQueueCore and record full state on every cycle."""
    rng = np.random.default_rng(seed)
    # Deterministic small integers so products and sums are easy to read in the UI
    a = rng.integers(-7, 8, size=(m, k), dtype=np.int8)
    b = rng.integers(-7, 8, size=(k, n), dtype=np.int8)
    ref_c = reference(a, b)

    trace_lines: list[str] = []
    mem, prog, layout = build_gemm(a, b, params)
    core = TriQueueCore(mem, prog, params, trace=trace_lines)

    # Parse instruction table with byte offsets and sizes
    instr_table = []
    pc_walk = 0
    while pc_walk < len(prog):
        op, arg, imm, sz = isa.decode(prog, pc_walk)
        mnem = isa.MNEMONIC.get(op, f"OP{op:X}")
        reg_names = ["A_BASE", "B_BASE", "C_BASE", "C_LD", "K", "M", "N", "MODE"]
        if op == isa.CFG:
            label = f"CFG {reg_names[arg]}, 0x{imm:x} ({imm})"
        elif op == isa.VOUTER_MAC:
            sh_str = (
                "AUTO"
                if (arg & isa.AUTO)
                else f"{params.tile if arg & 2 else params.tile // 2}x{params.tile if arg & 1 else params.tile // 2}"
            )
            label = f"VOUTER_MAC {sh_str}"
        elif op in (isa.LOOP_K, isa.LOOP_T):
            label = f"{mnem} {arg}"
        else:
            label = mnem
        instr_table.append({
            "pc": pc_walk,
            "size": sz,
            "op": op,
            "mnemonic": mnem,
            "arg": arg,
            "imm": imm,
            "bytes_hex": prog[pc_walk:pc_walk + sz].hex(),
            "label": label,
            "is_kernel": pc_walk >= 40,
        })
        pc_walk += sz

    cycles_data = []

    while not core.halted:
        cyc = core.cycle
        ev_start = len(trace_lines)

        prev_qa_n = core.fifo["A"].n
        prev_qb_n = core.fifo["B"].n
        prev_pc = core.pc

        # Stage 1: memory arrivals
        core.arrivals()
        arrived_a = core.fifo["A"].n - prev_qa_n
        arrived_b = core.fifo["B"].n - prev_qb_n

        # Stage 2: MAC unit
        core.step_mac()

        # Stage 3: memory ports & panel buffer
        core.step_port()

        # Stage 4: front end
        core.step_front()
        if core.err_align and not core.stats.error:
            core.stop(isa.ERR_ALIGN)

        cyc_events = [line.split(" ", 1)[1] for line in trace_lines[ev_start:]]

        def dump_fifo(bf):
            raw = b"".join(ch.tobytes() for ch in bf.chunks)
            arr = np.frombuffer(raw, dtype=np.int8).tolist()
            vecs = []
            for i in range(0, len(arr), params.port_bytes):
                vecs.append(arr[i:i + params.port_bytes])
            return {"bytes": len(arr), "vectors": vecs}

        qa_state = dump_fifo(core.fifo["A"])
        qb_state = dump_fifo(core.fifo["B"])

        macq_list = []
        for cmd in core.macq:
            if cmd[0] == "ZERO":
                macq_list.append({"kind": "ZERO_C", "detail": "Clear Banks X & Y"})
            elif cmd[0] == "SWAP":
                ti, tj = core.tile_coords(cmd[1])
                macq_list.append({"kind": "SWAP_C", "tile": cmd[1], "detail": f"Tile {cmd[1]} ({ti},{tj})"})
            elif cmd[0] == "MAC":
                macq_list.append({
                    "kind": "VOUTER_MAC",
                    "shape": f"{cmd[1]}x{cmd[2]}x{cmd[3]}",
                    "r": cmd[1],
                    "c": cmd[2],
                    "d": cmd[3],
                })

        bank0_vals = (core.sum[0] + core.carry[0]).view(np.int32).tolist()
        bank1_vals = (core.sum[1] + core.carry[1]).view(np.int32).tolist()

        beat_event = next((e for e in cyc_events if e.startswith("mac beat ")), None)
        start_event = next((e for e in cyc_events if e.startswith("mac start ")), None)
        zero_event = next((e for e in cyc_events if e.startswith("mac zero")), None)
        wr_event = next((e for e in cyc_events if e.startswith("port wr ")), None)
        fe_event = next((e for e in cyc_events if e.startswith("fe issue ")), None)

        cg_prog = bool(fe_event and " op=CFG " in fe_event)
        cg_qa_push = arrived_a > 0
        cg_qb_push = arrived_b > 0
        cg_macq_push = bool(fe_event and any(x in fe_event for x in ("VOUTER_MAC", "SWAP_C", "ZERO_C")))
        cg_b1_latch = start_event is not None
        cg_prod_reg = beat_event is not None

        acc_bank = int(beat_event.split("bank=")[1].split()[0]) if beat_event else -1

        cycles_data.append({
            "cycle": cyc,
            "pc": prev_pc,
            "next_pc": core.pc,
            "fence": core.fence,
            "halted": core.halted,
            "loops": [
                {"kind": f[0], "start": f[1], "end": f[2], "rem": f[3], "iter": f[4]}
                for f in core.loops
            ],
            "front_tile": core.front_tile(),
            "cfg": list(core.cfg),
            "macq": macq_list,
            "scmd_a": list(core.scmd["A"]),
            "scmd_b": list(core.scmd["B"]),
            "stream_a": {"addr": core.stream["A"][0], "left": core.stream["A"][1], "mode": core.stream["A"][2]} if core.stream["A"] else None,
            "stream_b": {"addr": core.stream["B"][0], "left": core.stream["B"][1], "mode": core.stream["B"][2]} if core.stream["B"] else None,
            "qa": qa_state,
            "qb": qb_state,
            "inflight": [{"arrive": arr, "s": s, "bytes": len(d)} for arr, s, d in core.inflight],
            "pinflight": [{"arrive": arr, "bytes": len(d)} for arr, d in core.pinflight],
            "active_bank": core.active,
            "bank0": bank0_vals,
            "bank1": bank1_vals,
            "drain": {
                "bank": core.drain["bank"],
                "tile": core.drain["tile"],
                "ready": core.drain["ready"],
                "writes_left": len(core.drain["writes"]),
            } if core.drain else None,
            "rd_pend": core.rd_pend,
            "wr_pend": core.wr_pend,
            "events": cyc_events,
            "arrived_a": arrived_a,
            "arrived_b": arrived_b,
            "cg": {
                "prog": cg_prog,
                "qa": cg_qa_push,
                "qb": cg_qb_push,
                "macq": cg_macq_push,
                "b1_latch": cg_b1_latch,
                "prod_reg": cg_prod_reg,
                "bank0": (acc_bank == 0) or (zero_event is not None) or (core.drain is not None and core.drain["bank"] == 0 and len(core.drain["writes"]) == 1),
                "bank1": (acc_bank == 1) or (zero_event is not None) or (core.drain is not None and core.drain["bank"] == 1 and len(core.drain["writes"]) == 1),
                "drain": wr_event is not None,
            },
        })

        core.cycle += 1

    # Second pass: enrich beat details by intercepting `do_beat` cleanly
    mem2, prog2, _ = build_gemm(a, b, params)
    core2 = TriQueueCore(mem2, prog2, params)
    orig_do_beat = core2.do_beat

    beat_records_by_cycle = {}

    def hooked_do_beat():
        cur = core2.cur
        r, c, d, h, T = cur["r"], cur["c"], cur["d"], core2.h, core2.T
        mode = core2.cfg[isa.MODE]
        a_ext = core2._ext(cur["a"], bool(mode & isa.MODE_A_SIGNED))
        b_ext = core2._ext(cur["b"], bool(mode & isa.MODE_B_SIGNED))
        beat_idx = cur["beat"]
        base = beat_idx * d
        quads = []
        for qi in (0, 1):
            for qj in (0, 1):
                rb = qi if r == T else 0
                cb = qj if c == T else 0
                off = (qi if r == h else 0) * (2 if c == h else 1) + (qj if c == h else 0)
                kk = base + off
                ah = a_ext[kk * r + h * rb: kk * r + h * rb + h].tolist()
                bh = b_ext[kk * c + h * cb: kk * c + h * cb + h].tolist()
                prod = np.outer(ah, bh).tolist()
                quads.append({
                    "qi": qi,
                    "qj": qj,
                    "name": ["TL", "TR", "BL", "BR"][2 * qi + qj],
                    "dest_q": ["TL", "TR", "BL", "BR"][2 * rb + cb],
                    "rb": rb,
                    "cb": cb,
                    "k_off": off,
                    "k_step": kk,
                    "a_vec": ah,
                    "b_vec": bh,
                    "products": prod,
                })
        beat_records_by_cycle[core2.cycle] = {
            "shape": f"{r}x{c}x{d}",
            "r": r,
            "c": c,
            "d": d,
            "beat": beat_idx,
            "bank": core2.active,
            "raw_a": cur["a"].view(np.int8).tolist(),
            "raw_b": cur["b"].view(np.int8).tolist(),
            "quadrants": quads,
        }
        orig_do_beat()

    core2.do_beat = hooked_do_beat
    core2.run()

    for c_entry in cycles_data:
        c_entry["mac_beat"] = beat_records_by_cycle.get(c_entry["cycle"], None)

    out_c = read_c(mem, layout)
    assert np.array_equal(out_c, ref_c), "Bit-exact check failed!"

    return {
        "id": sc_id,
        "title": title,
        "subtitle": subtitle,
        "badge": badge,
        "description": description,
        "shape": {"m": m, "n": n, "k": k, "kp": kp(k)},
        "params": {
            "tile": params.tile,
            "port_bytes": params.port_bytes,
            "fifo_depth": params.fifo_depth,
            "mac_queue_depth": params.mac_queue_depth,
            "stream_queue_depth": params.stream_queue_depth,
            "mem_latency": params.mem_latency,
            "shared_port": params.shared_port,
            "panel_bytes": params.panel_bytes,
            "gnt_stall": params.gnt_stall,
        },
        "layout": layout,
        "stats": {
            "cycles": core.cycle,
            "ideal_cycles": -(-(m * n * k) // (params.tile * params.tile)),
            "useful_macs": m * n * k,
            "utilization": round((m * n * k) / (params.tile * params.tile * core.cycle), 4),
            "instructions": core.stats.instructions,
            "beats": core.stats.beats,
            "read_vectors": core.stats.read_vectors,
            "read_bytes": core.stats.read_bytes,
            "write_vectors": core.stats.write_vectors,
            "write_bytes": core.stats.write_bytes,
            "panel_hits": core.stats.panel_hits,
            "panel_vectors": core.stats.panel_vectors,
            "mac_ops": dict(core.stats.mac_ops),
            "mac_state": dict(core.stats.mac_state),
            "port_stall": dict(core.stats.port_stall),
        },
        "matrices": {
            "a": a.tolist(),
            "b": b.tolist(),
            "c": out_c.tolist(),
        },
        "program": instr_table,
        "cycles": cycles_data,
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[ui-pipeline] 1/3 Generating deterministic GEMM scenarios from model/tri_queue...")

    ref_q = dict(fifo_depth=4, mac_queue_depth=3, mem_latency=2)
    scenarios = [
        capture_scenario(
            sc_id="overlap_16x16x32",
            title="16×16×32 Multi-Tile 3-Way Overlap",
            subtitle="4 Tiles (8×8×1, K=32) · 100% Hidden Drain · Ping-Pong Swap",
            badge="80.5% Util · 4 Tiles",
            description=(
                "Demonstrates the core Tri-Queue mechanism across four 8×8 output tiles (K=32, 32 cycles/tile): "
                "while the MAC array accumulates Tile t into the active bank, the Stream Engine "
                "prefetches Tile t+1 into QA/QB, and the 16-cycle Drain of Tile t-1 from the idle "
                "bank hides 100% under compute across SWAP_C with zero cycle bubble."
            ),
            m=16, n=16, k=32,
            params=Params(tile=8, **ref_q),
            seed=11,
        ),
        capture_scenario(
            sc_id="arity_12x12x16",
            title="12×12×16 Variable-Arity Edge Tiles",
            subtitle="All 4 Shapes: 8×8×1, 8×4×2, 4×8×2, 4×4×4",
            badge="4 Arity Modes",
            description=(
                "Showcases VOUTER_MAC AUTO dynamically folding idle multipliers into the k dimension "
                "on edge tiles: Tile 0 (interior 8×8×1, arity 1), Tile 1 (right-edge 8×4×2, arity 2), "
                "Tile 2 (bottom-edge 4×8×2, arity 2), and Tile 3 (corner 4×4×4, arity 4 into TL's 6:2 compressor)."
            ),
            m=12, n=12, k=16,
            params=Params(tile=8, **ref_q),
            seed=23,
        ),
        capture_scenario(
            sc_id="panel_8x16x16",
            title="8×16×16 On-Chip A-Panel Buffer Replay",
            subtitle="PANEL_BYTES=1024 · Saves 50% of A-Stream Reads",
            badge="Panel Buffer Hit",
            description=(
                "Demonstrates the optional A Panel Buffer (spec §10.9): Tile 0 (0,0) reads the A panel "
                "from SRAM and fills the panel buffer; Tile 1 (0,1) replays A directly from the buffer "
                "into QA without touching the SRAM read port, leaving 100% of port bandwidth for B."
            ),
            m=8, n=16, k=16,
            params=Params(tile=8, panel_bytes=1024, **ref_q),
            seed=37,
        ),
        capture_scenario(
            sc_id="backpressure_8x16x16",
            title="8×16×16 OBI Memory Back-Pressure",
            subtitle="30% Random gnt Stalls · Decoupled FIFO Absorption",
            badge="OBI Stability",
            description=(
                "Demonstrates OBI request stability (held rd_req/wr_req when gnt=0) and how the "
                "decoupled QA/QB FIFOs and MAC Command Queue absorb memory port stalls while keeping "
                "the outer-product pipeline fed."
            ),
            m=8, n=16, k=16,
            params=Params(tile=8, gnt_stall=0.30, gnt_seed=7, **ref_q),
            seed=49,
        ),
    ]

    for sc in scenarios:
        print(
            f"  - {sc['id']}: {sc['stats']['cycles']} cycles, {sc['stats']['beats']} beats, "
            f"util={sc['stats']['utilization']*100:.1f}%"
        )

    print("[ui-pipeline] 2/3 Verifying locked 3D Silicon Die bundles (never overwritten here)...")
    for chip_file in ("chip_asap7.js", "chip_sky130hd.js"):
        p = OUT_DIR / chip_file
        if not p.exists():
            raise FileNotFoundError(
                f"Missing locked layout {p.relative_to(ROOT)}; run flow/release_layout.py to generate."
            )
        print(f"  - {chip_file}: present ({p.stat().st_size:,} bytes, read-only)")

    print("[ui-pipeline] 3/3 Collecting committed PPA records from flow/records/...")
    ppa_records = {"asap7": {}, "sky130hd": {}}
    for pdk in ("asap7", "sky130hd"):
        rec_dir = ROOT / "flow" / "records" / pdk
        if rec_dir.exists():
            for jf in sorted(rec_dir.glob("*.json")):
                ppa_records[pdk][jf.stem] = json.loads(jf.read_text())

    (OUT_DIR / "traces_data.js").write_text(
        "window.TRIQUEUE_SCENARIOS = " + json.dumps(scenarios, sort_keys=True, separators=(",", ":")) + ";\n"
    )
    (OUT_DIR / "ppa_records.js").write_text(
        "window.TRIQUEUE_PPA_RECORDS = " + json.dumps(ppa_records, sort_keys=True, separators=(",", ":")) + ";\n"
    )
    print(f"[ui-pipeline] Done! Deterministic trace & PPA bundles written to {OUT_DIR}")


if __name__ == "__main__":
    main()
