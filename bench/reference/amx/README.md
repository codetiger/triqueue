# Apple AMX reference measurements

Real-hardware utilization numbers for Apple's AMX matrix coprocessor (M1–M3; measured on an M2 Pro), used as a reference for the Tri-Queue design. AMX is undocumented, so the instruction encodings come from [corsix/amx](https://github.com/corsix/amx).

```
clang -O2 -o amx_peak amx_peak.c
clang -O2 -o amx_gemm amx_gemm.c
clang -O2 -o sgemm_sweep sgemm_sweep.c -framework Accelerate

./amx_peak 50000000                                 > peak.csv
./amx_gemm                                          > amx_gemm.csv
VECLIB_MAXIMUM_THREADS=1 ./sgemm_sweep              > sgemm_1t.csv
./sgemm_sweep                                       > sgemm_mt.csv
python3 compare.py                                  # writes results.md (runs the Tri-Queue sim too)
python3 ablation.py                                 # writes ablation.md
```

| File | What it measures |
| --- | --- |
| `amx_peak.c` | Peak outer-product throughput (fp32, fp16, int16) with no memory traffic, plus latency (dependent chains) and thread scaling (number of AMX units) |
| `amx_gemm.c` | A hand-written fp32 GEMM on pre-packed operands with a 32×32 output tile in Z. It reads 0.25 bytes per MAC, the same byte intensity as the Tri-Queue (AMX: 16 MACs per fp32 element; Tri-Queue: 4 per int8 element). Every run is checked for exact results. |
| `sgemm_sweep.c` | Accelerate `cblas_sgemm` (which runs on AMX), the production library path |
| `workloads.h` | Reads the shapes from `bench/workloads/gemm.csv`: `amx_gemm` runs the rows tagged `amx_hand`, `sgemm_sweep` those tagged `amx_sgemm` (run from this directory, or pass the CSV path as the first argument) |
| `ablation.py` | Precision-independent test: Tri-Queue simulator with 1 vs 2 accumulator banks, and one first-order model calibrated on both the simulator and the measured AMX kernel (with held-out validation) |
| `compare.py` | Divides each measurement by the measured peak and puts it beside the Tri-Queue cycle model |
