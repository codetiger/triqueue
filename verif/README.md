# RTL verification

```
make rtl-lint        # Verilator -Wall (12 parameter sets + 3 adapter tops + burst AXI) + Yosys elaboration
make rtl-test        # cocotb + Verilator regression against the cycle model (2,685 cases in 41 configs + golden-vector $readmemh test, ~170 s)
make rtl-coverage    # the same with Verilator line + toggle coverage, merged per file (build/rtl-cov/summary.md, ~10 min)
make formal          # SymbiYosys: clock-gating, FIFO, AXI4-Lite & CV-X-IF proofs, AXI4 master (cover, burst BMC, unbounded prove/burst_prove), epilogue lane vs TFLite, both roundings (tq_requant), T=4 core split (typically 3–11 min, but PDR variance can exceed 30 min)
make formal-nightly  # SymbiYosys: T=4 core (shared, ack, ack_split, panel) + lazy clear csa1 + AXI4 burst read data (burst_data) (3–10 min each)
make synth-check     # generic Yosys synthesis, both ACC_FORMs: no unintended latches, loops or undriven nets
python3 -m pytest verif -q -k t16    # selects t16 and t16_panel; use -k "t16 and not panel" for one
```

Tools: Verilator ≥ 5, cocotb ≥ 2.0, Yosys (with `yosys-abc`), SymbiYosys with Yices.

## Regression (`test_rtl.py`, `tb/`)

Each configuration in `test_rtl.py` builds the DUT once (in `build/rtl/<name>/`) and runs its cases in one simulation. For every case the testbench (`tb/tq_tb.py`) loads the program through the register port (or adapter), serves memory from a Python SRAM/bus model, and records every read, write and MAC start. `tb/tq_cocotb.py` runs the same GEMM in the cycle model and compares:

1. Final memory, bit-exact, and C against numpy.
2. The read and write transactions with their cycles, and the cycle and shape of every MAC start.
3. The performance counters (instructions, beats, reads, writes, cycles), error codes, and the total cycle count.

| Configuration | What it covers | Cases |
| --- | --- | --- |
| `t8_default` | Spec design point. Every edge shape and K padding, signedness, SRAM latency 1–13, 64³, drain-limited K, GEMV | 273 |
| `t8_shared_port` | Drain writes on the read port (`SHARED_PORT = 1`) | 44 |
| `t8_small_queues` | FIFO depth 2, MAC queue 2: maximum internal stalls | 77 |
| `t8_backpressure` | Random `gnt` stalls (10–80%) on both ports | 53 |
| `t8_shared_backpressure` | Shared port + random `gnt` stalls | 12 |
| `t8_acc_csa` | `ACC_FORM = CSA` (carry-save accumulator): edge shapes, signedness, 64³, GEMV, stalls | 118 |
| `t8_reference_queues` | Physical-design reference queues (`FIFO_DEPTH = 4`, `MACQ_DEPTH = 3`) | 42 |
| `t8_edge_cases` | Illegal programs (error codes & exact stop cycle), `ZERO_C` during drain, `K = 0`, `PROG` writes while busy, mid-run `ABORT`/error restart, seeded random valid/invalid programs, `LOOP_T` nested in a once-running `LOOP_K`, register/IRQ/W1C contract | 47 |
| `t8_edge_shared` | Illegal/edge-case programs and mid-run `ABORT`/error restart with `SHARED_PORT = 1` and `WR_ACK = 1` | 11 |
| `t8_mcu_custom` | `PERF = 0` (counters tie to 0), `PROG_BYTES = 120` (`HW_CONFIG[31:24]`, programs > 64 B) | 4 |
| `t8_panel` | `A` panel buffer (`PANEL_BYTES = 1024`, spec §10.9): replay hits, K overflow fallback, stalls, errors | 175 |
| `t8_panel_shared` | `A` panel buffer (`PANEL_BYTES = 512`) + shared port + `WR_ACK = 1` | 9 |
| `t16_panel` | `T = 16` with `PANEL_BYTES = 2048` | 77 |
| `t4_panel` | `T = 4` with `PANEL_BYTES = 256` | 149 |
| `t8_walk_cols` | Column-major tile walk (`MODE[8]`): edge shapes, latency sweep, signedness, int8 C, back-pressure | 123 |
| `t8_walk_panel` | Column walk + panel buffer keeping the B panel (`PANEL_BYTES = 1024`), back-pressure, illegal programs | 174 |
| `t8_walk_panel_shared` | Column walk + B panel buffer on a shared port with `WR_ACK = 1` | 9 |
| `t4_walk_panel` | Column walk + B panel buffer at T = 4 | 149 |
| `t8_epi` | TFLite epilogue (`MODE[9]`, 4 lanes): edge shapes, latency sweep, clamp windows, back-pressure, latency 20 and 24 (parameter reads on top of a full set of A/B reads); single rounding (`MODE[10]`) on the edge shapes with every shift in [−31, 30], a clamp window, back-pressure and latency 20; C against `requant.requantize` | 157 |
| `t8_epi_walk` | Epilogue with the column walk and the B panel buffer, both roundings | 114 |
| `t8_epi_l1` / `t8_epi_l8` | Epilogue with 1 and 8 lanes (`EPI_LANES`), both roundings | 7 + 7 |
| `t8_epi_shared` | Epilogue on a shared port with `WR_ACK = 1` and back-pressure (the lanes hold while a row waits), both roundings | 10 |
| `t4_epi` | Epilogue at T = 4 (2 lanes), both roundings | 147 |
| `t8_dscnn` | Real DS-CNN layers (MLPerf Tiny, `bench/e2e`): all six engine layers of one input (im2col'd 10×4 conv, four 1×1 convs, the classifier), TFLite weights, activations and quantization; C equal to the TFLite interpreter's tensor; plus latency 3–9, back-pressure and the row walk | 10 |
| `t4_dscnn` | The same at T = 4 (Tang Nano 20K size): first conv, one 1×1 conv with back-pressure, the classifier | 3 |
| `t8_resnet8` | Real ResNet-8 layers (MLPerf Tiny): all ten engine layers of one input (3×3 convs at stride 1 and 2, 1×1 stride-2 shortcuts, the classifier, m up to 1,024 and k up to 576), C equal to TFLite's tensor (the classifier with single rounding, as TFLite's FULLY_CONNECTED); plus latency, back-pressure and the row walk | 14 |
| `t4_resnet8` | The same at T = 4: first conv (k = 27), a shortcut with back-pressure, the deepest conv, the classifier | 4 |
| `t8_mac1` | 1-stage MAC (`MAC_PIPE = 1`, no product register) | 119 |
| `t8_mac1_csa_shared` | `MAC_PIPE = 1` + carry-save + shared port + panel buffer | 9 |
| `t8_no_clock_gating` | `CLK_GATE = 0` (plain enable flops) | 74 |
| `t8_axil` | `tq_top_axil`: AXI4-Lite slave register adapter + host/IRQ contract | 11 |
| `t8_axil_prot` | `tq_top_axil` with `CHECK_PROT = 1`: privileged secure accesses work, unprivileged or non-secure ones get `SLVERR` and do not reach the registers | 2 |
| `t8_xif` | `tq_top_xif`: CV-X-IF coprocessor instruction adapter (speculative/killed copies, random order, batched commit/kill) | 11 |
| `t8_write_ack` | `WR_ACK = 1`, `WR_OUT_MAX = 2` with variable write-response latency | 30 |
| `t8_write_ack_shared` | `WR_ACK = 1`, `WR_OUT_MAX = 1`, `SHARED_PORT = 1` | 9 |
| `t8_axi` | `tq_top_axi`: AXI4-Lite registers + AXI4 memory master, ready stalls, zero-latency (`lat = 1`), `SLVERR` → `STATUS.BUS_ERR` | 29 |
| `t8_axi_burst` | `tq_top_axi` with `BURST_BEATS = 4` and `ID_W = 4`: read line buffer, aligned bursts, back-to-back address reuse, read-after-write line invalidation, eviction of a still-valid line, multi-beat `SLVERR`, orphan burst abort | 21 |
| `t32` | `T = 32` (1024 MACs/cycle, 512-bit ports) | 81 |
| `t16` | `T = 16` (256 MACs/cycle, 256-bit ports) | 118 |
| `t4` | `T = 4` (16 MACs/cycle, 64-bit ports) | 150 |
| **Total** | **41 configurations** (+ `test_vectors_readmemh`) | **2,685** |

Stalled cases are cycle-exact too: the testbench draws its grants from the model's `GrantSource` with the case's seed (`docs/golden-vectors.md`). It also checks OBI request stability: every ungranted request reappears unchanged the next cycle (except on `ABORT` or fatal error stop).

## Formal (`formal/`)

**`tq_cg.sby`** (with `check_cg_pattern.py`) checks that every gated register in `rtl/` uses the `tq_cg` template, and proves (multiclock) that the `tq_icg` latch model is equal to an ungated enable flop with and without async reset.

**`tq_requant.sby`** (`pre`, `post`, ~25 s) proves the TFLite epilogue lane equal to TFLite's int8 output stage for every input, in both rounding forms: `tq_requant_pre` (bias add, shift clamp to [−31, 30], left and right shift; the left shift applied for double rounding, not for single) against `MultiplyByQuantizedMultiplier`'s `x * (1 << left_shift)`, and `tq_requant_post`, for every 64-bit product of two int32 values and every shift pair the pre stage gives, against `SaturatingRoundingDoublingHighMul` as written in TFLite (C++ division) followed by an independent round-half-away-from-zero divide by 2^right, or (`MODE[10]`) against `TFLITE_SINGLE_ROUNDING`'s `(x * M0 + (1 << (total_shift - 1))) >> total_shift`, then the zero point and the clamp (`tq_requant_check.sv`). It is combinational, so one SMT step is the whole proof.

**`tq_fifo.sby`** proves by k-induction (unbounded), at depths 2, 3, 4, 8 and 16: the occupancy invariant, no overflow or underflow under legal use, and that every entry is stored and popped in order with its data intact. Every queue in the core is an instance of this FIFO.

**`tq_axil_regs.sby`**, **`tq_xif_regs.sby`** and **`tq_axi_mem.sby`** prove the AXI4-Lite register slave, CV-X-IF instruction adapter and AXI4 memory master (`tq_axi_mem.sby`, all in `make formal`: `cover`; bounded `burst` BMC depth 10 at `W = 4`, `MAX_OUT = 3`, `BURST_BEATS = 2`, including read data; the unbounded `prove` at `BURST_BEATS = 1`; and the unbounded `burst_prove` at `BURST_BEATS = 2` with every property except the read-data equality, which PDR does not prove at `MAX_OUT = 3` without strengthening invariants; `make formal-nightly` runs `burst_data`, the unbounded burst proof including read data at `MAX_OUT = 1`. In burst mode the environment follows one arbitrary address (`sym_a`) through the line buffer instead of carrying full addresses as data. Like `tq_core.sby`, it reads `tq_fifo` with `TQ_FORMAL_CORE`, so the FIFOs' legal use is asserted).

**`tq_core.sby`** proves, unbounded (ABC PDR), the core invariants of `tq_core` at T = 4 with minimal queues (`FIFO_DEPTH = 2`, `MACQ_DEPTH = 2`, `PROG_BYTES = 8`, `tq_core_env.sv`): an accumulate never targets the bank being drained, no queue (QA, QB, MAC and stream command queues, read tags) overflows or underflows, and an ungranted request stays asserted with the same address (OBI request stability). It holds for any program (the program buffer is an arbitrary constant), START and ABORT at any time, any `gnt` pattern on both ports, and any read latency including zero. Tasks `split`, `shared`, `ack` (`SHARED_PORT = 1, WR_ACK = 1`), `ack_split` (`SHARED_PORT = 0, WR_ACK = 1`) and `panel` (`PANEL_BYTES = 32`) cover all modes. Programs may set the column walk (`MODE[8]`) in every task but `ack_split`, which reads the RTL with `TQ_FORMAL_NO_WALK` so that `MODE[8]` is reserved (with the walk, PDR did not converge in 90 min). With the TFLite epilogue (`MODE[9]`) the same invariants are checked to depth 16 (`epi_bmc`, nightly); the other tasks read the RTL with `TQ_FORMAL_NO_EPI` (`MODE[9]` reserved). `make formal` runs `split` (typically 3–11 min, but PDR variance can exceed 30 min); `shared`, `ack`, `ack_split` and `panel` run in `make formal-nightly`. With `TQ_FORMAL_CORE` defined, `tq_fifo`'s legal-use conditions become assertions instead of assumptions.

Before solving, the script keeps only the input cone of the assertions and assumptions, so the multipliers and accumulators drop out (and the accumulator form doesn't matter). The environment isn't vacuous: with the reference kernel (`KERNEL = 1`) a cover of accumulate-during-drain is reached at step 20. The proof found that START or ABORT with reads in flight let the stale responses overflow an operand FIFO; `tq_stream` now discards them.

## What is not covered yet

- **Coverage metrics.** The regression is a directed/random functional suite; it does not publish line, toggle or branch coverage. Adding `verilator --coverage` to CI is an open task (`docs/verification.md`).
- **Unbounded coverage-guided program fuzzing.** `t8_edge_cases` runs seeded random valid and illegal programs, but there is no coverage-guided generator.
- **Data correctness in formal.** `tq_core.sby` proves structural invariants, not the arithmetic result; data correctness comes from the cycle-exact differential regression. The exception is the epilogue lane (`tq_requant.sby`).
- **T > 4 formal.** The unbounded core proof is at T = 4; T = 8/16/32 are covered by simulation only.
- **Real silicon.** CV-X-IF has not run on a real CVA6/CV32E40X, and there is no post-silicon test.

## Adding a case

Add it to a configuration's case list in `test_rtl.py`. Shapes that matter for benchmarking belong in `bench/workloads/gemm.csv` as well.
