<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bare-metal driver

`tq.h` / `tq.c`: a freestanding C99 driver for the Tri-Queue register port (spec §6.4). No allocation and no libc, so it builds for any RISC-V (or other) bare-metal target. Build it with `make -C sw` (produces `libtq.a`), or vendor `tq.c` + `tq.h` and add the include path.

| Function | Does |
| --- | --- |
| `tq_probe` | Checks `ID` and decodes `HW_CONFIG` (T, W, FIFO and MAC queue depth, shared port, program buffer size, `panel_bytes`, `mac_pipe`); returns `TQ_ERR_ARG` for an invalid configuration. |
| `tq_pack_a`, `tq_pack_b`, `tq_packed_*_bytes` | Host packing (spec §5.3): A into column panels, B into row panels, k-major, K padded to a multiple of 8. int8 and uint8 pack the same way. |
| `tq_c_ld` | int32 C row stride: 4n bytes rounded up to whole port words. |
| `tq_c_ld_int8` | int8 C row stride: n bytes rounded up to whole port words. |
| `tq_gemm_program` | The 50-byte GEMM program: 8 `CFG` + the 10-byte kernel (spec §6.2). |
| `tq_run` | Rejects starting if `STATUS.BUSY` or `STATUS.PENDING` is set (`TQ_ERR_BUSY`). Otherwise loads a program into `PROG`, issues `TQ_WMB()`, writes `START`, polls `STATUS` until `DONE` (and `PENDING == 0`), and issues `TQ_RMB()`; returns the core's error code, else `TQ_ERR_BUS` if a memory access got an error response (`STATUS.BUS_ERR`). On `max_polls` timeout (`0` polls forever), writes `ABORT` and drains `STATUS.PENDING == 0` before returning `TQ_ERR_TIMEOUT`. |
| `tq_set_cache_ops` | Registers optional platform cache-clean and cache-invalidate callbacks (`clean_fn(ptr, bytes)`, `inv_fn(ptr, bytes)`), invoked automatically by `tq_gemm_prepare` and `tq_gemm_finish`. |
| `tq_read_perf` | The performance counters of the last run. |
| `tq_gemm_workspace_bytes`, `tq_gemm_prepare`, `tq_gemm_finish`, `tq_gemm_run`, `tq_gemm_run_timeout` | The int32 GEMM API: C[m][n] = A[m][k] × B[k][n] in one call. The caller gives a workspace in core-visible memory (its CPU pointer and its address on the core's memory port); prepare packs A and B into it, cleans the cache if `tq_set_cache_ops` was configured, and builds the program; finish invalidates the C cache range and copies C out with the caller's row stride; `tq_gemm_run` does prepare, run (`TQ_DEFAULT_MAX_POLLS`) and finish. `tq_gemm_run_timeout` takes `max_polls` (0 = forever); use it when a run must be bounded. |

Int8 output (`MODE[2]`, shift-only) is available through the low-level path: set `TQ_MODE_OUT_INT8 | TQ_MODE_OUT_SHIFT(s)` in `tq_gemm.mode`, use `tq_c_ld_int8`, and read C as bytes. The one-call GEMM API writes int32 only.

Register access is memory-mapped by default (`base` points at the registers, e.g. behind `tq_axil_regs`). Built with `-DTQ_XIF`, the driver uses the CV-X-IF custom instructions of `tq_xif_regs` instead (`tq.wr` / `tq.rd` on custom-0, emitted with `.insn r`, so no assembler patch is needed) and ignores `base`.

A, B and C go in the core's memory (its SRAM) and must not overlap; the bases and the C stride must be multiples of W. The core is not coherent with host caches: flush/invalidate the buffers before and after a run. The run polls; an interrupt-driven host enables `IRQ_EN` and waits for `irq_o` instead. See [`docs/integration.md`](../docs/integration.md).

`test_tq.py` (`make sw-test`) compiles the driver and checks it against the cycle model's host side, byte for byte, and runs the GEMM API end to end (C prepares the workspace, the cycle model executes that memory image and program, C finishes; the result equals numpy): the program against `isa.gemm_config` + `gemm_kernel`, packing against `packing.pack_a` / `pack_b`, the C stride against `build_gemm`, at T = 4, 8, 16 and 32, and the register sequence of `tq_probe` / `tq_run` on a fake register file.
