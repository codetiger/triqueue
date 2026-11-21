# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""TFLite requantization reference (tri_queue.requant): vectorized == line-by-line C++ transcription."""
import numpy as np
import pytest

from tri_queue import requant as rq

I32 = (rq.INT32_MIN, rq.INT32_MAX)


def _samples(rng, n):
    x = np.concatenate([rng.integers(*I32, n, endpoint=True), rng.integers(-1000, 1000, n),
                        np.array([0, 1, -1, 2, -2, 3, -3, rq.INT32_MIN, rq.INT32_MAX, rq.INT32_MIN + 1])])
    return x.astype(np.int64)


@pytest.mark.parametrize("rounding,shifts", [("double", range(-31, 31)), ("single", range(-31, 31))])
def test_vector_matches_scalar(rounding, shifts):
    rng = np.random.default_rng(11)
    scalar = rq.mbqm_double_scalar if rounding == "double" else rq.mbqm_single_scalar
    for shift in shifts:
        x = _samples(rng, 300)
        m0 = rng.integers(1 << 30, (1 << 31) - 1, x.size, endpoint=True)
        got = rq.mbqm(x, m0, shift, rounding)
        want = [scalar(int(a), int(b), shift) for a, b in zip(x, m0)]
        assert got.tolist() == want, (rounding, shift)


def test_srdhm_edge_cases():
    assert rq.srdhm_scalar(rq.INT32_MIN, rq.INT32_MIN) == rq.INT32_MAX       # the one saturating case
    assert rq.srdhm_scalar(3, 1 << 30) == 2                                  # 1.5 -> 2
    assert rq.srdhm_scalar(-3, 1 << 30) == -1                                # -1.5 -> -1 (ties toward +inf)
    assert rq.rdbypot_scalar(3, 1) == 2 and rq.rdbypot_scalar(-3, 1) == -2   # ties away from zero
    v = rq.mbqm(np.array([rq.INT32_MIN]), np.array([rq.INT32_MIN]), 0)       # vectorized saturation
    assert v.tolist() == [rq.INT32_MAX]


@pytest.mark.parametrize("rounding", ["double", "single"])
def test_close_to_real_product(rounding):
    """Both roundings are within 1 of x * M0 * 2**(shift - 31) (double rounding can be off by 1)."""
    rng = np.random.default_rng(5)
    x = rng.integers(-(1 << 24), 1 << 24, 5000)
    for shift in (-12, -5, 0, 3):
        m0 = rng.integers(1 << 30, 1 << 31, x.size)
        real = x.astype(np.float64) * m0 * 2.0 ** (shift - 31)
        assert np.max(np.abs(rq.mbqm(x, m0, shift, rounding) - real)) <= 1.0


def test_requantize_clamps_and_offsets():
    acc = np.array([[0, 1000, -1000, rq.INT32_MAX]], np.int64)
    m0, shift = rq.quantize_multiplier(0.01)
    out = rq.requantize(acc, bias=[0, 0, 0, 1], m0=m0, shift=shift, zp_out=-5, act_min=-128, act_max=127)
    assert out.dtype == np.int8
    assert out.tolist() == [[-5, 5, -15, -128]]       # INT32_MAX + 1 wraps to INT32_MIN like the int32 add


def test_quantize_multiplier():
    for scale in (1e-6, 0.0123, 0.5, 0.75, 1.0, 3.9):
        m0, shift = rq.quantize_multiplier(scale)
        assert (1 << 30) <= m0 < (1 << 31)
        assert abs(m0 * 2.0 ** (shift - 31) - scale) <= scale * 2 ** -30
    assert rq.quantize_multiplier(0.0) == (0, 0)
