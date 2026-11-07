// SPDX-License-Identifier: Apache-2.0
// Hand-written AMX fp32 GEMM on pre-packed operands: a like-for-like counterpart to the Tri-Queue model.
// Output-stationary 32x32 tile held in Z (4 x 16x16 fp32 tiles). Per k: 1 ldy pair (32 A values),
// 1 ldx pair (32 B values), 4 fma32 = 1024 MACs per 64 fp32 loaded = 256 B per 1024 MACs
// (0.25 bytes/MAC, the same byte intensity as the int8 Tri-Queue's 16 B per 64 MACs).
// AMX encodings: github.com/corsix/amx (undocumented ISA).
//
// Build: clang -O2 -o amx_gemm amx_gemm.c
// Output CSV: m,n,k,sec_per_call,macs_per_s,max_abs_err
#include <mach/mach_time.h>
#include <math.h>
#include <pthread/qos.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AMX_NOP_OP_IMM5(op, imm5) \
    __asm volatile("nop\nnop\nnop\n.word (0x201000 + (%0 << 5) + %1)" : : "i"(op), "i"(imm5) : "memory")
#define AMX_OP_GPR(op, gpr) \
    __asm volatile(".word (0x201000 + (%0 << 5) + 0%1 - ((0%1 >> 4) * 6))" : : "i"(op), "r"((uint64_t)(gpr)) : "memory")
#define AMX_LDX(x) AMX_OP_GPR(0, x)
#define AMX_LDY(x) AMX_OP_GPR(1, x)
#define AMX_STZ(x) AMX_OP_GPR(5, x)
#define AMX_FMA32(x) AMX_OP_GPR(12, x)
#define AMX_SET() AMX_NOP_OP_IMM5(17, 0)
#define AMX_CLR() AMX_NOP_OP_IMM5(17, 1)

#define PAIR (1ull << 62)
#define REG(r) ((uint64_t)(r) << 56)
#define SKIP_Z (1ull << 27)

static double now_s(void) {
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info(&tb);
    return (double)mach_absolute_time() * tb.numer / tb.denom * 1e-9;
}

// ap: packed A, per 32-row block: k-major, 32 floats per k.  bp: packed B, per 32-col block: k-major, 32 floats per k.
static void amx_gemm(int m, int n, int k, const float *ap, const float *bp, float *c, float *zbuf) {
    for (int ti = 0; ti < m / 32; ti++) {
        for (int tj = 0; tj < n / 32; tj++) {
            const float *a = ap + (size_t)ti * 32 * k;
            const float *b = bp + (size_t)tj * 32 * k;
            for (int kk = 0; kk < k; kk++) {
                uint64_t buf = (kk & 3) * 2;                 // 4-deep rotation over X/Y register pairs
                AMX_LDY((uint64_t)(a + 32 * kk) | REG(buf) | PAIR);
                AMX_LDX((uint64_t)(b + 32 * kk) | REG(buf) | PAIR);
                uint64_t skip = kk == 0 ? SKIP_Z : 0;
                for (int ah = 0; ah < 2; ah++)
                    for (int bh = 0; bh < 2; bh++) {
                        uint64_t yoff = (buf + ah) * 64, xoff = (buf + bh) * 64, z = 2 * ah + bh;
                        AMX_FMA32(yoff | (xoff << 10) | (z << 20) | skip);
                    }
            }
            // Z row (ia*4 + z) holds C[16*ah + ia][16*bh .. +15], z = 2*ah + bh: store straight into C.
            (void)zbuf;
            for (int ah = 0; ah < 2; ah++)
                for (int bh = 0; bh < 2; bh++)
                    for (int ia = 0; ia < 16; ia++)
                        AMX_STZ((uint64_t)(c + (size_t)(32 * ti + 16 * ah + ia) * n + 32 * tj + 16 * bh)
                                | REG(ia * 4 + 2 * ah + bh));
        }
    }
}

static void *amalloc(size_t bytes) {
    void *p = NULL;
    posix_memalign(&p, 128, (bytes + 127) / 128 * 128);
    return p;
}

static void bench(int m, int n, int k) {
    float *a = amalloc(sizeof(float) * m * k), *b = amalloc(sizeof(float) * k * n);
    float *ap = amalloc(sizeof(float) * m * k), *bp = amalloc(sizeof(float) * k * n);
    float *c = amalloc(sizeof(float) * m * n), *zbuf = amalloc(64 * 64);
    for (int i = 0; i < m * k; i++) a[i] = (float)(rand() % 17 - 8);   // small ints: exact in fp32
    for (int i = 0; i < k * n; i++) b[i] = (float)(rand() % 17 - 8);
    for (int ti = 0; ti < m / 32; ti++)
        for (int kk = 0; kk < k; kk++)
            for (int r = 0; r < 32; r++) ap[(size_t)ti * 32 * k + 32 * kk + r] = a[(size_t)(32 * ti + r) * k + kk];
    for (int tj = 0; tj < n / 32; tj++)
        for (int kk = 0; kk < k; kk++)
            for (int cc = 0; cc < 32; cc++) bp[(size_t)tj * 32 * k + 32 * kk + cc] = b[(size_t)kk * n + 32 * tj + cc];

    AMX_SET();
    amx_gemm(m, n, k, ap, bp, c, zbuf);
    double err = 0;
    for (int i = 0; i < m; i++)
        for (int j = 0; j < n; j++) {
            double s = 0;
            for (int kk = 0; kk < k; kk++) s += (double)a[(size_t)i * k + kk] * b[(size_t)kk * n + j];
            err = fmax(err, fabs(s - c[(size_t)i * n + j]));
        }
    double best = 1e30;
    for (int trial = 0; trial < 5; trial++) {
        long reps = 0;
        double t0 = now_s(), t;
        do { amx_gemm(m, n, k, ap, bp, c, zbuf); reps++; t = now_s() - t0; } while (t < 0.1 || reps < 3);
        if (t / reps < best) best = t / reps;
    }
    AMX_CLR();
    printf("%d,%d,%d,%.6e,%.4e,%g\n", m, n, k, best, (double)m * n * k / best, err);
    fflush(stdout);
    free(a); free(b); free(ap); free(bp); free(c); free(zbuf);
}

int main(void) {
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    static const int sizes[][3] = {
        {32, 32, 32}, {64, 64, 64}, {128, 128, 128}, {256, 256, 256}, {512, 512, 512},
        {64, 64, 8}, {64, 64, 16}, {64, 64, 24}, {64, 64, 32}, {64, 64, 128}, {64, 64, 1024},
        {32, 32, 4096},
        {32, 32, 8}, {32, 32, 16}, {32, 32, 32}, {32, 32, 128}, {32, 32, 1024},
        {128, 128, 8}, {128, 128, 16}, {128, 128, 32}, {128, 128, 1024},
    };
    printf("m,n,k,sec_per_call,macs_per_s,max_abs_err\n");
    for (size_t i = 0; i < sizeof sizes / sizeof sizes[0]; i++) bench(sizes[i][0], sizes[i][1], sizes[i][2]);
    return 0;
}
