# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""A panel buffer (spec 10.9): A panels of a tile row are read once and replayed."""
import itertools

import numpy as np
import pytest

from _helpers import rand
from tri_queue.core import Params, run_gemm, reference
from tri_queue.packing import kp


@pytest.mark.parametrize("m,n,k", [(1, 1, 1), (9, 17, 33), (64, 64, 64), (20, 24, 27), (5, 40, 1), (4, 512, 64)])
@pytest.mark.parametrize("tile", [4, 8, 16])
def test_bit_exact_with_buffer(m, n, k, tile):
    rng = np.random.default_rng(m * n + k + tile)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.uint8)
    c, s = run_gemm(a, b, Params(tile=tile, panel_bytes=tile * kp(k)))
    assert np.array_equal(c, reference(a, b))


def test_reads_each_a_panel_once():
    """64^3 at T = 8: A is read once per tile row (8 x 4 KiB = 4 KiB total), B as before."""
    rng = np.random.default_rng(1)
    a, b = rand(rng, (64, 64), np.int8), rand(rng, (64, 64), np.int8)
    _, s0 = run_gemm(a, b, Params())
    _, s1 = run_gemm(a, b, Params(panel_bytes=512))
    assert s0.read_bytes == 64 * 1024 and s1.read_bytes == 36 * 1024
    assert s1.panel_hits == 8 * 7 and s1.panel_vectors * 16 == 28 * 1024
    assert s1.cycles == s0.cycles                     # compute-bound: same cycles


def test_gemv_reaches_the_port_cap():
    """M = 1: the vector is the A panel, replayed for every tile; only B uses the port, so the
    run approaches the port cap of 25% (16 of 64 MACs per cycle)."""
    rng = np.random.default_rng(2)
    a, b = rand(rng, (1, 512), np.int8), rand(rng, (512, 512), np.int8)
    _, s0 = run_gemm(a, b, Params())
    _, s1 = run_gemm(a, b, Params(panel_bytes=4096))
    u0, u1 = 512 * 512 / (64 * s0.cycles), 512 * 512 / (64 * s1.cycles)
    assert u0 < 0.17 and 0.24 < u1 <= 0.25


def test_panel_larger_than_buffer_streams_as_before():
    rng = np.random.default_rng(3)
    a, b = rand(rng, (32, 128), np.int8), rand(rng, (128, 32), np.int8)
    _, s0 = run_gemm(a, b, Params())
    _, s1 = run_gemm(a, b, Params(panel_bytes=8 * 128 - 16))    # one vector too small
    assert s1.panel_hits == 0 and (s1.read_bytes, s1.cycles) == (s0.read_bytes, s0.cycles)


@pytest.mark.parametrize("lat,stall,shared", list(itertools.product((1, 2, 6, 13), (0.0, 0.5), (False, True))))
def test_order_kept_under_latency_and_stalls(lat, stall, shared):
    """Replayed vectors never overtake A reads in flight: results stay exact at any latency."""
    rng = np.random.default_rng(lat)
    a, b = rand(rng, (37, 64), np.int8), rand(rng, (64, 41), np.int8)
    p = Params(mem_latency=lat, gnt_stall=stall, gnt_seed=lat, shared_port=shared, panel_bytes=1024)
    c, s = run_gemm(a, b, p)
    assert np.array_equal(c, reference(a, b)) and s.panel_hits > 0


def test_off_by_default():
    rng = np.random.default_rng(4)
    a, b = rand(rng, (20, 24), np.int8), rand(rng, (24, 27), np.int8)
    _, s = run_gemm(a, b, Params())
    assert s.panel_hits == 0 and s.panel_vectors == 0
