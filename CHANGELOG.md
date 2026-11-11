<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

All notable changes to the Tri-Queue project. This format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

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

[v1.0.1]: https://github.com/codetiger/triqueue/compare/v1.0.0...v1.0.1
[v1.0.0]: https://github.com/codetiger/triqueue/releases/tag/v1.0.0
