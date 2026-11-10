# AGENTS.md

This file provides shared guidance to AI coding agents when working with code in this repository. It is the common reference for all agents; keep it public-safe and up to date with the release.

## Project intent

The goal is a **simple, efficient open-source RTL matrix coprocessor for RISC-V**, inspired by Apple AMX, Arm SME, and Gemmini, that is easy to integrate into larger SoCs and is benchmarked fairly against other open designs. In priority order: performance per watt, performance per component (area, kGE, state bits per MAC), design reusability (parameterized, interface-agnostic core with thin adapters), and overall efficiency (utilization across a workload suite). Peak throughput is a size parameter, not a goal.

- `README.md` covers the goals. `docs/` is public and holds only the final design: the spec (`docs/specification.md`), its golden-vector/trace contract (`docs/golden-vectors.md`), the SoC integration contract (`docs/integration.md`), the verification scope (`docs/verification.md`), and `docs/benchmarks.md` (a summary of completed results; update it when a generated result it quotes changes). Benchmark results live next to the scripts that generate them (`model/results.md`, `model/dse.md`, `flow/results.md`, `bench/reference/amx/*.md`).
- Planning material is private and gitignored, in `private/` (only present in the author's checkout): `roadmap.md` (phased plan), `decisions/` (decision records), `metrics.md` (how efficiency numbers must be computed), `history/` (superseded specs). Keep progress tracking, phase status and decision history there, never in public files. Public docs must not link to `private/`. When a decision is made, record it in `private/decisions/` and put the resulting final design in the spec.
- Efficiency claims are only valid once RTL has been through synthesis and place-and-route. Until then only utilization is known. Don't present model numbers or generic Yosys gate counts as power or area results.

## Current state

RTL v1.0 exists and matches the model cycle for cycle. The architecture is the **Tri-Queue**: an int8 outer-product engine with a T×T int32 accumulator tile (double-banked, plain 32-bit adder by default, carry-save selectable), T² 9×9 multipliers and one 2T-byte SRAM read port. T is a parameter; the spec's design point and all spec figures are T = 8 (64 MACs, 128-bit port).

- `docs/specification.md`: the spec (rev 5) and the source of truth, including the memory (§6.3) and host (§6.4) interfaces and the design choices (§10). `docs/golden-vectors.md` is the golden-vector and trace contract between the model and the RTL testbenches.
- `model/`: a cycle-level Python model of the spec that runs the real instruction bytes and checks every result bit-exact against numpy. It is the golden reference for the RTL.
- `rtl/`: SystemVerilog, top `tq_top`, sources in compile order in `rtl/files.f`. `rtl/README.md` covers parameters, the register and memory port contracts, the block map and the Yosys-compatible coding rules.
- `verif/`: cocotb + Verilator regression against the model (`test_rtl.py` holds the configurations and cases; `tb/` the testbench), plus SymbiYosys proofs in `formal/`.
- `flow/`: physical design with OpenROAD-flow-scripts (`flow/README.md`). The reference is a native ORFS build pinned to `ORFS_COMMIT` in `flow/ppa.py`; never mix its numbers with the `--docker` flow's. `ppa.py run` (RTL to routed layout), `power.py` (activity-based power per workload), `results.py collect` (records a run in `flow/records/`, committed). `flow/results.md` is generated from the records only, and CI checks it.
- `rtl/adapters/`: bus adapters (Phase 4). `tq_axil_regs` (AXI4-Lite to the register port), `tq_axi_mem` (AXI4 master for the memory ports; needs the core's `WR_ACK = 1`), `tq_xif_regs` (CV-X-IF custom instructions `tq.wr` / `tq.rd` to the register port, only after commit), and the tops `tq_top_axil` (AXI4-Lite only), `tq_top_axi` (both) and `tq_top_xif`.
- `sw/`: freestanding C driver (`tq.h`, `tq.c`), tested against the model's program builder and packing (`make sw-test`).
- `bench/workloads/gemm.csv`: the single list of GEMM shapes. Benchmarks select rows by tag (`model`, `amx`, `dse`). Add shapes there, never hard-coded in a script.
- `bench/reference/amx/`: real-hardware Apple AMX (M2 Pro) measurements and scripts comparing them with the model.

The spec's performance numbers come from the model. If you change the model or the spec's claims, keep them in sync: regenerate `model/results.md` and `model/dse.md` and update the matching spec sections (§6 ISA, §7 performance, §7.3 scaling). CI fails if either committed file differs from a fresh `bench.py` / `dse.py` run, or if `flow/results.md` differs from `python3 flow/results.py` (regenerated from the records; the flow itself is too slow for CI). Any model change must leave the T = 8 results byte-identical unless the change is intended to move them.

**The RTL and the model must stay cycle-exact.** The RTL's per-cycle evaluation order mirrors the model's (memory responses → MAC unit → ports → front end): later stages see earlier stages' pops in the same cycle, and halt detection uses this cycle's post-update state, like the model's `all_idle`. An SRAM latency of L in the testbench corresponds to model `mem_latency = L + 1`. A behaviour change goes into the model first (spec + tests), then the RTL. `make rtl-test` must stay green, with exact cycle counts, in every configuration. The spec rules that keep the hardware simple are enforced by the model too: at most one `ZERO_C`/`SWAP_C` per MAC cycle, and loop bodies contain only 1-byte instructions.

Every source file starts with an SPDX header: `Apache-2.0` for software, and `Apache-2.0 WITH SHL-2.1` for files under `rtl/`, `verif/` and `flow/`. `make spdx-check` (`tools/check_spdx.py`) enforces this in CI.

## Commands

Setup: `pip install -r requirements.txt` (Python 3.10+). RTL work also needs Verilator ≥ 5, Yosys, SymbiYosys and Yices (`brew install verilator yosys sby yices2`).

From the repo root:

```
make                  # model-test, sw-test, spdx-check, lock-check, rtl-lint, rtl-test, formal, synth-check
make model-test       # model pytest suite
make sw-test          # C driver against the model
make spdx-check       # every checked-in source/config file has an SPDX header
make model-results    # regenerate model/results.md and model/dse.md (bench.py + dse.py)
make rtl-lint         # verilator -Wall in 12 configurations + adapter tops + burst AXI + Yosys elaboration (both must be warning-free)
make rtl-test         # cocotb regression, 1,709 cases in 25 configurations (~165 s); builds go to build/rtl/<config>/
python3 -m pytest verif -q -k t8_default    # one RTL configuration
make formal           # SymbiYosys: clock-gating template (check_cg_pattern.py + tq_cg.sby), FIFO (tq_fifo.sby), AXI4-Lite and CV-X-IF adapters (tq_axil_regs.sby, tq_xif_regs.sby), AXI master burst cover, unbounded core proof, split port (tq_core.sby split, ~3 min)
make formal-nightly   # core proofs, shared port, WR_ACK and panel buffer (tq_core.sby shared/ack/panel), AXI4 master proof (tq_axi_mem.sby prove, ~10 min)
make flow-results     # rewrite flow/results.md from flow/records/ (no flow run)
make synth-check      # generic Yosys synthesis: no latches, loops or undriven nets (stats in build/synth/)
make fpga             # ECP5 out-of-context synth + nextpnr (flow/fpga/; needs nextpnr-ecp5, not part of make)
make clean            # removes build/ and SymbiYosys work dirs
```

RTL regression configurations (names for `-k`): `t8_default`, `t8_shared_port`, `t8_small_queues`, `t8_backpressure`, `t8_shared_backpressure`, `t8_acc_csa`, `t8_no_clock_gating`, `t8_reference_queues`, `t8_axil`, `t8_axi`, `t8_axi_burst`, `t8_xif`, `t8_write_ack`, `t8_write_ack_shared`, `t8_edge_cases`, `t8_edge_shared`, `t8_panel`, `t8_panel_shared`, `t16_panel`, `t4_panel`, `t8_mac1`, `t8_mac1_csa_shared`, `t32`, `t16`, `t4` (25 configurations, 1,709 cases). CI (`.github/workflows/ci.yml`) runs four jobs on every push: model tests on Python 3.10/3.12, SPDX headers, the results-up-to-date diff, and rtl-lint/rtl-test/formal/synth-check. `.github/workflows/nightly.yml` runs `make formal-nightly` daily.

Run model commands from `model/` (`pytest.ini` sets `pythonpath = .`, so imports are `from tri_queue...`):

```
cd model
python3 -m pytest -q                                          # all tests (~2 s)
python3 -m pytest -q tests/test_core.py::test_random_sizes    # single test
python3 -m pytest -q -k signedness                            # by name
python3 bench.py                                              # ~70 s, rewrites model/results.md
python3 dse.py                                                # ~35 s, rewrites model/dse.md (T = 8/16/32)
python3 -m tri_queue.vectors --m 16 --n 16 --k 16 -o ../build/vectors/x   # golden vectors (build/ is gitignored)
```

AMX benchmarks (macOS on Apple Silicon only; run from `bench/reference/amx/`):

```
clang -O2 -o amx_peak amx_peak.c
clang -O2 -o amx_gemm amx_gemm.c
clang -O2 -o sgemm_sweep sgemm_sweep.c -framework Accelerate
./amx_peak 50000000 > peak.csv
./amx_gemm > amx_gemm.csv
VECLIB_MAXIMUM_THREADS=1 ./sgemm_sweep > sgemm_1t.csv
./sgemm_sweep > sgemm_mt.csv
python3 compare.py     # reads the CSVs, runs the model, writes results.md (~20 s)
python3 ablation.py    # writes ablation.md (~40 s)
```

`compare.py`, `ablation.py` and `model/bench.py` find `model/` and `bench/workloads/` through `sys.path` entries computed relative to their own file location. The checked-in CSVs are M2 Pro measurements; re-running on other hardware overwrites them. `amx_gemm` and `sgemm_sweep` read their shapes from `gemm.csv` (tags `amx_hand`, `amx_sgemm`; optional path argument, default `../../workloads/gemm.csv`) through `workloads.h`. AMX encodings are undocumented and come from corsix/amx.

## Model architecture (`model/tri_queue/`)

- `isa.py`: the ISA. One byte per instruction (`opcode[7:4] | arg[3:0]`); only `CFG` is multi-byte (1 + 32-bit LE immediate). Defines opcodes, the 8 config registers (`A_BASE, B_BASE, C_BASE, C_LD, K, M, N, MODE`), the encoder, `decode`/`disassemble`, and `gemm_kernel()`, the 10-byte reference kernel from spec §6.2.
- `packing.py`: host-side panel packing (spec §5.3). A becomes column panels and B row panels, k-major, T wide (T/2 wide for a final panel with ≤T/2 left), K padded to `kp(k)` (multiple of 8 for every T). `auto_shape` picks the per-tile `(r, c, d)` with `r·c·d = T²`, giving four shapes (at T = 8: 8x8x1, 8x4x2, 4x8x2, 4x4x4). The ISA encodes shapes as full/half, so it's independent of T.
- `core.py`: `TriQueueCore`, plus `build_gemm` (packs and builds the memory image and program), `run_gemm(a, b, params, trace=None)` (returns `(C, Stats)`), `read_c` and `reference(a, b)` (numpy int32 GEMM). Each cycle runs in a fixed order: memory responses land → MAC unit → memory port(s) → front end. `Params` holds the size parameters (`tile` T, `port_bytes` W, which defaults to 2T) and the tunable microarchitecture knobs (memory latency, FIFO depth in W-byte vectors, queue depths, `shared_port`, `banks`, `gnt_stall`/`gnt_seed`, `wr_ack`/`wr_resp_latency`/`wr_out_max`, `mac_pipe`, `panel_bytes`, `out_bytes`/`out_shift`/`drain_lanes`). `Stats` records cycles, instruction count, per-shape MAC counts, MAC stall reasons (`mac_state`), front-end stall reasons, read/write transactions and bytes, and `gnt` stalls per port (`port_stall`). Passing a list as `trace` collects the event lines defined in `docs/golden-vectors.md`; tracing must never change timing.
- `vectors.py`: the golden-vector exporter for RTL (`python3 -m tri_queue.vectors`).

Properties the model enforces and the tests check:
- Arithmetic is done the hardware way: 9×9 multiplies with sign extension per `MODE`, and 32-bit wrapping 3:2 carry-save accumulation (`_csa_reduce`). The carry-propagate add happens only at drain. The RTL's default plain adder gives the same values mod 2³², so this checks both forms. Don't replace this with a plain numpy matmul inside the core; the tests compare against `reference()`.
- Per-quadrant arity limits (`HW_CELL_ARITY`: TL 4, TR 2, BL 2, BR 1) are asserted, so a shape/operand-selection bug fails loudly.
- Illegal programs stop with the RTL's error codes at the RTL's cycle (`Stats.error`); `tri_queue/programs.py` holds the edge-case programs shared with the RTL regression (`t8_edge_cases`).
- `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` must be multiples of W (asserted). C rows are padded to whole words, so `C_LD` is not always `4·N` (or `N` for int8 output); use `read_c` to extract C.
- The modelling assumptions are listed in both `model/README.md` and the `core.py` docstring: 1 instruction decoded per cycle, zero-overhead loops, `ZERO_C`/`SWAP_C` take no MAC cycle, `VOUTER_MAC` waits for all its operand bytes, pipelined SRAM reads with fixed latency, ports always granted unless `gnt_stall` > 0 (seeded random `gnt` back-pressure that the RTL testbench reproduces through the shared `GrantSource`; an ungranted request is held unchanged until granted), writes complete at their grant unless `wr_ack` (then after `wr_resp_latency`, FENCE waits for them), no bank conflicts. If you change an assumption, update both places.

`bench.py` writes `results.md` section by section and labels each spec claim ✅/❌ against the simulated value. When you add a claim to the spec, add a row for it there.

## RTL architecture (`rtl/core/`)

`tq_top` = `tq_regs` (register port, program buffer, perf counters) + `tq_core`. Inside `tq_core`: `tq_frontend` (decoder, 2-level loop stack, tile walker; tile addresses are kept as running offsets, so there are no multipliers outside the array) pushes into the MAC command queue and two stream command queues. `tq_stream` turns stream commands into W-byte reads (round-robin A/B, credit-based so responses never back-pressure, a tag FIFO routes in-order responses to QA/QB). The MAC control in `tq_core` pops operands for a beat; `tq_mac_array` does per-quadrant operand select, T² 9×9 multiplies into a product register, then accumulation into one of two banks the next cycle (a plain 32-bit adder by default; `ACC_FORM = tq_pkg::ACC_CSA` selects carry-save, which adds T/2 CPA lanes to the drain path). `tq_drain` drains the idle bank through a staging register into aligned writes, then clears it. With `CLK_GATE = 1` (default) the wide register groups are clock-gated through `rtl/common/tq_cg.sv` / `tq_icg.sv` (pattern and rules in `rtl/README.md`); the core proofs run with `CLK_GATE = 0`, and `tq_cg.sby` proves the gating template itself equivalent. All queues use the shared `rtl/common/tq_fifo.sv`, which is proved on its own (`tq_fifo.sby`); `tq_core.sby` proves the whole core (T = 4, any program) with the FIFOs' legal use as assertions (`TQ_FORMAL_CORE`). When you add a source file, add it to `rtl/files.f` in compile order (`tq_pkg` first). The Makefile, lint and the cocotb runner all read that list.

Yosys-compatible SV subset (Verilator accepts more, so lint with both): no `import`, so write `tq_pkg::NAME`; no `return`, so assign to the function name; no `$bits(type)`, so use the widths in `tq_pkg` (`MCMD_W`, `SCMD_W`); no `break`. Packed structs, enums and packed 2-D arrays are fine. Simulation-only assertions go under `ifndef SYNTHESIS`, and formal properties under `ifdef FORMAL`. Formal environments can't use hierarchical references (Yosys turns `dut.x` into a new free wire), so put properties and covers inside the RTL module.

cocotb (2.x) can't see fields of packed structs through VPI. Monitor flat signals instead (e.g. `u_core.beat_rf`, not `mc.rfull`). The runner passes `sys.path` of the pytest process to the simulator as `PYTHONPATH`, so `verif/test_rtl.py` adds `verif/tb` to `sys.path`.
