<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Verification scope

This page states what the v1 evidence covers, how to reproduce it, and what it does not cover. The
detailed commands and the per-configuration case list are in [`verif/README.md`](../verif/README.md).

## 1. Differential regression (RTL vs cycle model)

`make rtl-test` builds `tq_top` (and the adapters) with Verilator for 25 configurations and runs
**1,709 cases**. Each case drives a real program through the register port, serves memory from a
Python SRAM/bus model, records every read, write and MAC start, and compares the RTL with the
cycle-model run:

1. final memory and C against numpy, bit-exact;
2. read/write transactions and MAC-start cycles and shapes;
3. performance counters, error codes and total cycles.

The configurations cover T = 4/8/16/32, shared and separate ports, minimal and reference queues,
both accumulator forms, `MAC_PIPE = 1`, clock gating on/off, the panel buffer, single-beat and burst
AXI, CV-X-IF, random `gnt` back-pressure (10–80%), SRAM latency 1–13, illegal programs and K = 0.
The full matrix and the exact counts are in [`verif/README.md`](../verif/README.md).

**What this gives you:** cycle-exact agreement with the golden model, including control, addressing,
queueing and error timing, for the tested configurations.

## 2. Formal (SymbiYosys)

| Proof | Scope | When |
| --- | --- | --- |
| `tq_cg.sby` + `check_cg_pattern.py` | Every gated register uses the `tq_cg` template; the ICG latch model equals an enable flop, with and without async reset; the gated clock never rises without the clock | `make formal` |
| `tq_fifo.sby` | Occupancy invariant, no overflow/underflow under legal use, in-order data, depths 2/3/4/8/16 | `make formal` |
| `tq_axil_regs.sby` | AXI4-Lite slave: handshake rules, response stability, one open transaction, OBI request stability | `make formal` |
| `tq_xif_regs.sby` | CV-X-IF adapter: commit-gated register access, one result per committed instruction, killed/speculative instructions have no effect | `make formal` |
| `tq_axi_mem.sby` (`cover burst`) | AXI master burst path reachability | `make formal` |
| `tq_axi_mem.sby` (`prove`) | Unbounded AXI master rules and response routing | `make formal-nightly` |
| `tq_core.sby split` | T = 4 core, any program, START/ABORT at any time, any `gnt`, any read latency: no accumulate into the draining bank, no queue overflow/underflow, OBI request stability | `make formal` |
| `tq_core.sby shared/ack/panel` | The same core invariants for shared port, `WR_ACK`, and panel buffer | `make formal-nightly` |

**What this does not prove:** arithmetic correctness. The core proof keeps only the input cone of the
structural assertions, so the multipliers and accumulators drop out. Data correctness comes from the
differential regression. The unbounded core proof is at T = 4; larger T values are simulation-only.
The AXI proof covers the shipped adapter, not a user wrapper.

## 3. Model and driver

- `make model-test`: 347 tests, including bit-exact GEMM against numpy, the real instruction bytes,
  packing, error codes, trace format, panel buffer and scaling.
- `make sw-test`: 25 tests, including byte-for-byte packing vs the model, the program builder, the
  register sequence on a fake register file, and the GEMM API end to end (C prepares, the model runs,
  C finishes) at T = 4/8/16/32.
- `make synth-check`: generic Yosys synthesis of both accumulator forms, no unintended latches, loops
  or undriven nets.
- `make rtl-lint`: Verilator `-Wall` for 12 parameter sets + 3 adapter tops + burst AXI, and Yosys
  elaboration.

## 4. Physical design

`flow/results.md` is generated from committed records in `flow/records/`. Each record names the RTL
commit, ORFS commit, platform, utilization, clock, and that it was collected from a post-route run
with activity-based power. `flow/check_lock.py` verifies the records and the locked 3D layouts.

**Known provenance gap:** the comparison records (`t8_cpa_nocg`, `t8_csa`, `t8_shared`, `t16_cpa`)
currently name older RTL commits than the reference `t8_cpa` record. The v1 release re-records them
at one commit. Until then, treat the gating and accumulator deltas as directional.

## 5. What is not covered yet

- **Coverage metrics.** No line/toggle/branch coverage report is published. Adding
  `verilator --coverage` to CI is the next verification task.
- **Random program fuzzing.** The illegal-program cases are fixed; there is no random program
  generator.
- **Reset/abort stress in simulation.** Formal covers the invariants; a directed restart-after-abort
  test would also exercise recovery and stale-response handling.
- **CV-X-IF on real silicon.** The adapter is a reference implementation; it has not run on a real
  CVA6/CV32E40X.
- **Post-silicon.** There is no silicon or FPGA SoC demo in v1 (the ECP5 out-of-context build is a
  resource/timing sanity check, not a board bring-up).
- **Same-flow comparison** against Gemmini/RVV is not complete.

## 6. How to reproduce

```sh
pip install -r requirements.txt
make model-test sw-test          # no EDA tools
make rtl-lint rtl-test           # Verilator + cocotb
make formal                      # SymbiYosys, ~3 min
make synth-check                 # generic Yosys
make lock-check                  # PPA records and locked UI layouts
python3 flow/results.py >/dev/null   # regenerate flow/results.md from records
```

The full `make` target runs model, driver, SPDX, lock, lint, regression, formal and synth checks.
CI (`.github/workflows/ci.yml`) runs the model/driver, the results-up-to-date diff, the SPDX check and
the RTL job on every push. `nightly.yml` runs the long formal proofs.
