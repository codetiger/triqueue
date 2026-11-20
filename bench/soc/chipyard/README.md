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
| `vsrc/tq_chipyard.sv` | Shim: `tq_top_axi` with 4-beat read bursts (`BURST_BEATS = 4`, one 64-byte line), active-high reset, AXI IDs echoed on the register port (one transaction at a time), `RLAST`. |
| `tq_gemm_test.c` | 64×64×64 int8 GEMM: packs with `sw/tq.c`, runs on the Tri-Queue, compares C with the CPU, prints the hardware counters. |
| `run.sh` | Builds the runtime, the program and the simulator in the Chipyard container (`bench/chipyard/setup.sh`), runs the program, and fails unless it prints `tq: PASS`. Output in `build/soc/<config>/`. |

Result (Nov 20, 2026, v1.0.2 RTL): `tq: PASS, 0 mismatches`, with 4,096 beats, 4,096 reads and 1,024 writes (the same as the cycle model), in **17,187 cycles (23.8% utilization)**. With single-beat reads (`BURST_BEATS = 1` in the shim) the same run takes 38,686 cycles (10.5%), close to the pre-v1 result (38,295 cycles, Nov 8, `65815f3`): the 4-beat bursts cut the cycles 2.25× by taking one TileLink/L2 round trip per 64-byte line instead of per 16-byte word. The DRAM, L2 and FIFO-ordered path still have far more latency than the 8-word operand FIFOs hide (the fixed-latency SRAM case takes 4,130 cycles). With `BURST_BEATS = 4`, `FIFO_DEPTH = 16` or `32` gives the same cycles (17,181, 17,168) and a 1 KiB panel buffer gives 14,075 (29.1%, 2,304 reads); `docs/benchmarks.md` §6 has the table. It is an integration check, not a performance result.

Integration notes:
- Chipyard 1.15.0 pins Verilator 5.022, which rejects default values on input ports. The RTL gives
  the optional ports `ext_pending_i` (`tq_top*`) and `flush_i` (`tq_axi_mem`) a default of `1'b0`;
  `TriQueue.scala` drops those defaults from its inlined copy, since `tq_top_axi` connects every one
  of them. Verilator 5.054 accepts the RTL as is.
- Gemmini's test submodule in Chipyard 1.15.0 pulls `github.com/ibm/rocc-software`, which no longer
  exists; `bench/chipyard/setup.sh` checks Gemmini out without it (the SoC does not use it).
- `AXI4ToTL` reuses TileLink sources in order and needs responses in order per AXI ID, but the system bus only orders volatile regions. Without the `TLFIFOFixer`, cacheable DRAM answers out of order and TileLink's monitor reports a reused source.
- GCC 16's nano newlib dereferences a NULL `_on_exit_args` at exit; the program links Chipyard's `tests/atexit-workaround.c` with `-Wl,--wrap=atexit`.
- The Chipyard container (`ucbbar/chipyard-image`) is published for `linux/amd64` only; on macOS Apple Silicon (`arm64`), `bench/chipyard/setup.sh` runs it with `--platform linux/amd64` under Rosetta emulation.
