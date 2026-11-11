# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Functional and structural proofs for the Tri-Queue model. Run: python3 -m pytest -q"""
import itertools

import numpy as np
import pytest

from _helpers import DTYPES, rand
from tri_queue import isa
from tri_queue.core import run_gemm, reference, Params, build_gemm, TriQueueCore
from tri_queue.packing import kp, pack_a


EDGE_SIZES = [1, 3, 4, 5, 7, 8, 9, 12, 13, 16, 17, 31]


@pytest.mark.parametrize("m,n", list(itertools.product([1, 4, 5, 8, 12, 17], repeat=2)))
@pytest.mark.parametrize("k", [1, 7, 8, 9, 33])
def test_bit_exact_edge_sizes(m, n, k):
    """Every tile shape (8x8x1, 8x4x2, 4x8x2, 4x4x4) and every K padding case matches numpy."""
    rng = np.random.default_rng(m * 10007 + n * 101 + k)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    c, _ = run_gemm(a, b)
    np.testing.assert_array_equal(c, reference(a, b))


@pytest.mark.parametrize("da,db", list(itertools.product(DTYPES, repeat=2)))
def test_signedness_modes(da, db):
    rng = np.random.default_rng(1)
    a, b = rand(rng, (21, 40), DTYPES[da]), rand(rng, (40, 19), DTYPES[db])
    c, _ = run_gemm(a, b)
    np.testing.assert_array_equal(c, reference(a, b))


def test_random_sizes():
    rng = np.random.default_rng(2)
    for _ in range(25):
        m, n, k = (int(x) for x in rng.integers(1, 48, 3))
        a = rand(rng, (m, k), DTYPES[rng.choice(list(DTYPES))])
        b = rand(rng, (k, n), DTYPES[rng.choice(list(DTYPES))])
        c, _ = run_gemm(a, b)
        np.testing.assert_array_equal(c, reference(a, b), err_msg=f"{m}x{n}x{k}")


def test_extreme_values():
    for va, vb, dt in [(-128, -128, np.int8), (-128, 127, np.int8), (255, 255, np.uint8)]:
        a = np.full((9, 50), va, dt)
        b = np.full((50, 9), vb, dt)
        c, _ = run_gemm(a, b)
        np.testing.assert_array_equal(c, reference(a, b))


def test_no_overflow_up_to_k_32768():
    """Doc claim: int32 accumulation cannot overflow for K <= 32,768 in any signedness mode."""
    k = 32768
    a = np.full((1, k), 255, np.uint8)
    b = np.full((k, 1), 255, np.uint8)
    c, _ = run_gemm(a, b)
    assert c[0, 0] == 255 * 255 * k and c[0, 0] < 2**31


def test_wraps_like_int32_beyond_bound():
    k = 40000
    a = np.full((1, k), 255, np.uint8)
    b = np.full((k, 1), 255, np.uint8)
    c, _ = run_gemm(a, b)
    np.testing.assert_array_equal(c, reference(a, b))   # reference wraps mod 2^32
    assert c[0, 0] < 0


def test_cell_arity_matches_hardware_claim():
    """Corner tiles exercise TL arity 4, edge tiles TR/BL arity 2 (asserted inside do_beat)."""
    rng = np.random.default_rng(3)
    a, b = rand(rng, (12, 32), np.int8), rand(rng, (32, 12), np.int8)
    _, s = run_gemm(a, b)
    assert set(s.mac_ops) == {"8x8x1", "8x4x2", "4x8x2", "4x4x4"}
    assert s.max_cell_arity == {(0, 0): 4, (0, 1): 2, (1, 0): 2, (1, 1): 1}


def test_program_size_and_instruction_count():
    assert len(isa.gemm_kernel()) == 10
    assert len(isa.gemm_config(0, 0, 0, 0, 0, 0, 0, 0)) == 40
    rng = np.random.default_rng(4)
    a, b = rand(rng, (64, 64), np.int8), rand(rng, (64, 64), np.int8)
    _, s = run_gemm(a, b)
    # 8 CFG + ZERO_C + 2 STREAM + LOOP_T + 64 tiles x (2 STREAM + LOOP_K + 32 VOUTER + SWAP) + FENCE
    assert s.instructions == 8 + 1 + 2 + 1 + 64 * 36 + 1 == 2317


def test_64cube_utilization():
    rng = np.random.default_rng(5)
    a, b = rand(rng, (64, 64), np.int8), rand(rng, (64, 64), np.int8)
    c, s = run_gemm(a, b)
    np.testing.assert_array_equal(c, reference(a, b))
    assert s.utilization > 0.99


@pytest.mark.parametrize("p", [Params(shared_port=True), Params(mem_latency=12), Params(fifo_depth=2),
                               Params(mac_queue_depth=1), Params(stream_queue_depth=1),
                               Params(banks=1), Params(banks=1, shared_port=True)])
def test_correct_under_any_timing(p):
    """Timing parameters change cycles, never results."""
    rng = np.random.default_rng(6)
    a, b = rand(rng, (20, 24), np.int8), rand(rng, (24, 27), np.uint8)
    c, _ = run_gemm(a, b, p)
    np.testing.assert_array_equal(c, reference(a, b))


@pytest.mark.parametrize("tile", [4, 8, 16, 32])
@pytest.mark.parametrize("m,n,k", [(19, 21, 24), (13, 5, 9), (8, 8, 8)])
@pytest.mark.parametrize("snapshot", [False, True])
def test_chaining_output_layout(tile, m, n, k, snapshot):
    """chain_c writes C as the next layer's A panels bit for bit, in fewer words than int8 rows."""
    rng = np.random.default_rng(tile * 1000 + m * 10 + n)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    p = Params(tile=tile, out_bytes=1, chain_c=True, chain_snapshot=snapshot)
    c, s = run_gemm(a, b, p)
    ref = np.clip(reference(a, b) >> 8, -128, 127).astype(np.int8)
    np.testing.assert_array_equal(c, ref)
    # the whole C region is exactly what the host packer would have built for the next layer
    mem, prog, L = build_gemm(a, b, p)
    TriQueueCore(mem, prog, p).run()
    packed = pack_a(ref, kp(n), tile)
    np.testing.assert_array_equal(mem[L["c_base"]:L["c_base"] + len(packed)], packed)
    # fewer write transactions than one int8 tile row per word; the bytes are the padded
    # panel image (at most the packed size), not just the m x n values
    _, s_rows = run_gemm(a, b, Params(tile=tile, out_bytes=1))
    assert s.write_vectors < s_rows.write_vectors
    assert s.write_bytes <= len(packed)
    # a wide bank read only removes the fill cycles, it never changes a result or a write
    _, s_fill = run_gemm(a, b, Params(tile=tile, out_bytes=1, chain_c=True))
    assert s.write_vectors == s_fill.write_vectors and s.write_bytes == s_fill.write_bytes
    if snapshot:
        assert s.mac_state.get("wait_drain", 0) <= s_fill.mac_state.get("wait_drain", 0)
        assert s.cycles <= s_fill.cycles


def test_chaining_param_checks():
    with pytest.raises(AssertionError):
        Params(chain_c=True)                      # needs int8 output
    with pytest.raises(AssertionError):
        Params(out_bytes=1, chain_snapshot=True)  # needs the chaining layout


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("stall", [0.2, 0.5, 0.8])
@pytest.mark.parametrize("m,n,k", [(20, 24, 27), (9, 9, 9), (64, 64, 32)])
def test_gnt_backpressure(m, n, k, stall, shared):
    """Random gnt stalls slow the run down but never change the result or the transactions."""
    from _helpers import gemm
    a, b = gemm(m, n, k, seed=m + n + k)
    c0, s0 = run_gemm(a, b, Params(shared_port=shared))
    c, s = run_gemm(a, b, Params(shared_port=shared, gnt_stall=stall, gnt_seed=7))
    np.testing.assert_array_equal(c, reference(a, b))
    assert (s.read_vectors, s.write_vectors, s.beats, s.instructions) == \
        (s0.read_vectors, s0.write_vectors, s0.beats, s0.instructions)
    assert s.cycles > s0.cycles and s.port_stall["rd"] > 0 and s.port_stall["wr"] > 0


def test_gnt_deterministic_and_zero_is_default():
    from _helpers import gemm
    a, b = gemm(33, 17, 40)
    runs = [run_gemm(a, b, Params(gnt_stall=0.4, gnt_seed=3))[1].cycles for _ in range(2)]
    assert runs[0] == runs[1]
    assert run_gemm(a, b, Params(gnt_stall=0.0, gnt_seed=99))[1].cycles == run_gemm(a, b)[1].cycles


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("stall", [0.3, 0.7])
@pytest.mark.parametrize("m,n,k", [(20, 24, 27), (64, 64, 32)])
def test_gnt_request_stability(m, n, k, stall, shared):
    """OBI: a request that is not granted is presented again, unchanged, the next cycle."""
    from _helpers import gemm
    from tri_queue.core import TriQueueCore, build_gemm

    class Probe(TriQueueCore):
        def __init__(self, *args):
            super().__init__(*args)
            self.reqs = []                       # (cycle, port, request, granted)

        def _try_read(self, s, gnt):
            port = "p" if self.p.shared_port else "rd"
            self.reqs.append((self.cycle, port, ("rd", self.stream[s][0]), gnt))
            super()._try_read(s, gnt)

        def _try_write(self, gnt):
            port = "p" if self.p.shared_port else "wr"
            self.reqs.append((self.cycle, port, ("wr", self.drain["writes"][0][0]), gnt))
            super()._try_write(gnt)

    a, b = gemm(m, n, k, seed=3)
    p = Params(shared_port=shared, gnt_stall=stall, gnt_seed=11)
    mem, prog, _ = build_gemm(a, b, p)
    core = Probe(mem, prog, p)
    core.run()
    by_port = {}
    for cyc, port, req, _ in core.reqs:
        by_port.setdefault(port, {})[cyc] = req
    held = 0
    for cyc, port, req, gnt in core.reqs:
        if not gnt:
            held += 1
            assert by_port[port].get(cyc + 1) == req, f"{port} request changed after cycle {cyc}"
    assert held > 0


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("lat,out_max", [(0, 15), (1, 15), (5, 15), (12, 2), (30, 1)])
def test_write_ack(lat, out_max, shared):
    """wr_ack: results unchanged, FENCE waits for every write response, and with latency 0 the
    timing equals the default (write complete at its grant)."""
    rng = np.random.default_rng(lat * 7 + out_max)
    a, b = rand(rng, (37, 64), np.int8), rand(rng, (64, 41), np.int8)
    base = Params(shared_port=shared, gnt_stall=0.3, gnt_seed=5)
    c0, s0 = run_gemm(a, b, base)
    p = Params(shared_port=shared, gnt_stall=0.3, gnt_seed=5, wr_ack=True, wr_resp_latency=lat, wr_out_max=out_max)
    c1, s1 = run_gemm(a, b, p)
    assert np.array_equal(c1, reference(a, b)) and np.array_equal(c0, c1)
    assert s1.write_vectors == s0.write_vectors
    if lat == 0:
        assert s1.cycles == s0.cycles
    else:
        assert s1.cycles >= s0.cycles + lat - 1       # the last response arrives lat cycles after its grant


@pytest.mark.parametrize("shared", [False, True])
def test_one_stage_mac_pipe(shared):
    """mac_pipe = 1 (RTL MAC_PIPE = 1, no product register): same C, drains start a cycle earlier."""
    rng = np.random.default_rng(11)
    a, b = rand(rng, (40, 64), np.int8), rand(rng, (64, 36), np.uint8)
    c2, s2 = run_gemm(a, b, Params(shared_port=shared))
    c1, s1 = run_gemm(a, b, Params(shared_port=shared, mac_pipe=1))
    assert np.array_equal(c1, reference(a, b)) and np.array_equal(c1, c2)
    assert s1.cycles <= s2.cycles
