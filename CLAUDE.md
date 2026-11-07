# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project intent

The goal is an **open-source RTL matrix coprocessor for RISC-V**, comparable to Apple AMX, that is easy to integrate into larger SoCs and is benchmarked fairly against other open designs. In priority order: performance per watt, performance per component (area, kGE, state bits per MAC), design reusability (parameterized, interface-agnostic core with thin adapters), and overall efficiency (utilization across a workload suite). Peak throughput is a size parameter, not a goal.

- `README.md` covers the goals, `docs/roadmap.md` the phased plan (currently Phase 0, foundation), and `docs/metrics.md` how every number must be computed.
- `docs/decisions/` holds the decision records. Already decided: SystemVerilog RTL (0001), SHL-2.1 for hardware and Apache-2.0 for software (0002), int8 only through Phase 4 (0003). When a new design or project decision is made, add a record there rather than only editing the spec.
- Efficiency claims are only valid once RTL has been through synthesis. Until then, only utilization is known; don't present model numbers as power or area results.

## Current state

Pre-RTL. The architecture is the **Tri-Queue**: an int8 outer-product engine with 64 9×9 multipliers, one 128-bit SRAM read port, and an 8×8 int32 carry-save accumulator tile, double-banked.

- `docs/spec/tri_queue_matrix_core_design.md`: the current spec (rev 3) and the source of truth. `docs/history/` holds the original Tri-Stack spec and the comparison that led to the redesign. Treat those as history; don't update them.
- `model/`: a cycle-level Python model of the spec that runs the real instruction bytes and checks every result bit-exact against numpy. It will be the golden reference for the RTL.
- `bench/workloads/gemm.csv`: the single list of GEMM shapes. Benchmarks select rows by tag (`model`, `amx`). Add shapes there, never hard-coded in a script.
- `bench/reference/amx/`: real-hardware Apple AMX (M2 Pro) measurements and scripts comparing them with the model.

The spec's performance numbers come from the model. If you change the model or the spec's claims, keep them in sync: regenerate `model/results.md` and update the matching spec sections (§6 ISA, §7 performance). CI fails if the committed `model/results.md` differs from a fresh `bench.py` run.

Every source file starts with an SPDX header: `Apache-2.0` for software, and `Apache-2.0 WITH SHL-2.1` for files under `rtl/`, `verif/` and `flow/`.

## Commands

Setup: `pip install -r requirements.txt` (Python 3.10+).

Run model commands from `model/` (`pytest.ini` sets `pythonpath = .`, so imports are `from tri_queue...`):

```
cd model
python3 -m pytest -q                                          # all tests (~2 s)
python3 -m pytest -q tests/test_core.py::test_random_sizes    # single test
python3 -m pytest -q -k signedness                            # by name
python3 bench.py                                              # ~70 s, rewrites model/results.md
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

`compare.py`, `ablation.py` and `model/bench.py` find `model/` and `bench/workloads/` through `sys.path` entries computed relative to their own file location. The checked-in CSVs are M2 Pro measurements; re-running on other hardware overwrites them. The C programs still have their own size lists, separate from `gemm.csv`. AMX encodings are undocumented and come from corsix/amx.

## Model architecture (`model/tri_queue/`)

- `isa.py`: the ISA. One byte per instruction (`opcode[7:4] | arg[3:0]`); only `CFG` is multi-byte (1 + 32-bit LE immediate). Defines opcodes, the 8 config registers (`A_BASE, B_BASE, C_BASE, C_LD, K, M, N, MODE`), the encoder, `decode`/`disassemble`, and `gemm_kernel()`, the 10-byte reference kernel from spec §6.2.
- `packing.py`: host-side panel packing (spec §5.3). A becomes column panels and B row panels, k-major, 8 wide (4 wide for a final panel with ≤4 left), K padded to `kp(k)` (multiple of 8). `auto_shape` picks the per-tile `(r, c, d)` with `r·c·d = 64`, giving the four shapes 8x8x1, 8x4x2, 4x8x2, 4x4x4.
- `core.py`: `TriQueueCore`, plus `run_gemm(a, b, params)` (packs, builds memory and program, runs, returns `(C, Stats)`) and `reference(a, b)` (numpy int32 GEMM). Each cycle runs in a fixed order: memory responses land → MAC unit → memory port(s) → front end. `Params` holds the tunable microarchitecture knobs (memory latency, FIFO depth, queue depths, `shared_port`, `banks`), and the bench sweeps vary these. `Stats` records cycles, instruction count, per-shape MAC counts, MAC stall reasons (`mac_state`), and front-end stall reasons.

Properties the model enforces and the tests check:
- Arithmetic is done the hardware way: 9×9 multiplies with sign extension per `MODE`, and 32-bit wrapping 3:2 carry-save accumulation (`_csa_reduce`). The carry-propagate add happens only at drain. Don't replace this with a plain numpy matmul inside the core; the tests compare against `reference()`.
- Per-quadrant arity limits (`HW_CELL_ARITY`: TL 4, TR 2, BL 2, BR 1) are asserted, so a shape/operand-selection bug fails loudly.
- The modelling assumptions are listed in both `model/README.md` and the `core.py` docstring: 1 instruction decoded per cycle, zero-overhead loops, `ZERO_C`/`SWAP_C` take no MAC cycle, `VOUTER_MAC` waits for all its vectors, pipelined SRAM reads with fixed latency, no bank conflicts. If you change an assumption, update both places.

`bench.py` writes `results.md` section by section and labels each spec claim ✅/❌ against the simulated value. When you add a claim to the spec, add a row for it there.
