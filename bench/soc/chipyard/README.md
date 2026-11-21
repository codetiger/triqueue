<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Tri-Queue in a Chipyard SoC

A one-core Rocket SoC (Chipyard 1.15.0) with `tq_top_axi` attached, simulated in Verilator, running a bare-metal GEMM and two real int8 layers through the C driver. It checks that the RTL, the AXI adapters and the driver work with a real CPU, caches and interconnect, and measures what the SoC's memory path costs.

```
bench/soc/chipyard/run.sh      # TriQueueRocketConfig; ~1 h the first time (runtime + simulator build), then ~10 min
CONFIGS="TriQueueRocketConfig TriQueueFbusConfig TriQueueOneIdConfig" bench/soc/chipyard/run.sh
```

| File | Purpose |
| --- | --- |
| `TriQueue.scala` | Registers: periphery bus → TileLink-to-AXI4 (4-byte beats, `AXI4UserYanker` since the slave does not echo user fields) → `tq_axil_regs`, at `0x1004_0000`. Memory: `tq_axi_mem` → AXI4-to-TileLink → `TLFIFOFixer` → system bus (16 bytes) or front bus (8 bytes), coherent with Rocket's caches. `TriQueueParams` sets `tq_top_axi`'s `LINES`, `WR_IDS`, `FIFO_DEPTH`, `MAX_OUT` and the bus. Configurations: `TriQueueRocketConfig` (system bus, an AXI ID per read line slot and per write, `WR_IDS = 4`), `TriQueueFbusConfig` (front bus, `WR_IDS = 4`), `TriQueueOneIdConfig` (front bus, one ID: the v1.0 attachment). The RTL is inlined as one file in `rtl/files.f` order. |
| `vsrc/tq_chipyard.sv` | Shim: `tq_top_axi` with 4-beat read bursts (`BURST_BEATS = 4`, one 64-byte line), active-high reset, AXI IDs echoed on the register port (one transaction at a time), `RLAST`. In simulation it prints a `tqbus:` line per run: AXI reads and writes in flight on average, and the mean latency from AR to the first R beat. |
| `tq_gemm_test.c` | 64×64×64 int8 GEMM: packs with `sw/tq.c`, runs on the Tri-Queue, compares C with numpy's product (`tq_gemm_ref.h`), prints the hardware counters. |
| `tq_layer_test.c` | Real int8 layers through the driver's layer API and the core's epilogue: DS-CNN's first 1×1 convolution (125×64×64, double rounding, column walk) and ResNet-8's classifier (1×10×64, single rounding, `MODE[10]`). Each int8 output is compared with TFLite's own tensor; prints the core's counters and the host's cycles for prepare (packing A, B and the epilogue table) and finish. |
| `export_gemm.py` | Writes `tq_gemm_ref.h`: numpy's int32 product of the A and B that `tq_gemm_test.c` generates (its LCG), so the simulated Rocket does not compute a reference GEMM in software (millions of simulated cycles). |
| `export_layers.py` | Writes `tq_layers.h` for `tq_layer_test.c` from `bench/e2e/*_golden.npz` (weights, quantization, TFLite's input and output tensors). The inputs are chosen so that the other rounding form changes outputs of each layer. |
| `run.sh` | Writes both headers (host Python with numpy), then in one Chipyard container (`bench/chipyard/setup.sh`) builds the runtime, the programs, and the simulator of each configuration in `CONFIGS` one after another (regenerated when the inlined RTL or the bench changes), runs every program in `TESTS` on every configuration in parallel, and fails unless each prints `tq: PASS`. Output in `build/soc/<config>/` (`build.log`, `run-<program>.log`). |

Results (Nov 21, 2026, v1.1 RTL): every run passes bit-exact, the GEMM against numpy and every int8 output of the layers against TFLite's tensor, with the cycle model's beats, reads and writes (the GEMM: 4,096 beats, 4,096 reads, 1,024 writes; the convolution: 8,192 beats plus 40 epilogue-table reads). The core reports `epilogue=1 lanes=4 walk=1 round1=1` through `HW_CONFIG2`. Core cycles (utilization of 64 MACs/cycle):

| Configuration | Memory master | GEMM 64³ | DS-CNN op 2, 1×1 `CONV_2D` + ReLU, 125×64×64, double rounding | ResNet-8 op 14, `FULLY_CONNECTED`, 1×10×64, single rounding |
| --- | --- | --- | --- | --- |
| `TriQueueOneIdConfig` | front bus, one ID | 17,031 (24.0%) | 25,258 (31.6%) | 417 |
| (system bus, one ID) | | 11,878 (34.4%) | 17,082 (46.8%) | 355 |
| `TriQueueFbusConfig` | front bus, `WR_IDS = 4` | 9,346 (43.8%) | 17,738 (45.1%) | 273 |
| **`TriQueueRocketConfig`** | **system bus, `WR_IDS = 4`** | **5,787 (70.7%)** | **10,383 (77.0%)** | **240** |
| (system bus, `WR_IDS = 8`, `LINES = 8`, `FIFO_DEPTH = 16`, `MAX_OUT = 32`) | | 5,598 (73.1%) | 9,831 (81.3%) | 232 |
| cycle model, fixed-latency SRAM | | 4,130 (99.2%) | 8,260 (96.9%) | 111 |

The rows in parentheses were measured with `TriQueueParams` changed in a scratch copy of `TriQueue.scala`.

What the bus statistics (`tqbus:`, GEMM) show:

- **One AXI ID serializes in TileLink.** `AXI4ToTL` makes one TileLink client per AXI ID, reads and writes together, and asks for FIFO order; cached memory is not FIFO, so the `TLFIFOFixer` lets one transaction of that client into L2 at a time. With one ID the core's reads and writes take turns: 2,048 transactions (1,024 bursts and 1,024 writes) in 17,031 cycles, about 8.3 cycles each, and the deeper FIFOs of the v1.0.2 runs (`FIFO_DEPTH = 16`, `32`) could not change that. With `WR_IDS = 4` each read line slot and each write has its own ID, so its own client: 3.2 reads and 3.7 writes in flight on average instead of 1.8 and 1.4.
- **The front bus is 8 bytes wide**, half the core's 16-byte port: with the IDs fixed it carries the 8,192 read-data beats of the GEMM in 9,346 cycles. The system bus is 16 bytes wide (`WithSystemBusWidth(128)`); there the latency from AR to the first R beat drops from 26 to 11 cycles. (Widening the front bus fails elaboration: Chipyard's serial TileLink bridge shares it.)
- With both fixed, more slots and a deeper FIFO add 3–6%: more reads are in flight, but the latency grows with them (11 → 25–29 cycles), so `FIFO_DEPTH = 8` is enough on this SoC.

The host's cost on the convolution is 161,469 cycles to prepare (packing A and B, the epilogue table) and ~142,000 to finish (copying the int8 output out of the workspace, 17.7 cycles per byte on Rocket; the cause is not measured), about 29× the core's cycles at `TriQueueRocketConfig`. With `N = 64` the core's C row stride is already `N` bytes, so a network runner can hand the workspace's C to the next layer instead of copying it, and pack B and the table once per model rather than per inference.

Earlier (Nov 20, 2026, v1.0.2 RTL, `TriQueueOneIdConfig`'s attachment): the GEMM took 17,187 cycles with 4-beat bursts and 38,686 (10.5%) with single-beat reads (pre-v1: 38,295, Nov 8, `65815f3`); `FIFO_DEPTH = 16` or `32` gave 17,181 and 17,168, and a 1 KiB panel buffer 14,075 (29.1%, 2,304 reads). These are integration measurements on one simulated SoC, not performance results; `docs/benchmarks.md` §6 summarizes them.

Integration notes:
- Chipyard 1.15.0 pins Verilator 5.022, which rejects default values on input ports. The RTL gives
  the optional ports `ext_pending_i` (`tq_top*`) and `flush_i` (`tq_axi_mem`) a default of `1'b0`;
  `TriQueue.scala` drops those defaults from its inlined copy, since `tq_top_axi` connects every one
  of them. Verilator 5.054 accepts the RTL as is.
- Gemmini's test submodule in Chipyard 1.15.0 pulls `github.com/ibm/rocc-software`, which no longer
  exists; `bench/chipyard/setup.sh` checks Gemmini out without it (the SoC does not use it).
- `AXI4ToTL` reuses TileLink sources in order and needs responses in order per AXI ID, but the system bus only orders volatile regions. Without the `TLFIFOFixer`, cacheable DRAM answers out of order and TileLink's monitor reports a reused source.
- GCC 16's nano newlib dereferences a NULL `_on_exit_args` at exit; the program links Chipyard's `tests/atexit-workaround.c` with `-Wl,--wrap=atexit`.
- The container is Ubuntu 24.04 for `linux/arm64` (native on Apple Silicon). CIRCT publishes no Linux arm64 `firtool`, so `bench/chipyard/setup.sh` runs Chipyard's pinned `firtool` (linux-x64) under Docker Desktop's Rosetta emulation; the rest runs natively.
