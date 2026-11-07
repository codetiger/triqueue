<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1 -->

# Physical design flow

Runs `tq_top` from RTL to a routed layout with [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts) (ORFS), then measures power from simulated switching activity.

ORFS runs in Docker, pinned to one image (`ORFS_IMAGE` in `ppa.py`), so every result names its flow version. The image is amd64 only; on Apple Silicon it runs under emulation, a few times slower than native.

## Files

| File | Purpose |
| --- | --- |
| `ppa.py` | Configurations (`CONFIGS`), platforms with their target clocks (`PLATFORMS`), and the ORFS driver. Each run works in `build/orfs/<platform>/<config>/`. |
| `constraint.sdc` | Clock on `clk_i` (period from `ppa.py`), 20% of the period as I/O delay, `rst_ni` untimed (asynchronous, synchronized in the SoC). |
| `activity.py` | Runs one workload in Verilator through the cocotb testbench (checked against the cycle model) and dumps SAIF. |
| `power.py`, `power.tcl` | Scales the SAIF to the flow clock, reads it into OpenSTA with the routed parasitics, reports power and energy per MAC. |

## Usage

```
docker pull --platform linux/amd64 openroad/orfs:<tag in ppa.py>
python3 flow/ppa.py list
python3 flow/ppa.py run sky130hd t8_csa              # full flow
python3 flow/ppa.py run asap7 t8_cpa --to synth      # stop after a stage
python3 flow/power.py sky130hd t8_csa gemm64         # after the flow: activity-based power
```

## How the numbers are produced

- **Area:** post-route standard-cell area of `tq_top`. The operand SRAM is outside `tq_top`, so this is the engine only.
- **Timing:** post-route STA at the target clock in `PLATFORMS`, typical corner of the platform.
- **Power:** OpenSTA with SAIF from an RTL simulation of the workload. Register and port toggles are annotated from the simulation; combinational nets are propagated by OpenSTA. Operands are uniform random int8, which switches more than real data.
- **Energy:** average power × the whole simulated window (the run plus a few dozen setup cycles), charged to the workload's useful MACs (M × N × K).
