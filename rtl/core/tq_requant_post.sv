// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// TFLite epilogue lane, after the multiply (spec 10.1; model: requant.mbqm / requantize): from the
// 64-bit product xs * M0, SaturatingRoundingDoublingHighMul (sat_i: both operands INT32_MIN),
// RoundingDivideByPOT by right_i, + output zero point, clamp to [act_lo, act_hi], as int8.
// verif/formal/tq_requant.sby proves this against TFLite's reference code for every product of
// two int32 values.

module tq_requant_post (
    input  logic [63:0] prod_i,
    input  logic        sat_i,
    input  logic [4:0]  right_i,
    input  logic [7:0]  zp_i,
    input  logic [7:0]  act_lo_i,
    input  logic [7:0]  act_hi_i,
    output logic [7:0]  q_o
);
  logic signed [63:0] ab, q;
  logic signed [31:0] hi, hs, res;
  logic [31:0]        mask, rem, thr;
  logic signed [32:0] o, lo_c;
  logic signed [7:0]  zp, act_lo, act_hi;
  assign zp     = zp_i;
  assign act_lo = act_lo_i;
  assign act_hi = act_hi_i;
  assign ab     = $signed(prod_i);
  assign q      = ab + (ab[63] ? (64'sd1 - 64'sd1073741824) : 64'sd1073741824);
  assign hi     = sat_i ? 32'sh7fff_ffff
                : (q[63] ? -(32'(64'(-q) >> 31)) : 32'(q >> 31));   // truncate toward zero
  assign mask   = (32'd1 << right_i) - 32'd1;
  assign rem    = hi & mask;
  assign thr    = (mask >> 1) + 32'(hi[31]);
  assign hs     = hi >>> right_i;                                   // arithmetic (hi is signed)
  assign res    = hs + $signed({31'd0, rem > thr});
  assign o      = 33'(res) + 33'(zp);
  assign lo_c   = (o < 33'(act_lo)) ? 33'(act_lo) : o;
  assign q_o    = (lo_c > 33'(act_hi)) ? act_hi : lo_c[7:0];
endmodule
