// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Integrated clock-gating cell, used by tq_cg. The model (a latch transparent while the clock
// is low, then an AND) is for simulation and generic synthesis. Physical design maps it to the
// PDK's cell: the ORFS flow (flow/ppa.py) defines OPENROAD_CLKGATE, and ORFS provides an
// OPENROAD_CLKGATE module for each platform. An SoC flow replaces this file with its own cell.

module tq_icg (
    input  logic clk_i,
    input  logic en_i,
    output logic gclk_o
);
`ifdef OPENROAD_CLKGATE
  OPENROAD_CLKGATE u_cell (.CK(clk_i), .E(en_i), .GCK(gclk_o));
`else
  logic en_l;
  always_latch begin
    if (!clk_i) en_l = en_i;
  end
  assign gclk_o = clk_i & en_l;
`endif
endmodule
