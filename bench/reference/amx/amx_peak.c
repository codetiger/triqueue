// SPDX-License-Identifier: Apache-2.0
// Apple AMX peak-throughput microbenchmark (M1-M3).
// AMX is undocumented. Encodings follow the community reverse-engineering at github.com/corsix/amx.
// The kernels issue outer-product ops with no loads, so they measure pure compute peak.
//
// Build: clang -O2 -o amx_peak amx_peak.c
// Usage: ./amx_peak            (prints CSV: kind,threads,chains,ops_per_s,macs_per_s)
#include <mach/mach_time.h>
#include <pthread.h>
#include <pthread/qos.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AMX_NOP_OP_IMM5(op, imm5) \
    __asm volatile("nop\nnop\nnop\n.word (0x201000 + (%0 << 5) + %1)" : : "i"(op), "i"(imm5) : "memory")
#define AMX_OP_GPR(op, gpr) \
    __asm volatile(".word (0x201000 + (%0 << 5) + 0%1 - ((0%1 >> 4) * 6))" : : "i"(op), "r"((uint64_t)(gpr)) : "memory")
#define AMX_SET() AMX_NOP_OP_IMM5(17, 0)
#define AMX_CLR() AMX_NOP_OP_IMM5(17, 1)
#define AMX_FMA32(x) AMX_OP_GPR(12, x)   // 16x16 fp32 outer product  -> 256 MACs
#define AMX_MAC16(x) AMX_OP_GPR(14, x)   // 32x32 int16 outer product -> 1024 MACs
#define AMX_FMA16(x) AMX_OP_GPR(15, x)   // 32x32 fp16 outer product  -> 1024 MACs

#define ZROW(z) ((uint64_t)(z) << 20)

enum { K_FMA32, K_FMA16, K_MAC16 };
static const char *KNAME[] = {"fma32", "fma16", "mac16"};
static const int KMACS[] = {256, 1024, 1024};

static double now_s(void) {
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info(&tb);
    return (double)mach_absolute_time() * tb.numer / tb.denom * 1e-9;
}

// chains = number of independent Z accumulators used round-robin (hides op latency).
// fp32 has 4 Z tiles (z = 0..3); 16-bit types have 2 (z = 0..1).
static void run_kernel(int kind, int chains, long iters) {
    AMX_SET();
    for (long i = 0; i < iters; i++) {
        switch (kind) {
        case K_FMA32:
            if (chains == 1) { AMX_FMA32(ZROW(0)); AMX_FMA32(ZROW(0)); AMX_FMA32(ZROW(0)); AMX_FMA32(ZROW(0)); }
            else if (chains == 2) { AMX_FMA32(ZROW(0)); AMX_FMA32(ZROW(1)); AMX_FMA32(ZROW(0)); AMX_FMA32(ZROW(1)); }
            else { AMX_FMA32(ZROW(0)); AMX_FMA32(ZROW(1)); AMX_FMA32(ZROW(2)); AMX_FMA32(ZROW(3)); }
            break;
        case K_FMA16:
            if (chains == 1) { AMX_FMA16(ZROW(0)); AMX_FMA16(ZROW(0)); AMX_FMA16(ZROW(0)); AMX_FMA16(ZROW(0)); }
            else { AMX_FMA16(ZROW(0)); AMX_FMA16(ZROW(1)); AMX_FMA16(ZROW(0)); AMX_FMA16(ZROW(1)); }
            break;
        case K_MAC16:
            if (chains == 1) { AMX_MAC16(ZROW(0)); AMX_MAC16(ZROW(0)); AMX_MAC16(ZROW(0)); AMX_MAC16(ZROW(0)); }
            else { AMX_MAC16(ZROW(0)); AMX_MAC16(ZROW(1)); AMX_MAC16(ZROW(0)); AMX_MAC16(ZROW(1)); }
            break;
        }
    }
    AMX_CLR();
}

// macOS has no pthread_barrier; minimal one.
typedef struct { pthread_mutex_t m; pthread_cond_t c; int n, count, gen; } barrier_t;
static void barrier_wait(barrier_t *b) {
    pthread_mutex_lock(&b->m);
    int gen = b->gen;
    if (++b->count == b->n) { b->gen++; b->count = 0; pthread_cond_broadcast(&b->c); }
    else while (gen == b->gen) pthread_cond_wait(&b->c, &b->m);
    pthread_mutex_unlock(&b->m);
}

typedef struct { int kind, chains; long iters; barrier_t *bar; } job_t;

static void *worker(void *p) {
    job_t *j = p;
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    run_kernel(j->kind, j->chains, 1000);  // warm-up
    barrier_wait(j->bar);
    run_kernel(j->kind, j->chains, j->iters);
    return NULL;
}


static double measure(int kind, int chains, int threads, long iters) {
    pthread_t th[64];
    job_t jobs[64];
    barrier_t bar;
    pthread_mutex_init(&bar.m, NULL);
    pthread_cond_init(&bar.c, NULL);
    bar.n = threads + 1; bar.count = 0; bar.gen = 0;
    for (int t = 0; t < threads; t++) {
        jobs[t] = (job_t){kind, chains, iters, &bar};
        pthread_create(&th[t], NULL, worker, &jobs[t]);
    }
    barrier_wait(&bar);
    double t0 = now_s();
    for (int t = 0; t < threads; t++) pthread_join(th[t], NULL);
    double dt = now_s() - t0;
    return (double)threads * iters * 4 / dt;  // ops per second (4 ops per iteration)
}

int main(int argc, char **argv) {
    long iters = argc > 1 ? atol(argv[1]) : 20000000;
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    printf("kind,threads,chains,ops_per_s,macs_per_s\n");
    // Single thread: latency vs throughput (chains)
    for (int kind = 0; kind < 3; kind++) {
        int maxc = kind == K_FMA32 ? 4 : 2;
        for (int c = 1; c <= maxc; c *= 2) {
            double best = 0;
            for (int rep = 0; rep < 3; rep++) { double r = measure(kind, c, 1, iters); if (r > best) best = r; }
            printf("%s,1,%d,%.4e,%.4e\n", KNAME[kind], c, best, best * KMACS[kind]);
            fflush(stdout);
        }
    }
    // Thread scaling: how many AMX units?
    for (int t = 2; t <= 12; t++) {
        double best = 0;
        for (int rep = 0; rep < 3; rep++) { double r = measure(K_FMA32, 4, t, iters / 2); if (r > best) best = r; }
        printf("fma32,%d,4,%.4e,%.4e\n", t, best, best * KMACS[K_FMA32]);
        fflush(stdout);
    }
    return 0;
}
