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
    ("opcode", isa.ERR_OPCODE), ("cfg_reg", isa.ERR_CFG_REG), ("cfg_loop", isa.ERR_CFG_LOOP),
    ("loop_depth", isa.ERR_LOOP), ("second_loop_t", isa.ERR_LOOP), ("pc", isa.ERR_PC)])
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


@pytest.mark.parametrize("m,n", [(1, 1), (9, 13), (16, 16)])
def test_k_zero_writes_zero(m, n):
    a, b = np.zeros((m, 0), np.int8), np.zeros((0, n), np.int8)
    c, s = run_gemm(a, b)
    assert s.error == 0 and s.read_vectors == 0
    assert np.array_equal(c, np.zeros((m, n), np.int32))


def test_tile_must_be_a_power_of_two():
    with pytest.raises(AssertionError):
        Params(tile=12)
    Params(tile=16)
