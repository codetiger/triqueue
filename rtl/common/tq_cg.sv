// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Clock gate for one register group. Every gated register keeps its own write condition, and
// the gate removes only the clock edges where that condition is false:
//
//   tq_cg #(.EN(GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
//   always_ff @(posedge gclk) if (we || GATE) q <= d;
//
// With EN = 0 the clock passes through and the register is a plain enable flop, which is how
// the formal proofs see the design (they run with CLK_GATE = 0). With EN = 1 the enable mux is
// gone, so the result depends on the gate: it must be glitch-free and latch the enable while
// the clock is low.
//
// tq_icg (tq_icg.sv) is the clock-gating cell itself.

module tq_cg #(
    parameter bit EN = 1'b1
) (
    input  logic clk_i,
    input  logic en_i,
    output logic gclk_o
);
  if (EN) begin : g_gate
    tq_icg u_icg (.clk_i, .en_i, .gclk_o);
  end else begin : g_pass
    assign gclk_o = clk_i;
    /* verilator lint_off UNUSEDSIGNAL */
    logic unused_en;
    assign unused_en = en_i;
    /* verilator lint_on UNUSEDSIGNAL */
  end
endmodule
