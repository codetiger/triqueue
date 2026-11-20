# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Column-major tile walk (Params.walk_cols, model study for v1.1): same C, B-panel reuse."""
import numpy as np
import pytest

from _helpers import rand
from tri_queue.core import Params, reference, run_gemm


@pytest.mark.parametrize("m,n,k", [(1, 1, 1), (8, 8, 8), (20, 24, 27), (37, 41, 16), (64, 9, 40),
                                   (5, 64, 33), (65, 65, 65)])
@pytest.mark.parametrize("panel", [0, 512, 4096])
@pytest.mark.parametrize("shared", [False, True])
def test_walk_cols_is_bit_exact(m, n, k, panel, shared):
    rng = np.random.default_rng(m * 1000 + n * 10 + k)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    c, s = run_gemm(a, b, Params(walk_cols=True, panel_bytes=panel, shared_port=shared))
    assert s.error == 0
    assert np.array_equal(c, reference(a, b))


def test_walk_cols_reuses_the_b_panel():
    """Row walk + panel buffer replays A across a tile row; column walk replays B down a column."""
    rng = np.random.default_rng(3)
    m, n, k = 64, 16, 64                       # 8 tile rows x 2 tile columns
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    _, row = run_gemm(a, b, Params(panel_bytes=4096))
    _, col = run_gemm(a, b, Params(panel_bytes=4096, walk_cols=True))
    tiles = (m // 8) * (n // 8)
    assert row.panel_hits == tiles - m // 8    # each tile row reads A once
    assert col.panel_hits == tiles - n // 8    # each tile column reads B once
    assert col.read_vectors < row.read_vectors


def test_walk_cols_rejects_chain_c():
    with pytest.raises(ValueError):
        Params(walk_cols=True, chain_c=True, out_bytes=1)


def test_walk_is_mode_bit_8():
    """Params.walk_cols only sets MODE[8] in the program; the core reads the walk from MODE."""
    from tri_queue import isa
    from tri_queue.core import build_gemm
    a = np.zeros((9, 9), np.int8)
    for walk in (False, True):
        *_, L = build_gemm(a, a, Params(walk_cols=walk))
        assert bool(L["mode"] & isa.MODE_WALK_COLS) == walk
        assert L["mode"] >> isa.MODE_RESERVED_SHIFT == 0
