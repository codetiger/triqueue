# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""cocotb test: run GEMM cases on tq_top and check each one against the cycle model.

Cases and the RTL configuration come from environment variables set by verif/test_rtl.py:
  TQ_PARAMS  JSON of model Params fields matching the RTL parameters (tile, fifo_depth, ...)
  TQ_CASES   JSON list of {m, n, k, a, b, seed, lat}

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
from tri_queue.core import Params, TriQueueCore, build_gemm, read_c, reference  # noqa: E402

from tq_tb import TqTb  # noqa: E402

DT = {"s8": np.int8, "u8": np.uint8}


def operands(case):
    rng = np.random.default_rng(case["seed"])

    def rand(shape, dt):
        lo, hi = (-128, 128) if dt == "s8" else (0, 256)
        return rng.integers(lo, hi, shape).astype(DT[dt])

    return rand((case["m"], case["k"]), case["a"]), rand((case["k"], case["n"]), case["b"])


def model_run(a, b, p: Params):
    mem, prog, layout = build_gemm(a, b, p)
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
    tb = TqTb(dut, params["tile"])
    await tb.start()
    failures = []
    for case in cases:
        p = Params(**params, mem_latency=case["lat"])
        a, b = operands(case)
        mem0, prog, layout, mem_model, s, ev = model_run(a, b, p)
        mem = mem0.copy()
        name = f"{case['m']}x{case['n']}x{case['k']} {case['a']}/{case['b']} lat={case['lat']}"
        if case.get("stall"):
            name += f" stall={case['stall']}"
        try:
            stall = case.get("stall", 0.0)
            rec = await tb.run(mem, prog, case["lat"], max_cycles=int(4 / (1 - stall)) * s.cycles + 200,
                               stall=stall, seed=case["seed"])
        except TimeoutError:
            failures.append(f"{name}: hang (model finishes in {s.cycles} cycles)")
            dut._log.error("%s: hang (model %d cycles)", name, s.cycles)
            await tb.reg_write(0x008, 2)                       # ABORT, continue with the next case
            for _ in range(4):
                await tb.reg_write(0xFFC, 0, 0)
            continue
        errs = []
        if not np.array_equal(read_c(mem, layout), reference(a, b)):
            errs.append("C differs from numpy")
        if not np.array_equal(mem, mem_model):
            errs.append("final memory differs from model")
        timed = not case.get("stall")      # back-pressure is not in the cycle model: compare order only
        if timed:
            checks = (("reads", rec.reads, ev["rd"]), ("writes", rec.writes, ev["wr"]),
                      ("mac starts", rec.mac_starts, ev["start"]))
        else:
            checks = (("write order", [w[1:] for w in rec.writes], [w[1:] for w in ev["wr"]]),
                      ("mac start shapes", [x[1] for x in rec.mac_starts], [x[1] for x in ev["start"]]))
        for label, xs, ys in checks:
            d = first_diff(xs, ys)
            if d:
                errs.append(f"{label} {d}")
        expect = {"instret": s.instructions, "beats": s.beats, "reads": s.read_vectors, "writes": s.write_vectors}
        got = dict(rec.perf)
        if timed:
            expect["cycles"] = s.cycles
            if rec.cycles != s.cycles:
                errs.append(f"busy cycles rtl {rec.cycles} vs model {s.cycles}")
        for k_, v in expect.items():
            if got[k_] != v:
                errs.append(f"perf {k_} rtl {got[k_]} vs model {v}")
        if rec.status & 0x4:
            errs.append(f"error status {rec.status:#x}")
        if errs:
            failures.append(f"{name}: " + "; ".join(errs))
            dut._log.error("%s: %s", name, "; ".join(errs))
        else:
            dut._log.info("%s: ok, %d cycles (model %d without back-pressure)", name, rec.cycles, s.cycles)
    assert not failures, f"{len(failures)}/{len(cases)} cases failed:\n" + "\n".join(failures[:20])
