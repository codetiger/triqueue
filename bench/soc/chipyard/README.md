<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Tri-Queue in a Chipyard SoC

A one-core Rocket SoC (Chipyard 1.15.0) with `tq_top_axi` attached, simulated in Verilator, running a bare-metal GEMM through the C driver. It checks that the RTL, the AXI adapters and the driver work with a real CPU, caches and interconnect.

```
bench/soc/chipyard/run.sh      # ~1 h the first time (runtime + simulator build), then minutes
```

| File | Purpose |
| --- | --- |
| `TriQueue.scala` | `TriQueueRocketConfig`. Registers: periphery bus → TileLink-to-AXI4 (4-byte beats, `AXI4UserYanker` since the slave does not echo user fields) → `tq_axil_regs`, at `0x1004_0000`. Memory: `tq_axi_mem` → AXI4-to-TileLink → `TLFIFOFixer` → front bus, coherent with Rocket's caches. The RTL is inlined as one file in `rtl/files.f` order. |
| `vsrc/tq_chipyard.sv` | Shim: active-high reset, AXI IDs echoed on the register port (one transaction at a time), `RLAST`. |
| `tq_gemm_test.c` | 64×64×64 int8 GEMM: packs with `sw/tq.c`, runs on the Tri-Queue, compares C with the CPU, prints the hardware counters. |
| `run.sh` | Builds the runtime, the program and the simulator in the Chipyard container (`bench/chipyard/setup.sh`), runs the program, and fails unless it prints `tq: PASS`. Output in `build/soc/<config>/`. |

Result (Nov 8, 2026): `tq: PASS, 0 mismatches`, with 4,096 beats, 4,096 reads and 1,024 writes (the same as the cycle model), in 38,295 cycles. That is 10.6% utilization: the DRAM, L2 and FIFO-ordered path have far more latency than the 8-word operand FIFOs hide (the fixed-latency SRAM case takes 4,130 cycles). It is an integration check, not a performance result.

Integration notes:
- `AXI4ToTL` reuses TileLink sources in order and needs responses in order per AXI ID, but the system bus only orders volatile regions. Without the `TLFIFOFixer`, cacheable DRAM answers out of order and TileLink's monitor reports a reused source.
- GCC 16's nano newlib dereferences a NULL `_on_exit_args` at exit; the program links Chipyard's `tests/atexit-workaround.c` with `-Wl,--wrap=atexit`.
