// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal driver for the Tri-Queue matrix core (docs/specification.md 5.3, 6.1, 6.2, 6.4).
// Freestanding C99: needs only <stdint.h> and <stddef.h>, no allocation, no libc.
//
// A GEMM C[m][n] (int32) = A[m][k] x B[k][n] (int8 or uint8) takes four steps:
//   1. tq_probe()            read ID and HW_CONFIG (tile T, port width W, program buffer size)
//   2. tq_pack_a/b()         pack A and B into the core's panel layout in its SRAM (spec 5.3)
//   3. tq_gemm_program()     build the 50-byte program: 8 CFG + the 10-byte kernel (spec 6.2)
//   4. tq_run()              load the program, START, wait for DONE (polling)
// C rows are tq_c_ld() bytes apart (padded to whole port words). The addresses given to the
// core are byte offsets in its memory port's address space; A, B and C bases and the C row
// stride must be multiples of W (the core stops with TQ_ERR_ALIGN otherwise).
#ifndef TQ_H
#define TQ_H

#include <stddef.h>
#include <stdint.h>

// ---- register map (spec 6.4) ----
#define TQ_REG_ID          0x000u
#define TQ_REG_HW_CONFIG   0x004u
#define TQ_REG_CTRL        0x008u
#define TQ_REG_STATUS      0x00Cu
#define TQ_REG_IRQ_EN      0x010u
#define TQ_REG_IRQ_STATUS  0x014u
#define TQ_REG_PERF_CYCLES 0x020u
#define TQ_REG_PERF_INSTRET 0x024u
#define TQ_REG_PERF_BEATS  0x028u
#define TQ_REG_PERF_READS  0x02Cu
#define TQ_REG_PERF_WRITES 0x030u
#define TQ_REG_CFG(i)      (0x040u + 4u * (unsigned)(i))
#define TQ_REG_PROG        0x100u

#define TQ_ID              0x54510001u
#define TQ_CTRL_START      0x1u
#define TQ_CTRL_ABORT      0x2u
#define TQ_STATUS_BUSY     0x1u
#define TQ_STATUS_DONE     0x2u
#define TQ_STATUS_ERROR    0x4u
#define TQ_STATUS_BUS_ERR  0x8u   // an interconnect error response during the run (AXI SLVERR / DECERR)

// STATUS[11:8] error codes
enum tq_err {
    TQ_OK = 0,
    TQ_ERR_OPCODE = 1,     // undefined opcode
    TQ_ERR_CFG_REG = 2,    // CFG to a register >= 8
    TQ_ERR_LOOP = 3,       // loop stack overflow or nested LOOP_T
    TQ_ERR_CFG_LOOP = 4,   // CFG inside a loop body
    TQ_ERR_PC = 5,         // instruction past the program buffer
    TQ_ERR_ALIGN = 6,      // misaligned base or C_LD
    // driver-side errors (not from the hardware)
    TQ_ERR_NO_DEVICE = 16, // ID does not match
    TQ_ERR_PROG_SIZE = 17, // program does not fit the program buffer
    TQ_ERR_TIMEOUT = 18,   // tq_run gave up polling
    TQ_ERR_ARG = 19,       // invalid argument
    TQ_ERR_BUS = 20,       // the run finished, but a memory access got an error response
};

// MODE bits (CFG register 7)
#define TQ_MODE_A_SIGNED 0x1u
#define TQ_MODE_B_SIGNED 0x2u

// Size of the program tq_gemm_program builds: 8 x CFG (5 bytes) + 10-byte kernel.
#define TQ_GEMM_PROGRAM_BYTES 50u

typedef struct {
    uint32_t tile;          // T: T x T output tile, T*T MACs per cycle
    uint32_t port_bytes;    // W = 2T: memory port width
    uint32_t fifo_depth;    // operand FIFO depth in port words
    uint32_t macq_depth;    // MAC command queue depth
    uint32_t shared_port;   // 1: drain writes share the read port
    uint32_t prog_bytes;    // program buffer size
} tq_info;

typedef struct {
    uint32_t m, n, k;
    uint32_t a_base, b_base, c_base;   // byte addresses in the core's memory space, multiples of W
    uint32_t c_ld;                      // C row stride in bytes (tq_c_ld), a multiple of W
    uint32_t mode;                      // TQ_MODE_* bits
} tq_gemm;

typedef struct {
    uint32_t cycles, instret, beats, reads, writes;
} tq_perf;

// ---- device access (base = the register port's address in the host's address space) ----
int tq_probe(volatile uint32_t *base, tq_info *info);
// Loads `prog` (len bytes), starts it and polls STATUS until DONE. max_polls = 0 polls forever.
// Returns TQ_OK or a tq_err code: the core's error code first, else TQ_ERR_BUS for a bus error.
int tq_run(volatile uint32_t *base, const tq_info *info, const uint8_t *prog, size_t len,
           uint32_t max_polls);
void tq_read_perf(volatile uint32_t *base, tq_perf *perf);

// ---- host-side helpers (no device access) ----
uint32_t tq_kp(uint32_t k);                                  // K padded to a multiple of 8
uint32_t tq_c_ld(uint32_t n, uint32_t port_bytes);           // C row stride: 4n up to whole port words
size_t tq_packed_a_bytes(uint32_t m, uint32_t k, uint32_t tile);
size_t tq_packed_b_bytes(uint32_t k, uint32_t n, uint32_t tile);
// A (m x k, row stride lda bytes) -> column panels, B (k x n, row stride ldb bytes) -> row panels.
// int8 and uint8 pack the same way (bytes); dst must hold tq_packed_*_bytes().
void tq_pack_a(uint8_t *dst, const uint8_t *a, uint32_t m, uint32_t k, size_t lda, uint32_t tile);
void tq_pack_b(uint8_t *dst, const uint8_t *b, uint32_t k, uint32_t n, size_t ldb, uint32_t tile);
// Writes the GEMM program into out (cap bytes). Returns its size, or 0 if cap is too small.
size_t tq_gemm_program(uint8_t *out, size_t cap, const tq_gemm *g);

#endif
