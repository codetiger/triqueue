# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""cocotb test: run GEMM cases on tq_top and check each one against the cycle model.

Cases and the RTL configuration come from environment variables set by verif/test_rtl.py:
  TQ_PARAMS  JSON of model Params fields matching the RTL parameters (tile, fifo_depth, ...)
  TQ_CASES   JSON list of {m, n, k, a, b, seed, lat}, optionally prog (a tri_queue.programs
             edge program), misalign (a layout field moved off the port width), prog_poke
  TQ_HOST    "axil": the top level is tq_top_axil, registers are reached through AXI4-Lite;
             "axi": tq_top_axi, AXI4-Lite registers and an AXI4 memory slave

Checks per case (docs/golden-vectors.md, "How the RTL is checked against the model"):
  1. final memory bit-exact (and C equal to numpy)
  2. read and write transaction sequences, with cycles
  3. MAC start cycles and shapes
  4. counters: instructions, beats, reads, writes; total cycles
"""
import json
import os
import sys
from pathlib import Path

import cocotb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
from tri_queue import isa  # noqa: E402
from tri_queue.core import Params, TriQueueCore, build_gemm, read_c, reference  # noqa: E402
from tri_queue.programs import edge_program  # noqa: E402

from tq_tb import TqTb, TqTbAxi, TqTbAxil  # noqa: E402

DT = {"s8": np.int8, "u8": np.uint8}


def operands(case):
    """Uniform random operands, or with dist="relu" a synthetic inference-like distribution:
    A as post-ReLU activations (uint8, about half zeros, half-normal magnitudes) and B as
    Gaussian int8 weights. Not data from a real network; it bounds the uniform case from below."""
    rng = np.random.default_rng(case["seed"])

    def rand(shape, dt):
        lo, hi = (-128, 128) if dt == "s8" else (0, 256)
        return rng.integers(lo, hi, shape).astype(DT[dt])

    m, n, k = case["m"], case["n"], case["k"]
    if case.get("dist") == "relu":
        assert (case["a"], case["b"]) == ("u8", "s8")
        a = np.clip(np.rint(np.maximum(rng.standard_normal((m, k)), 0) * 40), 0, 255).astype(np.uint8)
        b = np.clip(np.rint(rng.standard_normal((k, n)) * 24), -127, 127).astype(np.int8)
        return a, b
    return rand((m, k), case["a"]), rand((k, n), case["b"])


def model_run(a, b, p: Params, case=None):
    case = case or {}
    mem, prog, layout = build_gemm(a, b, p)
    if "prog" in case or "misalign" in case:
        L = dict(layout)
        if "misalign" in case:
            L[case["misalign"]] += 4
        cfg = isa.gemm_config(L["a_base"], L["b_base"], L["c_base"], L["c_ld"], L["k"], L["m"], L["n"], L["mode"])
        prog = edge_program(case["prog"], cfg) if "prog" in case else cfg + isa.gemm_kernel()
    trace = []
    core = TriQueueCore(mem.copy(), prog, p, trace)
    stats = core.run()
    ev = {"rd": [], "wr": [], "start": []}
    for line in trace:
        cyc, unit, event, *kv = line.split()
        kv = dict(x.split("=", 1) for x in kv)
        if (unit, event) == ("port", "rd"):
            ev["rd"].append((int(cyc), int(kv["addr"])))
        elif (unit, event) == ("port", "wr"):
            ev["wr"].append((int(cyc), int(kv["addr"]), int(kv["n"]), bytes.fromhex(kv["data"])))
        elif (unit, event) == ("mac", "start"):
            ev["start"].append((int(cyc), kv["shape"]))
    return mem, prog, layout, core.mem, stats, ev


def first_diff(xs, ys):
    for i, (x, y) in enumerate(zip(xs, ys)):
        if x != y:
            return f"#{i}: rtl {x} vs model {y}"
    return f"length rtl {len(xs)} vs model {len(ys)}" if len(xs) != len(ys) else None


@cocotb.test()
async def gemm_cases(dut):
    params = json.loads(os.environ["TQ_PARAMS"])
    cases = json.loads(os.environ["TQ_CASES"])
    tb = {"axil": TqTbAxil, "axi": TqTbAxi}.get(os.environ.get("TQ_HOST"), TqTb)(dut, params["tile"])
    await tb.start()
    failures = []
    for case in cases:
        stall = case.get("stall", 0.0)
        p = Params(**params, mem_latency=case["lat"], gnt_stall=stall, gnt_seed=case["seed"],
                   **({"wr_resp_latency": case["wr_lat"]} if "wr_lat" in case else {}))
        a, b = operands(case)
        mem0, prog, layout, mem_model, s, ev = model_run(a, b, p, case)
        mem = mem0.copy()
        name = f"{case['m']}x{case['n']}x{case['k']} {case['a']}/{case['b']} lat={case['lat']}"
        for key in ("prog", "misalign", "prog_poke"):
            if key in case:
                name += f" {key}={case[key]}"
        if case.get("stall"):
            name += f" stall={case['stall']}"
        if "wr_lat" in case:
            name += f" wr_lat={case['wr_lat']}"
        try:
            rec = await tb.run(mem, prog, case["lat"], max_cycles=2 * s.cycles + 200,
                               stall=stall, seed=case["seed"],
                               wr_resp_latency=p.wr_resp_latency if p.wr_ack else None,
                               **({"bus_err": case["bus_err"]} if "bus_err" in case else {}),
                               **({"prog_poke": True} if case.get("prog_poke") else {}))
        except TimeoutError:
            failures.append(f"{name}: hang (model finishes in {s.cycles} cycles)")
            dut._log.error("%s: hang (model %d cycles)", name, s.cycles)
            await tb.reg_write(0x008, 2)                       # ABORT, continue with the next case
            for _ in range(4):
                await tb.reg_write(0xFFC, 0, 0)
            continue
        errs = []
        if not s.error and "misalign" not in case and not np.array_equal(read_c(mem, layout), reference(a, b)):
            errs.append("C differs from numpy")
        if not np.array_equal(mem, mem_model):
            errs.append("final memory differs from model")
        checks = (("reads", rec.reads, ev["rd"]), ("writes", rec.writes, ev["wr"]),
                  ("mac starts", rec.mac_starts, ev["start"]))
        for label, xs, ys in checks:
            d = first_diff(xs, ys)
            if d:
                errs.append(f"{label} {d}")
        expect = {"instret": s.instructions, "beats": s.beats, "reads": s.read_vectors,
                  "writes": s.write_vectors, "cycles": s.cycles}
        got = dict(rec.perf)
        if rec.cycles != s.cycles:
            errs.append(f"busy cycles rtl {rec.cycles} vs model {s.cycles}")
        for k_, v in expect.items():
            if got[k_] != v:
                errs.append(f"perf {k_} rtl {got[k_]} vs model {v}")
        got_err = (rec.status >> 8) & 0xF if rec.status & 0x4 else 0
        if got_err != s.error:
            errs.append(f"error code rtl {got_err} vs model {s.error} (STATUS {rec.status:#x})")
        if bool(rec.status & 0x8) != ("bus_err" in case):
            errs.append(f"STATUS.BUS_ERR {bool(rec.status & 0x8)}, expected {'bus_err' in case}")
        if errs:
            failures.append(f"{name}: " + "; ".join(errs))
            dut._log.error("%s: %s", name, "; ".join(errs))
        else:
            dut._log.info("%s: ok, %d cycles", name, rec.cycles)
    assert not failures, f"{len(failures)}/{len(cases)} cases failed:\n" + "\n".join(failures[:20])
