# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Host-side packing (spec section 5.3), for a T x T tile (T = 8 in the spec).

A -> column panels: T rows (T/2 for a final panel with <= T/2 rows left) x Kp, k-major.
B -> row panels:    Kp x T columns (T/2 for a final panel with <= T/2 cols left), k-major.
Kp = K rounded up to a multiple of 8 (the deepest shape, T/2 x T/2 x 4, covers 8 k steps per
instruction, for every T). All padding is zero.
"""
import numpy as np


def kp(k: int) -> int:
    return -(-k // 8) * 8


def panel_width(left: int, t: int = 8) -> int:
    return t // 2 if left <= t // 2 else t


def auto_shape(rows_left: int, cols_left: int, t: int = 8):
    """(r, c, d) with r*c*d = t*t: full or half rows/cols, reduction depth 1, 2 or 4."""
    r, c = panel_width(rows_left, t), panel_width(cols_left, t)
    return r, c, t * t // (r * c)


def _as_u8(x: np.ndarray) -> np.ndarray:
    assert x.dtype in (np.int8, np.uint8), x.dtype
    return x.view(np.uint8)


def pack_a(a: np.ndarray, kpad: int, t: int = 8) -> np.ndarray:
    m, k = a.shape
    a = _as_u8(a)
    panels = []
    for ti in range(-(-m // t)):
        rows = a[t * ti:t * ti + t]
        w = panel_width(m - t * ti, t)
        p = np.zeros((kpad, w), np.uint8)
        p[:k, :rows.shape[0]] = rows.T
        panels.append(p.reshape(-1))
    return np.concatenate(panels)


def pack_b(b: np.ndarray, kpad: int, t: int = 8) -> np.ndarray:
    k, n = b.shape
    b = _as_u8(b)
    panels = []
    for tj in range(-(-n // t)):
        cols = b[:, t * tj:t * tj + t]
        w = panel_width(n - t * tj, t)
        p = np.zeros((kpad, w), np.uint8)
        p[:k, :cols.shape[1]] = cols
        panels.append(p.reshape(-1))
    return np.concatenate(panels)
