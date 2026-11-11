<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

All notable changes to the Tri-Queue project. This format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **int8 layer-path baseline** (`model/dse.md` §9, tag `layerpath` in `bench/workloads/gemm.csv`): the
  small-K pointwise layer that CNN inference is bound by, batched decode, and the GEMV shapes that size
  the A-panel buffer, with the buffer capacity each one needs.
- **Chaining output layout** (`model/dse.md` §10, model study only, `Params.chain_c`): the drain writes C
  in the next layer's A-panel order, bit-exact with the host packer and half the int8 write transactions,
  with the drain-fill cost at small K quantified.
- **Epilogue delivery cost** (`model/dse.md` §11): per-column bias and scale, as a per-tile stream or as
  an extra k step.

## [v1.0.0] — 2026-11-10

The first release of the int8 matrix coprocessor: a cycle-exact model, synthesizable RTL, formal
proofs, a physical-design flow, a bare-metal driver and the SoC integration contract.

### Added

- **RTL core** (`tq_top`): T×T int8/uint8 outer-product engine with a double-banked int32 accumulator
  tile, variable-arity edge cells, 2-beat `VOUTER_MAC`, zero-overhead `LOOP_T`/`LOOP_K`, and a
  10-byte GEMM kernel. Parameterized T = 4/8/16/32.
- **Int8 output** (`MODE[2]`, shift-only): arithmetic right shift by `MODE[7:3]`, saturating clamp to
  [−128, 127], one T-byte tile row per write. Shared-port 64³ rises from 79.8% to 88.7% in the model.
- **Adapters**: AXI4-Lite registers (`tq_axil_regs`), AXI4 memory master with an optional read burst
  line buffer (`tq_axi_mem`), CV-X-IF reference adapter (`tq_xif_regs`), and the tops `tq_top_axil`,
  `tq_top_axi`, `tq_top_xif`.
- **Panel buffer** (`PANEL_BYTES`, default off): A-panel replay across a tile row; experimentally
  implemented in flip-flops.
- **Clock gating** (`CLK_GATE`): per-group ICG with a formally checked pattern; `PERF` counters can be
  omitted.
- **MAC pipeline option** (`MAC_PIPE`): 2 stages by default, 1 stage without the product register.
- **Cycle model** (`model/`): real instruction bytes, bit-exact against numpy, packing, traces,
  golden-vector export, T/W sweeps and DSE.
- **Verification**: 1,709 differential cases in 25 configurations, including random back-pressure,
  illegal programs, burst AXI and CV-X-IF; SymbiYosys proofs for the FIFO, ICG, adapters and the T=4
  core.
- **Physical design**: OpenROAD-flow-scripts flow with activity-based power, committed post-route
  records, and a locked 3D layout/PPA bundle.
- **Software**: freestanding C99 driver with packing, program builder, register access and a one-call
  GEMM API; `make -C sw` builds `libtq.a`.
- **Docs**: spec rev 5, `docs/integration.md`, `docs/verification.md`, `docs/benchmarks.md`,
  `docs/golden-vectors.md`, and the interactive `ui/` showcase.

### Changed

- The public spec now matches the RTL: int8 output, int8 C addressing/strobes, 17-bit products,
  AXI read bursts, and 8,562 reference flip-flops.
- The README and benchmark summary no longer describe int8 output as deferred.
- The AXI4 master now exposes `AxID`/`AxPROT`/`AxCACHE`/`AxQOS`/`AxREGION` and accepts `RID`/`BID`
  (constant values, configured by `ID_W`/`AXPROT`/`AXCACHE`/`AXQOS`); `AxUSER` is not exposed.
- Parameter errors in `tq_top` and `tq_axi_mem` now fail at elaboration instead of producing broken
  hardware (T, queue depths, program size, panel size, burst size and the 4 KiB AXI boundary).
- `tq_gemm_run_timeout` adds a bounded-poll variant of the one-call GEMM API; `tq_gemm_run` keeps
  its signature and polls forever.
- `tq_probe` now rejects an invalid `HW_CONFIG`.
- `make dist` builds the source tarball, a representative golden-vector set and `SHA256SUMS`;
  `.github/workflows/release.yml` publishes them on a `v*` tag.

### Known limitations

- Int8 output is shift-only: no per-tensor/per-channel scale, bias or activation.
- A, B and C must not overlap; the core is not coherent with host caches; no host writes during a run.
- The AXI4 master drives constant `AxID`/`AxPROT`/`AxCACHE`/`AxQOS`/`AxREGION` and accepts `RID`/`BID`; `AxUSER` is not exposed.
- The panel buffer uses flip-flops and has no PPA record; treat it as experimental.
- T=16 closes at 762 MHz on ASAP7 (run at 741 MHz), not 1 GHz; T=32 has no PPA record.
- Cached-DRAM integration is correctness-only (10.6% on Chipyard Rocket).
- CV-X-IF is a reference adapter and has not run on real silicon; APB and RoCC are not included.
- Comparative Gemmini/RVV results are not part of v1.

### Release notes

All PPA comparison records were re-recorded at RTL commit `cfd68f4` and the layout/PPA bundle is
locked as `v1.0.0`. T = 16 is recorded at 741 MHz (762 MHz post-route); T = 32 has no PPA record.
`make dist` and `.github/workflows/release.yml` produce and publish the source tarball, a
representative golden-vector set and `SHA256SUMS`.
