<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1 -->

# Physical design flow

Runs `tq_top` from RTL to a routed layout with [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts) (ORFS), then measures power from simulated switching activity.

The flow is pinned to one ORFS commit (`ORFS_COMMIT` in `ppa.py`), and every result names it. The reference build is native: ORFS built from source at that commit on macOS arm64. A Docker image of the same commit (`ORFS_IMAGE`, amd64) is available with `--docker` for Linux hosts. Its numbers are close to the native ones but not bit-identical (ABC breaks ties differently with another compiler and CPU), so the two are never mixed in one table.

## Native build (once per ORFS commit)

```
brew install bazelisk bison flex tcl-tk@8 make        # make: GNU make 4 as gmake
git clone https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git ~/Development/OpenROAD-flow-scripts
cd ~/Development/OpenROAD-flow-scripts
git checkout <ORFS_COMMIT>
git submodule update --init --recursive --depth 1 tools/OpenROAD tools/yosys
(cd tools/OpenROAD && bazelisk run --//:platform=cli --config=release //:install)
PATH="$(brew --prefix bison)/bin:$(brew --prefix flex)/bin:$PATH" \
  cmake -B tools/yosys/build tools/yosys -DCMAKE_BUILD_TYPE=Release \
        -DYOSYS_SKIP_ABC_SUBMODULE_CHECK=ON -DCMAKE_INSTALL_PREFIX="$PWD/tools/install/yosys"
cmake --build tools/yosys/build --target install -j
```

`ppa.py` looks for the checkout in `ORFS_HOME` (default `~/Development/OpenROAD-flow-scripts`) and stops if it is not at `ORFS_COMMIT` or a tool is missing. Native runs stop before the GDS (KLayout), which the metrics do not need.

## Files

| File | Purpose |
| --- | --- |
| `ppa.py` | Configurations (`CONFIGS`), platforms with their target clocks (`PLATFORMS`), and the ORFS driver. Runs work in `build/orfs_native/<platform>/<config>/` (`build/orfs_docker/` with `--docker`). |
| `constraint.sdc` | Clock on `clk_i` (period from `ppa.py`), 20% of the period as I/O delay, `rst_ni` untimed (asynchronous, synchronized in the SoC). |
| `activity.py` | Runs one workload in Verilator through the cocotb testbench (checked against the cycle model) and dumps SAIF. |
| `power.py`, `power.tcl` | Flattens the SAIF to the routed netlist's names, scales it to the flow clock, and reads it into OpenSTA with the routed parasitics. Reports power and energy per MAC. |

## Usage

```
python3 flow/ppa.py list
python3 flow/ppa.py run sky130hd t8_csa              # full flow
python3 flow/ppa.py run asap7 t8_cpa --to synth      # stop after a stage
python3 flow/power.py sky130hd t8_csa gemm64         # after the flow: activity-based power
```

## How the numbers are produced

- **Area:** post-route standard-cell area of `tq_top`. The operand SRAM is outside `tq_top`, so this is the engine only.
- **Timing:** post-route STA at the target clock in `PLATFORMS`, typical corner of the platform.
- **Power:** OpenSTA with SAIF from an RTL simulation of the workload. Every register output is annotated from the simulation (those driving ports through the port's net); combinational nets are propagated by OpenSTA. Operands are uniform random int8, which switches more than real data. The SAIF is re-simulated whenever the RTL is newer.
- **Energy:** average power × the whole simulated window (the run plus a few dozen setup cycles), charged to the workload's useful MACs (M × N × K).
