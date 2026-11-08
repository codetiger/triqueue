# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Checks the C driver (sw/tq.c) against the cycle model's host side, byte for byte:
program (isa.gemm_config + gemm_kernel), packing (packing.pack_a / pack_b), C row stride
(core.build_gemm), and the register sequence of tq_probe / tq_run on a fake register file.

    python3 -m pytest sw -q
"""
import ctypes
import itertools
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from tri_queue import isa  # noqa: E402
from tri_queue.core import Params, build_gemm  # noqa: E402
from tri_queue.packing import kp, pack_a, pack_b  # noqa: E402

CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
FLAGS = ["-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic"]


class Gemm(ctypes.Structure):
    _fields_ = [(f, ctypes.c_uint32) for f in ("m", "n", "k", "a_base", "b_base", "c_base", "c_ld", "mode")]


class Info(ctypes.Structure):
    _fields_ = [(f, ctypes.c_uint32) for f in
                ("tile", "port_bytes", "fifo_depth", "macq_depth", "shared_port", "prog_bytes")]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if CC is None:
        pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("tq") / "libtq.so"
    subprocess.run([CC, *FLAGS, "-shared", "-fPIC", "-o", str(out), str(ROOT / "sw" / "tq.c")], check=True)
    so = ctypes.CDLL(str(out))
    u8p = ctypes.POINTER(ctypes.c_uint8)
    so.tq_gemm_program.restype = ctypes.c_size_t
    so.tq_gemm_program.argtypes = [u8p, ctypes.c_size_t, ctypes.POINTER(Gemm)]
    so.tq_packed_a_bytes.restype = so.tq_packed_b_bytes.restype = ctypes.c_size_t
    so.tq_packed_a_bytes.argtypes = so.tq_packed_b_bytes.argtypes = [ctypes.c_uint32] * 3
    so.tq_pack_a.argtypes = so.tq_pack_b.argtypes = [u8p, u8p, ctypes.c_uint32, ctypes.c_uint32,
                                                     ctypes.c_size_t, ctypes.c_uint32]
    so.tq_kp.argtypes = [ctypes.c_uint32]
    so.tq_c_ld.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    so.tq_kp.restype = so.tq_c_ld.restype = ctypes.c_uint32
    so.tq_probe.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(Info)]
    so.tq_run.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(Info), u8p, ctypes.c_size_t,
                          ctypes.c_uint32]
    return so


def ptr(x: np.ndarray):
    return x.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))


def test_freestanding_compile(tmp_path):
    if CC is None:
        pytest.skip("no C compiler")
    subprocess.run([CC, *FLAGS, "-ffreestanding", "-c", "-o", str(tmp_path / "tq.o"), str(ROOT / "sw" / "tq.c")],
                   check=True)


SHAPES = [(1, 1, 1), (3, 5, 7), (8, 8, 8), (9, 17, 33), (64, 64, 64), (20, 24, 27), (2304, 16, 8), (5, 40, 1)]


@pytest.mark.parametrize("tile", [4, 8, 16, 32])
def test_program_and_layout(lib, tile):
    for (m, n, k), a_s, b_s in itertools.product(SHAPES, (True, False), (True, False)):
        a = np.zeros((m, k), np.int8 if a_s else np.uint8)
        b = np.zeros((k, n), np.int8 if b_s else np.uint8)
        _, prog, L = build_gemm(a, b, Params(tile=tile))
        assert lib.tq_c_ld(n, 2 * tile) == L["c_ld"]
        g = Gemm(m, n, k, L["a_base"], L["b_base"], L["c_base"], L["c_ld"], L["mode"])
        out = np.zeros(64, np.uint8)
        size = lib.tq_gemm_program(ptr(out), 64, ctypes.byref(g))
        assert bytes(out[:size]) == prog
        assert lib.tq_gemm_program(ptr(out), 49, ctypes.byref(g)) == 0
    assert bytes(prog[-10:]) == isa.gemm_kernel()


@pytest.mark.parametrize("tile", [4, 8, 16, 32])
def test_packing(lib, tile):
    rng = np.random.default_rng(tile)
    for m, n, k in SHAPES:
        assert lib.tq_kp(k) == kp(k)
        a = rng.integers(-128, 128, (m, k)).astype(np.int8)
        b = rng.integers(0, 256, (k, n)).astype(np.uint8)
        for x, pack, size, pyref in ((a, lib.tq_pack_a, lib.tq_packed_a_bytes(m, k, tile), pack_a(a, kp(k), tile)),
                                     (b, lib.tq_pack_b, lib.tq_packed_b_bytes(k, n, tile), pack_b(b, kp(k), tile))):
            assert size == len(pyref)
            src = np.zeros((x.shape[0], x.shape[1] + 3), np.uint8)    # row stride != row length
            src[:, :x.shape[1]] = x.view(np.uint8)
            dst = np.full(size, 0xAA, np.uint8)
            r, c = x.shape
            pack(ptr(dst), ptr(src), r, c, src.shape[1], tile)
            assert np.array_equal(dst, pyref)


def test_probe_and_run(lib):
    regs = (ctypes.c_uint32 * 256)()
    info = Info()
    assert lib.tq_probe(regs, ctypes.byref(info)) == 16                      # no device
    regs[0] = 0x54510001
    regs[1] = (16 << 24) | (1 << 20) | (3 << 16) | (4 << 8) | 8                # HW_CONFIG
    assert lib.tq_probe(regs, ctypes.byref(info)) == 0
    assert (info.tile, info.port_bytes, info.fifo_depth, info.macq_depth, info.shared_port, info.prog_bytes) \
        == (8, 16, 4, 3, 1, 64)
    _, prog, _ = build_gemm(np.zeros((9, 9), np.int8), np.zeros((9, 9), np.int8))
    p = np.frombuffer(prog, np.uint8).copy()
    regs[3] = 0x2                                                              # STATUS: DONE
    assert lib.tq_run(regs, ctypes.byref(info), ptr(p), len(p), 10) == 0
    words = [regs[0x40 + i] for i in range(13)]                                 # PROG at 0x100
    padded = prog + bytes(-len(prog) % 4)
    assert words == [int.from_bytes(padded[i:i + 4], "little") for i in range(0, len(padded), 4)]
    assert regs[2] == 1                                                         # CTRL: START
    regs[3] = 0x2 | 0x4 | (6 << 8)                                              # DONE + ERROR(align)
    assert lib.tq_run(regs, ctypes.byref(info), ptr(p), len(p), 10) == 6
    regs[3] = 0x2 | 0x8                                                         # DONE + BUS_ERR
    assert lib.tq_run(regs, ctypes.byref(info), ptr(p), len(p), 10) == 20
    regs[3] = 0x1                                                               # BUSY forever
    assert lib.tq_run(regs, ctypes.byref(info), ptr(p), len(p), 10) == 18
    assert regs[2] == 2                                                         # ABORT after the timeout
    big = np.zeros(65, np.uint8)
    assert lib.tq_run(regs, ctypes.byref(info), ptr(big), 65, 10) == 17


class Plan(ctypes.Structure):
    _fields_ = [("a_off", ctypes.c_uint32), ("b_off", ctypes.c_uint32), ("c_off", ctypes.c_uint32),
                ("c_ld", ctypes.c_uint32), ("prog_len", ctypes.c_uint32), ("prog", ctypes.c_uint8 * 50)]


@pytest.mark.parametrize("tile", [4, 8, 16])
@pytest.mark.parametrize("m,n,k,sa,sb", [(1, 1, 1, 1, 1), (20, 24, 27, 1, 0), (64, 64, 64, 0, 1), (9, 13, 0, 1, 1),
                                         (37, 5, 100, 0, 0)])
def test_gemm_api_end_to_end(lib, tile, m, n, k, sa, sb):
    """C prepares the workspace, the cycle model runs that memory image and program, C finishes:
    the result equals numpy."""
    from tri_queue.core import TriQueueCore, reference
    u8p = ctypes.POINTER(ctypes.c_uint8)
    lib.tq_gemm_workspace_bytes.restype = ctypes.c_size_t
    lib.tq_gemm_workspace_bytes.argtypes = [ctypes.c_uint32] * 4
    lib.tq_gemm_prepare.argtypes = [ctypes.POINTER(Plan), u8p, ctypes.c_uint32, ctypes.POINTER(Info), u8p,
                                    ctypes.c_size_t, u8p, ctypes.c_size_t] + [ctypes.c_uint32] * 3 + [ctypes.c_int] * 2
    lib.tq_gemm_finish.argtypes = [ctypes.POINTER(Plan), u8p, ctypes.POINTER(ctypes.c_int32), ctypes.c_size_t,
                                   ctypes.c_uint32, ctypes.c_uint32]
    rng = np.random.default_rng(m + n + k + tile)
    a = rng.integers(-128 if sa else 0, 128 if sa else 256, (m, max(k, 1))).astype(np.int8 if sa else np.uint8)[:, :k]
    b = rng.integers(-128 if sb else 0, 128 if sb else 256, (max(k, 1), n)).astype(np.int8 if sb else np.uint8)[:k]
    a, b = np.ascontiguousarray(a), np.ascontiguousarray(b)
    info = Info(tile, 2 * tile, 4, 3, 0, 64)
    ws = np.zeros(lib.tq_gemm_workspace_bytes(m, n, k, tile) + 64, np.uint8)
    plan = Plan()
    a_ptr = ptr(a) if a.size else ptr(np.zeros(1, np.uint8))
    b_ptr = ptr(b) if b.size else ptr(np.zeros(1, np.uint8))
    assert lib.tq_gemm_prepare(ctypes.byref(plan), ptr(ws), 0, ctypes.byref(info), a_ptr, max(k, 1), b_ptr,
                               n, m, n, k, sa, sb) == 0
    core = TriQueueCore(ws, bytes(plan.prog[:plan.prog_len]), Params(tile=tile))
    s = core.run()
    assert s.error == 0
    ldc = n + 3
    c = np.full((m, ldc), -1, np.int32)
    lib.tq_gemm_finish(ctypes.byref(plan), ptr(core.mem), c.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)), ldc, m, n)
    assert np.array_equal(c[:, :n], reference(a, b)) and np.all(c[:, n:] == -1)
