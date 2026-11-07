# RTL verification

```
make rtl-lint      # Verilator -Wall (7 configurations) + Yosys elaboration (both ACC_FORMs)
make rtl-test      # cocotb + Verilator regression against the cycle model (~60 s)
make formal        # SymbiYosys: FIFO proof (~1 min) + core proof, split port (~3 min)
make formal-nightly  # core proof, shared port (1-80 min, varies widely; nightly CI)
make synth-check   # generic Yosys synthesis, both ACC_FORMs: no latches, loops or undriven nets
python3 -m pytest verif -q -k t16    # one configuration
```

Tools: Verilator ≥ 5, cocotb ≥ 2.0, Yosys (with `yosys-abc`), SymbiYosys with Yices.

## Regression (`test_rtl.py`, `tb/`)

Each configuration in `test_rtl.py` builds `tq_top` once (in `build/rtl/<name>/`) and runs its cases in one simulation. For every case the testbench (`tb/tq_tb.py`) loads the program through the register port, serves memory from a Python SRAM model, and records every read, write and MAC start. `tb/tq_cocotb.py` runs the same GEMM in the cycle model and compares:

1. Final memory, bit-exact, and C against numpy.
2. The read and write transactions with their cycles, and the cycle and shape of every MAC start.
3. The performance counters (instructions, beats, reads, writes, cycles) and the total cycle count.

| Configuration | What it covers | Cases |
| --- | --- | --- |
| `t8_default` | Spec design point. Every edge shape and K padding, signedness, SRAM latency 1–13, 64³, drain-limited K, GEMV | 198 |
| `t8_shared_port` | Drain writes on the read port | 41 |
| `t8_small_queues` | FIFO depth 2, MAC queue 2: maximum stalls | 77 |
| `t8_backpressure` | Random `gnt` stalls (10–80%) on both ports | 51 |
| `t8_shared_backpressure` | Shared port + stalls | 12 |
| `t8_acc_csa` | `ACC_FORM = CSA` (carry-save accumulator, the non-default form): edge shapes, signedness, 64³, GEMV, stalls | 116 |
| `t32` | T = 32 (1024 MACs) | 81 |
| `t16` | T = 16 (256 MACs) | 116 |
| `t4` | T = 4 | 148 |

Stalled cases are cycle-exact too: the testbench draws its grants from the model's `GrantSource` with the case's seed (`docs/golden-vectors.md`). It also checks OBI request stability: every ungranted request reappears unchanged the next cycle.

## Formal (`formal/`)

**`tq_fifo.sby`** proves by k-induction (unbounded), at depths 2, 3, 4, 8 and 16: the occupancy invariant, no overflow or underflow under legal use, and that every entry is stored and popped in order with its data intact. Every queue in the core is an instance of this FIFO.

**`tq_core.sby`** proves, unbounded (ABC PDR), three properties of the whole `tq_core` at T = 4 with minimal queues (`tq_core_env.sv`): an accumulate never targets the bank being drained, no queue (QA, QB, MAC and stream command queues, read tags) overflows or underflows, and an ungranted request stays asserted with the same address (OBI request stability). It holds for any program (the program buffer is an arbitrary constant), START and ABORT at any time, any `gnt` pattern on both ports, and any read latency including zero. Tasks `split` and `shared` cover both port arrangements. `make formal` runs `split` (~3 min); `shared` takes 1–80 min (PDR time varies widely) and runs in the nightly CI workflow (`make formal-nightly`). With `TQ_FORMAL_CORE` defined, `tq_fifo`'s legal-use conditions become assertions instead of assumptions.

Before solving, the script keeps only the input cone of the assertions and assumptions, so the multipliers and accumulators drop out (and the accumulator form doesn't matter). The environment isn't vacuous: with the reference kernel (`KERNEL = 1`) a cover of accumulate-during-drain is reached at step 20. The proof found that START or ABORT with reads in flight let the stale responses overflow an operand FIFO; `tq_stream` now discards them.

## Adding a case

Add it to a configuration's case list in `test_rtl.py`. Shapes that matter for benchmarking belong in `bench/workloads/gemm.csv` as well.
