# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""cocotb test: run GEMM cases on tq_top and check each one against the cycle model.

Cases and the RTL configuration come from environment variables set by verif/test_rtl.py:
  TQ_PARAMS  JSON of model Params fields matching the RTL parameters (tile, fifo_depth, ...)
  TQ_CASES   JSON list of {m, n, k, a, b, seed, lat}, optionally prog (a tri_queue.programs
             edge program), misalign (a layout field moved off the port width), prog_poke
  TQ_HOST    "axil": the top level is tq_top_axil, registers are reached through AXI4-Lite;
             "axi": tq_top_axi, AXI4-Lite registers and an AXI4 memory slave;
             "xif": tq_top_xif, registers reached with tq.wr / tq.rd through CV-X-IF

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
from cocotb.triggers import FallingEdge, ReadOnly

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
from tri_queue import isa  # noqa: E402
from tri_queue.core import Params, TriQueueCore, build_gemm, read_c, reference  # noqa: E402
from tri_queue.programs import edge_program  # noqa: E402

from tq_tb import REG_CTRL, REG_PROG, REG_STATUS, TqTb, TqTbAxi, TqTbAxil, TqTbXif, val  # noqa: E402

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
    if case.get("k") == 0 and case.get("m", 0) > 0 and case.get("n", 0) > 0 and "misalign" not in case:
        c0 = layout["c_base"]
        mem[c0:c0 + layout["m"] * layout["c_ld"]] = 0xA5
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


async def check_host_contract(tb, dut, params, host, perf_off: bool):
    assert await tb.reg_read(0x000) == 0x5451_0001
    hw = await tb.reg_read(0x004)
    assert (hw & 0xFF) == params["tile"]
    assert ((hw >> 8) & 0xFF) == params.get("fifo_depth", 8)
    assert ((hw >> 16) & 0xF) == params.get("mac_queue_depth", 4)
    assert ((hw >> 20) & 0x1) == int(params.get("shared_port", False))
    assert ((hw >> 24) & 0xFF) * 4 == params.get("prog_bytes", 64)
    if host != "xif":
        assert await tb.reg_read(0x000 + 2) == 0x5451_0001
        assert await tb.reg_read(0x004 + 1) == hw

    await tb.reg_write(0x010, 0x0)
    await tb.reg_write(0x014, 0x3)
    tiny_mem = np.zeros(tb.W, dtype=np.uint8)
    tiny_prog = isa.cfg(isa.A_BASE, 0x12340) + isa.enc(isa.FENCE)
    wr_lat = 1 if params.get("wr_ack") else None
    await tb.run(tiny_mem, tiny_prog, 1, wr_resp_latency=wr_lat)
    assert await tb.reg_read(0x014) == 0x1 and val(dut.irq_o) == 0
    assert await tb.reg_read(0x040) == 0x12340
    await tb.reg_write(REG_CTRL, 2)
    assert await tb.reg_read(0x040) == 0x12340

    await tb.reg_write(0x010, 0x3)
    assert val(dut.irq_o) == 1
    await tb.reg_write(0x014, 0x1)
    assert await tb.reg_read(0x014) == 0x0 and val(dut.irq_o) == 0

    if host == "native":
        await tb.reg_write(REG_PROG, 0x00000090)
        await tb.start_cmd()
        while not val(dut.u_core.done_o):
            await FallingEdge(dut.clk_i)
        await tb.reg_write(0x014, 0x1)
        assert await tb.reg_read(0x014) == 0x1 and val(dut.irq_o) == 1
        await tb.reg_write(0x014, 0x1)

    await tb.run(tiny_mem, bytes([0xFF]), 1, wr_resp_latency=wr_lat)
    assert await tb.reg_read(0x014) == 0x3 and val(dut.irq_o) == 1
    await tb.reg_write(0x014, 0x3)
    await tb.reg_write(0x010, 0x0)
    assert await tb.reg_read(0x014) == 0x0 and val(dut.irq_o) == 0

    if host in ("axil", "axi", "axi_burst"):
        dut.s_awaddr_i.value, dut.s_awvalid_i.value, dut.s_wvalid_i.value = 0x010, 1, 0
        await tb._handshake(dut.s_awready_o, "AW-only not accepted")
        dut.s_awvalid_i.value = 0
        dut.s_wdata_i.value, dut.s_wstrb_i.value, dut.s_wvalid_i.value = 0x2, 0xF, 1
        await tb._handshake(dut.s_wready_o, "W-after-AW not accepted")
        dut.s_wvalid_i.value = 0
        await tb._write_resp()
        assert await tb.reg_read(0x010) == 0x2

        dut.s_wdata_i.value, dut.s_wstrb_i.value, dut.s_wvalid_i.value, dut.s_awvalid_i.value = 0x0, 0xF, 1, 0
        await tb._handshake(dut.s_wready_o, "W-only not accepted")
        dut.s_wvalid_i.value = 0
        dut.s_awaddr_i.value, dut.s_awvalid_i.value = 0x010, 1
        await tb._handshake(dut.s_awready_o, "AW-after-W not accepted")
        dut.s_awvalid_i.value = 0
        await tb._write_resp()
        assert await tb.reg_read(0x010) == 0x0

    if host == "xif":
        xid = tb.next_id
        tb.next_id = (tb.next_id + 1) % 16
        dut.x_issue_valid_i.value, dut.x_issue_req_instr_i.value = 1, tb._instr(True)
        dut.x_issue_req_id_i.value, dut.x_issue_req_hartid_i.value = xid, 0
        dut.x_register_valid_i.value, dut.x_register_id_i.value, dut.x_register_hartid_i.value = 1, xid, 0
        dut.x_register_rs0_i.value, dut.x_register_rs1_i.value, dut.x_register_rs_valid_i.value = 0x000, 0, 0b11
        dut.x_commit_valid_i.value = 0
        await FallingEdge(dut.clk_i)
        dut.x_issue_valid_i.value = dut.x_register_valid_i.value = 0
        dut.x_commit_valid_i.value, dut.x_commit_id_i.value, dut.x_commit_kill_i.value = 1, (xid - 1) % 16, 0
        await FallingEdge(dut.clk_i)
        dut.x_commit_valid_i.value, dut.x_commit_id_i.value, dut.x_commit_kill_i.value = 1, (xid + 1) % 16, 1
        await FallingEdge(dut.clk_i)
        dut.x_commit_valid_i.value, dut.x_commit_id_i.value, dut.x_commit_kill_i.value = 1, (xid + 2) % 16, 0
        await FallingEdge(dut.clk_i)
        dut.x_commit_valid_i.value = 0
        dut.x_result_ready_i.value = 1
        for _ in range(10):
            await ReadOnly()
            ok, rdata = val(dut.x_result_valid_o), val(dut.x_result_data_o)
            await FallingEdge(dut.clk_i)
            if ok:
                assert rdata == 0x5451_0001
                break
        else:
            raise TimeoutError("no batched-commit CV-X-IF result")
        dut.x_result_ready_i.value = 0


async def check_burst_special(tb, dut, params):
    p = Params(**params, mem_latency=2, wr_resp_latency=2)
    a1, b1 = operands({"m": 1, "n": 16, "k": 8, "a": "s8", "b": "s8", "seed": 101})
    a2, b2 = operands({"m": 1, "n": 16, "k": 8, "a": "s8", "b": "s8", "seed": 202})
    mem1, prog1, layout1 = build_gemm(a1, b1, p)
    mem2, _, _ = build_gemm(a2, b2, p)
    await tb.run(mem1, prog1, 2, wr_resp_latency=2)
    assert np.array_equal(read_c(mem1, layout1), reference(a1, b1))
    mem1[:layout1["c_base"]] = mem2[:layout1["c_base"]]
    await tb.run(mem1, prog1, 2, wr_resp_latency=2)
    assert np.array_equal(read_c(mem1, layout1), reference(a2, b2)), "stale burst line after START"

    padded = prog1 + bytes(tb.prog_bytes - len(prog1))
    for i in range(0, len(padded), 4):
        await tb.reg_write(REG_PROG + i, int.from_bytes(padded[i:i + 4], "little"))
    dut.m_arready_i.value = 0
    await tb.start_cmd()
    for _ in range(20):
        if val(dut.m_arvalid_o):
            break
        await FallingEdge(dut.clk_i)
    assert val(dut.m_arvalid_o), "expected AR valid before ABORT"
    await tb.reg_write(REG_CTRL, 2)
    dut.m_arready_i.value = 1
    await FallingEdge(dut.clk_i)
    st = await tb.reg_read(REG_STATUS)
    assert (st & 0x1) == 0 and (st & 0x10) != 0, f"expected BUSY=0, PENDING=1 after ABORT, got {st:#x}"
    for b in range(4):
        dut.m_rvalid_i.value = 1
        dut.m_rdata_i.value = 0xDEADBEEF
        dut.m_rresp_i.value = 2
        dut.m_rlast_i.value = int(b == 3)
        await FallingEdge(dut.clk_i)
    dut.m_rvalid_i.value = 0
    dut.m_rresp_i.value = 0
    dut.m_rlast_i.value = 1
    st = await tb.reg_read(REG_STATUS)
    assert (st & 0x10) == 0, f"expected PENDING=0 after orphan drain, got {st:#x}"
    assert (st & 0x8) == 0, f"orphan SLVERR must not set BUS_ERR, got {st:#x}"


@cocotb.test()
async def gemm_cases(dut):
    params = dict(json.loads(os.environ["TQ_PARAMS"]))
    perf_off = params.pop("perf_off", False)
    cases = json.loads(os.environ["TQ_CASES"])
    host = os.environ.get("TQ_HOST", "native")
    case_filter = os.environ.get("TQ_CASE")
    burst = host == "axi_burst"
    tb = {"axil": TqTbAxil, "axi": TqTbAxi, "axi_burst": TqTbAxi, "xif": TqTbXif}.get(
        host, TqTb
    )(dut, params["tile"], params.get("prog_bytes", 64))
    await tb.start()
    if not case_filter:
        await check_host_contract(tb, dut, params, host, perf_off)
        if burst:
            await check_burst_special(tb, dut, params)
    failures = []
    for case in cases:
        stall = case.get("stall", 0.0)
        p = Params(**params, mem_latency=case["lat"], gnt_stall=stall, gnt_seed=case["seed"],
                   **({"out_bytes": 1, "out_shift": case["shift"]} if "shift" in case else {}),
                   **({"wr_resp_latency": case["wr_lat"]} if "wr_lat" in case else {}))
        a, b = operands(case)
        mem0, prog, layout, mem_model, s, ev = model_run(a, b, p, case)
        mem = mem0.copy()
        name = f"{case['m']}x{case['n']}x{case['k']} {case['a']}/{case['b']} lat={case['lat']} seed={case['seed']}"
        for key in ("prog", "misalign", "prog_poke", "shift", "dist", "bus_err", "rresp_err"):
            if key in case:
                name += f" {key}={case[key]}"
        if case.get("stall"):
            name += f" stall={case['stall']}"
        if "wr_lat" in case:
            name += f" wr_lat={case['wr_lat']}"
        if case_filter and case_filter not in name:
            continue
        try:
            rec = await tb.run(mem, prog, case["lat"], max_cycles=2 * s.cycles + 200,
                               stall=stall, seed=case["seed"],
                               wr_resp_latency=p.wr_resp_latency if p.wr_ack else None,
                               **({"bus_err": case["bus_err"]} if "bus_err" in case else {}),
                               **({"rresp_err": case["rresp_err"]} if "rresp_err" in case else {}),
                               **({"prog_poke": True} if case.get("prog_poke") else {}))
        except TimeoutError:
            failures.append(f"{name}: hang (model finishes in {s.cycles} cycles)")
            dut._log.error("%s: hang (model %d cycles)", name, s.cycles)
            await tb.reg_write(0x008, 2)                       # ABORT, continue with the next case
            for _ in range(4):
                await tb.reg_write(0xFFC, 0, 0)
            continue
        errs = []
        want = (np.clip(reference(a, b) >> p.out_shift, -128, 127).astype(np.int8)
                if p.out_bytes == 1 else reference(a, b))
        if not s.error and "misalign" not in case and not np.array_equal(read_c(mem, layout), want):
            errs.append("C differs from numpy")
        if not np.array_equal(mem, mem_model):
            errs.append("final memory differs from model")
        if burst:
            b0 = layout["b_base"]
            checks = (("reads A", [a for _, a in rec.reads if a < b0], [a for _, a in ev["rd"] if a < b0]),
                      ("reads B", [a for _, a in rec.reads if a >= b0], [a for _, a in ev["rd"] if a >= b0]),
                      ("writes", [w[1:] for w in rec.writes], [w[1:] for w in ev["wr"]]),
                      ("mac starts", [m for _, m in rec.mac_starts], [m for _, m in ev["start"]]))
        else:
            checks = (("reads", rec.reads, ev["rd"]), ("writes", rec.writes, ev["wr"]),
                      ("mac starts", rec.mac_starts, ev["start"]))
        for label, xs, ys in checks:
            d = first_diff(xs, ys)
            if d:
                errs.append(f"{label} {d}")
        if perf_off:
            expect = {k_: 0 for k_ in rec.perf}
        else:
            expect = {"instret": s.instructions, "beats": s.beats, "reads": s.read_vectors,
                      "writes": s.write_vectors, "cycles": rec.cycles if burst else s.cycles}
        got = dict(rec.perf)
        if not burst and rec.cycles != s.cycles:
            errs.append(f"busy cycles rtl {rec.cycles} vs model {s.cycles}")
        for k_, v in expect.items():
            if got[k_] != v:
                errs.append(f"perf {k_} rtl {got[k_]} vs model {v}")
        got_err = (rec.status >> 8) & 0xF if rec.status & 0x4 else 0
        if got_err != s.error:
            errs.append(f"error code rtl {got_err} vs model {s.error} (STATUS {rec.status:#x})")
        want_bus_err = ("bus_err" in case) or ("rresp_err" in case)
        if bool(rec.status & 0x8) != want_bus_err:
            errs.append(f"STATUS.BUS_ERR {bool(rec.status & 0x8)}, expected {want_bus_err}")
        if errs:
            failures.append(f"{name}: " + "; ".join(errs))
            dut._log.error("%s: %s", name, "; ".join(errs))
        else:
            dut._log.info("%s: ok, %d cycles", name, rec.cycles)
    assert not failures, f"{len(failures)}/{len(cases)} cases failed:\n" + "\n".join(failures[:20])
