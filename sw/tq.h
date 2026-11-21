// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal driver for the Tri-Queue matrix core (docs/specification.md 5.3, 6.1, 6.2, 6.4).
// Freestanding C99: needs only <stdint.h> and <stddef.h>, no allocation, no libc.
//
// A GEMM C[m][n] (int32) = A[m][k] x B[k][n] (int8 or uint8) takes four steps:
//   1. tq_probe()            read ID, HW_CONFIG and HW_CONFIG2 (tile T, port width W, program
//                            buffer size, build options, features)
//   2. tq_pack_a/b()         pack A and B into the core's panel layout in its SRAM (spec 5.3)
//   3. tq_gemm_program()     build the 50-byte program: 8 CFG + the 10-byte kernel (spec 6.2)
//   4. tq_run()              load the program, START, wait for DONE (polling)
// Set TQ_MODE_OUT_INT8 | TQ_MODE_OUT_SHIFT(s) to write C as saturating int8 and use
// tq_c_ld_int8(). For an int8 layer with TFLite requantization (MODE[9], v1.1) use the int8
// layer API (tq_layer_*) or, low level, TQ_MODE_EPI with tq_pack_epi() and tq_epi_out().
// Registers are memory-mapped at `base`; with -DTQ_XIF they are reached with the CV-X-IF
// instructions tq.wr / tq.rd (rtl/adapters/tq_xif_regs.sv) and `base` is ignored.
// C rows are tq_c_ld() bytes apart (padded to whole port words). The addresses given to the
// core are byte offsets in its memory port's address space; A, B and C bases and the C row
// stride must be multiples of W (the core stops with TQ_ERR_ALIGN otherwise).
#ifndef TRIQUEUE_TQ_H
#define TRIQUEUE_TQ_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// ---- host memory barriers (override before including tq.h if needed) ----
#ifndef TQ_WMB
#if defined(__riscv)
#define TQ_WMB() __asm__ volatile("fence ow, ow" ::: "memory")
#elif defined(__GNUC__) || defined(__clang__)
#define TQ_WMB() __asm__ volatile("" ::: "memory")
#else
#define TQ_WMB() ((void)0)
#endif
#endif

#ifndef TQ_RMB
#if defined(__riscv)
#define TQ_RMB() __asm__ volatile("fence ir, ir" ::: "memory")
#elif defined(__GNUC__) || defined(__clang__)
#define TQ_RMB() __asm__ volatile("" ::: "memory")
#else
#define TQ_RMB() ((void)0)
#endif
#endif

// ---- register map (spec 6.4) ----
#define TQ_REG_ID          0x000u
#define TQ_REG_HW_CONFIG   0x004u
#define TQ_REG_CTRL        0x008u
#define TQ_REG_STATUS      0x00Cu
#define TQ_REG_IRQ_EN      0x010u
#define TQ_REG_IRQ_STATUS  0x014u
#define TQ_REG_HW_CONFIG2  0x018u
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
#define TQ_STATUS_PENDING  0x10u  // busy, or reads/writes/pipeline still draining after ABORT or error

// STATUS[11:8] error codes
enum tq_err {
    TQ_OK = 0,
    TQ_ERR_OPCODE = 1,     // undefined opcode or non-zero reserved argument bits
    TQ_ERR_CFG_REG = 2,    // CFG to a register >= 10
    TQ_ERR_LOOP = 3,       // loop stack overflow, zero body, inner past outer, or nested LOOP_T
    TQ_ERR_CFG_LOOP = 4,   // CFG inside a loop body
    TQ_ERR_PC = 5,         // instruction past the program buffer
    TQ_ERR_ALIGN = 6,      // misaligned base or C_LD
    TQ_ERR_CFG_VAL = 7,    // invalid CFG value (K, M, N, MODE or C_LD < row bytes)
    TQ_ERR_OPERAND = 8,    // leftover or starved operand FIFOs
    // driver-side errors (not from the hardware)
    TQ_ERR_NO_DEVICE = 16, // ID does not match
    TQ_ERR_PROG_SIZE = 17, // program does not fit the program buffer
    TQ_ERR_TIMEOUT = 18,   // tq_run gave up polling
    TQ_ERR_ARG = 19,       // invalid argument
    TQ_ERR_BUS = 20,       // the run finished, but a memory access got an error response
    TQ_ERR_BUSY = 21,      // device was busy or had pending transactions at tq_run
    TQ_ERR_UNSUPPORTED = 22, // the core lacks a feature the call needs (HW_CONFIG2)
};

// MODE bits (CFG register 7)
#define TQ_MODE_A_SIGNED     0x1u
#define TQ_MODE_B_SIGNED     0x2u
#define TQ_MODE_OUT_INT8     0x4u
#define TQ_MODE_OUT_SHIFT(s) (((uint32_t)(s) & 0x1Fu) << 3)
#define TQ_MODE_WALK_COLS    0x100u  // v1.1: LOOP_T walks down tile columns (panel buffer keeps B)
#define TQ_MODE_EPI          0x200u  // v1.1: TFLite epilogue (needs TQ_MODE_OUT_INT8; CFG 8, 9)
#define TQ_MODE_ROUND1       0x400u  // v1.1: epilogue single rounding, TFLITE_SINGLE_ROUNDING (needs TQ_MODE_EPI)

// Size of the program tq_gemm_program builds: 8 x CFG (5 bytes) + 10-byte kernel, and with
// TQ_MODE_EPI 10 x CFG + kernel.
#define TQ_GEMM_PROGRAM_BYTES 50u
#define TQ_GEMM_EPI_PROGRAM_BYTES 60u
#define TQ_EPI_WORDS 5u               // port words per tile column of the epilogue table

typedef struct {
    uint32_t tile;          // T: T x T output tile, T*T MACs per cycle
    uint32_t port_bytes;    // W = 2T: memory port width
    uint32_t fifo_depth;    // operand FIFO depth in port words
    uint32_t macq_depth;    // MAC command queue depth
    uint32_t shared_port;   // 1: drain writes share the read port
    uint32_t prog_bytes;    // program buffer size
    // HW_CONFIG2 (all 0 on a core before v1.1, which has none of the features)
    uint32_t walk_cols;     // MODE[8] column-major tile walk
    uint32_t epilogue;      // MODE[9] TFLite int8 epilogue (CFG 8 EPI_BASE, CFG 9 EPI_OUT)
    uint32_t epi_lanes;     // epilogue lanes (T / lanes cycles per output row); 0 without epilogue
    uint32_t perf;          // 1: PERF_* counters count (0: they read 0)
    uint32_t wr_ack;        // 1: writes complete at their response (WR_ACK, e.g. behind AXI)
    uint32_t wr_out_max;    // outstanding writes with wr_ack
    uint32_t acc_csa;       // 1: carry-save accumulator (ACC_FORM); same results
    uint32_t mac_pipe;      // MAC pipeline stages (1 or 2; same results)
    uint32_t panel_bytes;   // panel buffer size (spec 10.9), 0 = none
    uint32_t round1;        // MODE[10] epilogue single rounding (TQ_ROUND_SINGLE)
} tq_info;

typedef struct {
    uint32_t m, n, k;
    uint32_t a_base, b_base, c_base;   // byte addresses in the core's memory space, multiples of W
    uint32_t c_ld;                      // C row stride in bytes (tq_c_ld), a multiple of W
    uint32_t mode;                      // TQ_MODE_* bits
    uint32_t epi_base;                  // with TQ_MODE_EPI: table address (tq_pack_epi), a multiple of W
    uint32_t epi_out;                   // with TQ_MODE_EPI: tq_epi_out(zp_out, act_min, act_max)
} tq_gemm;

// TFLite int8 requantization of each output column j (spec 10.1):
//   out = clamp(MultiplyByQuantizedMultiplier(acc + bias[j], m0[j], shift[j]) + zp_out, act_min, act_max)
// m0 / shift are TFLite's per-channel output_multiplier / output_shift (> 0 left); the core clamps
// shift to [-31, 30]. Fold the input zero point into bias with tq_fold_input_zp.
// rounding: TQ_ROUND_DOUBLE is TFLite's default MultiplyByQuantizedMultiplier (CMSIS-NN, TFLite
// Micro, TFLite's reference CONV_2D); TQ_ROUND_SINGLE is the TFLITE_SINGLE_ROUNDING form (MODE[10],
// tq_info.round1), which LiteRT's reference int8 FULLY_CONNECTED uses. Match the kernel to be
// bit-exact with.
#define TQ_ROUND_DOUBLE 0u
#define TQ_ROUND_SINGLE 1u
typedef struct {
    const int32_t *bias;    // n values, or NULL for 0
    const int32_t *m0;      // n values
    const int32_t *shift;   // n values
    int8_t zp_out, act_min, act_max;
    uint8_t rounding;       // TQ_ROUND_DOUBLE or TQ_ROUND_SINGLE
} tq_requant;

typedef struct {
    uint32_t cycles, instret, beats, reads, writes;
} tq_perf;

// Optional cache-maintenance callbacks for non-coherent DMA workspaces:
//   clean(ptr, len)      called at the end of tq_gemm_prepare on packed A and B
//   invalidate(ptr, len) called at the start of tq_gemm_finish on padded C
typedef void (*tq_cache_op_fn)(const void *ptr, size_t len);
void tq_set_cache_ops(tq_cache_op_fn clean, tq_cache_op_fn invalidate);

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
uint32_t tq_c_ld_int8(uint32_t n, uint32_t port_bytes);      // int8 C row stride: n up to whole port words
size_t tq_packed_a_bytes(uint32_t m, uint32_t k, uint32_t tile);
size_t tq_packed_b_bytes(uint32_t k, uint32_t n, uint32_t tile);
// A (m x k, row stride lda bytes) -> column panels, B (k x n, row stride ldb bytes) -> row panels.
// int8 and uint8 pack the same way (bytes); dst must hold tq_packed_*_bytes().
void tq_pack_a(uint8_t *dst, const uint8_t *a, uint32_t m, uint32_t k, size_t lda, uint32_t tile);
void tq_pack_b(uint8_t *dst, const uint8_t *b, uint32_t k, uint32_t n, size_t ldb, uint32_t tile);
// Writes the GEMM program into out (cap bytes). Returns its size, or 0 if cap is too small.
// With TQ_MODE_EPI it also writes CFG 8 (epi_base) and 9 (epi_out): TQ_GEMM_EPI_PROGRAM_BYTES.
size_t tq_gemm_program(uint8_t *out, size_t cap, const tq_gemm *g);
// Epilogue (v1.1): the parameter table for n output columns (per tile column: T x int32 bias,
// T x int32 m0, T x int8 shift, zero-padded to TQ_EPI_WORDS port words), and the EPI_OUT word.
size_t tq_epi_table_bytes(uint32_t n, uint32_t tile);
void tq_pack_epi(uint8_t *dst, const tq_requant *rq, uint32_t n, uint32_t tile);
uint32_t tq_epi_out(int8_t zp_out, int8_t act_min, int8_t act_max);
// bias_out[j] = bias[j] - zp_in * sum_k B[k][j] (int32, wrapping): TFLite's input offset folded into
// the bias, since the core multiplies the raw int8 A. bias may be NULL (0); bias_out may alias bias.
void tq_fold_input_zp(int32_t *bias_out, const int32_t *bias, const int8_t *b, size_t ldb,
                      uint32_t k, uint32_t n, int32_t zp_in);


// ---- GEMM API: C[m][n] = A[m][k] x B[k][n] in one call ----
// The workspace is core-visible memory (the core's memory port reaches it at ws_addr, the host at
// ws) holding packed A, packed B and the core's padded C. Size: tq_gemm_workspace_bytes().
typedef struct {
    uint32_t a_off, b_off, c_off;        // byte offsets in the workspace (multiples of 64)
    uint32_t c_ld;                       // the core's C row stride in bytes
    uint32_t prog_len;
    uint8_t prog[TQ_GEMM_EPI_PROGRAM_BYTES];
    uint32_t epi_off;                    // int8 layer API: the epilogue table's offset
} tq_gemm_plan;

size_t tq_gemm_workspace_bytes(uint32_t m, uint32_t n, uint32_t k, uint32_t tile);
// Packs A (row stride lda bytes) and B (ldb) into the workspace and builds the program.
// a_signed / b_signed: int8 (1) or uint8 (0). Returns TQ_OK or TQ_ERR_ARG.
int tq_gemm_prepare(tq_gemm_plan *plan, uint8_t *ws, uint32_t ws_addr, const tq_info *info,
                    const uint8_t *a, size_t lda, const uint8_t *b, size_t ldb,
                    uint32_t m, uint32_t n, uint32_t k, int a_signed, int b_signed);
// Copies C from the workspace to c (row stride ldc int32 elements).
void tq_gemm_finish(const tq_gemm_plan *plan, const uint8_t *ws, int32_t *c, size_t ldc,
                    uint32_t m, uint32_t n);
// tq_gemm_run: prepare + tq_run + finish. Returns TQ_OK or a tq_err code.
// tq_gemm_run_timeout: the same, with tq_run's max_polls (0 = poll forever).
int tq_gemm_run(volatile uint32_t *base, const tq_info *info, uint8_t *ws, uint32_t ws_addr,
            const uint8_t *a, size_t lda, const uint8_t *b, size_t ldb, int32_t *c, size_t ldc,
            uint32_t m, uint32_t n, uint32_t k, int a_signed, int b_signed);
int tq_gemm_run_timeout(volatile uint32_t *base, const tq_info *info, uint8_t *ws, uint32_t ws_addr,
            const uint8_t *a, size_t lda, const uint8_t *b, size_t ldb, int32_t *c, size_t ldc,
            uint32_t m, uint32_t n, uint32_t k, int a_signed, int b_signed, uint32_t max_polls);

// ---- int8 layer API (v1.1): C[m][n] (int8) = requant(A[m][k] x B[k][n]), A and B int8 ----
// Needs a core with the epilogue (tq_info.epilogue, and tq_info.round1 for TQ_ROUND_SINGLE, else
// TQ_ERR_UNSUPPORTED) and a program buffer
// of at least TQ_GEMM_EPI_PROGRAM_BYTES. The workspace holds packed A, packed B, int8 C and the
// epilogue table. The tile walk is by columns (each column's parameters read once) when the core
// has it and A has more than one tile row; otherwise by rows (a GEMV keeps reusing its A panel).
size_t tq_layer_workspace_bytes(uint32_t m, uint32_t n, uint32_t k, uint32_t tile);
int tq_layer_prepare(tq_gemm_plan *plan, uint8_t *ws, uint32_t ws_addr, const tq_info *info,
                     const int8_t *a, size_t lda, const int8_t *b, size_t ldb,
                     uint32_t m, uint32_t n, uint32_t k, const tq_requant *rq);
// Copies the int8 C from the workspace to c (row stride ldc bytes).
void tq_layer_finish(const tq_gemm_plan *plan, const uint8_t *ws, int8_t *c, size_t ldc,
                     uint32_t m, uint32_t n);
// prepare + tq_run (max_polls, 0 = poll forever) + finish.
int tq_layer_run(volatile uint32_t *base, const tq_info *info, uint8_t *ws, uint32_t ws_addr,
                 const int8_t *a, size_t lda, const int8_t *b, size_t ldb, int8_t *c, size_t ldc,
                 uint32_t m, uint32_t n, uint32_t k, const tq_requant *rq, uint32_t max_polls);

#ifdef __cplusplus
}
#endif

#endif /* TRIQUEUE_TQ_H */
