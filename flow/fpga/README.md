<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1 -->
# FPGA build (ECP5, open tools)

An out-of-context build of `tq_top` on a Lattice ECP5-85F (`LFE5U-85F`, CABGA381, speed 6: the ULX3S / OrangeCrab part) with Yosys `synth_ecp5` and `nextpnr-ecp5`. It is a sanity check of Fmax and resources for an FPGA demo or prototype. It is **not** a PPA result: FPGA numbers never go into `flow/records/` or `flow/results.md`.

- Configurations are the ASIC ones in `flow/ppa.py` (`CONFIGS`, reference queues), with `CLK_GATE = 0` (FPGA clocks are not gated with latches; the enable flops are used instead).
- Out of context: no IO pads, because the memory ports (2 × 128 bits at T = 8) are wider than any ECP5 package. A board build wraps the core with an SoC (`rtl/adapters/tq_top_axi`) and on-chip RAM.

```
python3 flow/fpga/fpga.py synth t8_cpa              # build/fpga/t8_cpa/: tq_top.json, synth_stat.txt
python3 flow/fpga/fpga.py pnr   t8_cpa --freq 50    # place and route, prints Fmax and utilization
python3 flow/fpga/fpga.py all   t8_cpa
```

Tools come from `PATH`, or from `$OSS_CAD_SUITE/bin` (YosysHQ [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build)).

Synthesis at T = 8 (`t8_cpa`, Yosys 0.70, v1.0.0 RTL): 16,658 LUT4, 8,562 flip-flops, 2,294 CCU2C carry cells, 64 MULT18X18D (one per 9×9 multiplier). That is 20% of the 85F's LUTs and 41% of its multipliers. The flip-flop count equals the ASIC record (`flow/results.md`). These are synthesis-only figures: `nextpnr-ecp5` place and route has not been run, so there is no Fmax or utilization result here yet.
