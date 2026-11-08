// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal GEMM on the Tri-Queue in a Chipyard Rocket SoC (TriQueueRocketConfig): runs
// C = A x B through the driver's GEMM API (sw/tq.c, tq_gemm_run: pack, program, run, unpack),
// checks C against the CPU, and prints the core's performance counters. The core reaches A, B
// and C through the coherent front bus, so no cache maintenance is needed.
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
static int32_t ref[M][N], c[M][N];
// workspace for packed A, packed B and the core's C (tq_gemm_workspace_bytes, any T <= TILE_MAX)
static uint8_t ws[2 * (M + TILE_MAX) * ((K + 7) / 8 * 8) + M * (4 * N + 4 * TILE_MAX) + 128]
    __attribute__((aligned(64)));

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

    if (tq_gemm_workspace_bytes(M, N, K, info.tile) > sizeof ws) {
        printf("tq: workspace too small for T=%u\n", (unsigned)info.tile);
        return 1;
    }
    int rc = tq_gemm_run(tq, &info, ws, (uint32_t)(uintptr_t)ws, (const uint8_t *)a, K,
                         (const uint8_t *)b, N, &c[0][0], N, M, N, K, 1, 1);
    tq_perf perf;
    tq_read_perf(tq, &perf);
    printf("tq: run rc=%d cycles=%u beats=%u reads=%u writes=%u\n", rc, (unsigned)perf.cycles,
           (unsigned)perf.beats, (unsigned)perf.reads, (unsigned)perf.writes);

    int bad = 0;
    for (int i = 0; i < M; i++)
        for (int j = 0; j < N; j++) {
            int32_t v = c[i][j];
            if (v != ref[i][j] && bad++ < 5)
                printf("tq: C[%d][%d] = %d, expected %d\n", i, j, (int)v, (int)ref[i][j]);
        }
    unsigned macs = (unsigned)M * N * K, peak = info.tile * info.tile;
    printf("tq: %s, %d mismatches, utilization %u.%u%% of %u MACs/cycle\n", bad || rc ? "FAIL" : "PASS", bad,
           100u * macs / (peak * perf.cycles), (1000u * macs / (peak * perf.cycles)) % 10, peak);
    return bad || rc;
}
