# RTL verification

```
make rtl-lint        # Verilator -Wall (12 parameter sets + 3 adapter tops + burst AXI) + Yosys elaboration
make rtl-test        # cocotb + Verilator regression against the cycle model (1,709 cases in 25 configs, ~165 s)
make formal          # SymbiYosys: clock-gating, FIFO, AXI4-Lite & CV-X-IF proofs, AXI4 burst cover, T=4 core split (~3–11 min, PDR variance)
make formal-nightly  # SymbiYosys: T=4 core (shared, ack, panel) + unbounded AXI4 master proof (~10–80 min)
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
| `t8_edge_cases` | Illegal programs (error codes & exact stop cycle), `ZERO_C` during drain, `K = 0`, `PROG` writes while busy | 22 |
| `t8_edge_shared` | Illegal/edge-case programs with `SHARED_PORT = 1` and `WR_ACK = 1` | 6 |
| `t8_panel` | `A` panel buffer (`PANEL_BYTES = 1024`, spec §10.9): replay hits, K overflow fallback, stalls, errors | 175 |
| `t8_panel_shared` | `A` panel buffer (`PANEL_BYTES = 512`) + shared port + `WR_ACK = 1` | 9 |
| `t16_panel` | `T = 16` with `PANEL_BYTES = 2048` | 77 |
| `t4_panel` | `T = 4` with `PANEL_BYTES = 256` | 149 |
| `t8_mac1` | 1-stage MAC (`MAC_PIPE = 1`, no product register) | 119 |
| `t8_mac1_csa_shared` | `MAC_PIPE = 1` + carry-save + shared port + panel buffer | 9 |
| `t8_no_clock_gating` | `CLK_GATE = 0` (plain enable flops) | 74 |
| `t8_axil` | `tq_top_axil`: AXI4-Lite slave register adapter | 11 |
| `t8_xif` | `tq_top_xif`: CV-X-IF coprocessor instruction adapter (speculative/killed copies, random order) | 11 |
| `t8_write_ack` | `WR_ACK = 1`, `WR_OUT_MAX = 2` with variable write-response latency | 30 |
| `t8_write_ack_shared` | `WR_ACK = 1`, `WR_OUT_MAX = 1`, `SHARED_PORT = 1` | 9 |
| `t8_axi` | `tq_top_axi`: AXI4-Lite registers + AXI4 memory master, ready stalls, `SLVERR` → `STATUS.BUS_ERR` | 28 |
| `t8_axi_burst` | `tq_top_axi` with `BURST_BEATS = 4` and `ID_W = 4`: read line buffer, aligned bursts, same transactions/results | 12 |
| `t32` | `T = 32` (1024 MACs/cycle, 512-bit ports) | 81 |
| `t16` | `T = 16` (256 MACs/cycle, 256-bit ports) | 118 |
| `t4` | `T = 4` (16 MACs/cycle, 64-bit ports) | 150 |
| **Total** | **25 configurations** | **1,709** |

Stalled cases are cycle-exact too: the testbench draws its grants from the model's `GrantSource` with the case's seed (`docs/golden-vectors.md`). It also checks OBI request stability: every ungranted request reappears unchanged the next cycle.

## Formal (`formal/`)

**`tq_cg.sby`** (with `check_cg_pattern.py`) checks that every gated register in `rtl/` uses the `tq_cg` template, and proves (multiclock) that the `tq_icg` latch model is equal to an ungated enable flop with and without async reset.

**`tq_fifo.sby`** proves by k-induction (unbounded), at depths 2, 3, 4, 8 and 16: the occupancy invariant, no overflow or underflow under legal use, and that every entry is stored and popped in order with its data intact. Every queue in the core is an instance of this FIFO.

**`tq_axil_regs.sby`**, **`tq_xif_regs.sby`** and **`tq_axi_mem.sby`** prove the AXI4-Lite register slave, CV-X-IF instruction adapter and AXI4 memory master (`tq_axi_mem.sby` runs `cover` in `make formal` and the unbounded `prove` in `make formal-nightly`).

**`tq_core.sby`** proves, unbounded (ABC PDR), the core invariants of `tq_core` at T = 4 with minimal queues (`tq_core_env.sv`): an accumulate never targets the bank being drained, no queue (QA, QB, MAC and stream command queues, read tags) overflows or underflows, and an ungranted request stays asserted with the same address (OBI request stability). It holds for any program (the program buffer is an arbitrary constant), START and ABORT at any time, any `gnt` pattern on both ports, and any read latency including zero. Tasks `split`, `shared`, `ack` (`WR_ACK = 1`) and `panel` (`PANEL_BYTES = 64`) cover all modes. `make formal` runs `split` (~3–11 min, PDR variance); `shared`, `ack` and `panel` run in `make formal-nightly`. With `TQ_FORMAL_CORE` defined, `tq_fifo`'s legal-use conditions become assertions instead of assumptions.

Before solving, the script keeps only the input cone of the assertions and assumptions, so the multipliers and accumulators drop out (and the accumulator form doesn't matter). The environment isn't vacuous: with the reference kernel (`KERNEL = 1`) a cover of accumulate-during-drain is reached at step 20. The proof found that START or ABORT with reads in flight let the stale responses overflow an operand FIFO; `tq_stream` now discards them.

## What is not covered yet

- **Coverage metrics.** The regression is a directed/random functional suite; it does not publish line, toggle or branch coverage. Adding `verilator --coverage` to CI is a v1.1 task (`docs/verification.md`).
- **Random program fuzzing.** The illegal-program cases are fixed. Random valid/illegal program generation would exercise the front end and error paths more broadly.
- **Data correctness in formal.** `tq_core.sby` proves structural invariants, not the arithmetic result; data correctness comes from the cycle-exact differential regression.
- **T > 4 formal.** The unbounded core proof is at T = 4; T = 8/16/32 are covered by simulation only.
- **Real silicon.** CV-X-IF has not run on a real CVA6/CV32E40X, and there is no post-silicon test.

## Adding a case

Add it to a configuration's case list in `test_rtl.py`. Shapes that matter for benchmarking belong in `bench/workloads/gemm.csv` as well.
