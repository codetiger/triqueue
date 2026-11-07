// SPDX-License-Identifier: Apache-2.0
// Accelerate cblas_sgemm sweep (runs on AMX). Prints CSV: m,n,k,sec_per_call,macs_per_s
// Build: clang -O2 -o sgemm_sweep sgemm_sweep.c -framework Accelerate
// Single AMX unit: VECLIB_MAXIMUM_THREADS=1 ./sgemm_sweep
#define ACCELERATE_NEW_LAPACK
#include <Accelerate/Accelerate.h>
#include <mach/mach_time.h>
#include <pthread/qos.h>
#include <stdio.h>
#include <stdlib.h>

static double now_s(void) {
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info(&tb);
    return (double)mach_absolute_time() * tb.numer / tb.denom * 1e-9;
}

static float *rnd(size_t n) {
    float *p = malloc(n * sizeof(float));
    for (size_t i = 0; i < n; i++) p[i] = (float)rand() / RAND_MAX - 0.5f;
    return p;
}

static void bench(int m, int n, int k) {
    float *a = rnd((size_t)m * k), *b = rnd((size_t)k * n), *c = rnd((size_t)m * n);
    for (int i = 0; i < 3; i++)  // warm-up
        cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, m, n, k, 1.0f, a, k, b, n, 0.0f, c, n);
    double best = 1e30;
    for (int trial = 0; trial < 5; trial++) {
        long reps = 0;
        double t0 = now_s(), t;
        do {
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, m, n, k, 1.0f, a, k, b, n, 0.0f, c, n);
            reps++;
            t = now_s() - t0;
        } while (t < 0.1 || reps < 3);
        if (t / reps < best) best = t / reps;
    }
    printf("%d,%d,%d,%.6e,%.4e\n", m, n, k, best, (double)m * n * k / best);
    fflush(stdout);
    free(a); free(b); free(c);
}

int main(void) {
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    static const int sizes[][3] = {
        {16, 16, 16}, {32, 32, 32}, {64, 64, 64}, {128, 128, 128}, {256, 256, 256},
        {512, 512, 512}, {1024, 1024, 1024}, {2048, 2048, 2048},
        {60, 60, 60}, {65, 65, 65}, {100, 100, 100},
        {64, 64, 8}, {64, 64, 16}, {64, 64, 24}, {64, 64, 32}, {64, 64, 128}, {64, 64, 1024},
        {1, 512, 512}, {512, 1, 512}, {1, 4096, 4096},
        {64, 4, 1024}, {4, 64, 1024}, {4, 4, 1024},
    };
    printf("m,n,k,sec_per_call,macs_per_s\n");
    for (size_t i = 0; i < sizeof sizes / sizeof sizes[0]; i++) bench(sizes[i][0], sizes[i][1], sizes[i][2]);
    return 0;
}
