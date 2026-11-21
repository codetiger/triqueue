// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal int8 layers on the Tri-Queue in a Chipyard Rocket SoC (TriQueueRocketConfig): real
// MLPerf Tiny DS-CNN layers (tq_layers.h, from export_layers.py) through the driver's int8 layer
// API (tq_layer_prepare: pack A and B and the epilogue table; tq_run; tq_layer_finish), with the
// requantization in the core's epilogue (MODE[9], and MODE[10] for the FULLY_CONNECTED layer's
// single rounding). Each output is compared with TFLite's own tensor, and the host's cycles for
// prepare and finish are printed next to the core's.
#include <stdint.h>
#include <stdio.h>

#include "tq.h"

#ifndef TQ_BASE
#define TQ_BASE 0x10040000u
#endif

typedef struct {
    const char *name;
    uint32_t m, n, k;
    const int8_t *a, *b, *gold;
    tq_requant rq;
} tq_soc_layer;

#include "tq_layers.h"

#define NLAYERS (sizeof layers / sizeof layers[0])

static uint8_t ws[64 * 1024] __attribute__((aligned(64)));
static int8_t c[128 * 64];

static inline uint64_t rdcycle(void) {
    uint64_t x;
    __asm__ volatile("rdcycle %0" : "=r"(x));
    return x;
}

static int run_layer(volatile uint32_t *tq, const tq_info *info, const tq_soc_layer *l) {
    if (tq_layer_workspace_bytes(l->m, l->n, l->k, info->tile) > sizeof ws || (size_t)l->m * l->n > sizeof c) {
        printf("tq: %s: buffers too small\n", l->name);
        return 1;
    }
    tq_gemm_plan plan;
    uint64_t t0 = rdcycle();
    int rc = tq_layer_prepare(&plan, ws, (uint32_t)(uintptr_t)ws, info, l->a, l->k, l->b, l->n, l->m, l->n, l->k,
                              &l->rq);
    uint64_t t1 = rdcycle();
    if (rc == TQ_OK) rc = tq_run(tq, info, plan.prog, plan.prog_len, 0);
    uint64_t t2 = rdcycle();
    if (rc == TQ_OK) tq_layer_finish(&plan, ws, c, l->n, l->m, l->n);
    uint64_t t3 = rdcycle();
    tq_perf perf;
    tq_read_perf(tq, &perf);

    int bad = 0;
    for (uint32_t i = 0; rc == TQ_OK && i < l->m * l->n; i++)
        if (c[i] != l->gold[i] && bad++ < 5)
            printf("tq: %s: C[%u][%u] = %d, TFLite %d\n", l->name, (unsigned)(i / l->n), (unsigned)(i % l->n),
                   (int)c[i], (int)l->gold[i]);
    unsigned macs = l->m * l->n * l->k, peak = info->tile * info->tile;
    printf("tq: %s %ux%ux%u %s rounding: rc=%d, %d mismatches vs TFLite\n", l->name, (unsigned)l->m,
           (unsigned)l->n, (unsigned)l->k, l->rq.rounding == TQ_ROUND_SINGLE ? "single" : "double", rc, bad);
    printf("tq:   core cycles=%u (%u.%u%% of %u MACs/cycle) beats=%u reads=%u writes=%u\n", (unsigned)perf.cycles,
           100u * macs / (peak * perf.cycles), (1000u * macs / (peak * perf.cycles)) % 10, peak,
           (unsigned)perf.beats, (unsigned)perf.reads, (unsigned)perf.writes);
    printf("tq:   host cycles: prepare=%u run=%u finish=%u\n", (unsigned)(t1 - t0), (unsigned)(t2 - t1),
           (unsigned)(t3 - t2));
    return bad || rc;
}

int main(void) {
    volatile uint32_t *tq = (volatile uint32_t *)(uintptr_t)TQ_BASE;
    tq_info info;
    if (tq_probe(tq, &info) != TQ_OK) {
        printf("tq: no device at %#x\n", TQ_BASE);
        return 1;
    }
    printf("tq: T=%u W=%u epilogue=%u lanes=%u walk=%u round1=%u\n", (unsigned)info.tile,
           (unsigned)info.port_bytes, (unsigned)info.epilogue, (unsigned)info.epi_lanes,
           (unsigned)info.walk_cols, (unsigned)info.round1);
    int fail = 0;
    for (unsigned i = 0; i < NLAYERS; i++) fail |= run_layer(tq, &info, &layers[i]);
    printf("tq: %s, %u layers\n", fail ? "FAIL" : "PASS", (unsigned)NLAYERS);
    return fail;
}
