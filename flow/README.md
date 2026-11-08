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
| `results.py` | `collect` condenses a finished native run and its power results into `records/<platform>/<config>.json` (committed); without arguments it writes `results.md` from the records. CI checks that `results.md` matches them. |
| `power.py`, `power.tcl` | Flattens the SAIF to the routed netlist's names, scales it to the flow clock, writes a register activity table, and reads both into OpenSTA with the routed parasitics. Reports power and energy per MAC. |

## Usage

```
python3 flow/ppa.py list
python3 flow/ppa.py run sky130hd t8_csa              # full flow
python3 flow/ppa.py run asap7 t8_cpa --to synth      # stop after a stage
python3 flow/ppa.py sweep asap7 t8_cpa 950 900 850   # clock sweep: one run per period, closure table
python3 flow/power.py sky130hd t8_csa gemm64         # after the flow: activity-based power
python3 flow/results.py collect sky130hd t8_csa      # record the run, rewrite results.md
```

`collect` refuses a run whose synthesis is older than `rtl/`, or when `rtl/` has uncommitted changes, so a record always names the RTL commit it was built from. A run made before an RTL change that cannot affect its netlist (a comment, or the default of a parameter the configuration sets) can be recorded with `--force`; say so in the commit.

## How the numbers are produced

- **Area:** post-route standard-cell area of `tq_top` (logic, flops, clock tree, buffers, taps; no fill). The operand SRAM is outside `tq_top`, so this is the engine only. kGE divides it by the PDK's smallest NAND2 (`NAND2_UM2` in `results.py`).
- **Clock gates:** the RTL's `tq_icg` cells (`CLK_GATE = 1`) become the platform's integrated clock-gating cell through ORFS's `OPENROAD_CLKGATE` map (`-DOPENROAD_CLKGATE`): `sky130_fd_sc_hd__dlclkp_1`, `ICGx1_ASAP7_75t_R`. CTS builds a subtree below each gate.
- **Timing:** post-route STA at the target clock in `PLATFORMS`, typical corner of the platform. Fmax is 1 ÷ (period − worst setup slack), from ORFS. `ppa.py sweep` reruns a config at other periods (work directories `<config>_p<period>`, never recorded) to find the fastest period that closes.
- **Power:** OpenSTA with SAIF from an RTL simulation of the workload. Nets that kept their RTL names are annotated from the SAIF, and every register's output pin is annotated by its cell name (the RTL bit plus a Yosys suffix), with the duty inverted on `QN`. That covers flops whose named net is gone, such as asap7's QN-only flops after ABC absorbs the inverter. Other combinational nets are propagated by OpenSTA. Operands are uniform random int8, which switches more than real data. The SAIF is re-simulated whenever the RTL is newer.
- **Clock-gate enables** are annotated from the simulated `en_i` of each `tq_cg` instance. OpenSTA scales the gated clock's activity by the enable's duty, so the gated subtree and its flops' clock pins are charged only for the cycles the gate is open.
- **Energy:** average power × the whole simulated window (the run plus a few dozen setup cycles), charged to the workload's useful MACs (M × N × K).
- **Engine + SRAM (sky130hd only):** each workload's read and write transactions (from the cycle model, which the activity run matches transaction for transaction) × the energy per access of ORFS's OpenRAM macro `sky130_sram_1rw1r_128x256_8` (128-bit word, one per 16 bytes of port), plus its leakage over the window. OpenRAM's liberty gives one energy per clock edge whatever the macro does, so an access is charged one clock cycle and the macro clock is assumed gated while idle. ASAP7 has only fakeram, whose values its generator calls not realistic, so it has no SRAM line.
