<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Gemmini baseline

Gemmini (UC Berkeley, a weight-stationary systolic array behind a RoCC interface) at the same peak as the Tri-Queue T = 8: an 8 × 8 int8 mesh, 64 MACs per cycle, int32 accumulation. It is the first design for the same-flow comparison: same PDK, flow version and workloads as `flow/`.

| File | Purpose |
| --- | --- |
| `Configs.scala` | `Gemmini8x8RocketConfig`: Gemmini's `leanConfig` with `meshRows = meshColumns = 8`, 16 KB scratchpad (4 banks), 8 KB accumulator (2 banks), on a one-core Rocket SoC. |
| `generate.sh` | Elaborates the config with Chipyard and copies the generated SystemVerilog to `build/gemmini/<config>/gen-collateral/` (the accelerator's top module is `Gemmini`). |

`generate.sh` runs Chipyard (1.15.0, Gemmini at `8c3f992`) in an arm64 Ubuntu container set up by `bench/chipyard/setup.sh`: Chipyard ships conda environments for Linux only, and CIRCT has no Linux arm64 `firtool`, so the x64 `firtool` from Maven Central runs under Docker Desktop's Rosetta emulation. The first run takes about an hour and keeps about 12 GB in the Docker volume `triqueue-chipyard`.

Gemmini's taped-out `chipConfig` (single-ported accumulator) does not elaborate at this commit (`AccumulatorMem` dereferences an external-memory port that exists only with shared external memory), so the base is `leanConfig`, the one Chipyard's `LeanGemminiRocketConfig` uses.

## Open: the SRAM boundary

Gemmini keeps its operands and partial sums in 24 KB of SRAM inside the accelerator (the generated memories `mem`, `mem_0`, `mem_1`); the Tri-Queue's operand SRAM is outside `tq_top`. A fair comparison needs both on the same footing: engine logic alone, and engine + SRAM with the same SRAM model. The physical-design harness (an ORFS config with the memories as macros) follows once that boundary is fixed.
