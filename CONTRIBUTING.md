<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

Thanks for helping. This repository is a hardware/software co-design: the model is the golden
reference, the RTL must match it cycle for cycle, and every performance number must be reproducible.

## Before you start

- If you use an AI coding agent, point it at [`AGENTS.md`](AGENTS.md); it is the shared repository guidance.
- Read [`docs/specification.md`](docs/specification.md), [`docs/integration.md`](docs/integration.md)
  and [`docs/verification.md`](docs/verification.md).
- Open an issue for a behavior change or a new interface before writing a large patch.
- For a bug, include the smallest program or model call that reproduces it, the expected result and
  the observed result, and the tool versions (`verilator --version`, `yosys -V`, `python3 -V`).

## The rule that matters most

**The RTL and the model must stay cycle-exact.** A behavior change goes into the model first (spec +
tests), then into the RTL. The regression compares final memory, every memory transaction, every MAC
start cycle and the counters. If you change a modeling assumption, update both `model/README.md` and
the model docstring, and the spec if the contract changes.

## Build and test

```sh
pip install -r requirements.txt

make model-test          # Python model
make sw-test             # C driver against the model
make rtl-lint            # Verilator + Yosys, must be warning-free
make rtl-test            # cocotb regression, 25 configurations
make formal              # SymbiYosys, ~3 min
make synth-check         # generic Yosys, both accumulator forms
make lock-check          # PPA records and locked UI layouts
make spdx-check          # license headers
```

`make` runs the full set. RTL work needs Verilator ≥ 5, Yosys, SymbiYosys and Yices
(`brew install verilator yosys sby yices2`).

## Source layout

| Path | Rule |
| --- | --- |
| `model/` | The golden reference. Run `python3 bench.py` / `dse.py` from `model/`; CI checks the committed results. |
| `rtl/` | SystemVerilog in the Yosys-compatible subset. Add new files to `rtl/files.f` in compile order. |
| `verif/` | cocotb + Verilator cases in `test_rtl.py`; formal properties in `formal/`. |
| `sw/` | Freestanding C99; no libc, no allocation. |
| `flow/` | OpenROAD flow and committed records. Do not hand-edit `flow/results.md`; regenerate it. |
| `bench/workloads/gemm.csv` | The single workload list. Add shapes there, not in a script. |
| `docs/` | Public contract. Update the spec when behavior changes. |
| `ui/` | Generated bundles are locked; see `ui/README.md` before changing them. |

## Coding rules

- **SPDX header on every source file**: `Apache-2.0` for software, `Apache-2.0 WITH SHL-2.1` for
  `rtl/`, `verif/` and `flow/`. `make spdx-check` enforces the set.
- **RTL**: Yosys-compatible SystemVerilog; no `import`, `return`, `$bits(type)`, `break` or
  `continue`. Refer to package items as `tq_pkg::NAME`. Use `tq_cg` for clock-gated registers and keep
  `verif/formal/check_cg_pattern.py` in sync. Lint with Verilator and elaborate with Yosys.
- **Python**: keep the model dependency-light (numpy); no new runtime dependency without a good reason.
- **C**: C99, `-Wall -Wextra`, no libc, no dynamic allocation.
- **Docs**: avoid hard-coded numbers that a script can generate; link to the generated file instead.

## Pull requests

Please include:

1. What changed and why, with the issue link.
2. The commands you ran and their results (`make model-test`, `make rtl-test`, etc.).
3. If the change moves a model result, the regenerated `model/results.md` / `model/dse.md`.
4. If the change touches PPA, the new `flow/records/` entry and `flow/results.md`; do not edit locked
   records by hand.
5. For a new RTL configuration, the `test_rtl.py` case list and the count update in `verif/README.md`.

CI must be green. A performance claim without a reproducible script will be asked to become one or be
labelled as an estimate.

## License

By contributing, you agree that your contribution is licensed under the same terms as the files you
touch: Apache-2.0 for software, Solderpad Hardware License 2.1 for `rtl/`, `verif/` and `flow/`.
