// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal Tri-Queue driver. Reference implementations: model/tri_queue/isa.py (program),
// model/tri_queue/packing.py (packing), verif/tb/tq_tb.py (run sequence).
#include "tq.h"

#define REG(base, off) ((base)[(off) / 4u])

// ---- device access ----

int tq_probe(volatile uint32_t *base, tq_info *info) {
    if (REG(base, TQ_REG_ID) != TQ_ID)
        return TQ_ERR_NO_DEVICE;
    uint32_t hw = REG(base, TQ_REG_HW_CONFIG);
    info->tile = hw & 0xFFu;
    info->port_bytes = 2u * info->tile;
    info->fifo_depth = (hw >> 8) & 0xFFu;
    info->macq_depth = (hw >> 16) & 0xFu;
    info->shared_port = (hw >> 20) & 0x1u;
    info->prog_bytes = 4u * ((hw >> 24) & 0xFFu);
    return TQ_OK;
}

int tq_run(volatile uint32_t *base, const tq_info *info, const uint8_t *prog, size_t len,
           uint32_t max_polls) {
    if (len > info->prog_bytes)
        return TQ_ERR_PROG_SIZE;
    for (size_t i = 0; i < len; i += 4) {          // whole words, little-endian, zero (NOP) padded
        uint32_t w = 0;
        for (size_t j = 0; j < 4 && i + j < len; j++)
            w |= (uint32_t)prog[i + j] << (8u * j);
        REG(base, TQ_REG_PROG + i) = w;
    }
    REG(base, TQ_REG_CTRL) = TQ_CTRL_START;
    for (uint32_t n = 0; max_polls == 0 || n < max_polls; n++) {
        uint32_t st = REG(base, TQ_REG_STATUS);
        if (st & TQ_STATUS_DONE)
            return (st & TQ_STATUS_ERROR) ? (int)((st >> 8) & 0xFu) : TQ_OK;
    }
    REG(base, TQ_REG_CTRL) = TQ_CTRL_ABORT;
    return TQ_ERR_TIMEOUT;
}

void tq_read_perf(volatile uint32_t *base, tq_perf *perf) {
    perf->cycles = REG(base, TQ_REG_PERF_CYCLES);
    perf->instret = REG(base, TQ_REG_PERF_INSTRET);
    perf->beats = REG(base, TQ_REG_PERF_BEATS);
    perf->reads = REG(base, TQ_REG_PERF_READS);
    perf->writes = REG(base, TQ_REG_PERF_WRITES);
}

// ---- packing (spec 5.3) ----

uint32_t tq_kp(uint32_t k) { return (k + 7u) / 8u * 8u; }

uint32_t tq_c_ld(uint32_t n, uint32_t port_bytes) {
    return (4u * n + port_bytes - 1u) / port_bytes * port_bytes;
}

// A panel is T wide, or T/2 for a final panel with at most T/2 rows (cols) left.
static uint32_t panel_width(uint32_t left, uint32_t tile) { return left <= tile / 2u ? tile / 2u : tile; }

static size_t packed_bytes(uint32_t len, uint32_t k, uint32_t tile) {
    size_t total = 0;
    for (uint32_t p = 0; p < len; p += tile)
        total += (size_t)tq_kp(k) * panel_width(len - p, tile);
    return total;
}

size_t tq_packed_a_bytes(uint32_t m, uint32_t k, uint32_t tile) { return packed_bytes(m, k, tile); }
size_t tq_packed_b_bytes(uint32_t k, uint32_t n, uint32_t tile) { return packed_bytes(n, k, tile); }

// Panel of rows [p, p + w) of A, k-major: dst[kk * w + i] = A[p + i][kk], zero outside A.
void tq_pack_a(uint8_t *dst, const uint8_t *a, uint32_t m, uint32_t k, size_t lda, uint32_t tile) {
    uint32_t kpad = tq_kp(k);
    for (uint32_t p = 0; p < m; p += tile) {
        uint32_t w = panel_width(m - p, tile);
        for (uint32_t kk = 0; kk < kpad; kk++)
            for (uint32_t i = 0; i < w; i++)
                *dst++ = (kk < k && p + i < m) ? a[(size_t)(p + i) * lda + kk] : 0u;
    }
}

// Panel of columns [p, p + w) of B, k-major: dst[kk * w + j] = B[kk][p + j], zero outside B.
void tq_pack_b(uint8_t *dst, const uint8_t *b, uint32_t k, uint32_t n, size_t ldb, uint32_t tile) {
    uint32_t kpad = tq_kp(k);
    for (uint32_t p = 0; p < n; p += tile) {
        uint32_t w = panel_width(n - p, tile);
        for (uint32_t kk = 0; kk < kpad; kk++)
            for (uint32_t j = 0; j < w; j++)
                *dst++ = (kk < k && p + j < n) ? b[(size_t)kk * ldb + p + j] : 0u;
    }
}

// ---- program (spec 6.1, 6.2) ----

enum { OP_CFG = 0x1, OP_STREAM_A = 0x2, OP_STREAM_B = 0x3, OP_ZERO_C = 0x4, OP_VOUTER = 0x5,
       OP_SWAP_C = 0x6, OP_LOOP_K = 0x7, OP_LOOP_T = 0x8, OP_FENCE = 0x9 };
#define ENC(op, arg) ((uint8_t)(((op) << 4) | (arg)))
#define VOUTER_AUTO 0x8

size_t tq_gemm_program(uint8_t *out, size_t cap, const tq_gemm *g) {
    static const uint8_t kernel[10] = {
        ENC(OP_ZERO_C, 0),
        ENC(OP_STREAM_A, 0), ENC(OP_STREAM_B, 0),   // prime: tile 0 panels
        ENC(OP_LOOP_T, 5),                          // for each output tile (5-byte body):
        ENC(OP_STREAM_A, 0), ENC(OP_STREAM_B, 0),   //   prefetch the next tile's panels
        ENC(OP_LOOP_K, 1),                          //   Kp / 2d times:
        ENC(OP_VOUTER, VOUTER_AUTO),                //     VOUTER_MAC AUTO
        ENC(OP_SWAP_C, 0),                          //   drain the finished tile in the background
        ENC(OP_FENCE, 0),
    };
    const uint32_t cfg[8] = {g->a_base, g->b_base, g->c_base, g->c_ld, g->k, g->m, g->n, g->mode};
    if (cap < TQ_GEMM_PROGRAM_BYTES)
        return 0;
    size_t pc = 0;
    for (uint32_t r = 0; r < 8; r++) {
        out[pc++] = ENC(OP_CFG, r);
        for (uint32_t b = 0; b < 4; b++)
            out[pc++] = (uint8_t)(cfg[r] >> (8u * b));
    }
    for (size_t i = 0; i < sizeof kernel; i++)
        out[pc++] = kernel[i];
    return pc;
}
