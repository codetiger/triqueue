# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Illegal programs stop with the RTL's error codes; edge cases that used to hang or corrupt C
are well defined (spec 6.1, 6.4)."""
import numpy as np
import pytest

from _helpers import rand
from tri_queue import isa
from tri_queue.core import Params, TriQueueCore, build_gemm, read_c, reference, run_gemm
from tri_queue.programs import edge_program as program

KERNEL = isa.gemm_kernel()


def run(name, m=20, n=24, k=27, **cfg_over):
    rng = np.random.default_rng(7)
    a, b = rand(rng, (m, k), np.int8), rand(rng, (k, n), np.int8)
    p = Params()
    mem, prog, L = build_gemm(a, b, p)
    L.update(cfg_over)
    cfg = isa.gemm_config(L["a_base"], L["b_base"], L["c_base"], L["c_ld"], L["k"], L["m"], L["n"], L["mode"])
    core = TriQueueCore(mem, program(name, cfg), p)
    s = core.run(max_cycles=100_000)
    return a, b, core, L, s


@pytest.mark.parametrize("name,code", [
    ("opcode", isa.ERR_OPCODE),
    ("reserved_arg", isa.ERR_OPCODE),
    ("reserved_vouter", isa.ERR_OPCODE),
    ("cfg_reg", isa.ERR_CFG_REG),
    ("cfg_loop", isa.ERR_CFG_LOOP),
    ("cfg_val_k", isa.ERR_CFG_VAL),
    ("cfg_val_m", isa.ERR_CFG_VAL),
    ("cfg_val_mode", isa.ERR_CFG_VAL),
    ("cfg_val_cld", isa.ERR_CFG_VAL),
    ("loop_depth", isa.ERR_LOOP),
    ("loop_nest_over", isa.ERR_LOOP),
    ("second_loop_t", isa.ERR_LOOP),
    ("pc", isa.ERR_PC),
    ("pc_skip_loop", isa.ERR_PC),
    ("operand_leftover", isa.ERR_OPERAND),
    ("operand_starved", isa.ERR_OPERAND),
])
def test_illegal_program_stops_with_error(name, code):
    *_, s = run(name)
    assert s.error == code


def test_error_stops_at_once():
    """The opcode error replaces FENCE. Unlike FENCE it does not wait for the core to go idle:
    the earlier tiles are in C, the last tile's drain is cut short."""
    a, b, core, L, s = run("opcode")
    assert s.error == isa.ERR_OPCODE
    c, ref = read_c(core.mem, L), reference(a, b)
    assert np.array_equal(c[:8], ref[:8]) and not np.array_equal(c, ref)


@pytest.mark.parametrize("field", ["a_base", "b_base", "c_base", "c_ld"])
def test_misaligned_base_is_err_align(field):
    rng = np.random.default_rng(1)
    a, b = rand(rng, (20, 27), np.int8), rand(rng, (27, 24), np.int8)
    mem, prog, L = build_gemm(a, b, Params())
    L[field] += 4
    cfg = isa.gemm_config(L["a_base"], L["b_base"], L["c_base"], L["c_ld"], L["k"], L["m"], L["n"], L["mode"])
    s = TriQueueCore(mem, cfg + KERNEL, Params()).run(max_cycles=100_000)
    assert s.error == isa.ERR_ALIGN


def test_zero_c_waits_for_the_drain():
    """ZERO_C after SWAP_C in every tile: it waits for the drain instead of clearing the bank
    being drained, so C is still right, and it costs cycles."""
    a, b, core, L, s = run("zero_in_drain")
    assert s.error == 0
    assert np.array_equal(read_c(core.mem, L), reference(a, b))
    _, s0 = run_gemm(a, b)
    assert s.cycles > s0.cycles and s.mac_state["wait_drain"] > s0.mac_state["wait_drain"]


def test_cfg_waits_for_queued_work():
    """CFG C_BASE after SWAP_C waits for macq and scmd to empty, so C drains to original C_BASE."""
    a, b, core, L, s = run("cfg_after_swap")
    assert s.error == 0
    assert core.cfg[isa.C_BASE] == 0x1000
    assert np.array_equal(read_c(core.mem, L), reference(a, b))


@pytest.mark.parametrize("m,n", [(1, 1), (9, 13), (16, 16)])
def test_k_zero_writes_zero(m, n):
    a, b = np.zeros((m, 0), np.int8), np.zeros((0, n), np.int8)
    mem, prog, L = build_gemm(a, b)
    mem[L["c_base"]:] = 0xAA                             # poison C so the test cannot pass vacuously
    s = TriQueueCore(mem, prog).run()
    assert s.error == 0 and s.read_vectors == 0 and s.write_vectors > 0
    assert np.array_equal(read_c(mem, L), np.zeros((m, n), np.int32))


@pytest.mark.parametrize("m,n,k", [(0, 8, 16), (8, 0, 16), (0, 0, 16), (0, 0, 0)])
def test_zero_m_or_n(m, n, k):
    a, b = np.zeros((m, k), np.int8), np.zeros((k, n), np.int8)
    c, s = run_gemm(a, b)
    assert s.error == 0 and s.read_vectors == 0 and s.write_vectors == 0
    assert c.shape == (m, n)
    # Also test a standalone LOOP_K with M=0 or N=0 outside LOOP_T (B10)
    mem = np.zeros(1024, np.uint8)
    prog = isa.gemm_config(0, 0, 0, 64, 16, m, n, 0) + isa.enc(isa.LOOP_K, 1) + isa.enc(isa.NOP) + isa.enc(isa.FENCE)
    s2 = TriQueueCore(mem, prog).run()
    assert s2.error == 0


def test_params_validation_and_port_bytes_sync():
    import dataclasses
    for bad in [
        dict(tile=12), dict(tile=256), dict(port_bytes=6), dict(mem_latency=0),
        dict(fifo_depth=1), dict(fifo_depth=256), dict(mac_queue_depth=0), dict(mac_queue_depth=16),
        dict(stream_queue_depth=0), dict(mac_pipe=3), dict(banks=0), dict(banks=3),
        dict(prog_bytes=50), dict(prog_bytes=1024), dict(panel_bytes=24),
    ]:
        with pytest.raises(ValueError):
            Params(**bad)
    p = Params()
    assert p.port_bytes == 16
    p2 = dataclasses.replace(p, tile=16)
    assert p2.port_bytes == 32
    p.tile = 32
    assert p.port_bytes == 64


def test_memory_bounds_raise_bus_error():
    rng = np.random.default_rng(9)
    a, b = rand(rng, (8, 8), np.int8), rand(rng, (8, 8), np.int8)
    mem, _, L = build_gemm(a, b)
    prog_rd = isa.gemm_config(len(mem), L["b_base"], L["c_base"], L["c_ld"], 8, 8, 8, 0) + KERNEL
    with pytest.raises(ValueError, match="bus error: read out of bounds"):
        TriQueueCore(mem.copy(), prog_rd).run()
    prog_wr = isa.gemm_config(L["a_base"], L["b_base"], len(mem), L["c_ld"], 8, 8, 8, 0) + KERNEL
    with pytest.raises(ValueError, match="bus error: write out of bounds"):
        TriQueueCore(mem.copy(), prog_wr).run()
