# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Shared test helpers (pytest puts this directory on sys.path)."""
import numpy as np

DTYPES = {"s8": np.int8, "u8": np.uint8}


def rand(rng, shape, dt):
    """Uniform int8 or uint8 operand of the given shape."""
    lo, hi = (-128, 128) if dt == np.int8 else (0, 256)
    return rng.integers(lo, hi, shape).astype(dt)


def gemm(m, n, k, seed=0):
    """Signed int8 A (m x k) and B (k x n)."""
    rng = np.random.default_rng(seed)
    return rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
