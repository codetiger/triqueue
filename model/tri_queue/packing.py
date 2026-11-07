# SPDX-License-Identifier: Apache-2.0
"""Host-side packing (design doc section 5.3).

A -> column panels: 8 rows (4 for a final panel with <= 4 rows left) x Kp, k-major.
B -> row panels:    Kp x 8 columns (4 for a final panel with <= 4 cols left), k-major.
Kp = K rounded up to a multiple of 8. All padding is zero.
"""
import numpy as np


def kp(k: int) -> int:
    return -(-k // 8) * 8


def panel_width(left: int) -> int:
    return 4 if left <= 4 else 8


def auto_shape(rows_left: int, cols_left: int):
    r, c = panel_width(rows_left), panel_width(cols_left)
    return r, c, 64 // (r * c)


def _as_u8(x: np.ndarray) -> np.ndarray:
    assert x.dtype in (np.int8, np.uint8), x.dtype
    return x.view(np.uint8)


def pack_a(a: np.ndarray, kpad: int) -> np.ndarray:
    m, k = a.shape
    a = _as_u8(a)
    panels = []
    for ti in range(-(-m // 8)):
        rows = a[8 * ti:8 * ti + 8]
        w = panel_width(m - 8 * ti)
        p = np.zeros((kpad, w), np.uint8)
        p[:k, :rows.shape[0]] = rows.T
        panels.append(p.reshape(-1))
    return np.concatenate(panels)


def pack_b(b: np.ndarray, kpad: int) -> np.ndarray:
    k, n = b.shape
    b = _as_u8(b)
    panels = []
    for tj in range(-(-n // 8)):
        cols = b[:, 8 * tj:8 * tj + 8]
        w = panel_width(n - 8 * tj)
        p = np.zeros((kpad, w), np.uint8)
        p[:k, :cols.shape[1]] = cols
        panels.append(p.reshape(-1))
    return np.concatenate(panels)
