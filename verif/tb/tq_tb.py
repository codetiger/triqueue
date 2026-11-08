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
PROG_BYTES = 64                                      # tq_top default in every configuration


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
                  stall: float = 0.0, seed: int = 0, wr_resp_latency=None, prog_poke=False) -> RunRecord:
        """Load `prog`, start, serve memory from `mem` (modified in place) until the core is idle.

        stall > 0 withholds each read and write grant with that probability (OBI back-pressure),
        drawn exactly as the model's GrantSource(stall, seed) draws them. wr_resp_latency (for a
        core built with WR_ACK) answers each granted write with a wr_done_i pulse that many
        cycles after its grant, like the model's wr_resp_latency. prog_poke writes the tile loop's body
        (PROG bytes 44-47) in the run's fourth cycle, which the core must ignore while busy.
        After the core stops, reads still in flight are answered (as real memory would; after an
        error stop the next START waits for them).
        """
        gnt = GrantSource(stall, seed)
        d, W, H = self.dut, self.W, self.T // 2
        assert mem_latency >= 1
        sram_lat = mem_latency - 1
        padded = prog + bytes(PROG_BYTES - len(prog))          # NOP-pad the whole buffer (as the model)
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
            if prog_poke:                                      # a host write to PROG while busy
                poke = busy and cyc == 3
                d.reg_req_i.value, d.reg_we_i.value = int(poke), int(poke)
                # bytes 44-47: the tile loop's body (STREAM_A STREAM_B LOOP_K VOUTER_MAC), which
                # the front end fetches again for every tile
                d.reg_addr_i.value, d.reg_be_i.value, d.reg_wdata_i.value = REG_PROG + 44, 0xF, 0xFFFFFFFF
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
        if prog_poke:                                          # native register port only
            d.reg_req_i.value = d.reg_we_i.value = 0
        while pending:                                         # reads in flight when the core stopped
            due, word = pending[0]
            d.rd_rvalid_i.value = int(due == absc)
            if due == absc:
                d.rd_rdata_i.value = word
                pending.pop(0)
            await FallingEdge(d.clk_i)
            absc += 1
        while wr_due:                                          # write responses (error stop only)
            d.wr_done_i.value = int(wr_due[0] == absc)
            if wr_due[0] == absc:
                wr_due.pop(0)
            await FallingEdge(d.clk_i)
            absc += 1
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
        padded = prog + bytes(PROG_BYTES - len(prog))          # NOP-pad the whole buffer (as the model)
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
        while r_due or b_due:                                  # in flight when the core stopped (error)
            d.m_rvalid_i.value = int(bool(r_due) and r_due[0][0] == absc)
            if r_due and r_due[0][0] == absc:
                d.m_rdata_i.value = r_due.pop(0)[1]
            d.m_bvalid_i.value = int(bool(b_due) and b_due[0] == absc)
            if b_due and b_due[0] == absc:
                b_due.pop(0)
            await FallingEdge(d.clk_i)
            absc += 1
        d.m_rvalid_i.value = d.m_bvalid_i.value = 0
        d.m_arready_i.value = d.m_awready_i.value = d.m_wready_i.value = 1
        rec.cycles = cyc + 1
        rec.status = await self.reg_read(REG_STATUS)
        for k, a in REG_PERF.items():
            rec.perf[k] = await self.reg_read(a)
        return rec


class TqTbXif(TqTb):
    """Drives tq_top_xif: registers reached with tq.wr / tq.rd through CV-X-IF, the way a CPU
    issues them. Register (operand) and commit transactions come a seeded random 0-3 cycles
    after the issue, in either order, and the result is taken a random 0-2 cycles after it is
    offered. Before every register write a speculative copy is issued and killed, and an
    instruction of another coprocessor is offered; neither may reach the core. Checks the
    rules a CPU relies on: one result per committed instruction, held until taken, with the
    issue's id and rd."""

    OPCODE = 0b0001011                                 # custom-0, funct7 = 0

    def __init__(self, dut, tile: int):
        super().__init__(dut, tile)
        self.rng = np.random.default_rng(7)
        self.next_id = 0

    def core_handle(self):
        return self.dut.u_tq.u_core

    def host_inputs(self):
        d = self.dut
        return (d.x_issue_valid_i, d.x_issue_req_instr_i, d.x_issue_req_hartid_i, d.x_issue_req_id_i,
                d.x_register_valid_i, d.x_register_hartid_i, d.x_register_id_i, d.x_register_rs0_i,
                d.x_register_rs1_i, d.x_register_rs_valid_i, d.x_commit_valid_i, d.x_commit_hartid_i,
                d.x_commit_id_i, d.x_commit_kill_i, d.x_result_ready_i)

    def _instr(self, read: bool, opcode=None) -> int:
        funct3, rd = (1, 10) if read else (0, 0)
        return (6 << 20) | (5 << 15) | (funct3 << 12) | (rd << 7) | (self.OPCODE if opcode is None else opcode)

    async def _op(self, read: bool, addr: int, data: int = 0, kill: bool = False, opcode=None,
                  at_access=False):
        """One instruction from issue to result. Returns the result data; with at_access, returns
        at the falling edge after the cycle the register port request was granted (the result
        is then taken later, x_result_ready_i stays 1)."""
        d, xif = self.dut, self.dut.u_xif
        ours = opcode is None
        xid = self.next_id
        self.next_id = (self.next_id + 1) % 16
        reg_at, commit_at = (int(x) for x in self.rng.integers(0, 4, 2))
        d.x_issue_valid_i.value, d.x_issue_req_instr_i.value = 1, self._instr(read, opcode)
        d.x_issue_req_id_i.value, d.x_issue_req_hartid_i.value = xid, 0
        d.x_result_ready_i.value = 0
        reg_done = commit_done = False
        t = 0
        while not (reg_done and commit_done):
            if t > 0:
                d.x_issue_valid_i.value = 0
            d.x_register_valid_i.value = int(t >= reg_at and not reg_done)
            d.x_register_id_i.value, d.x_register_hartid_i.value = xid, 0
            d.x_register_rs0_i.value, d.x_register_rs1_i.value = addr, data
            d.x_register_rs_valid_i.value = 0b11
            d.x_commit_valid_i.value = int(t == commit_at)
            d.x_commit_id_i.value, d.x_commit_hartid_i.value, d.x_commit_kill_i.value = xid, 0, int(kill)
            await ReadOnly()
            if t == 0:
                assert val(d.x_issue_ready_o), "CV-X-IF issue not ready"
                assert val(d.x_issue_resp_accept_o) == int(ours), "wrong accept"
                assert val(d.x_issue_resp_writeback_o) == int(ours and read)
            if t >= reg_at and not reg_done and val(d.x_register_ready_o):
                reg_done = True
            commit_done = commit_done or t == commit_at
            access = val(xif.reg_req_o) and val(xif.reg_gnt_i)
            assert not (access and (kill or not ours)), "register access by a killed or foreign instruction"
            await FallingEdge(d.clk_i)
            t += 1
            if access and at_access:
                break
        d.x_issue_valid_i.value = d.x_register_valid_i.value = d.x_commit_valid_i.value = 0
        if at_access:
            d.x_result_ready_i.value = 1
            return 0
        if kill or not ours:
            for _ in range(4):                          # nothing happens for this instruction
                await ReadOnly()
                assert not val(d.x_result_valid_o), "result for a killed or foreign instruction"
                assert not val(xif.reg_req_o)
                await FallingEdge(d.clk_i)
            return None
        wait = int(self.rng.integers(0, 3))
        for n in range(40):
            d.x_result_ready_i.value = int(n >= wait)
            await ReadOnly()
            if val(d.x_result_valid_o):
                assert val(d.x_result_id_o) == xid and val(d.x_result_we_o) == int(read)
                assert not read or val(d.x_result_rd_o) == 10
                if n >= wait:
                    out = val(d.x_result_data_o)
                    await FallingEdge(d.clk_i)
                    d.x_result_ready_i.value = 0
                    await ReadOnly()
                    assert not val(d.x_result_valid_o), "second result"
                    await FallingEdge(d.clk_i)
                    return out
            await FallingEdge(d.clk_i)
        raise TimeoutError("no CV-X-IF result")

    async def _noise(self, addr: int, data: int):
        await self._op(False, addr, data, kill=True)              # speculative, killed
        await self._op(False, addr, data, opcode=0b0101011)       # custom-1: another coprocessor's

    async def reg_write(self, addr: int, data: int, be: int = 0xF):
        assert be == 0xF, "tq.wr writes whole registers"
        await self._noise(addr, data)
        await self._op(False, addr, data)

    async def start_cmd(self):
        await self._noise(REG_CTRL, 1)
        assert not val(self.core.busy_o), "killed START reached the core"
        await self._op(False, REG_CTRL, 1, at_access=True)

    async def reg_read(self, addr: int) -> int:
        return await self._op(True, addr)
