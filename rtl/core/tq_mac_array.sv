// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Multiplier array and double-banked carry-save accumulator (spec sections 3, 4.1, 2.1).
// Model: TriQueueCore.do_beat (model/tri_queue/core.py).
//
// Pipeline for a beat issued in cycle n:
//   cycle n   : per-quadrant operand select -> T*T 9x9 multiplies -> product register
//   cycle n+1 : each accumulate cell compresses its (sum, carry) with up to 4 products
//   cycle n+2 : the bank holds the result (model: mac_pipe = 2)
//
// The array is split into four (T/2 x T/2) quadrants. In the reduced-depth shapes a quadrant
// works on a different k step and its products are routed to the TL, TR or BL quadrant's cells,
// so the cells' arity is fixed by position: TL 4, TR 2, BL 2, BR 1. Destinations that can never
// occur are constant-false, so synthesis removes those inputs.

module tq_mac_array #(
    parameter int T = 8,
    localparam int H   = T / 2,
    localparam int L2T = $clog2(T),
    localparam int VB  = 2 * T * 8          // port word (vector) width in bits
) (
    input  logic              clk_i,
    input  logic              rst_ni,
    // beat issue
    input  logic              beat_i,       // issue a beat this cycle
    input  logic              beat_idx_i,   // 0 or 1
    input  logic              rfull_i,      // shape: T rows (else T/2)
    input  logic              cfull_i,      // shape: T cols (else T/2)
    input  logic              bank_i,       // destination bank
    input  logic [2*VB-1:0]   a_i,          // up to two A vectors, first vector in the low bits
    input  logic [2*VB-1:0]   b_i,
    input  logic              a_signed_i,
    input  logic              b_signed_i,
    // bank clears (take priority over an accumulate in the same cycle)
    input  logic              clear_all_i,
    input  logic              clear_bank_i,
    input  logic              clear_bank_sel_i,
    // drain read port: values after the carry-propagate add
    input  logic              rd_bank_i,
    input  logic [L2T-1:0]    rd_row_i,
    input  logic              rd_half_i,    // columns [0, T/2) or [T/2, T)
    output logic [H*32-1:0]   rd_val_o,
    // product register status (drain must not read a bank with an accumulate still pending)
    output logic              prod_valid_o,
    output logic              prod_bank_o
);

  // ---------------------------------------------------------------- operand select + multiply
  logic [4*H*H*18-1:0] prod_d, prod_q;     // quadrant q = 2*qi + qj, element i*H + j
  logic                pv_q, pbank_q, prf_q, pcf_q;

  for (genvar qi = 0; qi < 2; qi++) begin : g_qi
    for (genvar qj = 0; qj < 2; qj++) begin : g_qj
      localparam int Q = 2 * qi + qj;
      logic [2:0]  d, off, kk;               // reduction depth, k-step offset, k step (0..7)
      logic [31:0] a_off, b_off;             // byte offsets into a_i / b_i
      logic [H*8-1:0] aq, bq;
      always_comb begin
        d     = (rfull_i && cfull_i) ? 3'd1 : (rfull_i || cfull_i) ? 3'd2 : 3'd4;
        // k offset: qi in 4xT shapes, qj in Tx4 shapes, 2*qi + qj in the 4x4x4 shape
        off   = (rfull_i ? 3'd0 : (cfull_i ? 3'(qi) : 3'(2 * qi))) + (cfull_i ? 3'd0 : 3'(qj));
        kk    = (beat_idx_i ? d : 3'd0) + off;
        // A: kk * r + H * rb, B: kk * c + H * cb  (r, c are T or T/2; rb, cb select the half)
        a_off = rfull_i ? ((32'(kk) << L2T) + 32'(H * qi)) : (32'(kk) << (L2T - 1));
        b_off = cfull_i ? ((32'(kk) << L2T) + 32'(H * qj)) : (32'(kk) << (L2T - 1));
        aq    = a_i[a_off * 8 +: H * 8];
        bq    = b_i[b_off * 8 +: H * 8];
      end
      for (genvar i = 0; i < H; i++) begin : g_i
        for (genvar j = 0; j < H; j++) begin : g_j
          logic signed [8:0]  av, bv;
          logic signed [17:0] p;
          assign av = {a_signed_i & aq[i*8+7], aq[i*8 +: 8]};
          assign bv = {b_signed_i & bq[j*8+7], bq[j*8 +: 8]};
          assign p  = av * bv;
          assign prod_d[(Q*H*H + i*H + j)*18 +: 18] = p;
        end
      end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      pv_q <= 1'b0; pbank_q <= 1'b0; prf_q <= 1'b0; pcf_q <= 1'b0;
    end else begin
      pv_q    <= beat_i;
      pbank_q <= bank_i;
      prf_q   <= rfull_i;
      pcf_q   <= cfull_i;
    end
  end
  always_ff @(posedge clk_i) begin
    if (beat_i) prod_q <= prod_d;
  end

  assign prod_valid_o = pv_q;
  assign prod_bank_o  = pbank_q;

  // ---------------------------------------------------------------- accumulate cells
  function automatic logic [63:0] csa(input logic [31:0] a, input logic [31:0] b, input logic [31:0] c);
    csa = {a ^ b ^ c, ((a & b) | (a & c) | (b & c)) << 1};    // {sum, carry}
  endfunction

  logic [T*T*32-1:0] sum0, car0, sum1, car1;   // every cell's state, for the drain read port

  for (genvar R = 0; R < T; R++) begin : g_row
    for (genvar C = 0; C < T; C++) begin : g_col
      localparam int QR = R / H, QC = C / H, I = R % H, J = C % H;
      logic [31:0]   s0_q, c0_q, s1_q, c1_q, s_cur, c_cur;
      logic [4*32-1:0] p_in;
      logic [63:0]   x0, x1, x2, x3;
      for (genvar s = 0; s < 4; s++) begin : g_src
        localparam int SI = s / 2, SJ = s % 2;
        localparam int PB = (s*H*H + I*H + J) * 18;
        logic hit;
        // source quadrant (SI, SJ) writes destination quadrant (rfull ? SI : 0, cfull ? SJ : 0)
        assign hit = ((prf_q ? SI : 0) == QR) && ((pcf_q ? SJ : 0) == QC);
        assign p_in[s*32 +: 32] = hit ? {{14{prod_q[PB + 17]}}, prod_q[PB +: 18]} : 32'd0;
      end
      assign s_cur = pbank_q ? s1_q : s0_q;
      assign c_cur = pbank_q ? c1_q : c0_q;
      assign x0 = csa(s_cur, c_cur, p_in[0 +: 32]);
      assign x1 = csa(x0[63:32], x0[31:0], p_in[32 +: 32]);
      assign x2 = csa(x1[63:32], x1[31:0], p_in[64 +: 32]);
      assign x3 = csa(x2[63:32], x2[31:0], p_in[96 +: 32]);
      always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
          s0_q <= '0; c0_q <= '0; s1_q <= '0; c1_q <= '0;
        end else begin
          if (clear_all_i || (clear_bank_i && !clear_bank_sel_i)) begin
            s0_q <= '0; c0_q <= '0;
          end else if (pv_q && !pbank_q) begin
            s0_q <= x3[63:32]; c0_q <= x3[31:0];
          end
          if (clear_all_i || (clear_bank_i && clear_bank_sel_i)) begin
            s1_q <= '0; c1_q <= '0;
          end else if (pv_q && pbank_q) begin
            s1_q <= x3[63:32]; c1_q <= x3[31:0];
          end
        end
      end
      assign sum0[(R*T + C)*32 +: 32] = s0_q;
      assign car0[(R*T + C)*32 +: 32] = c0_q;
      assign sum1[(R*T + C)*32 +: 32] = s1_q;
      assign car1[(R*T + C)*32 +: 32] = c1_q;
    end
  end

  // ---------------------------------------------------------------- drain read port
  // Select T/2 lanes of (sum, carry), then T/2 carry-propagate adders (spec 8: drain CPA lanes).
  localparam int BW = $clog2(T * T * 32);
  logic [BW-1:0]   base;
  logic [H*32-1:0] rs, rc;
  assign base = BW'((32'(rd_row_i) * T + (rd_half_i ? H : 0)) * 32);
  assign rs   = rd_bank_i ? sum1[base +: H * 32] : sum0[base +: H * 32];
  assign rc   = rd_bank_i ? car1[base +: H * 32] : car0[base +: H * 32];
  for (genvar l = 0; l < H; l++) begin : g_cpa
    assign rd_val_o[l*32 +: 32] = rs[l*32 +: 32] + rc[l*32 +: 32];
  end

endmodule
