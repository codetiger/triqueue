# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""TFLite int8 output requantization, bit-exact reference (v1.1 epilogue study, proposal 4.1).

TFLite quantizes a conv / fully-connected output per channel as

    out = clamp(MultiplyByQuantizedMultiplier(acc + bias, M0, shift) + zp_out, act_min, act_max)

with a Q31 multiplier M0 in [2**30, 2**31) (or 0) and a shift in [-31, 30]. TFLite has two
roundings for MultiplyByQuantizedMultiplier:

* "double" (the default, gemmlowp): SaturatingRoundingDoublingHighMul(x << left, M0) (ties toward
  +infinity: its nudge is 2**30 for a non-negative product and 1 - 2**30 for a negative one, then a
  truncating divide), then RoundingDivideByPOT(., right) (ties away from zero).
* "single" (TFLITE_SINGLE_ROUNDING, also CMSIS-NN's single-rounding option): one 64-bit product,
  one round-half-up shift.

`*_scalar` functions transcribe the C++ reference line by line with Python integers (wrapping where
C++ wraps int32); the numpy functions are the vectorized versions the model would use, and the tests
check that both agree.
"""
from __future__ import annotations

import numpy as np

INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1


def _wrap32(x: int) -> int:
    return ((x + (1 << 31)) % (1 << 32)) - (1 << 31)


def srdhm_scalar(a: int, b: int) -> int:
    """gemmlowp SaturatingRoundingDoublingHighMul(int32 a, int32 b)."""
    if a == b == INT32_MIN:
        return INT32_MAX
    ab = a * b
    nudge = (1 << 30) if ab >= 0 else 1 - (1 << 30)
    q = ab + nudge
    q = abs(q) // (1 << 31) * (1 if q >= 0 else -1)        # C++ division truncates toward zero
    return _wrap32(q)


def rdbypot_scalar(x: int, exponent: int) -> int:
    """gemmlowp RoundingDivideByPOT(int32 x, int exponent), 0 <= exponent <= 31."""
    mask = (1 << exponent) - 1
    remainder = x & mask
    threshold = (mask >> 1) + (1 if x < 0 else 0)
    return (x >> exponent) + (1 if remainder > threshold else 0)


def mbqm_double_scalar(x: int, m0: int, shift: int) -> int:
    """TFLite MultiplyByQuantizedMultiplier, double rounding (default build)."""
    left = shift if shift > 0 else 0
    right = 0 if shift > 0 else -shift
    return rdbypot_scalar(srdhm_scalar(_wrap32(x * (1 << left)), m0), right)


def mbqm_single_scalar(x: int, m0: int, shift: int) -> int:
    """TFLite MultiplyByQuantizedMultiplier with TFLITE_SINGLE_ROUNDING."""
    total_shift = 31 - shift
    rnd = 1 << (total_shift - 1)
    return _wrap32((x * m0 + rnd) >> total_shift)


def mbqm(x, m0, shift, rounding: str = "double") -> np.ndarray:
    """Vectorized MultiplyByQuantizedMultiplier over int32 arrays (m0, shift broadcast per column)."""
    x = np.asarray(x, np.int64)
    m0 = np.broadcast_to(np.asarray(m0, np.int64), x.shape)
    shift = np.broadcast_to(np.asarray(shift, np.int64), x.shape)
    if rounding == "single":
        total = 31 - shift
        r = (x * m0 + (np.int64(1) << (total - 1))) >> total
        return ((r + (1 << 31)) % (1 << 32) - (1 << 31)).astype(np.int32)
    if rounding != "double":
        raise ValueError(f"rounding must be 'double' or 'single', not {rounding!r}")
    left = np.where(shift > 0, shift, 0)
    right = np.where(shift > 0, 0, -shift)
    xs = ((x << left) + (1 << 31)) % (1 << 32) - (1 << 31)                # int32 wrap of x << left
    ab = xs * m0                                                           # |ab| < 2**62: fits int64
    nudge = np.where(ab >= 0, 1 << 30, 1 - (1 << 30))
    q = ab + nudge
    hi = np.where(q >= 0, q >> 31, -((-q) >> 31))                          # truncate toward zero
    hi = np.where((xs == INT32_MIN) & (m0 == INT32_MIN), INT32_MAX, hi)
    hi = (hi + (1 << 31)) % (1 << 32) - (1 << 31)
    mask = (np.int64(1) << right) - 1
    rem = hi & mask
    thr = (mask >> 1) + (hi < 0)
    return ((hi >> right) + (rem > thr)).astype(np.int32)


def requantize(acc, bias, m0, shift, zp_out: int, act_min: int = -128, act_max: int = 127,
               rounding: str = "double") -> np.ndarray:
    """TFLite int8 output stage for an int32 accumulator tile: per-column bias, m0 and shift
    (broadcast along the last axis), per-tensor output zero point and activation clamp.
    `acc + bias` wraps like the int32 add in the reference kernels."""
    acc = np.asarray(acc, np.int64)
    s = ((acc + np.asarray(bias, np.int64)) + (1 << 31)) % (1 << 32) - (1 << 31)
    out = mbqm(s, m0, shift, rounding).astype(np.int64) + zp_out
    return np.clip(out, act_min, act_max).astype(np.int8)


def quantize_multiplier(scale: float) -> tuple[int, int]:
    """TFLite QuantizeMultiplier: real scale -> (M0 in [2**30, 2**31) or 0, shift)."""
    if scale == 0.0:
        return 0, 0
    q, shift = np.frexp(scale)                       # scale = q * 2**shift, 0.5 <= q < 1
    q_fixed = int(np.floor(float(q) * (1 << 31) + 0.5))   # TfLiteRound: half away from zero (q > 0)
    if q_fixed == (1 << 31):
        q_fixed //= 2
        shift += 1
    if shift < -31:
        return 0, 0
    if shift > 30:
        shift, q_fixed = 30, (1 << 31) - 1
    return q_fixed, int(shift)
