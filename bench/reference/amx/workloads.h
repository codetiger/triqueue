// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Reads the GEMM shapes carrying one tag from bench/workloads/gemm.csv (the repo's single
// workload list), so the AMX programs measure exactly the rows the reports use.
#ifndef WORKLOADS_H
#define WORKLOADS_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WORKLOADS_CSV "../../workloads/gemm.csv"
#define WORKLOADS_MAX 256

// Fills shapes[i] = {m, n, k} for every row whose `tags` column (;-separated) contains `tag`,
// in file order. Returns the count, or exits with a message if the file cannot be read.
static int workload_shapes(const char *path, const char *tag, int shapes[][3]) {
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "cannot open %s (run from bench/reference/amx or pass the path)\n", path);
        exit(1);
    }
    char line[1024];
    int n = 0;
    if (!fgets(line, sizeof line, f)) exit(1);  // header: group,m,n,k,count,tags,why
    while (fgets(line, sizeof line, f) && n < WORKLOADS_MAX) {
        char *field[6], *p = line;
        int i;
        for (i = 0; i < 6 && p; i++) {
            field[i] = p;
            p = strchr(p, ',');
            if (p) *p++ = 0;
        }
        if (i < 6) continue;
        size_t len = strlen(tag);
        for (char *t = field[5]; t && *t; t = strchr(t, ';') ? strchr(t, ';') + 1 : NULL)
            if (!strncmp(t, tag, len) && (t[len] == ';' || t[len] == 0)) {
                shapes[n][0] = atoi(field[1]);
                shapes[n][1] = atoi(field[2]);
                shapes[n][2] = atoi(field[3]);
                n++;
                break;
            }
    }
    fclose(f);
    return n;
}
#endif
