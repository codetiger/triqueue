<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

All notable changes to the Tri-Queue project. This format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **TFLite int8 epilogue (`MODE[9]`, `CFG` 8 `EPI_BASE`, `CFG` 9 `EPI_OUT`, decision D1).** RTL:
  `EPI_LANES` (default T/2) two-stage lanes in `tq_drain` (+ bias, << shift, 32 × 32 × M0; rounding
  high multiply, rounding right shift, + zero point, clamp) into a T-byte row buffer that holds the
  lanes while a row waits for `gnt`; `tq_core` fetches a tile column's 5 words into a T × 9-byte
  parameter register (a tile-column FIFO rides beside the MAC command queue) and `tq_stream` reads
  them ahead of A/B with a third response tag. Regression `t8_epi`, `t8_epi_walk`, `t8_epi_l1`,
  `t8_epi_l8`, `t8_epi_shared`, `t4_epi` (2,573 cases in 37 configurations), bit-exact against
  `requant.requantize` and cycle-exact with the model; disabling the lane hold fails them.
  Formal found a bug the regression had missed: parameter reads bypass the operand-FIFO credits,
  so with memory latency above 2 × `FIFO_DEPTH` the response-tag FIFO (2 × `FIFO_DEPTH` deep)
  overflowed; it is now 2 × `FIFO_DEPTH` + 5 deep (at most 5 parameter reads are in flight), and
  `t8_epi` gained cases at latency 20 and 24 that fail on the old sizing. The core proofs other than
  the epilogue tasks build with the formal-only `TQ_FORMAL_NO_EPI` (`MODE[9]` reserved, and the tag
  FIFO without the parameter-read slots), since `split` with the epilogue did not converge in 60 min
  (was ~9). `ui/data/traces_data.js` regenerated (10 `CFG` registers).
  Model: Per output column `out = clamp(MultiplyByQuantizedMultiplier(acc +
  bias, M0, shift) + zp_out, act_min, act_max)`, bit-exact with TFLite's default double rounding
  (`tri_queue/requant.py`). The drain loads a tile column's parameters (5 port words) through the read
  port ahead of A/B reads when the column changes; `epi_lanes` (default T/2) values per cycle.
  `MODE[9]` needs `MODE[2]`; `MODE[31:10]`, `EPI_OUT[31:24]` and `CFG` registers 10–15 are rejected
  (`cfg_reg` now uses register 10, `cfg_val_mode` bit 10). `model/dse.md` §14: with the column walk
  and 4 lanes, DS-CNN 96.5%, VWW 84.1%, ResNet-8 98.9%, BERT-tiny 99.7% (shift-only int8: 97.1%,
  89.0%, 99.2%, 100.0%); VWW 87.8% with 8 lanes. `tests/test_epilogue.py`: 49 cases, both walks,
  1/4/8 lanes, back-pressure, shared port, error rules. Without `epi`, programs, memory images and
  every published result are unchanged.
- **Column-major tile walk (`MODE[8]`, decision D2).** `LOOP_T` and `STREAM_A`/`STREAM_B` walk down
  tile columns; the panel buffer then keeps the B panel (the weights), and per-column epilogue
  parameters stay constant down a column. Software picks the walk per GEMM; default 0 keeps every
  existing program, result and cycle count (`model/results.md`, `model/dse.md` byte-identical).
  RTL: the frontend's three tile walkers gain the column order; `tq_core` routes B through
  `tq_stream`'s buffered side and A through the other (`tq_stream.swap_i`), with the round-robin
  order and the panel key kept in terms of the real operands. `MODE[8]` may change only before the
  run's first `STREAM_A`/`STREAM_B` (`ERR_CFG_VAL`): the core proof found that flipping it with
  operands in flight overflowed QA. `MODE[31:9]` stays reserved (`cfg_val_mode` now sets bit 9).
  Model `Params.walk_cols` sets the bit; `tri_queue.vectors --walk-cols`; regression
  `t8_walk_cols`, `t8_walk_panel`, `t8_walk_panel_shared`, `t4_walk_panel` and the edge programs
  `walk_after_stream` / `walk_before_stream` (2,210 cases in 31 configurations).
  The core proofs cover the walk in `split`, `shared`, `ack` and `panel`; `ack_split` builds with
  the formal-only `TQ_FORMAL_NO_WALK` (`MODE[8]` reserved), since with the walk PDR did not converge
  in 90 min.

- **Capability discovery (`HW_CONFIG2`, `0x018`).** Reports the features (`[0]` column-major
  walk, `[1]` epilogue) and the build options software could not read before: `PERF`, `WR_ACK`,
  carry-save `ACC_FORM`, `MAC_PIPE = 1`, `WR_OUT_MAX`, log2 `EPI_LANES` and `PANEL_BYTES / W`. A
  core before v1.1 reads 0 (neither feature). `CFG` 8 and 9 read back at `0x060` / `0x064`.
  `tq_probe` decodes it into new `tq_info` fields; the adapter tops forward `EPI_LANES`. Every
  regression configuration checks `HW_CONFIG2` against its parameters and the `CFG` 8/9 readback.

- **Driver (`sw/`) for the v1.1 modes.** `TQ_MODE_WALK_COLS` / `TQ_MODE_EPI`; `tq_gemm_program`
  emits `CFG` 8/9 with the epilogue (60 bytes); `tq_pack_epi` / `tq_epi_table_bytes` /
  `tq_epi_out` build the parameter table and `EPI_OUT` from TFLite's per-channel multiplier and
  shift; `tq_fold_input_zp` folds the input zero point into the bias; and the int8 layer API
  (`tq_layer_prepare` / `finish` / `run`) runs one quantized layer, picking the column walk when A
  has more than one tile row. New error `TQ_ERR_UNSUPPORTED` (22) when the core lacks the epilogue.
  `make sw-test` runs it end to end on the cycle model against `requant.requantize` (78 tests).

- **End-to-end DS-CNN (`bench/e2e/`, roadmap C1).** MLPerf Tiny's int8 keyword-spotting DS-CNN
  (`kws_ref_model.tflite`, MLCommons, Apache-2.0) runs layer by layer on the cycle model: convolutions
  (host im2col padded with the input zero point) and the classifier as GEMM + epilogue on the core,
  depthwise and average pool on the host, every tensor bit-exact with the TFLite interpreter's
  reference kernels for 3 inputs at T = 8 and T = 4. T = 8: 38,339 core cycles per inference at 96.5%
  utilization; the host's 288,000 depthwise MACs dominate a scalar host. `export_dscnn.py` (needs
  `ai-edge-litert`, not a requirement) writes the checked-in `dscnn_golden.npz`; `dscnn.py` writes
  `results.md` (CI checks it); `make e2e-test`. RTL: `t8_dscnn` and `t4_dscnn` run the same layers with
  C compared against TFLite's tensors (2,586 cases in 39 configurations).

### Added (v1.1 studies, model only)

- `Params.walk_cols`: a column-major tile walk (since promoted to `MODE[8]`, below); the panel buffer then keeps the B panel. `dse.md` §12:
  utilization within 0.1 points of the row walk on every network, per-column epilogue parameters
  0.1–0.5% of the operand traffic instead of 2–6%, and 18% fewer SRAM bytes per MAC on ResNet-8.
- `tri_queue/requant.py`: TFLite's int8 requantization (`MultiplyByQuantizedMultiplier`, double and
  single rounding, `QuantizeMultiplier`), a line-by-line transcription checked against a vectorized
  version. `dse.md` §13: requantization lanes 1–8 against utilization and multiplier cost.

### Fixed

- **AXI4 master proofs were unsound and never finished (`verif/formal/tq_axi_mem.sby`):** the
  proof read `tq_fifo.sv` without `TQ_FORMAL_CORE`, so each embedded tag / slot / order FIFO
  loaded `tq_fifo`'s stand-alone proof: its legal-use conditions (no pop when empty, no push when
  full) were assumed instead of checked, and `pushed_q < 28` bounded every trace. The FIFOs now
  assert their legal use, as in `tq_core.sby`, and `prove` passes in about 20 s (was 3 min).
  `burst_prove` had been cancelled at the 2 h nightly limit since v1.0.1; it now proves every
  property except the burst read-data equality, unbounded, in about 20 s. That equality is not
  inductive without strengthening invariants at `MAX_OUT = 3` (PDR does not converge in hours), so
  it is checked there by the `burst` BMC, now depth 10 (was 8), and unbounded at `MAX_OUT = 1` by
  the new nightly task `burst_data` (~5 min).

### Fixed (docs)

- `docs/integration.md` and the v1.0.1 notes described `HW_CONFIG[26:21]` (`PANEL_BYTES / 64`) and
  `HW_CONFIG[27]` (`MAC_PIPE`), and `STATUS[12]` as `PENDING`. The RTL never had those fields
  (`[31:24]` is the program-buffer size) and `PENDING` is `STATUS[4]`; the panel size and
  `MAC_PIPE` are now in `HW_CONFIG2`. `sw/README.md` no longer says `tq_probe` decoded them from
  `HW_CONFIG`, and `verif/README.md` gives `t8_mcu_custom`'s real `PROG_BYTES` (120).
- Spec §1, §2.1, §7, §9, README and the architecture figure quoted 8,562 flip-flops (8,563 shared
  port, 12,530 carry-save) and spec §10.2 / §10.8 the v1.0.0 accumulator-form and clock-gating
  deltas; they now quote the v1.0.2 records (8,571 / 8,572 / 12,539; plain adder −20.9% / −16.9%
  cell area, −31.6% flops, −21.9% / −19.6% energy per MAC, 1,001.9 vs 968.4 MHz on ASAP7; gating
  −25–54% / −26–49% power, −10.3% / −9.0% cell area, −38.2% / −35.8% energy per MAC).
  `flow/fpga/README.md` now says its 8,563 flip-flops were measured at v1.0.1.

### Added

- `make rtl-coverage` (`TQ_COVERAGE=1` in `verif/test_rtl.py`, `tools/rtl_coverage.py`, nightly CI
  job with an `rtl-coverage` artifact): the regression with Verilator line + toggle coverage, merged
  per source file. 96.3% of line points and 94.3% of toggle points under `rtl/`; the unreached lines
  are listed in `docs/verification.md` §5.
- Edge programs `reserved_zero_c`, `reserved_stream_a`, `reserved_stream_b`, `reserved_swap_c` and
  `reserved_fence` (`model/tri_queue/programs.py`, model tests and `t8_edge_cases`): a non-zero
  reserved argument on each of these opcodes stops with `ERR_OPCODE` at the model's cycle. The
  first coverage run showed the regression never reached those checks (line coverage 94.9% →
  96.3%).
- `t8_axil_prot`: `tq_top_axil` with `CHECK_PROT = 1`. The AXI4-Lite testbench now issues privileged,
  secure accesses (`AxPROT = 0b001`) and checks that unprivileged or non-secure writes and reads get
  `SLVERR` and leave the registers unchanged; no configuration exercised `CHECK_PROT` before. Line
  coverage 97.2%.
- `t8_axi_burst`: four shapes with half-width edge panels and odd `Kp / 8` (12×12×24, 36×4×8,
  28×28×24, 5×64×40) leave partly read lines in the read burst buffer, so it has to evict a
  still-valid line, which no case did before. `tq_axi_mem` line coverage 100%, total 97.5%.
- Edge program `loop_t_in_k`: a `LOOP_T` nested in a `LOOP_K` that runs once, both ending at the same
  byte (legal for a 4×4 tile with K ≤ 8). It reaches the frontend's inner-`LOOP_T` and outer-`LOOP_K`
  frame ends, which the GEMM kernel never does; model test and two `t8_edge_cases` runs. Line
  coverage 98.0% (346/353): every line point reachable in `tq_top*` is hit. The regression is 1,755
  cases in 27 configurations.

### Fixed (SoC bench)

- `bench/soc/chipyard` had not been run since pre-v1 RTL and no longer built:
  `bench/chipyard/setup.sh` now skips Gemmini's `rocc-software` test submodule (its GitHub
  repository is gone, which failed Chipyard's setup) and repeats submodule setup when an earlier run
  stopped after the conda step; `TriQueue.scala` takes the master ID width from the edge
  (`me.bundle`), no longer drives `AxREGION` (rocket-chip's AXI4 bundle has none; `tq_top_axi` drives
  it 0), and drops the input port defaults that Chipyard's Verilator 5.022 rejects.
- Re-run on the v1.0.2 RTL: 64³ passes bit-exact with the model's beats, reads and writes in 17,187
  cycles (23.8%) with the shim's 4-beat read bursts, and 38,686 cycles (10.5%) with single-beat
  reads (was 38,295 on pre-v1 RTL). README, `docs/benchmarks.md` §6, `docs/integration.md` and spec
  §11 quote the new run; `docs/integration.md` §8 notes the input port defaults.
- First cached-memory profile (`docs/benchmarks.md` §6): with 4-beat bursts, `FIFO_DEPTH = 16` / `32`
  change nothing (17,181 / 17,168 cycles) and a 1 KiB panel buffer gives 14,075 cycles (29.1%,
  44% fewer reads). The SoC's TileLink path, not the core or adapter, sets the rate.

### Fixed (testbench)

- `TqTbXif` drew a 0–2 cycle `x_result_ready_i` delay but counted it from before the result was
  valid, so it always ran out first and `t8_xif` never back-pressured a CV-X-IF result. The delay
  now counts cycles with a valid result (coverage: `S_RESULT` held 619 cycles for 315 results;
  line coverage 96.6%).

### Changed

- `docs/benchmarks.md` §3: T = 32 has utilization results only, no place-and-route record.
- `tools/check_doc_numbers.py` (`make doc-check`, in `make all` and the CI results job): every
  flop count quoted in the public docs and the diagram generator must be a recorded one, and the
  spec §10.2 / §10.8 and `docs/benchmarks.md` §5 comparison paragraphs must quote the comparison
  rows of `flow/results.md`.
- `tq_axi_mem_env.sv`: in burst mode the environment keeps only the address bits its assumptions
  leave free and follows one arbitrary word address (`sym_a`) through the line buffer instead of
  full addresses as data (with the FIFO fix, 757 instead of 1,229 state bits after `opt`), and
  asserts that AR addresses are line aligned and in range. Injected wrong-beat and missing-flush
  bugs still fail `burst` within a few cycles.
- `make formal` now runs all four `tq_axi_mem.sby` tasks (about 1.5 min); `make formal-nightly` and
  `nightly.yml` keep the long `tq_core` proofs and add `tq_mac_array.sby csa1`, which the
  Makefile ran but the nightly workflow had left out.
- `release.yml` takes the release notes from the tag's `CHANGELOG.md` section (failing before
  publishing if there is none) instead of GitHub's generated list.

## [v1.0.2] — 2026-11-19

Idle-logic power and a new silicon layout release. Activity measurements showed the multiplier
inputs already change only when a beat consumes operands, so operand isolation in the array has
nothing to remove; these changes target the idle switching that is left. No cycle count, result or
register changes; the model is untouched. All nine PPA records are re-run at the new RTL (`8d850ba`)
and the 3D layouts are locked as `v1.0.2`. Against the earlier records of the same workloads, energy
per MAC is 4.9–7.2% lower on sky130hd and 8.2–12.8% lower on ASAP7.

### Changed

- **Drain output-mode isolation (`tq_drain`):** only the selected output path sees the bank
  values (the int32 lane select, or the int8 shift and clamp), gated by the run-constant
  `MODE[2]`; `wr_wdata_o` holds the last staged word between writes instead of returning to zero
  (it is defined only with `wr_req_o`, spec §6.3).
- **Lazy accumulator clear (`tq_mac_array`):** a clear sets one zero flag per bank and quadrant
  (eight flops at any T) instead of writing the bank; a flagged quadrant reads as zero as the
  accumulate base and at the drain read port. The bank clock gates now open only for accumulates.
- **Two-level clock gating (`tq_regs`, `tq_frontend`):** the 64 program-buffer byte gates and the
  eight `CFG` gates sit behind one parent gate per group, so their clock pins no longer toggle
  every cycle.

### Added

- `verif/formal/tq_mac_array.sby`: the lazy clear is the eager clear (T = 4, any inputs, both
  accumulator forms and both `MAC_PIPE` values; `csa1` runs nightly). `tq_cg.sby` also proves the
  two-level gate.
- `flow/census.py`, `flow/blocks.py`: toggles per functional block and per beat from an activity
  SAIF; `flow/power.py` adds power per block (`power_blocks_w`) to new records.
- `flow/logo.tcl`: layouts carry the author's signature, "codetiger.in", in 5×7-pixel letters of
  decap and filler cells at the bottom centre of the core (`NOTICE`). The netlist is unchanged;
  `results.py` keeps the logo cells out of the recorded cell area and count. The 3D viewer draws
  them gold.

### Physical design

- **Records re-run at `8d850ba`** (all nine, native ORFS `d0e5b3c13`, clean checkout) with the
  layout signature, and the GEMV `fcae_fc640_128` in every record and in the geometric means. ASAP7
  T = 8: 10,062 µm², 1,001.9 MHz, 0.93 pJ/MAC geometric mean over four workloads (0.72 pJ over the
  three of the earlier records, was 0.79). sky130hd T = 8: 0.677 mm², 99.2 MHz, 22.5 pJ/MAC (18.0 pJ
  over the earlier three, was 19.1). T = 16 now closes its 741 MHz clock (747 MHz post-route).
- **Fmax counts every setup check:** `results.py collect` records 1 ÷ (period − worst setup slack),
  as `flow/results.md` defines it. OpenROAD's register-to-register `fmax`, which skips clock-gating
  checks and read 2–13% high on the new runs, is kept as `fmax_reg2reg_hz`.
- `ppa.py` writes an integer clock period in one form (10, not 10.0), so `collect` matches a run's
  `config.mk` to the reference settings; `collect` no longer counts its own outputs (other records,
  `results.md`) as a dirty checkout.
- `flow/results.md` notes the simulated window (the run plus about 100 setup cycles since the v1.0.1
  regression added register-port checks), which can only overstate energy per MAC.
- Docs (`README.md`, `docs/benchmarks.md` §5, `docs/verification.md` §4, spec §7.3, §10 and §11,
  `docs/integration.md`) and the silicon PPA figure quote the new records.

## [v1.0.1] — 2026-11-11

A comprehensive robustness, verification, driver, build/flow and documentation release resolving 110
review findings across the RTL core, bus adapters, cycle model, C driver, formal proofs, physical
design lock, CI/release pipeline, and specification.

### Added

- **Quiescence tracking (`STATUS.PENDING`, `STATUS[12]`) and line-buffer flush (`flush_o`):**
  `tq_top` exposes `flush_o` (pulsed on `START` and `ABORT`) and `ext_pending_i`, and reports
  `STATUS.PENDING` (`STATUS[12]`, `TQ_STATUS_PENDING`) whenever the core is busy, read tags or
  `WR_ACK` write responses are in flight, or the AXI master (`tq_axi_mem.pending_o`) has open bus
  transactions. `tq_axi_mem` invalidates its read burst line buffer on `flush_i`.
- **Extended program and configuration error detection (`ERR_LOOP = 6`, `ERR_CFG_VAL = 7`, `ERR_OPERAND = 8`):**
  both the cycle model and RTL detect and report improperly nested loops or empty loop bodies
  (`ERR_LOOP`), out-of-range `CFG` values (`K > 0xFFFF_FFF8`, `M/N` exceeding 65,535 tiles,
  `0 < C_LD < row_bytes`, or 32-bit address wrap in `A_BASE/B_BASE/C_BASE`, `ERR_CFG_VAL`),
  leftover `QA`/`QB` operands at `FENCE` or starved `VOUTER_MAC` beats (`ERR_OPERAND`), and non-zero
  reserved instruction argument bits or `MODE[31:8]` (`ERR_OPCODE`).
- **Expanded `HW_CONFIG` discovery:** `HW_CONFIG[26:21]` reports `PANEL_BYTES / 64` and
  `HW_CONFIG[27]` reports `MAC_PIPE - 1`; `sw/tq.c` decodes both into `tq_info`.
- **Adapter capabilities and elaboration guards:** `tq_axil_regs` adds a 1-deep independent `AW`/`W`
  skid buffer and optional `CHECK_PROT` privilege/security checking (`SLVERR` on unprivileged or
  non-secure access). `tq_top_axil`, `tq_top_axi` and `tq_top_xif` forward all core synthesis
  parameters. Elaboration `$error` checks now guard `T <= 128`, `FIFO_DEPTH <= 255`,
  `MACQ_DEPTH` in `[2, 15]`, `SCMD_DEPTH >= 2`, `PROG_BYTES` in `[64, 1020]`, `PANEL_BYTES <= 2048`
  (unless `ALLOW_LARGE_FLOP_PANEL = 1`), `ADDR_W >= 12`, and `X_ID_W` in `[1, 8]`.
- **C driver hardening (`sw/tq.h`, `sw/tq.c`):** added `TQ_ERR_BUSY`, `TQ_DEFAULT_MAX_POLLS`,
  `TQ_WMB()` / `TQ_RMB()` memory-ordering barriers, `tq_set_cache_ops()` cache clean/invalidate
  callbacks, 64-bit overflow checks on all size and workspace calculations, dynamic `info->port_bytes`
  alignment checks, `PROG` NOP-padding past `len`, and `extern "C"` / `TRIQUEUE_TQ_H` guards.
- **Model & golden-vector enhancements (`model/tri_queue/`):** `Params.__post_init__` validates all
  parameters with `ValueError`, auto-derives `port_bytes = 2 * tile` on `dataclasses.replace()`,
  adds `TriQueueCore.step()`, checks memory bounds and A/B/C overlap (`MemoryAccessError`), and
  detects no-progress deadlocks. `vectors.py` exports `gnt.txt` when `gnt_stall > 0`, records
  `model_version`, `git_rev`, `seed`, `a_dtype`, `b_dtype`, `error`, `port_stall` and panel-buffer
  counters in `meta.json`, and supports `--out-bytes`, `--out-shift`, `--panel-bytes`, `--wr-ack`,
  `--mac-pipe`, `--shared-port`, `--gnt-stall` and `--gnt-seed`.
- **Verification & formal proofs (`verif/`):** expanded the cocotb differential regression to
  **1,742 cases across 26 configurations** (adding `t8_mcu_custom` for `PERF = 0, PROG_BYTES = 128`,
  a standalone Verilator `$readmemh` golden-vector test, `check_host_contract`, `check_burst_special`,
  mid-run `ABORT`/error restart tests, seeded random valid/invalid program fuzzing, and `TQ_CASE`
  filtering). Strengthened `tq_xif_regs.sby` with an independent 16-entry reference shadow,
  `tq_axi_mem.sby` with unconstrained `RRESP`/`BRESP`, epoch-tagged read data, and unbounded
  `burst_prove` (`BURST_BEATS = 2`), and `tq_core.sby` with `ack_split` (`SHARED_PORT = 0, WR_ACK = 1`).
- **Supply-chain, packaging & CI gates:** capped major dependency bounds in `requirements.txt` and
  `pyproject.toml`, added `REUSE.toml`, `tools/check_version.py`, and the `SECURITY.md`
  supported-versions table.

### Fixed

- **Burst line-buffer coherency across runs (`tq_axi_mem`):** `START` and `ABORT` now pulse `flush_o`
  to invalidate `lv_q` in `tq_axi_mem`, preventing stale partial burst lines from being served on
  back-to-back runs reusing the same operand addresses (`B1`).
- **Same-cycle `IRQ_STATUS` W1C and `START` vs `done_i` races (`tq_regs`):** hardware interrupt set
  events now take priority over a same-cycle W1C write on the same bit, and an accepted `START`
  clears `done_q` cleanly (`B2`).
- **PC wrap and `CFG` ordering (`tq_frontend`, `core.py`):** widened `PCW` to `$clog2(PROG_BYTES + 16)`
  and padded the model program buffer by 16 NOPs so a skipped `LOOP_K 15` near the end of `PROG`
  cannot wrap `pc_q` (`B4`). `CFG` now waits until the MAC, stream and drain pipelines are idle so
  queued work always sees the configuration under which it was issued (`B5`), and `cfg_we` is gated
  by `!err` on a truncated `CFG` (`B11`).
- **Beat / product-register leak across `START` and reset/X hygiene (`tq_core`, `tq_mac_array`, `tq_drain`, `tq_stream`, `tq_regs`):**
  gated `mac_start` and `beat` with `running_q && !clear`, cleared `pv_q` on `clear` (`B6`), made
  `ABORT` win over `START` in a combined `CTRL` write (`G13`), preserved `CFG` readback across
  `ABORT` (`G14`), and masked/reset `rd_addr_o`, `wr_addr_o`, `wr_be_o`, `wr_wdata_o`, `reg_rdata_o`
  and `m_wdata_o` when invalid (`G15`).
- **CV-X-IF modular-age batch commit and kill (`tq_xif_regs`):** `x_commit` now commits an open
  instruction when `commit_kill = 0` for `cur_id` or any younger ID, and kills it when
  `commit_kill = 1` for `cur_id` or any older ID (modulo `2^X_ID_W`, `B7`).
- **AXI master zero-latency and spurious responses (`tq_axi_mem`):** guarded `u_rtag`/`u_wtag` pops
  against empty tag FIFOs (`rt_cnt == 0`, `wt_cnt == 0`), bypassed the tag FIFO on same-cycle
  handshake + response (`lat = 1`), and flagged `bus_err_o` on spurious responses (`G8`).
- **Release and packaging reproducibility:** fixed duplicate vector tarball upload in
  `.github/workflows/release.yml`, made `make dist` deterministic (`tar --sort=name --mtime`,
  `gzip -n`), and updated `CITATION.cff` (`date-released: 2026-11-11`, dual license list).

### Changed

- **Dependencies and Python support (breaking for Python 3.10/3.11):** updated GitHub Actions
  (pinned by full commit SHA), `YosysHQ/setup-oss-cad-suite` (pinned to `2026-11-01`), `numpy` 2.5,
  `pytest` 9.2 and `cocotb` 2.1. **Python 3.12 is now the minimum supported version** (installing on
  Python 3.10 or 3.11 fails because `requires-python = ">=3.12"`).
- **Physical-design lock split (`flow/LAYOUT_LOCK.json`, `flow/check_lock.py`, `flow/release_layout.py`):**
  separated silicon artifacts (`records/`, `ui/data/chip_*.js`, locked at `v1.0.1` with
  `rtl_Functional_equiv: true` for the control-path RTL fixes) from derived reports
  (`flow/results.md`, `ui/data/traces_data.js`), added `--records-only` relocking, removed unused
  `ui/data/ppa_records.js` and `ihp-sg13g2`, and added `core_area_um2`, `stdcell_density_pct` and
  `row_fill_pct` to `flow/results.md`.
- **FPGA resource note refreshed at the v1.0.1 RTL (`flow/fpga/README.md`):** ECP5-85F out-of-context
  synthesis is 18,948 LUT4, 8,563 flip-flops, 2,341 CCU2C and 64 `MULT18X18D` at T = 8 (`t8_cpa`),
  against 16,658 / 8,562 / 2,294 at `v1.0.0`, and now records `t8_shared` and `t8_csa` too. Still
  synthesis only: no Fmax, utilization or board result.

## [v1.0.0] — 2026-11-11

The first release of the int8 matrix coprocessor: a cycle-exact model, synthesizable RTL, formal
proofs, a physical-design flow, a bare-metal driver and the SoC integration contract.

### Added

- **RTL core** (`tq_top`): T×T int8/uint8 outer-product engine with a double-banked int32 accumulator
  tile, variable-arity edge cells, 2-beat `VOUTER_MAC`, zero-overhead `LOOP_T`/`LOOP_K`, and a
  10-byte GEMM kernel. Parameterized T = 4/8/16/32, with elaboration checks on T, queue depths,
  program size and panel size.
- **Int8 output** (`MODE[2]`, shift-only): arithmetic right shift by `MODE[7:3]`, saturating clamp to
  [−128, 127], one T-byte tile row per write. Shared-port 64³ rises from 79.8% to 88.7% in the model.
- **Adapters**: AXI4-Lite registers (`tq_axil_regs`), AXI4 memory master with an optional read burst
  line buffer and configurable `AxID`/`AxPROT`/`AxCACHE`/`AxQOS`/`AxREGION` (`tq_axi_mem`), CV-X-IF
  reference adapter (`tq_xif_regs`), and the tops `tq_top_axil`, `tq_top_axi`, `tq_top_xif`.
- **Panel buffer** (`PANEL_BYTES`, default off): A-panel replay across a tile row; experimentally
  implemented in flip-flops.
- **Clock gating** (`CLK_GATE`): per-group ICG with a formally checked pattern; `PERF` counters can be
  omitted.
- **MAC pipeline option** (`MAC_PIPE`): 2 stages by default, 1 stage without the product register.
- **Cycle model** (`model/`): real instruction bytes, bit-exact against numpy, packing, traces,
  golden-vector export, T/W sweeps and DSE.
- **int8 layer-path studies** (cycle model, `model/dse.md` §9–§11, tag `layerpath`): the small-K
  pointwise layer, batched decode and the GEMV shapes that size the A-panel buffer; a chaining output
  layout (C written in the next layer's A-panel order, bit-exact with the host packer, model-only
  `Params.chain_c`); and the cycle cost of per-column bias/scale delivery.
- **Verification**: 1,709 differential cases in 25 configurations, including random back-pressure,
  illegal programs, burst AXI and CV-X-IF; SymbiYosys proofs for the FIFO, ICG, adapters and the T=4
  core.
- **Physical design**: OpenROAD-flow-scripts flow with activity-based power, committed post-route
  records (T = 8 at 100 MHz on sky130hd and 1 GHz on ASAP7; T = 16 at 741 MHz on ASAP7, 0.448 vs
  0.363 MACs/cycle/kGE), ECP5 synthesis resource check (16,658 LUT4, 8,562 flip-flops), and a locked
  3D layout/PPA bundle.
- **Software**: freestanding C99 driver with `tq_probe` validation, packing, program builder,
  register access and a one-call GEMM API (`tq_gemm_run`, `tq_gemm_run_timeout`); `make -C sw`
  builds `libtq.a`.
- **Release packaging & docs**: `make dist` and `.github/workflows/release.yml` for source and
  golden-vector tarballs; spec rev 5, `docs/integration.md`, `docs/verification.md`,
  `docs/benchmarks.md`, `docs/golden-vectors.md`, and the interactive `ui/` showcase.

### Known limitations

See [`docs/integration.md` §8](docs/integration.md#8-known-limitations) for the canonical list of v1
limitations.

### Release notes

All PPA comparison records were re-recorded at RTL commit `cfd68f4` and the layout/PPA bundle was
locked as `v1.0.0`. T = 16 is recorded at 741 MHz (762 MHz post-route); T = 32 has no PPA record.
`make dist` and `.github/workflows/release.yml` produce and publish the source tarball, a
representative golden-vector set and `SHA256SUMS`.

[Unreleased]: https://github.com/codetiger/triqueue/compare/v1.0.2...HEAD
[v1.0.2]: https://github.com/codetiger/triqueue/compare/v1.0.1...v1.0.2
[v1.0.1]: https://github.com/codetiger/triqueue/compare/v1.0.0...v1.0.1
[v1.0.0]: https://github.com/codetiger/triqueue/releases/tag/v1.0.0
