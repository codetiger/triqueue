# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""cocotb driver for tq_top: register-port host, SRAM model, and transaction monitors.

Timing contract with the cycle model (docs/golden-vectors.md):
  * model cycle 0 is the first cycle the core is busy;
  * the SRAM always grants and returns read data `mem_latency - 1` cycles after the request,
    so the data is in the operand FIFO `mem_latency` cycles after the request, as in the model.
Everything is sampled at the falling edge, when the cycle's combinational values are stable.
"""
from dataclasses import dataclass, field

import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

REG_CTRL, REG_STATUS = 0x008, 0x00C
REG_PERF = {"cycles": 0x020, "instret": 0x024, "beats": 0x028, "reads": 0x02C, "writes": 0x030}
REG_PROG = 0x100


def val(sig) -> int:
    return int(sig.value)


@dataclass
class RunRecord:
    cycles: int = 0
    reads: list = field(default_factory=list)       # (cycle, addr)
    writes: list = field(default_factory=list)      # (cycle, addr, n, data bytes)
    mac_starts: list = field(default_factory=list)  # (cycle, "r x c x d")
    perf: dict = field(default_factory=dict)
    status: int = 0


class TqTb:
    def __init__(self, dut, tile: int):
        self.dut = dut
        self.T = tile
        self.W = 2 * tile

    async def start(self):
        d = self.dut
        Clock(d.clk_i, 10, unit="ns").start()
        for s in (d.reg_req_i, d.reg_we_i, d.reg_addr_i, d.reg_be_i, d.reg_wdata_i, d.rd_rvalid_i, d.rd_rdata_i):
            s.value = 0
        d.rd_gnt_i.value = 1
        d.wr_gnt_i.value = 1
        d.rst_ni.value = 0
        for _ in range(3):
            await RisingEdge(d.clk_i)
        await FallingEdge(d.clk_i)
        d.rst_ni.value = 1

    async def reg_write(self, addr: int, data: int, be: int = 0xF):
        d = self.dut
        d.reg_req_i.value, d.reg_we_i.value, d.reg_addr_i.value = 1, 1, addr
        d.reg_be_i.value, d.reg_wdata_i.value = be, data
        await FallingEdge(d.clk_i)
        d.reg_req_i.value, d.reg_we_i.value = 0, 0

    async def reg_read(self, addr: int) -> int:
        d = self.dut
        d.reg_req_i.value, d.reg_we_i.value, d.reg_addr_i.value = 1, 0, addr
        await FallingEdge(d.clk_i)
        d.reg_req_i.value = 0
        assert val(d.reg_rvalid_o) == 1
        return val(d.reg_rdata_o)

    async def run(self, mem: np.ndarray, prog: bytes, mem_latency: int, max_cycles: int = 2_000_000,
                  stall: float = 0.0, seed: int = 0) -> RunRecord:
        """Load `prog`, start, serve memory from `mem` (modified in place) until the core is idle.

        stall > 0 withholds each read and write grant with that probability (OBI back-pressure).
        """
        rng = np.random.default_rng(seed)
        d, W, H = self.dut, self.W, self.T // 2
        assert mem_latency >= 1
        sram_lat = mem_latency - 1
        padded = prog + bytes(-len(prog) % 4)
        for i in range(0, len(padded), 4):
            await self.reg_write(REG_PROG + i, int.from_bytes(padded[i:i + 4], "little"))
        await self.reg_write(REG_CTRL, 1)                      # START: the core is busy from the next cycle

        core = d.u_core
        rec = RunRecord()
        pending = []                                           # (due absolute cycle, data int)
        absc, cyc, started = 0, -1, False
        while True:
            busy = val(core.busy_o)
            if busy:
                started = True
                cyc += 1
            elif started:
                break
            if cyc > max_cycles:
                raise TimeoutError("core did not finish")
            # grants for this cycle (driven before the rising edge that ends it)
            rd_gnt = int(rng.random() >= stall)
            wr_gnt = int(rng.random() >= stall)
            d.rd_gnt_i.value = rd_gnt
            d.wr_gnt_i.value = wr_gnt
            # read requests
            if val(d.rd_req_o) and rd_gnt:
                addr = val(d.rd_addr_o)
                word = int.from_bytes(mem[addr:addr + W].tobytes(), "little")
                pending.append((absc + sram_lat, word))
                rec.reads.append((cyc, addr))
            # write requests
            if val(d.wr_req_o) and wr_gnt:
                addr, be, data = val(d.wr_addr_o), val(d.wr_be_o), val(d.wr_wdata_o)
                raw = data.to_bytes(W, "little")
                n = 0
                for j in range(W):
                    if be >> j & 1:
                        mem[addr + j] = raw[j]
                        n = j + 1
                assert be == (1 << n) - 1, f"non-contiguous byte enables {be:#x}"
                rec.writes.append((cyc, addr, n, raw[:n]))
            # MAC starts (internal, for the cycle-level comparison)
            if val(core.mac_start):
                rf, cf = val(core.beat_rf), val(core.beat_cf)    # packed struct fields are not visible
                r, c = (self.T if rf else H), (self.T if cf else H)
                rec.mac_starts.append((cyc, f"{r}x{c}x{self.T * self.T // (r * c)}"))
            # read responses for this cycle
            if pending and pending[0][0] == absc:
                d.rd_rvalid_i.value = 1
                d.rd_rdata_i.value = pending.pop(0)[1]
            else:
                d.rd_rvalid_i.value = 0
            assert not pending or pending[0][0] > absc
            await FallingEdge(d.clk_i)
            absc += 1
        d.rd_rvalid_i.value = 0
        d.rd_gnt_i.value = 1
        d.wr_gnt_i.value = 1
        rec.cycles = cyc + 1
        rec.status = await self.reg_read(REG_STATUS)
        for k, a in REG_PERF.items():
            rec.perf[k] = await self.reg_read(a)
        return rec
