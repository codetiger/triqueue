<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1 -->
# FPGA build (ECP5, open tools)

An out-of-context build of `tq_top` on a Lattice ECP5-85F (`LFE5U-85F`, CABGA381, speed 6: the ULX3S / OrangeCrab part) with Yosys `synth_ecp5` (and optional `nextpnr-ecp5` place and route). In v1, only the synthesis-only resource sanity check has been run (`nextpnr-ecp5` place and route is optional). It is **not** a PPA result: FPGA numbers never go into `flow/records/` or `flow/results.md`.

- Configurations are the ASIC ones in `flow/ppa.py` (`CONFIGS`, reference queues), with `CLK_GATE = 0` (FPGA clocks are not gated with latches; the enable flops are used instead).
- Out of context: no IO pads, because the memory ports (2 × 128 bits at T = 8) are wider than any ECP5 package. A board build wraps the core with an SoC (`rtl/adapters/tq_top_axi`) and on-chip RAM.

```
python3 flow/fpga/fpga.py synth t8_cpa              # build/fpga/t8_cpa/: tq_top.json, synth_stat.txt
python3 flow/fpga/fpga.py pnr   t8_cpa --freq 50    # place and route, prints Fmax and utilization
python3 flow/fpga/fpga.py all   t8_cpa
```

Tools come from `PATH`, or from `$OSS_CAD_SUITE/bin` (YosysHQ [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build)).

Synthesis at T = 8 on the v1.0.1 RTL (Yosys 0.70+post, `0c11ea0`, measured 2026-11-15):

| Config | Parameters | LUT4 | Flip-flops | CCU2C | MULT18X18D |
| --- | --- | --- | --- | --- | --- |
| `t8_cpa` | `ACC_FORM=1` (plain 32-bit adder) | 18,948 | 8,563 | 2,341 | 64 |
| `t8_shared` | `ACC_FORM=1`, `SHARED_PORT=1` | 18,423 | 8,564 | 2,340 | 64 |
| `t8_csa` | `ACC_FORM=0` (carry-save) | 33,314 | 12,531 | 853 | 64 |

`t8_cpa` is 23% of the 85F's 83,640 LUT4 and 41% of its 156 multipliers, with one `MULT18X18D` per 9×9 multiplier. The flip-flop count is within one of the ASIC record (`flow/results.md`: 8,562 for `t8_cpa` and `t8_cpa_nocg`). Carry-save costs 14,366 more LUT4 and 3,968 more flip-flops than the default adder, which is why the plain adder is the default (spec §10.2).

The v1.0.1 control-plane work (wider `PCW`, `CFG` serialization, `STATUS.PENDING`, the new error checks) costs about 2.3k LUT4 at T = 8: the same command at tag `v1.0.0` gives 16,658 LUT4, 8,562 flip-flops and 2,294 CCU2C. The multiplier array, accumulator cells and FIFOs are unchanged, so the 64 `MULT18X18D` and the flip-flop count hold.

These are synthesis-only figures: `nextpnr-ecp5` place and route has not been run, so there is no Fmax or utilization result here yet. They are not area, power or a board result.
