# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# tq_top timing constraints for OpenROAD-flow-scripts. The clock period comes from the
# environment (TQ_CLK_PERIOD, in the platform's time unit: ns for sky130hd and ihp-sg13g2,
# ps for asap7), set by flow/ppa.py.
#
# I/O: 20% of the period on each side, a stand-in for the SoC adapter and SRAM.
# rst_ni is asynchronous and assumed to come from a reset synchronizer in the SoC,
# so it is not timed here.

current_design tq_top

set clk_period $::env(TQ_CLK_PERIOD)
set clk_io_pct 0.2

create_clock -name core_clock -period $clk_period [get_ports clk_i]
create_clock -name vclk -period $clk_period

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr {$clk_period * $clk_io_pct}] -clock vclk $non_clock_inputs
set_output_delay [expr {$clk_period * $clk_io_pct}] -clock vclk [all_outputs]
set_false_path -from [get_ports rst_ni]
