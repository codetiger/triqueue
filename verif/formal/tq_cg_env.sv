// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Formal environment for the clock-gating template (verif/formal/tq_cg.sby, multiclock).
// Every gated register in the RTL follows one pattern (rtl/README.md):
//
//   tq_cg #(.EN(GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
//   always_ff @(posedge gclk) if (we || GATE) q <= d;
//
// The core proofs run with CLK_GATE = 0, where this is an enable flop. This proves that with
// GATE = 1 and the real tq_icg model (latch + AND), the pattern is the same register: for any
// write enable and data that, like the core's, come from flops on the clock, a gated register
// always equals the enable flop, with and without an asynchronous reset, and the gated clock
// only rises on a rising edge of the clock.

module tq_cg_env (
    input logic we_i,
    input logic d_i,
    input logic rst_ni
);
  (* gclk *) logic step;                 // the solver's time step
  logic clk = 1'b0;
  always @(posedge step) clk <= !clk;    // a free-running clock, two steps per period

  // write enable and data from flops on clk (they change just after a rising edge)
  logic we_q = 1'b0, d_q = 1'b0;
  always @(posedge clk) begin
    we_q <= we_i;
    d_q  <= d_i;
  end

  logic gclk;
  tq_cg #(.EN(1'b1)) u_cg (.clk_i(clk), .en_i(we_q), .gclk_o(gclk));

  // without reset
  logic q_gated = 1'b0, q_ref = 1'b0;
  always @(posedge gclk) q_gated <= d_q;            // GATE = 1: no enable term
  always @(posedge clk) if (we_q) q_ref <= d_q;     // the enable flop it replaces

  // with an asynchronous reset (the accumulator banks, program buffer)
  logic r_gated = 1'b0, r_ref = 1'b0;
  always @(posedge gclk or negedge rst_ni) begin
    if (!rst_ni) r_gated <= 1'b0;
    else         r_gated <= d_q;
  end
  always @(posedge clk or negedge rst_ni) begin
    if (!rst_ni)   r_ref <= 1'b0;
    else if (we_q) r_ref <= d_q;
  end

  logic clk_p = 1'b0, gclk_p = 1'b0;
  always @(posedge step) begin
    clk_p  <= clk;
    gclk_p <= gclk;
  end

  always @(*) begin
    assert (q_gated == q_ref);
    assert (r_gated == r_ref);
    if (gclk && !gclk_p) assert (clk && !clk_p);    // no gated edge without a clock edge
    cover (gclk && !gclk_p);                        // a gated edge happens
    cover (q_gated && r_gated);                     // data is written through the gate
  end
endmodule
