# RTL verification

```
make rtl-lint      # Verilator -Wall (6 configurations) + Yosys elaboration
make rtl-test      # cocotb + Verilator regression against the cycle model (~40 s)
make formal        # SymbiYosys proofs
make synth-check   # generic Yosys synthesis: no latches, loops or undriven nets
python3 -m pytest verif -q -k t16    # one configuration
```

Tools: Verilator ≥ 5, cocotb ≥ 2.0, Yosys, SymbiYosys with Yices.

## Regression (`test_rtl.py`, `tb/`)

Each configuration in `test_rtl.py` builds `tq_top` once (in `build/rtl/<name>/`) and runs its cases in one simulation. For every case the testbench (`tb/tq_tb.py`) loads the program through the register port, serves memory from a Python SRAM model, and records every read, write and MAC start. `tb/tq_cocotb.py` runs the same GEMM in the cycle model and compares:

1. Final memory, bit-exact, and C against numpy.
2. The read and write transactions with their cycles, and the cycle and shape of every MAC start.
3. The performance counters (instructions, beats, reads, writes) and the total cycle count.

| Configuration | What it covers | Cases |
| --- | --- | --- |
| `t8_default` | Spec design point. Every edge shape and K padding, signedness, SRAM latency 1–13, 64³, drain-limited K, GEMV | 198 |
| `t8_shared_port` | Drain writes on the read port | 41 |
| `t8_small_queues` | FIFO depth 2, MAC queue 2: maximum stalls | 77 |
| `t8_backpressure` | Random `gnt` stalls (10–80%) on both ports | 51 |
| `t8_shared_backpressure` | Shared port + stalls | 12 |
| `t16` | T = 16 (256 MACs) | 116 |
| `t4` | T = 4 | 148 |

The cycle model has no back-pressure, so stalled cases check results, write order, MAC shapes and counters, but not cycles.

## Formal (`formal/`)

`tq_fifo.sby` proves by k-induction (unbounded), at depths 2, 3, 4, 8 and 16: the occupancy invariant, no overflow or underflow under legal use, and that every entry is stored and popped in order with its data intact. Every queue in the core is an instance of this FIFO.

## Adding a case

Add it to a configuration's case list in `test_rtl.py`. Shapes that matter for benchmarking belong in `bench/workloads/gemm.csv` as well.
