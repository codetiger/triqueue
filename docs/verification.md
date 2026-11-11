<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Verification scope

This page states what the v1 evidence covers, how to reproduce it, and what it does not cover. The
detailed commands and the per-configuration case list are in [`verif/README.md`](../verif/README.md).

## 1. Differential regression (RTL vs cycle model)

`make rtl-test` builds `tq_top` (and the adapters) with Verilator for 26 configurations and runs
**1,742 cases** (plus a standalone Verilator `$readmemh` golden-vector test). Each case drives a
real program through the register port, serves memory from a Python SRAM/bus model, records every
read, write and MAC start, and compares the RTL with the cycle-model run:

1. final memory and C against numpy, bit-exact;
2. read/write transactions and MAC-start cycles and shapes;
3. performance counters, error codes and total cycles.

The configurations cover T = 4/8/16/32, shared and separate ports, minimal and reference queues,
both accumulator forms, `MAC_PIPE = 1`, clock gating on/off, `PERF = 0`, `PROG_BYTES = 128`, the
panel buffer, single-beat and burst AXI (including multi-beat `SLVERR`, orphan burst abort, and
read-after-write line invalidation), CV-X-IF, random `gnt` back-pressure (10–80%), SRAM latency
0–13, illegal programs, bounded random valid/invalid program fuzzing, mid-run `ABORT`/error
restart, and K = 0. The full matrix and the exact counts are in [`verif/README.md`](../verif/README.md).

**What this gives you:** cycle-exact agreement with the golden model, including control, addressing,
queueing and error timing, for the tested configurations.

## 2. Formal (SymbiYosys)

| Proof | Scope | When |
| --- | --- | --- |
| `tq_cg.sby` + `check_cg_pattern.py` | Every gated register uses the `tq_cg` template; the ICG latch model equals an enable flop, with and without async reset; the gated clock never rises without the clock | `make formal` |
| `tq_fifo.sby` | Occupancy invariant, no overflow/underflow under legal use, in-order data, depths 2/3/4/8/16 | `make formal` |
| `tq_axil_regs.sby` | AXI4-Lite slave: handshake rules, response stability, one open transaction, OBI request stability | `make formal` |
| `tq_xif_regs.sby` | CV-X-IF adapter: commit-gated register access, one result per committed instruction, killed/speculative instructions have no effect | `make formal` |
| `tq_axi_mem.sby` (`cover`, `burst`) | AXI master reachability (`cover`, `BURST_BEATS = 1`, depth 8) and bounded burst proof (`burst`, `W = 4`, `MAX_OUT = 3`, `BURST_BEATS = 2`, BMC depth 8) | `make formal` |
| `tq_axi_mem.sby` (`prove`, `burst_prove`) | Unbounded AXI master rules, response routing, and orphan tracking at `W = 4`, `MAX_OUT = 3`, `BURST_BEATS = 1` (`prove`) and `BURST_BEATS = 2` (`burst_prove`) | `make formal-nightly` |
| `tq_core.sby split` | T = 4 core (`FIFO_DEPTH = 2`, `MACQ_DEPTH = 2`, `PROG_BYTES = 8`), any program, START/ABORT at any time, any `gnt`, any read latency: no accumulate into the draining bank, no queue overflow/underflow, OBI request stability | `make formal` |
| `tq_core.sby shared/ack/ack_split/panel` | The same unbounded core invariants for shared port (`shared`), `WR_ACK = 1` on shared (`ack`) and split (`ack_split`) ports, and panel buffer (`panel`, `PANEL_BYTES = 32`) | `make formal-nightly` |

**What this does not prove:** arithmetic correctness. The core proof keeps only the input cone of the
structural assertions, so the multipliers and accumulators drop out. Data correctness comes from the
differential regression. The unbounded core proof is at T = 4; larger T values are simulation-only.
The AXI proof covers the shipped adapter (`W = 4`, `MAX_OUT = 3`, `BURST_BEATS = 1` and `2`), not a
user wrapper or larger `BURST_BEATS` values (which are covered by simulation).

## 3. Model and driver

- `make model-test`: 392 tests, including bit-exact GEMM against numpy, the real instruction bytes,
  packing, error codes, trace format, panel buffer and scaling.
- `make sw-test`: 31 tests, including byte-for-byte packing vs the model, the program builder, the
  register sequence on a fake register file, error/abort/timeout/null-argument paths, and the GEMM
  API end to end (C prepares, the model runs, C finishes) at T = 4/8/16/32.
- `make synth-check`: generic Yosys synthesis of both accumulator forms, no unintended latches, loops
  or undriven nets.
- `make rtl-lint`: Verilator `-Wall` for 12 parameter sets + 3 adapter tops + burst AXI, and Yosys
  elaboration.

## 4. Physical design

`flow/results.md` is generated from committed records in `flow/records/`. Each record names the RTL
commit, ORFS commit, platform, utilization, clock, and that it was collected from a post-route run
with activity-based power. `flow/check_lock.py` verifies the records and the locked 3D layouts. For
v1, all nine records name the same RTL commit (`cfd68f4`) and the bundle is locked as `v1.0.1`
(`rtl_Functional_equiv: true` for post-record control-path fixes in `tq_core`, `tq_frontend`,
`tq_stream`, and `tq_regs`); the gating and accumulator deltas are therefore same-RTL comparisons.
T = 16 is recorded at 741 MHz (762 MHz post-route); T = 32 has no PPA record.

## 5. What is not covered yet

- **Coverage metrics.** No line/toggle/branch coverage report is published. Adding
  `verilator --coverage` to CI is an open verification task.
- **Unbounded coverage-guided program fuzzing.** `t8_edge_cases` includes seeded random valid and
  invalid program sequences and mid-run abort/restart checks, but there is no coverage-guided
  fuzzer.
- **CV-X-IF on real silicon.** The adapter is a reference implementation; it has not run on a real
  CVA6/CV32E40X.
- **Post-silicon.** There is no silicon or FPGA SoC demo in v1 (the ECP5 out-of-context Yosys build
  is a synthesis-only resource sanity check, not a place-and-route or board bring-up).
- **Same-flow comparison** against Gemmini/RVV is not complete.

## 6. How to reproduce

```sh
pip install -r requirements.txt
make model-test sw-test          # no EDA tools
make rtl-lint rtl-test           # Verilator + cocotb
make formal                      # SymbiYosys, typically 3–11 min (PDR variance can exceed 30 min)
make synth-check                 # generic Yosys
make lock-check                  # PPA records and locked UI layouts
python3 flow/results.py >/dev/null   # regenerate flow/results.md from records
```

The full `make` target runs model, driver, SPDX, lock, lint, regression, formal and synth checks.
CI (`.github/workflows/ci.yml`) runs the model/driver, the results-up-to-date diff, the SPDX check and
the RTL job on every push. `nightly.yml` runs the long formal proofs.
