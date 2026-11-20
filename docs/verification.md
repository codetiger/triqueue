<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Verification scope

This page states what the v1 evidence covers, how to reproduce it, and what it does not cover. The
detailed commands and the per-configuration case list are in [`verif/README.md`](../verif/README.md).

## 1. Differential regression (RTL vs cycle model)

`make rtl-test` builds `tq_top` (and the adapters) with Verilator for 26 configurations and runs
**1,747 cases** (plus a standalone Verilator `$readmemh` golden-vector test). Each case drives a
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
| `tq_cg.sby` + `check_cg_pattern.py` | Every gated register uses the `tq_cg` template; the ICG latch model equals an enable flop, with and without async reset, and so does a two-level gate whose enable implies its parent's; no gated clock rises without the clock | `make formal` |
| `tq_mac_array.sby cpa2/cpa1/csa2` (`csa1` nightly) | T = 4 array, any inputs: the lazy accumulator clear is the eager clear (every quadrant holds the eager value or is zero-flagged with an eager value of zero, and the drain read returns the eager value), both accumulator forms and both `MAC_PIPE` values | `make formal` / `make formal-nightly` |
| `tq_fifo.sby` | Occupancy invariant, no overflow/underflow under legal use, in-order data, depths 2/3/4/8/16 | `make formal` |
| `tq_axil_regs.sby` | AXI4-Lite slave: handshake rules, response stability, one open transaction, OBI request stability | `make formal` |
| `tq_xif_regs.sby` | CV-X-IF adapter: commit-gated register access, one result per committed instruction, killed/speculative instructions have no effect | `make formal` |
| `tq_axi_mem.sby` (`cover`, `burst`) | AXI master reachability (`cover`, `BURST_BEATS = 1`, depth 16) and bounded burst proof including read data (`burst`, `W = 4`, `MAX_OUT = 3`, `BURST_BEATS = 2`, BMC depth 10) | `make formal` |
| `tq_axi_mem.sby` (`prove`, `burst_prove`) | Unbounded AXI master rules, response routing, orphan tracking and legal use of the internal FIFOs at `W = 4`, `MAX_OUT = 3`: every property at `BURST_BEATS = 1` (`prove`); at `BURST_BEATS = 2` (`burst_prove`) every property except the read-data equality, which is bounded by `burst` | `make formal` |
| `tq_axi_mem.sby burst_data` | Unbounded burst proof including read data at `BURST_BEATS = 2`, `MAX_OUT = 1` (one core read in flight; line buffer, hits, orphans, flush and write invalidation unrestricted) | `make formal-nightly` |
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
with activity-based power. `flow/check_lock.py` verifies the records and the locked 3D layouts. All
nine records name the same RTL commit (`8d850ba`, the current RTL) and a clean checkout, and the
bundle is locked as `v1.0.2`; the gating and accumulator deltas are therefore same-RTL comparisons.
Fmax counts every setup check, clock-gating checks included. T = 16 is recorded at 741 MHz (747 MHz
post-route); T = 32 has no PPA record.

## 5. What is not covered yet

- **Simulation coverage gaps.** `make rtl-coverage` (nightly CI, artifact `rtl-coverage`) reruns the
  regression with Verilator line and toggle coverage: 96.6% of line points (341/353) and 94.3% of
  toggle points under `rtl/` are hit across the 26 configurations (2026-11-20). The line points the
  regression never reaches are each covered by a formal proof but not in simulation: the burst line
  buffer evicting a still-valid line (`tq_axi_mem`), the `CHECK_PROT` error responses of
  `tq_axil_regs` (no configuration sets it), and two loop-reset paths in `tq_frontend`. The remaining misses cannot be reached in `tq_top*`: the `CLK_GATE` arms of gated
  register writes with gating on, and `tq_xif_regs` waiting for a register grant or response, which
  `tq_regs` gives at once and one cycle later. There is no branch or expression
  coverage, and coverage is a report, not a gate.
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
make rtl-coverage                # regression with line + toggle coverage, build/rtl-cov/summary.md
make formal                      # SymbiYosys, typically 3–11 min (PDR variance can exceed 30 min)
make synth-check                 # generic Yosys
make lock-check                  # PPA records and locked UI layouts
python3 flow/results.py >/dev/null   # regenerate flow/results.md from records
```

The full `make` target runs model, driver, SPDX, lock, lint, regression, formal and synth checks.
CI (`.github/workflows/ci.yml`) runs the model/driver, the results-up-to-date diff, the SPDX check and
the RTL job on every push. `nightly.yml` runs the long formal proofs.
