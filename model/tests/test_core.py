# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Functional and structural proofs for the Tri-Queue model. Run: python3 -m pytest -q"""
import itertools

import numpy as np
import pytest

from _helpers import DTYPES, rand
from tri_queue import isa
from tri_queue.core import run_gemm, reference, Params


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
