# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""TFLite int8 epilogue in the drain (MODE[9], spec 10.1): bit-exact with tri_queue.requant, with
TFLite's default double rounding or, with MODE[10], TFLITE_SINGLE_ROUNDING."""
import numpy as np
import pytest

from _helpers import rand
from tri_queue import isa, requant
from tri_queue.core import Params, TriQueueCore, build_gemm, read_c, reference, run_gemm


def _epi(rng, n, zp=-3, lo=-128, hi=127):
    m0 = rng.integers(1 << 30, 1 << 31, n)
    shift = rng.integers(-12, 2, n)
    bias = rng.integers(-5000, 5000, n)
    return dict(bias=bias, m0=m0, shift=shift, zp=zp, act_min=lo, act_max=hi)


def _want(a, b, e):
    return requant.requantize(reference(a, b), e["bias"], e["m0"], np.clip(e["shift"], -31, 30),
                              e["zp"], e["act_min"], e["act_max"], e.get("rounding", "double"))


@pytest.mark.parametrize("m,n,k", [(1, 1, 1), (8, 8, 8), (20, 24, 27), (37, 41, 16), (64, 9, 40),
                                   (5, 64, 33), (65, 65, 65)])
@pytest.mark.parametrize("walk", [False, True])
@pytest.mark.parametrize("lanes", [1, 4, 8])
def test_epilogue_is_bit_exact(m, n, k, walk, lanes):
    rng = np.random.default_rng(m * 100 + n * 10 + k + lanes)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    e = _epi(rng, n)
    c, s = run_gemm(a, b, Params(out_bytes=1, walk_cols=walk, epi_lanes=lanes), epi=e)
    assert s.error == 0
    assert np.array_equal(c, _want(a, b, e))


@pytest.mark.parametrize("m,n,k", [(1, 1, 1), (20, 24, 27), (65, 65, 65)])
@pytest.mark.parametrize("walk", [False, True])
def test_single_rounding_is_bit_exact(m, n, k, walk):
    """MODE[10]: every shift in [-31, 30] (and the clamped -40 and 31), against TFLite's
    TFLITE_SINGLE_ROUNDING code (requant.mbqm_single_scalar through requantize)."""
    rng = np.random.default_rng(m + n + k)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    e = dict(_epi(rng, n), rounding="single")
    e["shift"] = rng.integers(-31, 31, n)
    e["shift"][: min(n, 3)] = [-40, 31, 30][: min(n, 3)]
    mem, prog, L = build_gemm(a, b, Params(out_bytes=1, walk_cols=walk), epi=e)
    assert L["mode"] & isa.MODE_ROUND1
    c, s = run_gemm(a, b, Params(out_bytes=1, walk_cols=walk), epi=e)
    assert s.error == 0 and np.array_equal(c, _want(a, b, e))


def test_single_and_double_rounding_differ_only_at_ties():
    """The two forms differ by at most 1, and only on some values; same cycles."""
    rng = np.random.default_rng(3)
    a, b = rand(rng, (64, 64), np.int8), rand(rng, (64, 64), np.int8)
    e = _epi(rng, 64, lo=-128, hi=127)
    e["shift"] = np.full(64, -9)
    c2, s2 = run_gemm(a, b, Params(out_bytes=1), epi=e)
    c1, s1 = run_gemm(a, b, Params(out_bytes=1), epi=dict(e, rounding="single"))
    d = np.abs(c1.astype(int) - c2.astype(int))
    assert d.max() == 1 and 0 < (d != 0).sum() < d.size // 20
    assert s1.cycles == s2.cycles


@pytest.mark.parametrize("shared,stall", [(False, 0.0), (True, 0.0), (False, 0.4), (True, 0.5)])
def test_epilogue_with_back_pressure(shared, stall):
    rng = np.random.default_rng(9)
    a, b = rand(rng, (40, 27), np.int8), rand(rng, (27, 33), np.int8)
    e = _epi(rng, 33, zp=5, lo=0, hi=100)                    # ReLU-like clamp
    c, s = run_gemm(a, b, Params(out_bytes=1, shared_port=shared, gnt_stall=stall, gnt_seed=4), epi=e)
    assert s.error == 0 and np.array_equal(c, _want(a, b, e))


def test_parameters_fetched_once_per_tile_column_in_the_column_walk():
    rng = np.random.default_rng(1)
    m, n, k = 64, 24, 16                                       # 8 x 3 tiles
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    e = _epi(rng, n)
    _, row = run_gemm(a, b, Params(out_bytes=1), epi=e)
    _, col = run_gemm(a, b, Params(out_bytes=1, walk_cols=True), epi=e)
    assert row.epi_reads == isa.EPI_WORDS * 8 * 3            # every tile changes column
    assert col.epi_reads == isa.EPI_WORDS * 3                # once per column
    assert col.cycles <= row.cycles


def test_mode_rules():
    a = np.ones((8, 8), np.int8)
    mem, prog, L = build_gemm(a, a, Params(out_bytes=1), epi=_epi(np.random.default_rng(0), 8))
    assert L["mode"] & isa.MODE_EPI and L["mode"] & isa.MODE_OUT_INT8
    cfg = prog[:-10]
    kernel = isa.gemm_kernel()
    bad = [cfg[:35] + isa.cfg(isa.MODE, L["mode"] & ~isa.MODE_OUT_INT8) + cfg[40:] + kernel,  # MODE[9] without [2]
           cfg[:45] + isa.cfg(isa.EPI_OUT, 1 << 24) + kernel,                                    # EPI_OUT[31:24]
           cfg[:40] + isa.cfg(isa.EPI_BASE, L["epi_base"] + 4) + cfg[45:] + kernel,               # misaligned
           cfg[:35] + isa.cfg(isa.MODE, (L["mode"] & ~isa.MODE_EPI) | isa.MODE_ROUND1) + cfg[40:] + kernel,  # [10] w/o [9]
           cfg[:35] + isa.cfg(isa.MODE, L["mode"] | 1 << isa.MODE_RESERVED_SHIFT) + cfg[40:] + kernel]  # MODE[11]
    codes = []
    for prog_bad in bad:
        core = TriQueueCore(mem.copy(), prog_bad, Params(out_bytes=1))
        codes.append(core.run(max_cycles=10_000).error)
    assert codes == [isa.ERR_CFG_VAL, isa.ERR_CFG_VAL, isa.ERR_ALIGN, isa.ERR_CFG_VAL, isa.ERR_CFG_VAL]
    with pytest.raises(ValueError):
        build_gemm(a, a, Params(out_bytes=1), epi=dict(_epi(np.random.default_rng(0), 8), rounding="nearest"))


def test_no_epilogue_keeps_the_v1_memory_image():
    a = np.ones((20, 20), np.int8)
    mem, prog, L = build_gemm(a, a, Params())
    assert "epi_base" not in L and len(prog) == 50
