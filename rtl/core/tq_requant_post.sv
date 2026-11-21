// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// TFLite epilogue lane, after the multiply (spec 10.1; model: requant.mbqm / requantize), from the
// 64-bit product xs * M0, then + output zero point and clamp to [act_lo, act_hi], as int8:
//   double rounding (single_i = 0, TFLite's default): SaturatingRoundingDoublingHighMul (sat_i:
//     both operands INT32_MIN), then RoundingDivideByPOT by right_i;
//   single rounding (single_i = 1, TFLITE_SINGLE_ROUNDING): (prod + 2^(t-1)) >> t with
//     t = 31 - shift = 31 + right - left, as int32.
// Both share the 64-bit adder (nudge or 2^(t-1)) and one arithmetic right shifter (the double
// path shifts the high half placed at bit 31 by 31 + right).
// verif/formal/tq_requant.sby proves this against TFLite's reference code for every product of
// two int32 values.

module tq_requant_post (
    input  logic [63:0] prod_i,
    input  logic        sat_i,
    input  logic        single_i,
    input  logic [4:0]  left_i,
    input  logic [4:0]  right_i,
    input  logic [7:0]  zp_i,
    input  logic [7:0]  act_lo_i,
    input  logic [7:0]  act_hi_i,
    output logic [7:0]  q_o
);
  logic signed [63:0] ab, add, q, sh_in;
  logic signed [31:0] hi, sh_out, res;
  logic [31:0]        mask, rem, thr;
  logic [5:0]         t, amt;
  logic signed [32:0] o, lo_c;
  logic signed [7:0]  zp, act_lo, act_hi;
  assign zp     = zp_i;
  assign act_lo = act_lo_i;
  assign act_hi = act_hi_i;
  assign ab     = $signed(prod_i);
  assign t      = 6'd31 + 6'(right_i) - 6'(left_i);                // 1..62 (left or right is 0)
  assign add    = single_i ? (64'sd1 <<< (t - 6'd1))
                : (ab[63] ? (64'sd1 - 64'sd1073741824) : 64'sd1073741824);
  assign q      = ab + add;
  assign hi     = sat_i ? 32'sh7fff_ffff
                : (q[63] ? -(32'(64'(-q) >> 31)) : 32'(q >> 31));   // truncate toward zero
  assign sh_in  = single_i ? q : {{1{hi[31]}}, hi, 31'd0};
  assign amt    = single_i ? t : 6'd31 + 6'(right_i);
  assign sh_out = 32'(sh_in >>> amt);                               // arithmetic, as int32
  assign mask   = (32'd1 << right_i) - 32'd1;
  assign rem    = hi & mask;
  assign thr    = (mask >> 1) + 32'(hi[31]);
  assign res    = sh_out + $signed({31'd0, !single_i && rem > thr});
  assign o      = 33'(res) + 33'(zp);
  assign lo_c   = (o < 33'(act_lo)) ? 33'(act_lo) : o;
  assign q_o    = (lo_c > 33'(act_hi)) ? act_hi : lo_c[7:0];
endmodule
