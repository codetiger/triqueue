# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Tri-Queue ISA: opcodes, config registers, encoder and the reference GEMM kernel.

The ISA does not depend on the tile size T: VOUTER_MAC shapes are encoded as full (T) or half (T/2)
rows and columns, and the tile walker derives everything else from M, N, K.

Encoding: one byte = opcode[7:4] | arg[3:0]. CFG is followed by a 32-bit little-endian immediate.
"""

NOP, CFG, STREAM_A, STREAM_B, ZERO_C, VOUTER_MAC, SWAP_C, LOOP_K, LOOP_T, FENCE = range(10)
MNEMONIC = {
    NOP: "NOP", CFG: "CFG", STREAM_A: "STREAM_A", STREAM_B: "STREAM_B", ZERO_C: "ZERO_C",
    VOUTER_MAC: "VOUTER_MAC", SWAP_C: "SWAP_C", LOOP_K: "LOOP_K", LOOP_T: "LOOP_T", FENCE: "FENCE",
}

# Config registers
A_BASE, B_BASE, C_BASE, C_LD, K, M, N, MODE = range(8)
EPI_BASE, EPI_OUT = 8, 9     # epilogue parameter table address; {zp_out, act_min, act_max} (spec 10.1)
NUM_CFG = 10
EPI_WORDS = 5                # W-byte words per tile column of the table: T x (int32 bias, int32 M0, int8 shift)

# STATUS error codes (spec 6.4): the core stops in the cycle it finds one
ERR_NONE, ERR_OPCODE, ERR_CFG_REG, ERR_LOOP, ERR_CFG_LOOP, ERR_PC, ERR_ALIGN, ERR_CFG_VAL, ERR_OPERAND = range(9)
MODE_A_SIGNED = 0x1
MODE_B_SIGNED = 0x2
MODE_OUT_INT8 = 0x4
MODE_OUT_SHIFT = 3
MODE_WALK_COLS = 0x100       # LOOP_T walks down tile columns; the panel buffer keeps B (spec 6.1)
MODE_EPI = 0x200             # TFLite int8 epilogue in the drain (needs MODE_OUT_INT8; spec 10.1)
MODE_RESERVED_SHIFT = 10     # MODE[31:10] must be 0 (ERR_CFG_VAL)


def epi_out(zp_out: int, act_min: int = -128, act_max: int = 127) -> int:
    """CFG EPI_OUT: int8 output zero point and activation clamp, bits [31:24] reserved."""
    return (zp_out & 0xFF) | ((act_min & 0xFF) << 8) | ((act_max & 0xFF) << 16)


def mode_out_int8(shift: int = 0) -> int:
    """MODE bits for int8 output: clip(acc >> shift, -128, 127), 0 <= shift < 32."""
    assert 0 <= shift < 32
    return MODE_OUT_INT8 | (shift << MODE_OUT_SHIFT)

# VOUTER_MAC argument
AUTO = 0x8


def enc(op: int, arg: int = 0) -> bytes:
    assert 0 <= op < 16 and 0 <= arg < 16
    return bytes([(op << 4) | arg])


def cfg(reg: int, imm: int) -> bytes:
    return enc(CFG, reg) + (imm & 0xFFFFFFFF).to_bytes(4, "little")


def vouter(shape=None, tile: int = 8) -> bytes:
    """shape=None -> AUTO, else (r, c) with r, c in {tile/2, tile}. arg[1] = full rows, arg[0] = full cols."""
    if shape is None:
        return enc(VOUTER_MAC, AUTO)
    r, c = shape
    assert r in (tile // 2, tile) and c in (tile // 2, tile), (shape, tile)
    return enc(VOUTER_MAC, (2 if r == tile else 0) | (1 if c == tile else 0))


def gemm_kernel() -> bytes:
    """The complete GEMM kernel from the spec, section 6.2 (without CFG)."""
    return b"".join([
        enc(ZERO_C),
        enc(STREAM_A),        # prime: tile 0 A panel
        enc(STREAM_B),        # prime: tile 0 B panel
        enc(LOOP_T, 5),       # for each output tile:
        enc(STREAM_A),        #   prefetch next tile's A panel
        enc(STREAM_B),        #   prefetch next tile's B panel
        enc(LOOP_K, 1),       #   Kp / 2d times:
        vouter(),             #     VOUTER_MAC AUTO
        enc(SWAP_C),          #   drain finished tile in background
        enc(FENCE),
    ])


def gemm_config(a_base, b_base, c_base, c_ld, k, m, n, mode, epi_base=None, epi_out_word=None) -> bytes:
    regs = [a_base, b_base, c_base, c_ld, k, m, n, mode]
    if epi_base is not None:
        regs += [epi_base, epi_out_word]
    return b"".join(cfg(r, v) for r, v in enumerate(regs))


def decode(prog: bytes, pc: int):
    """Returns (op, arg, imm, size)."""
    b = prog[pc]
    op, arg = b >> 4, b & 0xF
    if op == CFG:
        return op, arg, int.from_bytes(prog[pc + 1:pc + 5], "little"), 5
    return op, arg, None, 1


def disassemble(prog: bytes, tile: int = 8) -> list[str]:
    out, pc = [], 0
    while pc < len(prog):
        op, arg, imm, size = decode(prog, pc)
        name = MNEMONIC.get(op, f"OP{op:X}")
        if op == CFG:
            out.append(f"{pc:04x}  {name} r{arg}, {imm:#x}")
        elif op == VOUTER_MAC:
            out.append(f"{pc:04x}  {name} {'AUTO' if arg & AUTO else f'{tile if arg & 2 else tile // 2}x{tile if arg & 1 else tile // 2}'}")
        elif op in (LOOP_K, LOOP_T):
            out.append(f"{pc:04x}  {name} {arg}")
        else:
            out.append(f"{pc:04x}  {name}")
        pc += size
    return out
