# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Cycle-level model of the Tri-Queue int8 matrix core (spec rev 4, docs/spec/).

The simulator executes the real instruction bytes. The arithmetic is done the way the hardware
does it: 9x9 multiplies, per-quadrant operand selection for the k-depth arity modes, and
carry-save (sum, carry) accumulation in 32 bits. C therefore comes out of the same model that
produces the cycle count.

Size parameters: tile T (T x T output tile, T*T multipliers; the spec's design point is T = 8) and
port width W bytes (default 2T, which balances the read port against the multiplier array).

Per-cycle order: memory responses land -> MAC unit -> memory port(s) -> front end.

Modelling assumptions (stated in model/README.md):
  * Front end decodes 1 instruction/cycle; loops are zero-overhead (loop buffer).
  * ZERO_C and SWAP_C are bank-pointer operations that take no MAC cycle. Per cycle the MAC unit
    consumes at most one of them, followed by at most one VOUTER_MAC.
  * Loop bodies contain only 1-byte instructions (no CFG).
  * VOUTER_MAC needs all of its operand bytes present when it starts, and pops them at the start.
  * SRAM reads are fully pipelined with a fixed latency, one W-byte read per cycle.
"""
import math
from collections import deque, defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import isa
from .packing import kp, auto_shape, panel_width

# Max products an accumulate cell in each (T/2 x T/2) quadrant may sum per cycle (spec section 4.1).
HW_CELL_ARITY = {(0, 0): 4, (0, 1): 2, (1, 0): 2, (1, 1): 1}


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

    def __post_init__(self):
        if not self.port_bytes:
            self.port_bytes = 2 * self.tile
        assert self.tile >= 4 and self.tile % 2 == 0, "tile must be even and >= 4"
        assert self.port_bytes >= 4 and self.port_bytes % 4 == 0, "port must hold whole int32 lanes"


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
    max_cell_arity: dict = field(default_factory=lambda: defaultdict(int))
    useful_macs: int = 0

    @property
    def ideal_cycles(self):
        return -(-self.useful_macs // self.peak_macs)

    @property
    def utilization(self):
        return self.useful_macs / (self.peak_macs * self.cycles)


def _csa_reduce(ops):
    """3:2 carry-save compression (32-bit, wrapping) until two rows remain."""
    ops = list(ops)
    one = np.uint32(1)
    while len(ops) > 2:
        a, b, c = ops.pop(), ops.pop(), ops.pop()
        ops.append(a ^ b ^ c)
        ops.append(((a & b) | (a & c) | (b & c)) << one)
    return ops[0], ops[1]


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
        """trace: None, or a list that receives one text line per event (see docs/spec/trace_format.md)."""
        self.mem, self.prog, self.p = mem, program, params or Params()
        self.T, self.W = self.p.tile, self.p.port_bytes
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
        # command queues
        self.macq = deque()
        self.scmd = {"A": deque(), "B": deque()}
        self.scmd_issued = {"A": 0, "B": 0}
        # stream engine + operand FIFOs
        self.stream = {"A": None, "B": None}  # [addr, bytes_left]
        self.fifo = {"A": ByteFifo(), "B": ByteFifo()}
        self.inflight = deque()                 # (arrive_cycle, which, data)
        self.inflight_n = {"A": 0, "B": 0}      # bytes in flight
        self.rr = "A"
        # MAC + accumulator banks
        self.cur = None
        self.active = 0
        self.sum = np.zeros((2, self.T, self.T), np.uint32)
        self.carry = np.zeros((2, self.T, self.T), np.uint32)
        self.last_write = [-1, -1]
        self.drain = None        # dict(bank, tile, ready, writes: deque)
        self.swap_wait = False

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
        return divmod(t, ntn)

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
    def _body_bytes(self, pc, n):
        """Loop bodies hold only 1-byte instructions (no CFG), so the body is n bytes long."""
        for i in range(n):
            if isa.decode(self.prog, pc + i)[3] != 1:
                raise ValueError(f"CFG inside a loop body at {pc + i:#x}")
        return n

    def all_idle(self):
        return (not self.macq and self.cur is None and not self.scmd["A"] and not self.scmd["B"]
                and self.stream["A"] is None and self.stream["B"] is None and not self.inflight
                and self.drain is None)

    def step_front(self):
        if self.halted:
            return
        if self.fence:
            if self.all_idle():
                assert not self.fifo["A"].n and not self.fifo["B"].n, "operands left in FIFOs at FENCE"
                self.halted = True
                self.ev("fe", "halt")
            return
        if self.pc >= len(self.prog):
            self.halted = self.all_idle()
            return
        op, arg, imm, size = isa.decode(self.prog, self.pc)

        if op in (isa.VOUTER_MAC, isa.SWAP_C, isa.ZERO_C) and len(self.macq) >= self.p.mac_queue_depth:
            self.stats.front_stall["mac_queue_full"] += 1
            return
        if op in (isa.STREAM_A, isa.STREAM_B):
            s = "A" if op == isa.STREAM_A else "B"
            if len(self.scmd[s]) >= self.p.stream_queue_depth:
                self.stats.front_stall["stream_queue_full"] += 1
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
            end = next_pc + self._body_bytes(next_pc, arg)
            if count == 0:
                next_pc = end
            else:
                self.loops.append([kind, next_pc, end, count, 0])
        elif op == isa.FENCE:
            self.fence = True
        elif op != isa.NOP:
            raise ValueError(f"bad opcode {op:#x} at {self.pc:#x}")

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
                assert np.all(np.abs(prod) < (1 << 17)), "product exceeds 18-bit signed"
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
        T, lanes_per_write = self.T, self.W // 4
        ti, tj = self.tile_coords(tile)
        rows, cols = min(T, m - T * ti), min(T, n - T * tj)
        vals = (self.sum[bank] + self.carry[bank]).view(np.int32)  # CPA, wraps mod 2^32
        base = self.cfg[isa.C_BASE] + T * ti * self.cfg[isa.C_LD] + 4 * T * tj
        assert self.cfg[isa.C_BASE] % self.W == 0 and self.cfg[isa.C_LD] % self.W == 0, \
            "C_BASE and C_LD must be multiples of the port width"
        writes = deque()
        for rr in range(rows):
            for v in range(-(-cols // lanes_per_write)):
                lo = lanes_per_write * v
                lanes = min(lanes_per_write, cols - lo)
                writes.append((base + rr * self.cfg[isa.C_LD] + 4 * lo,
                               vals[rr, lo:lo + lanes].astype("<i4").tobytes()))
        ready = max(self.cycle, self.last_write[bank]) + self.p.cpa_latency
        self.drain = {"bank": bank, "tile": tile, "ready": ready, "writes": writes}
        self.ev("mac", "drain_start", bank=bank, tile=tile, writes=len(writes))

    def step_mac(self):
        self.swap_wait = False
        st = self.stats.mac_state
        if self.cur is not None:
            self.do_beat()
            st["busy"] += 1
            return
        ctl = False                                   # at most one ZERO_C / SWAP_C per cycle
        while self.macq:
            cmd = self.macq[0]
            if cmd[0] in ("ZERO", "SWAP") and ctl:
                break
            if cmd[0] == "ZERO":
                assert self.drain is None
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
                assert self.cfg[isa.A_BASE] % self.W == 0 and self.cfg[isa.B_BASE] % self.W == 0, \
                    "A_BASE and B_BASE must be multiples of the port width"
                if s == "A":
                    w = panel_width(m - T * ti, T)
                    self.stream[s] = [self.cfg[isa.A_BASE] + ti * T * kpad, w * kpad]
                else:
                    w = panel_width(n - T * tj, T)
                    self.stream[s] = [self.cfg[isa.B_BASE] + tj * T * kpad, w * kpad]
                self.ev("se", "panel", s=s, tile=tile, addr=self.stream[s][0], bytes=self.stream[s][1])
            if self.stream[s] is not None and self.fifo[s].n + self.inflight_n[s] + self.W <= cap:
                return s
        return None

    def _issue_read(self, s):
        addr = self.stream[s][0]
        nb = min(self.W, self.stream[s][1])
        self.inflight.append((self.cycle + self.p.mem_latency, s, self.mem[addr:addr + nb].copy()))
        self.inflight_n[s] += nb
        self.stream[s][0] += nb
        self.stream[s][1] -= nb
        if self.stream[s][1] == 0:
            self.stream[s] = None
        self.rr = "B" if s == "A" else "A"
        self.stats.read_vectors += 1
        self.stats.read_bytes += nb
        self.ev("port", "rd", s=s, addr=addr, n=nb)

    def _issue_write(self):
        addr, data = self.drain["writes"].popleft()
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

    def step_port(self):
        cand = self._read_candidate()
        can_write = self.drain is not None and self.cycle >= self.drain["ready"]
        if not self.p.shared_port:
            if can_write:
                self._issue_write()
            if cand:
                self._issue_read(cand)
            return
        if can_write and (self.swap_wait or cand is None):
            self._issue_write()
        elif cand:
            self._issue_read(cand)

    def arrivals(self):
        while self.inflight and self.inflight[0][0] <= self.cycle:
            _, s, data = self.inflight.popleft()
            self.inflight_n[s] -= len(data)
            self.fifo[s].push(data)

    # ---- top level ------------------------------------------------------------------------
    def run(self, max_cycles=50_000_000):
        while not self.halted:
            self.arrivals()
            self.step_mac()
            self.step_port()
            self.step_front()
            self.cycle += 1
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
    m, k = a.shape
    k2, n = b.shape
    assert k == k2 and m >= 1 and n >= 1 and k >= 1
    kpad = kp(k)
    pa, pb = pack_a(a, kpad, p.tile), pack_b(b, kpad, p.tile)
    al = math.lcm(64, p.port_bytes)          # bases and C rows aligned to the port width
    a_base = 0
    b_base = _align(a_base + len(pa), al)
    c_base = _align(b_base + len(pb), al)
    c_ld = _align(n * 4, p.port_bytes)        # C rows padded to whole port words
    mem = np.zeros(_align(c_base + m * c_ld + 64, al), np.uint8)
    mem[a_base:a_base + len(pa)] = pa
    mem[b_base:b_base + len(pb)] = pb
    mode = (isa.MODE_A_SIGNED if a.dtype == np.int8 else 0) | (isa.MODE_B_SIGNED if b.dtype == np.int8 else 0)
    prog = isa.gemm_config(a_base, b_base, c_base, c_ld, k, m, n, mode) + isa.gemm_kernel()
    layout = dict(m=m, n=n, k=k, a_base=a_base, b_base=b_base, c_base=c_base, c_ld=c_ld, mode=mode)
    return mem, prog, layout


def run_gemm(a: np.ndarray, b: np.ndarray, params: Params | None = None, trace=None):
    p = params or Params()
    mem, prog, L = build_gemm(a, b, p)
    core = TriQueueCore(mem, prog, p, trace)
    stats = core.run()
    stats.useful_macs = L["m"] * L["n"] * L["k"]
    return read_c(mem, L), stats


def read_c(mem: np.ndarray, L: dict) -> np.ndarray:
    """Extract the m x n int32 result from a memory image (rows are C_LD bytes apart)."""
    rows = mem[L["c_base"]:L["c_base"] + L["m"] * L["c_ld"]].view("<i4").reshape(L["m"], L["c_ld"] // 4)
    return rows[:, :L["n"]].copy()
