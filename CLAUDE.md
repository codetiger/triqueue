# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project intent

The goal is an **open-source RTL matrix coprocessor for RISC-V**, comparable to Apple AMX, that is easy to integrate into larger SoCs and is benchmarked fairly against other open designs. In priority order: performance per watt, performance per component (area, kGE, state bits per MAC), design reusability (parameterized, interface-agnostic core with thin adapters), and overall efficiency (utilization across a workload suite). Peak throughput is a size parameter, not a goal.

- `README.md` covers the goals, `docs/roadmap.md` the phased plan (Phase 1 done; Phase 2, RTL v0, is next), and `docs/metrics.md` how every number must be computed.
- `docs/decisions/` holds the decision records. Already decided: SystemVerilog RTL (0001), SHL-2.1 for hardware and Apache-2.0 for software (0002), int8 only through Phase 4 (0003), a register-port host interface with thin adapters (0004), OBI-style memory ports with W-aligned bases (0005), carry-save accumulator for v0 (0006), requantization deferred (0007), and RTL parameterized by T with T = 8 first (0008). When a new design or project decision is made, add a record there rather than only editing the spec. Records are not rewritten; add a dated follow-up or a superseding record.
- Efficiency claims are only valid once RTL has been through synthesis. Until then, only utilization is known; don't present model numbers as power or area results.

## Current state

Pre-RTL. The architecture is the **Tri-Queue**: an int8 outer-product engine with a T×T int32 carry-save accumulator tile (double-banked), T² 9×9 multipliers and one 2T-byte SRAM read port. T is a parameter; the spec's design point and all spec figures are T = 8 (64 MACs, 128-bit port).

- `docs/spec/tri_queue_matrix_core_design.md`: the current spec (rev 4) and the source of truth. `docs/spec/trace_format.md` is the golden-vector and trace contract between the model and the RTL testbenches. `docs/history/` holds the original Tri-Stack spec and the comparison that led to the redesign. Treat those as history; don't update them.
- `model/`: a cycle-level Python model of the spec that runs the real instruction bytes and checks every result bit-exact against numpy. It is the golden reference for the RTL.
- `bench/workloads/gemm.csv`: the single list of GEMM shapes. Benchmarks select rows by tag (`model`, `amx`, `dse`). Add shapes there, never hard-coded in a script.
- `bench/reference/amx/`: real-hardware Apple AMX (M2 Pro) measurements and scripts comparing them with the model.

The spec's performance numbers come from the model. If you change the model or the spec's claims, keep them in sync: regenerate `model/results.md` and `model/dse.md` and update the matching spec sections (§6 ISA, §7 performance, §7.3 scaling). CI fails if either committed file differs from a fresh `bench.py` / `dse.py` run. Any model change must leave the T = 8 results byte-identical unless the change is intended to move them.

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

`compare.py`, `ablation.py` and `model/bench.py` find `model/` and `bench/workloads/` through `sys.path` entries computed relative to their own file location. The checked-in CSVs are M2 Pro measurements; re-running on other hardware overwrites them. The C programs still have their own size lists, separate from `gemm.csv`. AMX encodings are undocumented and come from corsix/amx.

## Model architecture (`model/tri_queue/`)

- `isa.py`: the ISA. One byte per instruction (`opcode[7:4] | arg[3:0]`); only `CFG` is multi-byte (1 + 32-bit LE immediate). Defines opcodes, the 8 config registers (`A_BASE, B_BASE, C_BASE, C_LD, K, M, N, MODE`), the encoder, `decode`/`disassemble`, and `gemm_kernel()`, the 10-byte reference kernel from spec §6.2.
- `packing.py`: host-side panel packing (spec §5.3). A becomes column panels and B row panels, k-major, T wide (T/2 wide for a final panel with ≤T/2 left), K padded to `kp(k)` (multiple of 8 for every T). `auto_shape` picks the per-tile `(r, c, d)` with `r·c·d = T²`, giving four shapes (at T = 8: 8x8x1, 8x4x2, 4x8x2, 4x4x4). The ISA encodes shapes as full/half, so it's independent of T.
- `core.py`: `TriQueueCore`, plus `build_gemm` (packs and builds the memory image and program), `run_gemm(a, b, params, trace=None)` (returns `(C, Stats)`), `read_c` and `reference(a, b)` (numpy int32 GEMM). Each cycle runs in a fixed order: memory responses land → MAC unit → memory port(s) → front end. `Params` holds the size parameters (`tile` T, `port_bytes` W, which defaults to 2T) and the tunable microarchitecture knobs (memory latency, FIFO depth in W-byte vectors, queue depths, `shared_port`, `banks`). `Stats` records cycles, instruction count, per-shape MAC counts, MAC stall reasons (`mac_state`), front-end stall reasons, and read/write transactions and bytes. Passing a list as `trace` collects the event lines defined in `docs/spec/trace_format.md`; tracing must never change timing.
- `vectors.py`: the golden-vector exporter for RTL (`python3 -m tri_queue.vectors`).

Properties the model enforces and the tests check:
- Arithmetic is done the hardware way: 9×9 multiplies with sign extension per `MODE`, and 32-bit wrapping 3:2 carry-save accumulation (`_csa_reduce`). The carry-propagate add happens only at drain. Don't replace this with a plain numpy matmul inside the core; the tests compare against `reference()`.
- Per-quadrant arity limits (`HW_CELL_ARITY`: TL 4, TR 2, BL 2, BR 1) are asserted, so a shape/operand-selection bug fails loudly.
- `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` must be multiples of W (asserted). C rows are padded to whole words, so `C_LD` is not always `4·N`; use `read_c` to extract C.
- The modelling assumptions are listed in both `model/README.md` and the `core.py` docstring: 1 instruction decoded per cycle, zero-overhead loops, `ZERO_C`/`SWAP_C` take no MAC cycle, `VOUTER_MAC` waits for all its operand bytes, pipelined SRAM reads with fixed latency and no `gnt` stalls, no bank conflicts. If you change an assumption, update both places.

`bench.py` writes `results.md` section by section and labels each spec claim ✅/❌ against the simulated value. When you add a claim to the spec, add a row for it there.
