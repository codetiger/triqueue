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

    opcode            the kernel with FENCE replaced by an undefined opcode   -> ERR_OPCODE
    reserved_arg      a NOP with a non-zero argument nibble                   -> ERR_OPCODE
    reserved_vouter   a VOUTER_MAC with reserved bit arg[2] set               -> ERR_OPCODE
    reserved_zero_c, reserved_stream_a, reserved_stream_b, reserved_swap_c, reserved_fence
                      the kernel with that instruction's argument nibble = 1 -> ERR_OPCODE
                      (SWAP_C in the first tile's body, FENCE at the end)
    cfg_reg           a CFG to register 10 first                              -> ERR_CFG_REG
    cfg_loop          a CFG inside LOOP_T's body                              -> ERR_CFG_LOOP
    cfg_val_k         a CFG K above the supported range                       -> ERR_CFG_VAL
    cfg_val_m         a CFG M above 65,535 tiles                              -> ERR_CFG_VAL
    cfg_val_mode      a CFG MODE with non-zero reserved bits [31:11]          -> ERR_CFG_VAL
    cfg_val_cld       a CFG C_LD narrower than the row width                  -> ERR_CFG_VAL
    walk_after_stream a CFG MODE that flips MODE[8] after a STREAM_A          -> ERR_CFG_VAL
    walk_before_stream the same flip before any STREAM: legal, C is right (column walk)
    loop_depth        three nested LOOP_K                                     -> ERR_LOOP
    loop_zero_body    a LOOP_K with a zero-byte body (arg = 0)                -> ERR_LOOP
    loop_nest_over    an inner LOOP_K whose body extends past outer LOOP_T    -> ERR_LOOP
    second_loop_t     a LOOP_T after the kernel's tile loop                   -> ERR_LOOP
    pc                the kernel without FENCE (runs into the NOP padding)    -> ERR_PC
    pc_skip_loop      a skipped LOOP_K(15) at the end of PROG_BYTES           -> ERR_PC
    operand_leftover  STREAM_A followed by FENCE with no VOUTER_MAC           -> ERR_OPERAND
    operand_starved   VOUTER_MAC followed by FENCE with no STREAM_A/B         -> ERR_OPERAND
    zero_in_drain     ZERO_C right after SWAP_C in every tile: waits for the drain, C is right
    loop_t_in_k       LOOP_T nested in a LOOP_K that runs once, both ending at the same byte: legal
                      when the first tile's LOOP_K count is 1 (a 4x4 tile, K <= 8 at T = 8); C is right
    cfg_after_swap    CFG C_BASE after SWAP_C: waits for queued work, C is right at original C_BASE
    explicit_8x8      kernel with explicit 8x8x1 VOUTER_MAC instead of AUTO   -> ok (when tiles are full)
    explicit_8x4      kernel with explicit 8x4x2 VOUTER_MAC                   -> ok (when tiles are 8x4)
    explicit_4x8      kernel with explicit 4x8x2 VOUTER_MAC                   -> ok (when tiles are 4x8)
    explicit_4x4      kernel with explicit 4x4x4 VOUTER_MAC                   -> ok (when tiles are 4x4)
    """
    kernel = isa.gemm_kernel()
    body = kernel[:-1]                                     # without FENCE
    tile_loop = kernel[4:9]                                # STREAM_A STREAM_B LOOP_K VOUTER SWAP_C

    def explicit_kernel(r: int, c: int) -> bytes:
        return (kernel[:7] + isa.vouter((r, c), 8) + kernel[8:])

    programs = {
        "opcode": cfg + body + E(0xA),
        "reserved_arg": cfg + E(isa.NOP, 1) + kernel,
        "reserved_vouter": cfg + E(isa.ZERO_C) + E(isa.STREAM_A) + E(isa.STREAM_B) + E(isa.VOUTER_MAC, 0x4) + E(isa.FENCE),
        "reserved_zero_c": cfg + E(isa.ZERO_C, 1) + kernel[1:],
        "reserved_stream_a": cfg + kernel[:1] + E(isa.STREAM_A, 1) + kernel[2:],
        "reserved_stream_b": cfg + kernel[:2] + E(isa.STREAM_B, 1) + kernel[3:],
        "reserved_swap_c": cfg + kernel[:8] + E(isa.SWAP_C, 1) + kernel[9:],
        "reserved_fence": cfg + body + E(isa.FENCE, 1),
        "cfg_reg": E(isa.CFG, 10) + (0).to_bytes(4, "little") + cfg + kernel,
        "cfg_loop": cfg + E(isa.ZERO_C) + E(isa.LOOP_T, 5) + isa.cfg(isa.K, 8) + E(isa.FENCE),
        "cfg_val_k": cfg[:20] + isa.cfg(isa.K, 0xFFFF_FFF8) + cfg[25:] + kernel,
        "cfg_val_m": cfg[:25] + isa.cfg(isa.M, 0x10_0000) + cfg[30:] + kernel,
        "cfg_val_mode": cfg[:35] + isa.cfg(isa.MODE, 0x800) + kernel,
        "cfg_val_cld": cfg[:15] + isa.cfg(isa.C_LD, 16) + cfg[20:] + kernel,
        "walk_after_stream": cfg + E(isa.ZERO_C) + E(isa.STREAM_A)
                             + isa.cfg(isa.MODE, int.from_bytes(cfg[36:40], "little") ^ isa.MODE_WALK_COLS)
                             + E(isa.FENCE),
        "walk_before_stream": cfg + isa.cfg(isa.MODE, int.from_bytes(cfg[36:40], "little") ^ isa.MODE_WALK_COLS)
                              + kernel,
        "loop_depth": cfg + E(isa.LOOP_K, 3) + E(isa.LOOP_K, 2) + E(isa.LOOP_K, 1) + E(isa.NOP) + E(isa.FENCE),
        "loop_zero_body": cfg + E(isa.LOOP_K, 0) + E(isa.FENCE),
        "loop_nest_over": cfg + E(isa.LOOP_T, 2) + E(isa.LOOP_K, 3) + E(isa.NOP) * 3 + E(isa.FENCE),
        "second_loop_t": cfg + body + E(isa.LOOP_T, 1) + E(isa.NOP) + E(isa.FENCE),
        "pc": cfg + body,
        "pc_skip_loop": isa.cfg(isa.K, 0) + E(isa.NOP) * 58 + E(isa.LOOP_K, 15),
        "operand_leftover": cfg + E(isa.STREAM_A) + E(isa.FENCE),
        "operand_starved": cfg + E(isa.ZERO_C) + isa.vouter() + E(isa.FENCE),
        "zero_in_drain": cfg + kernel[:3] + E(isa.LOOP_T, 6) + tile_loop + E(isa.ZERO_C) + E(isa.FENCE),
        "cfg_after_swap": cfg + body + isa.cfg(isa.C_BASE, 0x1000) + E(isa.FENCE),
        "loop_t_in_k": cfg + E(isa.ZERO_C) + E(isa.LOOP_K, 5) + E(isa.LOOP_T, 4) + E(isa.STREAM_A)
                       + E(isa.STREAM_B) + isa.vouter() + E(isa.SWAP_C) + E(isa.FENCE),
        "explicit_8x8": cfg + explicit_kernel(8, 8),
        "explicit_8x4": cfg + explicit_kernel(8, 4),
        "explicit_4x8": cfg + explicit_kernel(4, 8),
        "explicit_4x4": cfg + explicit_kernel(4, 4),
    }
    return programs[name]


EXPECTED_ERROR = {
    "opcode": isa.ERR_OPCODE,
    "reserved_arg": isa.ERR_OPCODE,
    "reserved_vouter": isa.ERR_OPCODE,
    "reserved_zero_c": isa.ERR_OPCODE,
    "reserved_stream_a": isa.ERR_OPCODE,
    "reserved_stream_b": isa.ERR_OPCODE,
    "reserved_swap_c": isa.ERR_OPCODE,
    "reserved_fence": isa.ERR_OPCODE,
    "cfg_reg": isa.ERR_CFG_REG,
    "cfg_loop": isa.ERR_CFG_LOOP,
    "cfg_val_k": isa.ERR_CFG_VAL,
    "cfg_val_m": isa.ERR_CFG_VAL,
    "cfg_val_mode": isa.ERR_CFG_VAL,
    "cfg_val_cld": isa.ERR_CFG_VAL,
    "walk_after_stream": isa.ERR_CFG_VAL,
    "walk_before_stream": 0,
    "loop_depth": isa.ERR_LOOP,
    "loop_zero_body": isa.ERR_LOOP,
    "loop_nest_over": isa.ERR_LOOP,
    "second_loop_t": isa.ERR_LOOP,
    "pc": isa.ERR_PC,
    "pc_skip_loop": isa.ERR_PC,
    "operand_leftover": isa.ERR_OPERAND,
    "operand_starved": isa.ERR_OPERAND,
    "zero_in_drain": 0,
    "cfg_after_swap": 0,
    "loop_t_in_k": 0,
    "explicit_8x8": 0,
    "explicit_8x4": 0,
    "explicit_4x8": 0,
    "explicit_4x4": 0,
}
