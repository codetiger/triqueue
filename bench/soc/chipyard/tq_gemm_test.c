// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal GEMM on the Tri-Queue in a Chipyard Rocket SoC (TriQueueRocketConfig): packs A and
// B with the driver (sw/tq.c), runs C = A x B on the core, checks C against the CPU, and prints
// the core's performance counters. The core reaches A, B and C through the coherent front bus,
// so no cache maintenance is needed.
#include <stdint.h>
#include <stdio.h>

#include "tq.h"

#ifndef TQ_BASE
#define TQ_BASE 0x10040000u
#endif

#define M 64
#define N 64
#define K 64
#define TILE_MAX 32

static int8_t a[M][K], b[K][N];
static int32_t ref[M][N];
// packed operands and C, aligned to any port width (W = 2T <= 64 bytes)
static uint8_t pa[(M + TILE_MAX) * ((K + 7) / 8 * 8)] __attribute__((aligned(64)));
static uint8_t pb[(N + TILE_MAX) * ((K + 7) / 8 * 8)] __attribute__((aligned(64)));
static int32_t c[M * (N + 16)] __attribute__((aligned(64)));

static uint32_t lcg = 12345;
static int8_t rnd(void) {
    lcg = lcg * 1103515245u + 12345u;
    return (int8_t)(lcg >> 16);
}

int main(void) {
    volatile uint32_t *tq = (volatile uint32_t *)(uintptr_t)TQ_BASE;
    tq_info info;
    if (tq_probe(tq, &info) != TQ_OK) {
        printf("tq: no device at %#x\n", TQ_BASE);
        return 1;
    }
    printf("tq: T=%u W=%u fifo=%u macq=%u prog=%u bytes\n", (unsigned)info.tile, (unsigned)info.port_bytes,
           (unsigned)info.fifo_depth, (unsigned)info.macq_depth, (unsigned)info.prog_bytes);

    for (int i = 0; i < M; i++)
        for (int k = 0; k < K; k++) a[i][k] = rnd();
    for (int k = 0; k < K; k++)
        for (int j = 0; j < N; j++) b[k][j] = rnd();
    for (int i = 0; i < M; i++)
        for (int j = 0; j < N; j++) {
            int32_t s = 0;
            for (int k = 0; k < K; k++) s += (int32_t)a[i][k] * b[k][j];
            ref[i][j] = s;
        }

    tq_pack_a(pa, (const uint8_t *)a, M, K, K, info.tile);
    tq_pack_b(pb, (const uint8_t *)b, K, N, N, info.tile);
    tq_gemm g = {.m = M, .n = N, .k = K,
                 .a_base = (uint32_t)(uintptr_t)pa, .b_base = (uint32_t)(uintptr_t)pb,
                 .c_base = (uint32_t)(uintptr_t)c, .c_ld = tq_c_ld(N, info.port_bytes),
                 .mode = TQ_MODE_A_SIGNED | TQ_MODE_B_SIGNED};
    uint8_t prog[TQ_GEMM_PROGRAM_BYTES];
    size_t len = tq_gemm_program(prog, sizeof prog, &g);
    int rc = tq_run(tq, &info, prog, len, 0);
    tq_perf perf;
    tq_read_perf(tq, &perf);
    printf("tq: run rc=%d cycles=%u beats=%u reads=%u writes=%u\n", rc, (unsigned)perf.cycles,
           (unsigned)perf.beats, (unsigned)perf.reads, (unsigned)perf.writes);

    int bad = 0;
    for (int i = 0; i < M; i++)
        for (int j = 0; j < N; j++) {
            int32_t v = c[i * (g.c_ld / 4) + j];
            if (v != ref[i][j] && bad++ < 5)
                printf("tq: C[%d][%d] = %d, expected %d\n", i, j, (int)v, (int)ref[i][j]);
        }
    unsigned macs = (unsigned)M * N * K, peak = info.tile * info.tile;
    printf("tq: %s, %d mismatches, utilization %u.%u%% of %u MACs/cycle\n", bad || rc ? "FAIL" : "PASS", bad,
           100u * macs / (peak * perf.cycles), (1000u * macs / (peak * perf.cycles)) % 10, peak);
    return bad || rc;
}
