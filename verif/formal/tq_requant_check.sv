// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Equivalence of the epilogue lane (tq_requant_pre / tq_requant_post, spec 10.1) with TFLite's
// int8 output stage, for every input (tq_requant.sby; the inputs of this module are free):
//   pre:  (acc + bias) << left and the right shift, against MultiplyByQuantizedMultiplier's
//         x * (1 << left_shift) (int32 wrap) with shift clamped to [-31, 30];
//   post: for every 64-bit product of two int32 values, SaturatingRoundingDoublingHighMul as
//         written in TFLite ((ab + nudge) / 2^31, C++ division), then rounding divide by 2^right
//         defined independently (round half away from zero), + zero point, max(act_min) then
//         min(act_max), as int8.
// The 32 x 32 multiply between the two stages is the `*` operator in tq_drain.

module tq_requant_check (
    input logic [31:0] acc,
    input logic [31:0] bias,
    input logic [7:0]  shift,
    input logic [63:0] prod,
    input logic        sat,
    input logic [4:0]  right,
    input logic [7:0]  zp,
    input logic [7:0]  act_lo,
    input logic [7:0]  act_hi
);
`ifdef TQ_REQUANT_PRE
  logic [31:0] xs;
  logic [4:0]  r;
  tq_requant_pre dut (.acc_i(acc), .bias_i(bias), .shift_i(shift), .xs_o(xs), .right_o(r));

  logic signed [31:0] sh, left_ref, right_ref, x, a_ref;
  always_comb begin
    sh        = 32'($signed(shift));
    sh        = (sh < -32'sd31) ? -32'sd31 : (sh > 32'sd30) ? 32'sd30 : sh;
    left_ref  = sh > 0 ? sh : 32'sd0;
    right_ref = sh > 0 ? 32'sd0 : -sh;
    x         = $signed(acc) + $signed(bias);
    a_ref     = x * (32'sd1 <<< left_ref);
    assert (xs == a_ref);
    assert (32'(r) == right_ref);
  end
`endif

`ifdef TQ_REQUANT_POST
  logic [7:0] q;
  tq_requant_post dut (.prod_i(prod), .sat_i(sat), .right_i(right), .zp_i(zp), .act_lo_i(act_lo),
                       .act_hi_i(act_hi), .q_o(q));

  localparam logic signed [63:0] P_MAX = 64'sh4000_0000_0000_0000;            //  2^62 = MIN * MIN
  localparam logic signed [63:0] P_MIN = -64'sh4000_0000_0000_0000 + 64'sh8000_0000;  // MIN * MAX
  logic signed [63:0] ab, nudge, v;
  logic signed [31:0] hi;
  logic signed [33:0] h, half, mag, rq;
  logic signed [33:0] o;
  logic signed [7:0]  q_ref;
  always_comb begin
    assume ($signed(prod) >= P_MIN && $signed(prod) <= P_MAX);
    assume (sat == ($signed(prod) == P_MAX));                // both operands INT32_MIN
    ab    = $signed(prod);
    nudge = ab >= 0 ? 64'sd1073741824 : 64'sd1 - 64'sd1073741824;
    v     = ab + nudge;
    hi    = sat ? 32'sh7fff_ffff : 32'(v / 64'sh8000_0000);    // C++ / truncates toward zero
    h     = 34'(hi);
    half  = right == 5'd0 ? 34'sd0 : (34'sd1 <<< (right - 5'd1));
    mag   = (h < 0 ? -h : h) + half;
    rq    = h < 0 ? -(mag >>> right) : (mag >>> right);         // round half away from zero
    o     = rq + 34'($signed(zp));
    o     = o < 34'($signed(act_lo)) ? 34'($signed(act_lo)) : o;
    o     = o > 34'($signed(act_hi)) ? 34'($signed(act_hi)) : o;
    q_ref = o[7:0];
    assert (q == q_ref);
  end
`endif
endmodule
