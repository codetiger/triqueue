# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""cocotb driver for tq_top: register-port host, SRAM model, and transaction monitors.

Timing contract with the cycle model (docs/golden-vectors.md):
  * model cycle 0 is the first cycle the core is busy;
  * the SRAM returns read data `mem_latency - 1` cycles after the request, so the data is in the
    operand FIFO `mem_latency` cycles after the request, as in the model;
  * grants come from the model's GrantSource, one draw pair per busy cycle (always 1 otherwise),
    so stalled runs are cycle-exact too.
Everything is sampled at the falling edge, when the cycle's combinational values are stable.
"""
from dataclasses import dataclass, field

import numpy as np
from tri_queue.core import GrantSource
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

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
    """Drives tq_top through its native register port."""

    def __init__(self, dut, tile: int):
        self.dut = dut
        self.T = tile
        self.W = 2 * tile
        self.core = self.core_handle()

    def core_handle(self):
        return self.dut.u_core

    def host_inputs(self):
        d = self.dut
        return (d.reg_req_i, d.reg_we_i, d.reg_addr_i, d.reg_be_i, d.reg_wdata_i)

    async def start(self):
        d = self.dut
        Clock(d.clk_i, 10, unit="ns").start()
        for s in (*self.host_inputs(), d.rd_rvalid_i, d.rd_rdata_i, d.wr_done_i, d.bus_err_i):
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

    async def start_cmd(self):
        """Write CTRL.START and return at the falling edge after the core took it."""
        await self.reg_write(REG_CTRL, 1)

    async def reg_read(self, addr: int) -> int:
        d = self.dut
        d.reg_req_i.value, d.reg_we_i.value, d.reg_addr_i.value = 1, 0, addr
        await FallingEdge(d.clk_i)
        d.reg_req_i.value = 0
        assert val(d.reg_rvalid_o) == 1
        return val(d.reg_rdata_o)

    async def run(self, mem: np.ndarray, prog: bytes, mem_latency: int, max_cycles: int = 2_000_000,
                  stall: float = 0.0, seed: int = 0, wr_resp_latency=None) -> RunRecord:
        """Load `prog`, start, serve memory from `mem` (modified in place) until the core is idle.

        stall > 0 withholds each read and write grant with that probability (OBI back-pressure),
        drawn exactly as the model's GrantSource(stall, seed) draws them. wr_resp_latency (for a
        core built with WR_ACK) answers each granted write with a wr_done_i pulse that many
        cycles after its grant, like the model's wr_resp_latency.
        """
        gnt = GrantSource(stall, seed)
        d, W, H = self.dut, self.W, self.T // 2
        assert mem_latency >= 1
        sram_lat = mem_latency - 1
        padded = prog + bytes(-len(prog) % 4)
        for i in range(0, len(padded), 4):
            await self.reg_write(REG_PROG + i, int.from_bytes(padded[i:i + 4], "little"))
        await self.start_cmd()                                 # START: the core is busy from the next cycle

        core = self.core
        rec = RunRecord()
        pending = []                                           # (due absolute cycle, data int)
        absc, cyc, started = 0, -1, False
        held_rd = held_wr = None                               # OBI: ungranted request to repeat
        wr_due = []                                            # absolute cycles of write responses
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
            rd_gnt, wr_gnt = (int(g) for g in gnt.next()) if busy else (1, 1)
            d.rd_gnt_i.value = rd_gnt
            d.wr_gnt_i.value = wr_gnt
            # OBI request stability: an ungranted request is repeated unchanged
            rd_now = (val(d.rd_addr_o),) if val(d.rd_req_o) else None
            wr_now = (val(d.wr_addr_o), val(d.wr_be_o), val(d.wr_wdata_o)) if val(d.wr_req_o) else None
            assert held_rd is None or rd_now == held_rd, f"cycle {cyc}: read request changed before gnt"
            assert held_wr is None or wr_now == held_wr, f"cycle {cyc}: write request changed before gnt"
            held_rd = rd_now if rd_now and not rd_gnt else None
            held_wr = wr_now if wr_now and not wr_gnt else None
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
                if wr_resp_latency is not None:
                    wr_due.append(absc + wr_resp_latency)
            # write responses for this cycle (one grant per cycle, so at most one is due)
            done = bool(wr_due) and wr_due[0] == absc
            if done:
                wr_due.pop(0)
            d.wr_done_i.value = int(done)
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
        assert not wr_due, "core finished with write responses outstanding"
        d.rd_rvalid_i.value = 0
        d.wr_done_i.value = 0
        d.rd_gnt_i.value = 1
        d.wr_gnt_i.value = 1
        rec.cycles = cyc + 1
        rec.status = await self.reg_read(REG_STATUS)
        for k, a in REG_PERF.items():
            rec.perf[k] = await self.reg_read(a)
        return rec


class TqTbAxil(TqTb):
    """Drives tq_top_axil: the same runs, with the registers reached through AXI4-Lite. The
    AXI side is checked for the protocol rules a master relies on (VALID held until READY,
    exactly one response per request)."""

    def core_handle(self):
        return self.dut.u_tq.u_core

    def host_inputs(self):
        d = self.dut
        return (d.s_awaddr_i, d.s_awprot_i, d.s_awvalid_i, d.s_wdata_i, d.s_wstrb_i, d.s_wvalid_i,
                d.s_bready_i, d.s_araddr_i, d.s_arprot_i, d.s_arvalid_i, d.s_rready_i)

    async def _handshake(self, sig, what):
        """Called at a falling edge with this side's VALID/READY driven: wait until `sig` (the other
        side) is 1 in a cycle, then return at the falling edge after that cycle's rising edge."""
        for _ in range(20):
            await ReadOnly()
            ok = val(sig)
            await FallingEdge(self.dut.clk_i)
            if ok:
                return
        raise TimeoutError(what)

    async def _write_addr(self, addr: int, data: int, be: int):
        """Present AW and W until accepted; return at the falling edge after the handshake."""
        d = self.dut
        d.s_awaddr_i.value, d.s_awvalid_i.value = addr, 1
        d.s_wdata_i.value, d.s_wstrb_i.value, d.s_wvalid_i.value = data, be, 1
        await self._handshake(d.s_awready_o, "AXI write not accepted")
        d.s_awvalid_i.value, d.s_wvalid_i.value = 0, 0

    async def _write_resp(self):
        d = self.dut
        d.s_bready_i.value = 1
        assert val(d.s_bresp_o) == 0
        await self._handshake(d.s_bvalid_o, "no AXI write response")
        d.s_bready_i.value = 0
        assert not val(d.s_bvalid_o), "second write response"

    async def reg_write(self, addr: int, data: int, be: int = 0xF):
        await self._write_addr(addr, data, be)
        await self._write_resp()

    async def start_cmd(self):
        """START reaches the core in the AW/W handshake cycle; the B response is taken while
        the core runs (BREADY stays high), so cycle counting starts as with the native port."""
        await self._write_addr(REG_CTRL, 1, 0xF)
        self.dut.s_bready_i.value = 1

    async def reg_read(self, addr: int) -> int:
        d = self.dut
        d.s_bready_i.value = 0
        d.s_araddr_i.value, d.s_arvalid_i.value, d.s_rready_i.value = addr, 1, 1
        await self._handshake(d.s_arready_o, "AXI read not accepted")
        d.s_arvalid_i.value = 0
        for _ in range(20):                                     # R: data is taken in the handshake cycle
            await ReadOnly()
            ok, data, resp = val(d.s_rvalid_o), val(d.s_rdata_o), val(d.s_rresp_o)
            await FallingEdge(d.clk_i)
            if ok:
                break
        else:
            raise TimeoutError("no AXI read response")
        assert resp == 0
        d.s_rready_i.value = 0
        assert not val(d.s_rvalid_o), "second read response"
        return data


class TqTbAxi(TqTbAxil):
    """Drives tq_top_axi: registers over AXI4-Lite, memory as an AXI4 slave. AR / AW / W ready
    come from the model's GrantSource draws (as the native port's gnt), R returns mem_latency - 1
    cycles after AR, and B returns wr_resp_latency (>= 1) cycles after the write, so the run is
    cycle-exact with the model (wr_ack). Checks the AXI rules a slave relies on: VALID and its
    payload held until READY, single-beat INCR of one port word, AW and W of a write together."""

    def core_handle(self):
        return self.dut.u_tq.u_tq.u_core

    def host_inputs(self):
        d = self.dut
        return (*super().host_inputs(), d.m_rvalid_i, d.m_rdata_i, d.m_rresp_i, d.m_rlast_i,
                d.m_bvalid_i, d.m_bresp_i)

    async def start(self):
        d = self.dut
        Clock(d.clk_i, 10, unit="ns").start()
        for s in self.host_inputs():
            s.value = 0
        d.m_rlast_i.value = 1
        d.m_arready_i.value = d.m_awready_i.value = d.m_wready_i.value = 1
        d.rst_ni.value = 0
        for _ in range(3):
            await RisingEdge(d.clk_i)
        await FallingEdge(d.clk_i)
        d.rst_ni.value = 1

    async def run(self, mem, prog, mem_latency, max_cycles=2_000_000, stall=0.0, seed=0, wr_resp_latency=None,
                  bus_err=None):
        """bus_err = i: the i-th write's B response is SLVERR (the data is still written)."""
        gnt = GrantSource(stall, seed)
        d, W, H = self.dut, self.W, self.T // 2
        assert mem_latency >= 1 and wr_resp_latency is not None and wr_resp_latency >= 1
        sram_lat = mem_latency - 1
        size = W.bit_length() - 1
        padded = prog + bytes(-len(prog) % 4)
        for i in range(0, len(padded), 4):
            await self.reg_write(REG_PROG + i, int.from_bytes(padded[i:i + 4], "little"))
        await self.start_cmd()

        core = self.core
        rec = RunRecord()
        r_due, b_due = [], []
        n_b = 0
        absc, cyc, started = 0, -1, False
        held_ar = held_aw = held_w = None
        while True:
            busy = val(core.busy_o)
            if busy:
                started = True
                cyc += 1
            elif started:
                break
            if cyc > max_cycles:
                raise TimeoutError("core did not finish")
            rd_gnt, wr_gnt = (int(g) for g in gnt.next()) if busy else (1, 1)
            d.m_arready_i.value = rd_gnt
            d.m_awready_i.value = d.m_wready_i.value = wr_gnt
            arv, awv, wv = val(d.m_arvalid_o), val(d.m_awvalid_o), val(d.m_wvalid_o)
            ar = (val(d.m_araddr_o), val(d.m_arlen_o), val(d.m_arsize_o), val(d.m_arburst_o)) if arv else None
            aw = (val(d.m_awaddr_o), val(d.m_awlen_o), val(d.m_awsize_o), val(d.m_awburst_o)) if awv else None
            w = (val(d.m_wdata_o), val(d.m_wstrb_o), val(d.m_wlast_o)) if wv else None
            for name, now, held in (("AR", ar, held_ar), ("AW", aw, held_aw), ("W", w, held_w)):
                assert held is None or now == held, f"cycle {cyc}: {name} changed or dropped before READY"
            for x in (ar, aw):
                assert x is None or (x[1], x[2], x[3]) == (0, size, 1), f"cycle {cyc}: not a 1-beat INCR of W bytes"
            assert w is None or w[2] == 1
            held_ar = ar if ar and not rd_gnt else None
            held_aw = aw if aw and not wr_gnt else None
            held_w = w if w and not wr_gnt else None
            if ar and rd_gnt:
                addr = ar[0]
                r_due.append((absc + sram_lat, int.from_bytes(mem[addr:addr + W].tobytes(), "little")))
                rec.reads.append((cyc, addr))
            if (aw and wr_gnt) or (w and wr_gnt):
                assert aw and w, f"cycle {cyc}: AW and W of a write accepted in different cycles"
                addr, (data, be, _) = aw[0], w
                raw = data.to_bytes(W, "little")
                n = 0
                for j in range(W):
                    if be >> j & 1:
                        mem[addr + j] = raw[j]
                        n = j + 1
                assert be == (1 << n) - 1, f"non-contiguous byte enables {be:#x}"
                rec.writes.append((cyc, addr, n, raw[:n]))
                b_due.append(absc + wr_resp_latency)
            if val(core.mac_start):
                rf, cf = val(core.beat_rf), val(core.beat_cf)
                r, c = (self.T if rf else H), (self.T if cf else H)
                rec.mac_starts.append((cyc, f"{r}x{c}x{self.T * self.T // (r * c)}"))
            if r_due and r_due[0][0] == absc:
                d.m_rvalid_i.value = 1
                d.m_rdata_i.value = r_due.pop(0)[1]
            else:
                d.m_rvalid_i.value = 0
            d.m_bvalid_i.value = int(bool(b_due) and b_due[0] == absc)
            d.m_bresp_i.value = 0
            if b_due and b_due[0] == absc:
                b_due.pop(0)
                if bus_err is not None and n_b == bus_err:
                    d.m_bresp_i.value = 2                   # SLVERR
                n_b += 1
            assert val(d.m_rready_o) and val(d.m_bready_o)
            await FallingEdge(d.clk_i)
            absc += 1
        assert not b_due and not r_due, "core finished with AXI responses outstanding"
        d.m_rvalid_i.value = d.m_bvalid_i.value = 0
        d.m_arready_i.value = d.m_awready_i.value = d.m_wready_i.value = 1
        rec.cycles = cyc + 1
        rec.status = await self.reg_read(REG_STATUS)
        for k, a in REG_PERF.items():
            rec.perf[k] = await self.reg_read(a)
        return rec
