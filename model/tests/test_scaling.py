# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Tile size T and port width W are parameters: the same proofs must hold at every size."""
import itertools

import numpy as np
import pytest

from tri_queue import isa
from tri_queue.core import run_gemm, reference, Params


def rand(rng, shape, dt):
    lo, hi = (-128, 128) if dt == np.int8 else (0, 256)
    return rng.integers(lo, hi, shape).astype(dt)


def sizes_around(t):
    """Row/col counts that hit every shape at tile t: tiny, half, half+1, full, full+1, full+half."""
    return sorted({1, t // 2, t // 2 + 1, t, t + 1, t + t // 2})


@pytest.mark.parametrize("t", [4, 16, 32])
@pytest.mark.parametrize("k", [1, 9, 40])
def test_bit_exact_other_tiles(t, k):
    rng = np.random.default_rng(t * 1000 + k)
    for m, n in itertools.product(sizes_around(t), repeat=2):
        a = rand(rng, (m, k), np.int8)
        b = rand(rng, (k, n), np.uint8 if (m + n) % 2 else np.int8)
        c, _ = run_gemm(a, b, Params(tile=t))
        np.testing.assert_array_equal(c, reference(a, b), err_msg=f"T={t} {m}x{n}x{k}")


@pytest.mark.parametrize("t", [16, 32])
def test_all_shapes_and_arity_at_tile(t):
    rng = np.random.default_rng(t)
    m = n = t + t // 2           # one full and one half panel each way: all four shapes
    a, b = rand(rng, (m, 32), np.int8), rand(rng, (32, n), np.int8)
    c, s = run_gemm(a, b, Params(tile=t))
    np.testing.assert_array_equal(c, reference(a, b))
    h = t // 2
    assert set(s.mac_ops) == {f"{t}x{t}x1", f"{t}x{h}x2", f"{h}x{t}x2", f"{h}x{h}x4"}
    assert s.max_cell_arity == {(0, 0): 4, (0, 1): 2, (1, 0): 2, (1, 1): 1}


@pytest.mark.parametrize("t,w", [(8, 8), (8, 12), (8, 32), (8, 64), (16, 16), (16, 24), (16, 64), (32, 32)])
def test_any_port_width_is_correct(t, w):
    """Narrower, wider and non-power-of-two ports change cycles, never results."""
    rng = np.random.default_rng(t + w)
    a, b = rand(rng, (t + 3, 21), np.int8), rand(rng, (21, t + 5), np.uint8)
    c, s = run_gemm(a, b, Params(tile=t, port_bytes=w))
    np.testing.assert_array_equal(c, reference(a, b))
    assert s.write_bytes == 4 * a.shape[0] * b.shape[1]


@pytest.mark.parametrize("t", [16, 32])
def test_instruction_count_scales_with_tiles(t):
    rng = np.random.default_rng(9)
    a, b = rand(rng, (64, 64), np.int8), rand(rng, (64, 64), np.int8)
    _, s = run_gemm(a, b, Params(tile=t))
    tiles = (64 // t) ** 2
    assert s.instructions == 8 + 1 + 2 + 1 + tiles * (2 + 1 + 32 + 1) + 1


@pytest.mark.parametrize("t", [8, 16, 32])
def test_balanced_port_reads_and_reuse(t):
    """W = 2T: bytes read per MAC = 2/T, and the steady state stays near peak when K hides the drain."""
    m = n = 4 * t
    k = 4 * t + 32                                     # Kp comfortably above the 2T drain
    rng = np.random.default_rng(t)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    c, s = run_gemm(a, b, Params(tile=t))
    np.testing.assert_array_equal(c, reference(a, b))
    assert s.read_bytes == 2 * (m // t) * (n // t) * t * k     # every tile re-reads its two panels
    assert s.utilization > 0.9


def test_half_width_port_caps_utilization():
    """W = T supplies half the bytes the array consumes: utilization is capped near 50%."""
    rng = np.random.default_rng(1)
    a, b = rand(rng, (64, 256), np.int8), rand(rng, (256, 64), np.int8)
    _, s = run_gemm(a, b, Params(tile=16, port_bytes=16))
    assert 0.45 < s.utilization <= 0.5


def test_vouter_encoding_is_tile_independent():
    assert isa.vouter((8, 4)) == isa.vouter((16, 8), tile=16) == isa.vouter((32, 16), tile=32)
    assert isa.disassemble(isa.vouter((16, 8), tile=16), tile=16) == ["0000  VOUTER_MAC 16x8"]
