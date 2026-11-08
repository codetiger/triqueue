# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Edge-case and illegal programs, built from the GEMM program (isa.gemm_config + gemm_kernel).
Shared by the model tests (tests/test_errors.py) and the RTL regression (verif/), so both run
the same bytes.

The kernel is  ZERO_C STREAM_A STREAM_B LOOP_T(5) [STREAM_A STREAM_B LOOP_K(1) [VOUTER_MAC] SWAP_C] FENCE.
"""
from . import isa

E = isa.enc


def edge_program(name: str, cfg: bytes) -> bytes:
    """cfg: the 40 bytes of CFG instructions of a GEMM. Returns the program `name`:

    opcode         the kernel with FENCE replaced by an undefined opcode   -> ERR_OPCODE
    cfg_reg        a CFG to register 8 first                               -> ERR_CFG_REG
    cfg_loop       a CFG inside LOOP_T's body                              -> ERR_CFG_LOOP
    loop_depth     three nested LOOP_K                                     -> ERR_LOOP
    second_loop_t  a LOOP_T after the kernel's tile loop                   -> ERR_LOOP
    pc             the kernel without FENCE (runs into the NOP padding)    -> ERR_PC
    zero_in_drain  ZERO_C right after SWAP_C in every tile: waits for the drain, C is right
    """
    kernel = isa.gemm_kernel()
    body = kernel[:-1]                                     # without FENCE
    tile_loop = kernel[4:9]                                # STREAM_A STREAM_B LOOP_K VOUTER SWAP_C
    programs = {
        "opcode": cfg + body + E(0xA),
        "cfg_reg": E(isa.CFG, 8) + (0).to_bytes(4, "little") + cfg + kernel,
        "cfg_loop": cfg + E(isa.ZERO_C) + E(isa.LOOP_T, 5) + isa.cfg(isa.K, 8) + E(isa.FENCE),
        "loop_depth": cfg + E(isa.LOOP_K, 3) + E(isa.LOOP_K, 2) + E(isa.LOOP_K, 1) + E(isa.NOP) + E(isa.FENCE),
        "second_loop_t": cfg + body + E(isa.LOOP_T, 1) + E(isa.NOP) + E(isa.FENCE),
        "pc": cfg + body,
        "zero_in_drain": cfg + kernel[:3] + E(isa.LOOP_T, 6) + tile_loop + E(isa.ZERO_C) + E(isa.FENCE),
    }
    return programs[name]


EXPECTED_ERROR = {
    "opcode": isa.ERR_OPCODE, "cfg_reg": isa.ERR_CFG_REG, "cfg_loop": isa.ERR_CFG_LOOP,
    "loop_depth": isa.ERR_LOOP, "second_loop_t": isa.ERR_LOOP, "pc": isa.ERR_PC, "zero_in_drain": 0,
}
