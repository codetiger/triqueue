<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bare-metal driver

`tq.h` / `tq.c`: a freestanding C99 driver for the Tri-Queue register port (spec §6.4). No allocation and no libc, so it builds for any RISC-V (or other) bare-metal target.

| Function | Does |
| --- | --- |
| `tq_probe` | Checks `ID` and decodes `HW_CONFIG` (T, W, FIFO and MAC queue depth, shared port, program buffer size). |
| `tq_pack_a`, `tq_pack_b`, `tq_packed_*_bytes` | Host packing (spec §5.3): A into column panels, B into row panels, k-major, K padded to a multiple of 8. int8 and uint8 pack the same way. |
| `tq_c_ld` | C row stride: 4n bytes rounded up to whole port words. |
| `tq_gemm_program` | The 50-byte GEMM program: 8 `CFG` + the 10-byte kernel (spec §6.2). |
| `tq_run` | Loads a program into `PROG`, writes `START`, polls `STATUS` until `DONE`; returns the core's error code, else `TQ_ERR_BUS` if a memory access got an error response (`STATUS.BUS_ERR`). Aborts after `max_polls`. |
| `tq_read_perf` | The performance counters of the last run. |
| `tq_gemm_workspace_bytes`, `tq_gemm_prepare`, `tq_gemm_finish`, `tq_gemm_run` | The GEMM API: C[m][n] = A[m][k] × B[k][n] in one call. The caller gives a workspace in core-visible memory (its CPU pointer and its address on the core's memory port); prepare packs A and B into it and builds the program, finish copies C out with the caller's row stride, `tq_gemm_run` does prepare, run and finish. |

A, B and C go in the core's memory (its SRAM); the bases and the C stride must be multiples of W. The run polls; an interrupt-driven host enables `IRQ_EN` and waits for `irq_o` instead.

`test_tq.py` (`make sw-test`) compiles the driver and checks it against the cycle model's host side, byte for byte, and runs the GEMM API end to end (C prepares the workspace, the cycle model executes that memory image and program, C finishes; the result equals numpy): the program against `isa.gemm_config` + `gemm_kernel`, packing against `packing.pack_a` / `pack_b`, the C stride against `build_gemm`, at T = 4, 8, 16 and 32, and the register sequence of `tq_probe` / `tq_run` on a fake register file.
