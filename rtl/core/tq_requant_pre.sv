// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// TFLite epilogue lane, before the multiply (spec 10.1; model: requant.mbqm): acc + bias (int32
// wrap), the per-channel shift clamped to [-31, 30] and split into a left shift applied here and
// a right shift for tq_requant_post. tq_drain multiplies xs_o by M0 between the two.
// verif/formal/tq_requant.sby proves this against TFLite's MultiplyByQuantizedMultiplier.

module tq_requant_pre (
    input  logic [31:0] acc_i,
    input  logic [31:0] bias_i,
    input  logic [7:0]  shift_i,     // int8, from the parameter table
    output logic [31:0] xs_o,        // (acc + bias) << left, int32 wrap
    output logic [4:0]  right_o
);
  logic signed [7:0] sh8, shc;
  logic [4:0]        left;
  logic [31:0]       s;
  assign sh8     = shift_i;
  assign shc     = (sh8 < -8'sd31) ? -8'sd31 : (sh8 > 8'sd30) ? 8'sd30 : sh8;
  assign left    = shc[7] ? 5'd0 : shc[4:0];
  assign right_o = shc[7] ? 5'(-shc) : 5'd0;
  assign s       = acc_i + bias_i;                               // int32 wrap, as TFLite
  assign xs_o    = s << left;
endmodule
