# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Cycle-level model of the Tri-Queue int8 matrix core (spec rev 5, docs/specification.md).

The simulator executes the real instruction bytes. The arithmetic is done the way the hardware
does it: 9x9 multiplies, per-quadrant operand selection for the k-depth arity modes, and
carry-save (sum, carry) accumulation in 32 bits. C therefore comes out of the same model that
produces the cycle count.

Size parameters: tile T (T x T output tile, T*T multipliers; the spec's design point is T = 8) and
port width W bytes (default 2T, which balances the read port against the multiplier array).

Per-cycle order: memory responses land -> MAC unit -> memory port(s) -> front end.

Modelling assumptions (stated in model/README.md):
  * Front end decodes 1 instruction/cycle; loops are zero-overhead (loop buffer).
  * CFG waits until the MAC and stream command queues are empty so queued work always sees the
    configuration under which it was issued.
  * ZERO_C and SWAP_C are bank-pointer operations that take no MAC cycle. Per cycle the MAC unit
    consumes at most one of them, followed by at most one VOUTER_MAC.
  * Loop bodies contain only 1-byte instructions (a CFG reached inside a loop is ERR_CFG_LOOP).
  * Errors (spec 6.4) stop the core at the end of the cycle that finds them, with the RTL's
    error code (Stats.error): the front end checks the instruction it executes (opcode and
    reserved argument bits, CFG register and value ranges, CFG in a loop, loop depth / non-zero
    body / inner loop within outer / a second LOOP_T, running past the PROG_BYTES program
    buffer, which is NOP-padded), the stream engine checks a panel base when it loads it,
    the MAC unit checks a tile's C address and C_LD when its drain starts (ERR_ALIGN), and FENCE
    or a full MAC queue checks for leftover or starved operands (ERR_OPERAND).
  * VOUTER_MAC needs all of its operand bytes present when it starts, and pops them at the start.
  * SRAM reads are fully pipelined with a fixed latency, one W-byte read per cycle.
  * Port grants come from GrantSource: always granted by default, or seeded random OBI `gnt`
    back-pressure (gnt_stall, gnt_seed) that the RTL testbench reproduces draw for draw.
    A request that is not granted is held unchanged until it is (OBI/AXI request stability):
    no re-arbitration of the read stream, and on a shared port no read/write swap.
  * No SRAM bank conflicts.
  * A panel buffer (panel_bytes > 0, spec 10.9): an A panel no larger than the buffer is kept as
    it is read; a later STREAM_A of the same panel (the next tiles of the tile row) is replayed
    from the buffer, one W-byte vector per cycle without using the memory port. QA receives its
    vectors in order: a replay waits until no A read is in flight, and an A read waits until no
    replayed vector is.
  * Output requantization (MODE_OUT_INT8, spec 6.1, 10.1): C as int8 =
    clip(acc >> out_shift, -128, 127), T values per drain write by default (drain_lanes overrides).
  * A chaining output layout (chain_c, int8 output, model study, spec 10.5): C is written in the
    next layer's A-panel order, column-major with w = panel_width(rows) bytes per column, so it is
    already packed as the next layer's A. A tile is contiguous; padding rows and columns are zero
    in the bank. chain_snapshot models a wide bank read (first word one cycle after the bank is
    stable); the default drain reads one row per cycle and needs T - 1 cycles.
  * Write completion: by default a write is complete when granted (SRAM). With wr_ack (an
    interconnect such as AXI, where a write completes at its B response) each granted write
    completes wr_resp_latency cycles later, FENCE waits until none is outstanding, and no new
    write is requested while wr_out_max are outstanding.
"""
import math
from collections import deque, defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import isa
from .packing import kp, auto_shape, panel_width

# Max products an accumulate cell in each (T/2 x T/2) quadrant may sum per cycle (spec section 4.1).
HW_CELL_ARITY = {(0, 0): 4, (0, 1): 2, (1, 0): 2, (1, 1): 1}


class _AutoPort(int):
    """Marker int subclass so dataclasses.replace(Params(), tile=16) recomputes port_bytes = 2*tile."""


@dataclass
class Params:
    tile: int = 8                # T: output tile is T x T, the array has T*T multipliers
    port_bytes: int = 0          # W: read port (and write port) width in bytes; 0 -> 2*T (balanced)
    mem_latency: int = 2         # cycles from read issue to data in FIFO
    fifo_depth: int = 8          # QA / QB depth, in W-byte vectors
    mac_queue_depth: int = 4     # VOUTER_MAC / SWAP_C / ZERO_C command queue
    stream_queue_depth: int = 2  # STREAM_A / STREAM_B command queues
    mac_pipe: int = 2            # MUL + ACC stages: a beat's result is in SC this many cycles later
    cpa_latency: int = 1         # drain: carry-propagate before the first write
    shared_port: bool = False    # True: drain writes share the single read port
    banks: int = 2               # 1: ablation - compute must wait while the only SC bank drains
    gnt_stall: float = 0.0       # probability that a port's gnt is withheld in a cycle (OBI back-pressure)
    gnt_seed: int = 0            # seed of the grant generator (see GrantSource)
    wr_ack: bool = False         # writes complete at a response (wr_done), not at the grant
    wr_resp_latency: int = 0     # with wr_ack: cycles from a write's grant to its response (0: same cycle)
    wr_out_max: int = 15         # with wr_ack: most writes granted and not yet complete
    prog_bytes: int = 64         # program buffer (PROG_BYTES); the program is NOP-padded to it
    panel_bytes: int = 0         # A panel buffer (spec 10.9): 0 = none; else replays an A panel that fits
    panel_latency: int = 2       # panel buffer read -> data in QA (a 1-cycle buffer + the FIFO write)
    # Output requantization (spec 6.1, 10.1): out_bytes = 1 sets MODE_OUT_INT8 with out_shift in
    # build_gemm, writing C as int8 = clip(acc >> out_shift, -128, 127); drain_lanes overrides the
    # values the drain reads per cycle (0: RTL default, W/4 for int32 and T for int8).
    out_bytes: int = 4
    out_shift: int = 8
    drain_lanes: int = 0
    # Chaining output layout (model study; not in the RTL): int8 C only. The drain writes each
    # tile as contiguous bytes in the next layer's A-panel order (column-major, W/w columns per
    # word), so C is already packed as the next layer's A. Needs out_bytes = 1. The default drain
    # reads one bank row per cycle, so it needs T-1 cycles before its first word; chain_snapshot
    # models a wide bank read that forms the first word one cycle after the bank is stable.
    chain_c: bool = False
    chain_snapshot: bool = False

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "tile" and isinstance(getattr(self, "port_bytes", 0), _AutoPort):
            super().__setattr__("port_bytes", _AutoPort(2 * value))

    def __post_init__(self):
        if not self.port_bytes or isinstance(self.port_bytes, _AutoPort):
            super().__setattr__("port_bytes", _AutoPort(2 * self.tile))
        self.validate()

    def validate(self):
        if self.tile < 4 or (self.tile & (self.tile - 1)) != 0 or self.tile > 128:
            raise ValueError("tile must be a power of two in [4, 128]")
        if not self.port_bytes or isinstance(self.port_bytes, _AutoPort):
            super().__setattr__("port_bytes", _AutoPort(2 * self.tile))
        if self.port_bytes < 4 or self.port_bytes % 4 != 0:
            raise ValueError("port must hold whole int32 lanes (multiple of 4 >= 4)")
        if self.mem_latency < 1:
            raise ValueError("mem_latency must be >= 1")
        if self.fifo_depth < 2 or self.fifo_depth > 255:
            raise ValueError("fifo_depth must be in [2, 255]")
        if self.mac_queue_depth < 1 or self.mac_queue_depth > 15:
            raise ValueError("mac_queue_depth must be in [1, 15]")
        if self.stream_queue_depth < 1:
            raise ValueError("stream_queue_depth must be >= 1")
        if self.mac_pipe not in (1, 2):
            raise ValueError("mac_pipe must be 1 or 2")
        if self.cpa_latency < 0:
            raise ValueError("cpa_latency must be >= 0")
        if self.banks not in (1, 2):
            raise ValueError("banks must be 1 or 2")
        if not (0.0 <= self.gnt_stall < 1.0):
            raise ValueError("gnt_stall must be in [0, 1)")
        if self.wr_resp_latency < 0 or self.wr_out_max < 1:
            raise ValueError("wr_resp_latency must be >= 0 and wr_out_max >= 1")
        if self.prog_bytes < 50 or self.prog_bytes > 1020 or self.prog_bytes % 4 != 0:
            raise ValueError("prog_bytes must be a multiple of 4 in [50, 1020]")
        if self.panel_bytes < 0 or self.panel_bytes % (2 * self.tile) != 0:
            raise ValueError("panel_bytes must be >= 0 and a multiple of 2*tile")
        if self.panel_latency < 1:
            raise ValueError("panel_latency must be >= 1")
        if self.out_bytes not in (1, 4) or not (0 <= self.out_shift < 32) or self.drain_lanes < 0:
            raise ValueError("out_bytes must be 1 or 4, 0 <= out_shift < 32, drain_lanes >= 0")
        if self.chain_c and self.out_bytes != 1:
            raise ValueError("chain_c needs int8 output")
        if self.chain_snapshot and not self.chain_c:
            raise ValueError("chain_snapshot needs chain_c")


@dataclass
class Stats:
    peak_macs: int = 64          # MACs per cycle of this configuration (T*T)
    cycles: int = 0
    instructions: int = 0
    program_bytes: int = 0
    beats: int = 0
    mac_ops: dict = field(default_factory=lambda: defaultdict(int))     # shape -> VOUTER count
    mac_state: dict = field(default_factory=lambda: defaultdict(int))   # per-cycle MAC state
    front_stall: dict = field(default_factory=lambda: defaultdict(int))
    read_vectors: int = 0        # read-port transactions
    read_bytes: int = 0
    write_vectors: int = 0       # write transactions (drain)
    write_bytes: int = 0
    port_stall: dict = field(default_factory=lambda: defaultdict(int))  # "rd"/"wr": requests not granted
    max_cell_arity: dict = field(default_factory=lambda: defaultdict(int))
    useful_macs: int = 0
    error: int = 0               # STATUS error code (isa.ERR_*); 0 for a normal FENCE
    panel_hits: int = 0          # A panels replayed from the panel buffer instead of read
    panel_vectors: int = 0       # W-byte vectors replayed from the panel buffer

    @property
    def ideal_cycles(self):
        return -(-self.useful_macs // self.peak_macs)

    @property
    def utilization(self):
        return 0.0 if self.cycles == 0 else self.useful_macs / (self.peak_macs * self.cycles)


def _csa_reduce(ops):
    """3:2 carry-save compression (32-bit, wrapping) until two rows remain."""
    ops = list(ops)
    one = np.uint32(1)
    while len(ops) > 2:
        a, b, c = ops.pop(), ops.pop(), ops.pop()
        ops.append(a ^ b ^ c)
        ops.append(((a & b) | (a & c) | (b & c)) << one)
    return ops[0], ops[1]


class GrantSource:
    """Per-cycle `gnt` for the read and write ports (docs/golden-vectors.md, "Back-pressure").

    Every cycle from model cycle 0, both grants are drawn, read first, whether or not a port
    requests: gnt = rng.random() >= stall, with rng = numpy.random.default_rng(seed). The RTL
    testbench uses this class, so a stalled run is cycle-exact with the model.
    """

    def __init__(self, stall: float = 0.0, seed: int = 0):
        self.stall = stall
        self.rng = np.random.default_rng(seed) if stall else None

    def next(self) -> tuple[bool, bool]:
        if self.rng is None:
            return True, True
        return bool(self.rng.random() >= self.stall), bool(self.rng.random() >= self.stall)


class ByteFifo:
    """QA / QB operand FIFO. The port pushes W-byte reads; VOUTER_MAC pops whatever its shape needs."""

    def __init__(self):
        self.chunks = deque()
        self.n = 0

    def push(self, data: np.ndarray):
        self.chunks.append(data)
        self.n += len(data)

    def pop(self, nbytes: int) -> np.ndarray:
        assert nbytes <= self.n
        out = []
        while nbytes:
            c = self.chunks[0]
            if len(c) <= nbytes:
                out.append(self.chunks.popleft())
                nbytes -= len(c)
            else:
                out.append(c[:nbytes])
                self.chunks[0] = c[nbytes:]
                nbytes = 0
        res = np.concatenate(out)
        self.n -= len(res)
        return res


class TriQueueCore:
    def __init__(self, mem: np.ndarray, program: bytes, params: Params | None = None, trace=None):
        """trace: None, or a list that receives one text line per event (see docs/golden-vectors.md)."""
        self.p = params or Params()
        self.p.validate()
        if len(program) > self.p.prog_bytes:
            raise ValueError("program larger than the program buffer")
        # the buffer reads as NOP (0) beyond the program, and beyond PROG_BYTES (decode lookahead + skipped loop)
        self.mem, self.prog = mem, bytes(program) + bytes(self.p.prog_bytes + 16 - len(program))
        self.T, self.W = self.p.tile, int(self.p.port_bytes)
        self.h = self.T // 2
        self.trace = trace
        self.cfg = [0] * 8
        self.cycle = 0
        self.stats = Stats(peak_macs=self.T * self.T, program_bytes=len(program))
        # front end
        self.pc = 0
        self.loops = []          # frames: [kind, start, end, remaining, iteration]
        self.fence = False
        self.halted = False
        self.loop_t_seen = False # one LOOP_T per program (spec 6.1)
        self.err_align = False   # an alignment error found this cycle (stream or drain stage)
        # command queues
        self.macq = deque()
        self.scmd = {"A": deque(), "B": deque()}
        self.scmd_issued = {"A": 0, "B": 0}
        # stream engine + operand FIFOs
        self.stream = {"A": None, "B": None}  # [addr, bytes_left]
        self.fifo = {"A": ByteFifo(), "B": ByteFifo()}
        self.inflight = deque()                 # (arrive_cycle, which, data)
        self.inflight_n = {"A": 0, "B": 0}      # bytes in flight (memory and panel buffer)
        self.pinflight = deque()                # panel-buffer reads: (arrive_cycle, data)
        self.panel_key = None                   # (addr, bytes) of the A panel in the buffer
        self.panel_data = np.zeros(self.p.panel_bytes, np.uint8)
        self.rr = "A"
        # MAC + accumulator banks
        self.cur = None
        self.beat_ran = False
        self.active = 0
        self.sum = np.zeros((2, self.T, self.T), np.uint32)
        self.carry = np.zeros((2, self.T, self.T), np.uint32)
        self.last_write = [-1, -1]
        self.drain = None        # dict(bank, tile, ready, writes: deque)
        self.swap_wait = False
        self.gnt = GrantSource(self.p.gnt_stall, self.p.gnt_seed)
        self.rd_pend = None             # stream of a read request held for gnt
        self.wr_pend = False            # a write request held for gnt
        self.wr_out = 0                 # writes granted and not yet complete (wr_ack)
        self.wr_done_at = deque()       # completion cycles of those writes
        self._no_progress = 0

    def ev(self, unit, event, **kv):
        if self.trace is not None:
            args = "".join(f" {k}={v}" for k, v in kv.items())
            self.trace.append(f"{self.cycle} {unit} {event}{args}")

    # ---- derived configuration ------------------------------------------------------------
    @property
    def dims(self):
        m, n, k = self.cfg[isa.M], self.cfg[isa.N], self.cfg[isa.K]
        return m, n, k, kp(k), -(-m // self.T), -(-n // self.T)

    def tile_coords(self, t):
        *_, ntn = self.dims
        return (0, 0) if not ntn else divmod(t, ntn)

    def tile_shape(self, t):
        m, n, *_ = self.dims
        ti, tj = self.tile_coords(t)
        return auto_shape(m - self.T * ti, n - self.T * tj, self.T)

    def front_tile(self):
        for f in reversed(self.loops):
            if f[0] == "T":
                return f[4]
        return 0

    # ---- front end ------------------------------------------------------------------------
    def _front_error(self, op, arg, imm, size):
        """Error code of executing the instruction at pc (RTL tq_frontend; a later check overrides
        ERR_PC, as there). A loop with a zero count is skipped before its checks."""
        err = isa.ERR_PC if self.pc + size > self.p.prog_bytes else isa.ERR_NONE
        l2t = self.T.bit_length() - 1
        if op == isa.NOP:
            if arg != 0:
                err = isa.ERR_OPCODE
        elif op == isa.CFG:
            if arg >= 8:
                err = isa.ERR_CFG_REG
            elif self.loops:
                err = isa.ERR_CFG_LOOP
            elif (arg == isa.K and imm > ((0xFFFF_FFFF >> l2t) & ~7)) \
                    or (arg in (isa.M, isa.N) and imm > (0xFFFF << l2t)) \
                    or (arg == isa.MODE and (imm >> 8) != 0):
                err = isa.ERR_CFG_VAL
        elif op in (isa.STREAM_A, isa.STREAM_B, isa.ZERO_C, isa.FENCE):
            if arg != 0:
                err = isa.ERR_OPCODE
        elif op == isa.VOUTER_MAC:
            if (arg & isa.AUTO and (arg & 0x7) != 0) or (not (arg & isa.AUTO) and (arg & 0x4) != 0):
                err = isa.ERR_OPCODE
        elif op == isa.SWAP_C:
            if arg != 0:
                err = isa.ERR_OPCODE
            elif not self.p.chain_c and self.cfg[isa.M] and self.cfg[isa.N]:
                row_b = self.cfg[isa.N] * (1 if (self.cfg[isa.MODE] & isa.MODE_OUT_INT8) else 4)
                if self.cfg[isa.C_LD] < row_b:
                    err = isa.ERR_CFG_VAL
        elif op in (isa.LOOP_K, isa.LOOP_T):
            if op == isa.LOOP_T:
                count = self.dims[4] * self.dims[5]
            else:
                count = self.dims[3] // (2 * self.tile_shape(self.front_tile())[2])
            if count and (arg == 0 or len(self.loops) == 2
                          or (op == isa.LOOP_T and self.loop_t_seen)
                          or (len(self.loops) == 1 and self.pc + 1 + arg > self.loops[0][2])):
                err = isa.ERR_LOOP
        else:
            err = isa.ERR_OPCODE
        return err

    def stop(self, err):
        """Stop at the end of this cycle with an error code (STATUS.ERROR)."""
        self.stats.error = err
        self.halted = True
        self.ev("fe", "error", code=err)

    def queues_idle(self):
        return not self.macq and self.cur is None and not self.scmd["A"] and not self.scmd["B"]

    def stream_idle(self):
        return (not self.scmd["A"] and not self.scmd["B"]
                and self.stream["A"] is None and self.stream["B"] is None
                and not self.inflight and not self.pinflight)

    def all_idle(self):
        return (not self.macq and self.cur is None and self.stream_idle()
                and self.drain is None and self.wr_out == 0)

    def starved(self):
        if not (self.macq and not self.beat_ran and self.stream_idle()
                and self.drain is None and self.wr_out == 0):
            return False
        cmd = self.macq[0]
        if cmd[0] != "MAC":
            return False
        _, r, c, d = cmd
        return self.fifo["A"].n < 2 * d * r or self.fifo["B"].n < 2 * d * c

    def step_front(self):
        if self.halted:
            return
        if self.fence:
            if not self.macq and self.cur is None and self.drain is None and self.wr_out == 0:
                if self.stream_idle() and not self.fifo["A"].n and not self.fifo["B"].n:
                    self.halted = True
                    self.ev("fe", "halt")
                else:
                    self.stop(isa.ERR_OPERAND)
            elif self.starved():
                self.stop(isa.ERR_OPERAND)
            return
        op, arg, imm, size = isa.decode(self.prog, self.pc)

        if op == isa.CFG and not self.queues_idle():
            self.stats.front_stall["cfg_wait"] += 1
            return
        if op in (isa.VOUTER_MAC, isa.SWAP_C, isa.ZERO_C) and len(self.macq) >= self.p.mac_queue_depth:
            self.stats.front_stall["mac_queue_full"] += 1
            if self.starved():
                self.stop(isa.ERR_OPERAND)
            return
        if op in (isa.STREAM_A, isa.STREAM_B):
            s = "A" if op == isa.STREAM_A else "B"
            if len(self.scmd[s]) >= self.p.stream_queue_depth:
                self.stats.front_stall["stream_queue_full"] += 1
                return

        err = self._front_error(op, arg, imm, size)
        if err:
            self.stop(err)
            return
        if op == isa.CFG:
            self.ev("fe", "issue", pc=self.pc, op=isa.MNEMONIC[op], arg=arg, imm=imm)
        else:
            self.ev("fe", "issue", pc=self.pc, op=isa.MNEMONIC.get(op, f"OP{op:X}"), arg=arg)
        next_pc = self.pc + size
        if op == isa.CFG:
            self.cfg[arg] = imm
        elif op in (isa.STREAM_A, isa.STREAM_B):
            s = "A" if op == isa.STREAM_A else "B"
            t = self.scmd_issued[s]
            self.scmd_issued[s] += 1
            if t < self.dims[4] * self.dims[5]:     # commands past the last tile are ignored
                self.scmd[s].append(t)
        elif op == isa.ZERO_C:
            self.macq.append(("ZERO",))
        elif op == isa.VOUTER_MAC:
            if arg & isa.AUTO:
                r, c, d = self.tile_shape(self.front_tile())
            else:
                r, c = (self.T if arg & 2 else self.h), (self.T if arg & 1 else self.h)
                d = self.T * self.T // (r * c)
            self.macq.append(("MAC", r, c, d))
        elif op == isa.SWAP_C:
            self.macq.append(("SWAP", self.front_tile()))
        elif op in (isa.LOOP_K, isa.LOOP_T):
            if op == isa.LOOP_T:
                count, kind = self.dims[4] * self.dims[5], "T"
            else:
                d = self.tile_shape(self.front_tile())[2]
                count, kind = self.dims[3] // (2 * d), "K"
            end = next_pc + arg                  # bodies hold 1-byte instructions
            if count == 0:
                next_pc = end
            else:
                self.loops.append([kind, next_pc, end, count, 0])
                self.loop_t_seen |= kind == "T"
        elif op == isa.FENCE:
            self.fence = True

        self.stats.instructions += 1
        self.pc = next_pc
        while self.loops and self.pc == self.loops[-1][2]:   # zero-overhead loop-back
            f = self.loops[-1]
            f[3] -= 1
            if f[3] > 0:
                f[4] += 1
                self.pc = f[1]
                break
            self.loops.pop()

    # ---- MAC unit -------------------------------------------------------------------------
    def _ext(self, raw: np.ndarray, signed: bool) -> np.ndarray:
        return (raw.view(np.int8) if signed else raw).astype(np.int64)  # 9-bit extended operand

    def do_beat(self):
        cur = self.cur
        r, c, d, h, T = cur["r"], cur["c"], cur["d"], self.h, self.T
        mode = self.cfg[isa.MODE]
        a = self._ext(cur["a"], bool(mode & isa.MODE_A_SIGNED))
        b = self._ext(cur["b"], bool(mode & isa.MODE_B_SIGNED))
        base = cur["beat"] * d
        dest = defaultdict(list)
        for qi in (0, 1):
            for qj in (0, 1):
                rb = qi if r == T else 0
                cb = qj if c == T else 0
                off = (qi if r == h else 0) * (2 if c == h else 1) + (qj if c == h else 0)
                kk = base + off
                ah = a[kk * r + h * rb: kk * r + h * rb + h]       # quadrant's T/2 A bytes
                bh = b[kk * c + h * cb: kk * c + h * cb + h]       # quadrant's T/2 B bytes
                prod = np.outer(ah, bh)
                assert np.all(np.abs(prod) < (1 << 17)), "product exceeds 17-bit signed"
                dest[(rb, cb)].append((prod & 0xFFFFFFFF).astype(np.uint32))
        bank = self.active
        for (rb, cb), prods in dest.items():
            assert len(prods) <= HW_CELL_ARITY[(rb, cb)], f"cell arity exceeded in quadrant {(rb, cb)}"
            self.stats.max_cell_arity[(rb, cb)] = max(self.stats.max_cell_arity[(rb, cb)], len(prods))
            sl = (bank, slice(h * rb, h * rb + h), slice(h * cb, h * cb + h))
            s, cy = _csa_reduce([self.sum[sl], self.carry[sl], *prods])
            self.sum[sl], self.carry[sl] = s, cy
        self.last_write[bank] = self.cycle + self.p.mac_pipe
        self.stats.beats += 1
        self.ev("mac", "beat", bank=bank, beat=cur["beat"])
        cur["beat"] += 1
        if cur["beat"] == 2:
            self.cur = None

    def start_drain(self, bank, tile):
        m, n, *_ = self.dims
        mode = self.cfg[isa.MODE]
        int8_mode = bool(mode & isa.MODE_OUT_INT8)
        T = self.T
        ob = 1 if int8_mode else self.p.out_bytes
        shift = ((mode >> isa.MODE_OUT_SHIFT) & 0x1F) if int8_mode else self.p.out_shift
        default_lanes = T if ob == 1 else self.W // 4
        # values per drain write: what fits a port word, limited by the drain's read lanes
        lanes_per_write = min(self.W // ob, self.p.drain_lanes or default_lanes)
        ti, tj = self.tile_coords(tile)
        rows, cols = min(T, m - T * ti), min(T, n - T * tj)
        vals = (self.sum[bank] + self.carry[bank]).view(np.int32)  # CPA, wraps mod 2^32
        base = self.cfg[isa.C_BASE] + T * ti * self.cfg[isa.C_LD] + ob * T * tj
        # RTL: C_BASE and C_LD are W-aligned. The chaining layout never reads C_LD; every address
        # it forms starts on a whole word as long as C_BASE does (npad and w are multiples of T/2).
        if self.cfg[isa.C_BASE] % self.W or (not self.p.chain_c and self.cfg[isa.C_LD] % self.W):
            self.err_align = True                # ERR_ALIGN at the end of this cycle
        writes = deque()
        if self.p.chain_c:
            # Next layer's A panels: column-major, w = panel_width(rows) bytes per column (the
            # same tail compaction pack_a uses), W/w columns per word. Padding rows and columns
            # are zero in the bank, which is what pack_a writes, so a partial tile needs no
            # special case: the final word of the last column block is simply a short write.
            npad, w = kp(n), panel_width(rows, T)
            cpw = self.W // w                                 # columns per W-byte word
            tbase = self.cfg[isa.C_BASE] + T * ti * npad + T * tj * w
            q = np.clip(vals >> shift, -128, 127).astype(np.int8)
            for cw in range(-(-cols // cpw)):
                lo = cw * cpw
                chunk = b"".join(q[:w, c].tobytes() for c in range(lo, min(lo + cpw, cols)))
                writes.append((tbase + lo * w, chunk))
            # the whole tile must be read out of the bank before the first word exists (the
            # default drain reads one row per cycle; a wide snapshot costs only a cycle)
            fill = 1 if self.p.chain_snapshot else T - 1
            ready = max(self.cycle, self.last_write[bank]) + self.p.cpa_latency + fill
        else:
            for rr in range(rows):
                for v in range(-(-cols // lanes_per_write)):
                    lo = lanes_per_write * v
                    lanes = min(lanes_per_write, cols - lo)
                    v = vals[rr, lo:lo + lanes]
                    data = (np.clip(v >> shift, -128, 127).astype(np.int8).tobytes() if ob == 1
                            else v.astype("<i4").tobytes())
                    writes.append((base + rr * self.cfg[isa.C_LD] + ob * lo, data))
            ready = max(self.cycle, self.last_write[bank]) + self.p.cpa_latency
        self.drain = {"bank": bank, "tile": tile, "ready": ready, "writes": writes}
        self.ev("mac", "drain_start", bank=bank, tile=tile, writes=len(writes))

    def step_mac(self):
        self.swap_wait = False
        self.beat_ran = False
        st = self.stats.mac_state
        if self.cur is not None:
            self.beat_ran = True
            self.do_beat()
            st["busy"] += 1
            return
        ctl = False                                   # at most one ZERO_C / SWAP_C per cycle
        while self.macq:
            cmd = self.macq[0]
            if cmd[0] in ("ZERO", "SWAP") and ctl:
                break
            if cmd[0] == "ZERO":
                if self.drain is not None:           # would clear the bank being drained
                    self.swap_wait = True
                    st["wait_drain"] += 1
                    return
                self.sum[:] = 0
                self.carry[:] = 0
                self.macq.popleft()
                self.ev("mac", "zero")
                ctl = True
                continue
            if cmd[0] == "SWAP":
                if self.drain is not None:          # idle bank still draining
                    self.swap_wait = True
                    st["wait_drain"] += 1
                    return
                old = self.active
                if self.p.banks == 2:
                    self.active ^= 1
                self.macq.popleft()
                self.ev("mac", "swap", tile=cmd[1], active=self.active)
                self.start_drain(old, cmd[1])
                ctl = True
                continue
            _, r, c, d = cmd
            if self.p.banks == 1 and self.drain is not None:   # the only bank is draining
                st["wait_drain"] += 1
                return
            na, nb = 2 * d * r, 2 * d * c                      # bytes for 2 beats
            oka, okb = self.fifo["A"].n >= na, self.fifo["B"].n >= nb
            if not (oka and okb):
                st["wait_A" if okb else "wait_B" if oka else "wait_AB"] += 1
                return
            self.macq.popleft()
            a = self.fifo["A"].pop(na)
            b = self.fifo["B"].pop(nb)
            self.cur = {"r": r, "c": c, "d": d, "a": a, "b": b, "beat": 0}
            self.stats.mac_ops[f"{r}x{c}x{d}"] += 1
            self.ev("mac", "start", shape=f"{r}x{c}x{d}", bank=self.active)
            self.beat_ran = True
            self.do_beat()
            st["busy"] += 1
            return
        st["wait_cmd" if not self.fence else "tail"] += 1

    # ---- stream engine + memory port(s) ---------------------------------------------------
    def _read_candidate(self):
        m, n, k, kpad, ntm, ntn = self.dims
        T, cap = self.T, self.p.fifo_depth * self.W
        for s in (self.rr, "B" if self.rr == "A" else "A"):
            if self.stream[s] is None and self.scmd[s]:
                tile = self.scmd[s].popleft()
                ti, tj = self.tile_coords(tile)
                if s == "A":
                    w = panel_width(m - T * ti, T)
                    self.stream[s] = [self.cfg[isa.A_BASE] + ti * T * kpad, w * kpad, "mem", 0]
                else:
                    w = panel_width(n - T * tj, T)
                    self.stream[s] = [self.cfg[isa.B_BASE] + tj * T * kpad, w * kpad, "mem", 0]
                self.ev("se", "panel", s=s, tile=tile, addr=self.stream[s][0], bytes=self.stream[s][1])
                nbytes = self.stream[s][1]
                if s == "A" and 0 < nbytes <= self.p.panel_bytes:
                    key = (self.stream[s][0], nbytes)
                    if key == self.panel_key:            # the buffered panel: replay it
                        self.stream[s][2] = "buf"
                        self.stats.panel_hits += 1
                        self.ev("se", "reuse", s=s, tile=tile)
                    else:                                # read it and keep it
                        self.stream[s][2] = "fill"
                        self.panel_key = key
                if self.stream[s][0] % self.W:
                    self.err_align = True            # ERR_ALIGN at the end of this cycle
                if self.stream[s][1] == 0:           # K = 0: an empty panel completes at once
                    self.stream[s] = None
            # QA takes vectors in order: an A read waits for replayed vectors still in flight
            # (and the replay waits for A reads, step_panel)
            if self.stream[s] is not None and self.stream[s][2] != "buf" \
                    and not (s == "A" and self.pinflight) \
                    and self.fifo[s].n + self.inflight_n[s] + self.W <= cap:
                return s
        return None

    def step_panel(self):
        """Replay one vector of the buffered A panel into QA (no memory port). Waits until no A
        read is in flight, so QA receives the vectors in order."""
        st = self.stream["A"]
        cap = self.p.fifo_depth * self.W
        pin = sum(len(d) for _, d in self.pinflight)
        if st is None or st[2] != "buf" or self.inflight_n["A"] - pin:
            return
        if self.fifo["A"].n + self.inflight_n["A"] + self.W > cap:
            return
        nb = min(self.W, st[1])
        data = self.panel_data[st[3]:st[3] + nb].copy()
        self.pinflight.append((self.cycle + self.p.panel_latency, data))
        self.inflight_n["A"] += nb
        self.ev("pb", "rd", off=st[3], n=nb)
        st[0] += nb
        st[1] -= nb
        st[3] += nb
        self.stats.panel_vectors += 1
        if st[1] == 0:
            self.stream["A"] = None

    def _issue_read(self, s):
        addr = self.stream[s][0]
        nb = min(self.W, self.stream[s][1])
        if addr < 0 or addr + nb > len(self.mem):
            raise ValueError(f"bus error: read out of bounds at 0x{addr:x} ({nb} bytes, mem size 0x{len(self.mem):x})")
        data = self.mem[addr:addr + nb].copy()
        self.inflight.append((self.cycle + self.p.mem_latency, s, data))
        if self.stream[s][2] == "fill":                  # keep it in the panel buffer
            off = self.stream[s][3]
            self.panel_data[off:off + nb] = data
        self.inflight_n[s] += nb
        self.stream[s][0] += nb
        self.stream[s][1] -= nb
        self.stream[s][3] += nb
        if self.stream[s][1] == 0:
            self.stream[s] = None
        self.rr = "B" if s == "A" else "A"
        self.stats.read_vectors += 1
        self.stats.read_bytes += nb
        self.ev("port", "rd", s=s, addr=addr, n=nb)

    def _issue_write(self):
        addr, data = self.drain["writes"].popleft()
        if addr < 0 or addr + len(data) > len(self.mem):
            raise ValueError(f"bus error: write out of bounds at 0x{addr:x} ({len(data)} bytes, mem size 0x{len(self.mem):x})")
        self.mem[addr:addr + len(data)] = np.frombuffer(data, np.uint8)  # byte-enable masked
        self.stats.write_vectors += 1
        self.stats.write_bytes += len(data)
        self.ev("port", "wr", addr=addr, n=len(data), data=data.hex())
        if not self.drain["writes"]:
            bank = self.drain["bank"]
            self.sum[bank] = 0          # drain clears the bank
            self.carry[bank] = 0
            self.ev("mac", "drain_done", bank=bank, tile=self.drain["tile"])
            self.drain = None

    def _try_write(self, gnt):
        self.wr_pend = not gnt
        if gnt:
            self._issue_write()
            if self.p.wr_ack:
                self.wr_out += 1
                self.wr_done_at.append(self.cycle + self.p.wr_resp_latency)
        else:
            self.stats.port_stall["wr"] += 1

    def _try_read(self, s, gnt):
        self.rd_pend = None if gnt else s
        if gnt:
            self._issue_read(s)
        else:
            self.stats.port_stall["rd"] += 1

    def step_port(self):
        """Requests are decided without looking at gnt (OBI). A request without gnt is held
        unchanged until granted: a held read skips stream arbitration (and panel loads), and on a
        shared port a held read or write keeps the port."""
        rd_gnt, wr_gnt = self.gnt.next()
        cand = self.rd_pend or self._read_candidate()
        room = self.wr_pend or not self.p.wr_ack or self.wr_out < self.p.wr_out_max
        can_write = self.drain is not None and self.cycle >= self.drain["ready"] and room
        if not self.p.shared_port:
            if can_write:
                self._try_write(wr_gnt)
            if cand:
                self._try_read(cand, rd_gnt)
        elif self.wr_pend or (not self.rd_pend and can_write and (self.swap_wait or cand is None)):
            self._try_write(wr_gnt)
        elif cand:
            self._try_read(cand, rd_gnt)
        self.step_panel()
        # write responses due this cycle (wr_ack)
        while self.wr_done_at and self.wr_done_at[0] <= self.cycle:
            self.wr_done_at.popleft()
            self.wr_out -= 1

    def arrivals(self):
        while self.inflight and self.inflight[0][0] <= self.cycle:
            _, s, data = self.inflight.popleft()
            self.inflight_n[s] -= len(data)
            self.fifo[s].push(data)
        while self.pinflight and self.pinflight[0][0] <= self.cycle:
            _, data = self.pinflight.popleft()
            self.inflight_n["A"] -= len(data)
            self.fifo["A"].push(data)

    # ---- top level ------------------------------------------------------------------------
    def step(self):
        """Advance the core by one cycle (arrivals -> MAC -> port -> front end)."""
        if self.halted:
            return
        prev = (self.stats.instructions, self.stats.beats, self.stats.read_vectors,
                self.stats.write_vectors, self.stats.panel_vectors, len(self.macq),
                len(self.scmd["A"]), len(self.scmd["B"]), self.stream["A"] is not None,
                self.stream["B"] is not None)
        self.arrivals()
        self.step_mac()
        self.step_port()
        self.step_front()
        if self.err_align and not self.stats.error:
            self.stop(isa.ERR_ALIGN)
        self.cycle += 1
        cur = (self.stats.instructions, self.stats.beats, self.stats.read_vectors,
               self.stats.write_vectors, self.stats.panel_vectors, len(self.macq),
               len(self.scmd["A"]), len(self.scmd["B"]), self.stream["A"] is not None,
               self.stream["B"] is not None)
        if (not self.halted and cur == prev and not self.inflight and not self.pinflight
                and self.drain is None and self.wr_out == 0 and self.cur is None
                and self.rd_pend is None and not self.wr_pend):
            self._no_progress += 1
            if self._no_progress > 64:
                raise RuntimeError("deadlock detected: no progress with no active requests or timers")
        else:
            self._no_progress = 0

    def run(self, max_cycles=50_000_000):
        while not self.halted:
            self.step()
            if self.cycle > max_cycles:
                raise RuntimeError("cycle limit exceeded (deadlock?)")
        self.stats.cycles = self.cycle
        return self.stats


# ---- convenience: run one GEMM end to end ---------------------------------------------------
def _align(x, a=64):
    return -(-x // a) * a


def reference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """int64 matmul, then wrap to int32 exactly like a 32-bit accumulator."""
    full = a.astype(np.int64) @ b.astype(np.int64)
    return ((full + 2**31) % 2**32 - 2**31).astype(np.int32)


def build_gemm(a: np.ndarray, b: np.ndarray, params: Params | None = None):
    """Pack operands and build the memory image and program. Returns (mem, prog, layout)."""
    from .packing import pack_a, pack_b
    p = params or Params()
    p.validate()
    m, k = a.shape
    k2, n = b.shape
    if not (k == k2 and m >= 0 and n >= 0 and k >= 0):
        raise ValueError(f"invalid GEMM dimensions: A={a.shape}, B={b.shape}")
    kpad = kp(k)
    pa, pb = pack_a(a, kpad, p.tile), pack_b(b, kpad, p.tile)
    al = math.lcm(64, int(p.port_bytes))     # bases and C rows aligned to the port width
    a_base = 0
    b_base = _align(a_base + len(pa), al)
    c_base = _align(b_base + len(pb), al)
    if p.chain_c:
        # One tile row is T rows x kp(n) bytes of the next layer's A panels; the tile walker
        # covers ceil(m/T) of them. C_LD is not used by the drain in this mode.
        c_ld = kp(n)
        c_bytes = -(-m // p.tile) * p.tile * c_ld
    else:
        c_ld = _align(n * p.out_bytes, int(p.port_bytes))   # C rows padded to whole port words
        c_bytes = m * c_ld
    mem = np.zeros(_align(c_base + c_bytes + 64, al), np.uint8)
    mem[a_base:a_base + len(pa)] = pa
    mem[b_base:b_base + len(pb)] = pb
    mode = (isa.MODE_A_SIGNED if a.dtype == np.int8 else 0) | (isa.MODE_B_SIGNED if b.dtype == np.int8 else 0)
    if p.out_bytes == 1:
        mode |= isa.mode_out_int8(p.out_shift)
    prog = isa.gemm_config(a_base, b_base, c_base, c_ld, k, m, n, mode) + isa.gemm_kernel()
    layout = dict(m=m, n=n, k=k, a_base=a_base, b_base=b_base, c_base=c_base, c_ld=c_ld, mode=mode,
                  out_bytes=p.out_bytes)
    if p.chain_c:                 # only the chaining layout needs these (read_c, trace consumers)
        layout.update(tile=p.tile, chain_c=True)
    return mem, prog, layout


def run_gemm(a: np.ndarray, b: np.ndarray, params: Params | None = None, trace=None):
    p = params or Params()
    mem, prog, L = build_gemm(a, b, p)
    core = TriQueueCore(mem, prog, p, trace)
    stats = core.run()
    stats.useful_macs = L["m"] * L["n"] * L["k"]
    return read_c(mem, L), stats


def read_c(mem: np.ndarray, L: dict) -> np.ndarray:
    """Extract the m x n result (int32, or int8 with out_bytes = 1) from a memory image. Rows are
    C_LD bytes apart, or, with the chaining layout, T rows x kp(n) bytes of A panels."""
    ob = L.get("out_bytes", 4)
    if L["m"] == 0 or L["n"] == 0:
        return np.zeros((L["m"], L["n"]), np.int8 if ob == 1 else np.int32)
    if L.get("chain_c"):
        t, npad = L.get("tile", 8), kp(L["n"])
        out = np.zeros((L["m"], L["n"]), np.uint8)
        for ti in range(-(-L["m"] // t)):
            rows = min(t, L["m"] - ti * t)
            base = L["c_base"] + ti * t * npad
            w = panel_width(rows, t)
            for c in range(L["n"]):
                out[ti * t:ti * t + rows, c] = mem[base + c * w:base + c * w + rows]
        return out.view(np.int8).copy()
    rows = mem[L["c_base"]:L["c_base"] + L["m"] * L["c_ld"]].view("<i4" if ob == 4 else "i1")
    return rows.reshape(L["m"], L["c_ld"] // ob)[:, :L["n"]].copy()
