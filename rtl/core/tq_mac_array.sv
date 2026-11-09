// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Multiplier array and double-banked accumulator (spec sections 3, 4.1, 2.1).
// Model: TriQueueCore.do_beat (model/tri_queue/core.py).
//
// Pipeline for a beat issued in cycle n:
//   cycle n   : per-quadrant operand select -> T*T 9x9 multiplies -> product register
//   cycle n+1 : each accumulate cell adds up to 4 products to its stored value
//   cycle n+2 : the bank holds the result (model: mac_pipe = 2)
//
// MAC_PIPE = 1 removes the product register: the multiplies feed the accumulate cells in the
// beat's own cycle, and the bank holds the result in cycle n+1 (model: mac_pipe = 1). The
// critical path then runs from the operands through the multiplier and the adder.
//
// The array is split into four (T/2 x T/2) quadrants. In the reduced-depth shapes a quadrant
// works on a different k step and its products are routed to the TL, TR or BL quadrant's cells,
// so the cells' arity is fixed by position: TL 4, TR 2, BL 2, BR 1. Destinations that can never
// occur are constant-false, so synthesis removes those inputs.
//
// ACC_FORM (spec 10.2) selects how a cell stores its running sum. Both give the same values
// mod 2^32 and the same timing:
//   tq_pkg::ACC_CPA  one 32-bit value, a carry-propagate adder in the loop, no drain CPA (default)
//   tq_pkg::ACC_CSA  carry-save (sum, carry), 3:2 compressors in the loop, CPA lanes at the drain
//
// CLK_GATE (tq_cg): the product register is clocked only on a beat, one gate per quadrant. Each
// bank has one gate per quadrant, open for a clear or for an accumulate that can reach the
// quadrant: in the reduced-depth shapes only TL (4x4x4) or the top / left half (4xT, Tx4)
// receives products, so the other cells keep their value instead of adding zero.

module tq_mac_array #(
    parameter int T        = 8,
    parameter int ACC_FORM = tq_pkg::ACC_CPA,
    parameter bit CLK_GATE = 1'b1,
    parameter int MAC_PIPE = 2,          // 2: product register (default); 1: multiply and accumulate in one cycle
    localparam int H   = T / 2,
    localparam int L2T = $clog2(T),
    localparam int VB  = 2 * T * 8          // port word (vector) width in bits
) (
    input  logic              clk_i,
    input  logic              rst_ni,
    // beat issue
    input  logic              beat_i,       // issue a beat this cycle
    input  logic              rfull_i,      // shape: T rows (else T/2)
    input  logic              cfull_i,      // shape: T cols (else T/2)
    input  logic              bank_i,       // destination bank
    input  logic [VB-1:0]     a_i,          // current beat's A slice (pre-selected in tq_core)
    input  logic [VB-1:0]     b_i,
    input  logic              a_signed_i,
    input  logic              b_signed_i,
    // bank clears (take priority over an accumulate in the same cycle)
    input  logic              clear_all_i,
    input  logic              clear_bank_i,
    input  logic              clear_bank_sel_i,
    // drain read port: one T-value tile row after the carry-propagate add
    input  logic              rd_bank_i,
    input  logic [L2T-1:0]    rd_row_i,
    output logic [T*32-1:0]   rd_val_o,
    // product register status (drain must not read a bank with an accumulate still pending)
    output logic              prod_valid_o,
    output logic              prod_bank_o
);

  // ---------------------------------------------------------------- operand select + multiply
  localparam int PW = 17;                  // int8/uint8 product in [-32640, 65025] fits 17-bit signed
  logic [4*H*H*PW-1:0] prod_d, prod_q;     // quadrant q = 2*qi + qj, element i*H + j
  logic                pv_q, pbank_q, prf_q, pcf_q;   // the accumulate stage's beat (registered or not)

  logic [H*8-1:0] ac0, ac1, ac2, ac3, bc0, bc1, bc2, bc3;
  assign ac0 = a_i[0*H*8 +: H*8];
  assign ac1 = a_i[1*H*8 +: H*8];
  assign ac2 = a_i[2*H*8 +: H*8];
  assign ac3 = a_i[3*H*8 +: H*8];
  assign bc0 = b_i[0*H*8 +: H*8];
  assign bc1 = b_i[1*H*8 +: H*8];
  assign bc2 = b_i[2*H*8 +: H*8];
  assign bc3 = b_i[3*H*8 +: H*8];

  for (genvar qi = 0; qi < 2; qi++) begin : g_qi
    for (genvar qj = 0; qj < 2; qj++) begin : g_qj
      localparam int Q = 2 * qi + qj;
      logic [H*8-1:0] aq, bq;
      if (Q == 0) begin : g_sel0
        assign aq = ac0;
        assign bq = bc0;
      end else if (Q == 1) begin : g_sel1
        assign aq = cfull_i ? ac0 : (rfull_i ? ac2 : ac1);
        assign bq = bc1;
      end else if (Q == 2) begin : g_sel2
        assign aq = (!rfull_i && !cfull_i) ? ac2 : ac1;
        assign bq = rfull_i ? bc0 : bc2;
      end else begin : g_sel3
        assign aq = cfull_i ? ac1 : ac3;
        assign bq = rfull_i ? bc1 : bc3;
      end

      logic [H*7-1:0] a_inv, b_inv;
      for (genvar m = 0; m < H; m++) begin : g_inv
        assign a_inv[m*7 +: 7] = aq[m*8 +: 7] ^ {7{b_signed_i}};
        assign b_inv[m*7 +: 7] = bq[m*8 +: 7] ^ {7{a_signed_i}};
      end

      for (genvar i = 0; i < H; i++) begin : g_i
        for (genvar j = 0; j < H; j++) begin : g_j
          logic [7:0]  a, b;
          logic [13:0] p_lo;
          logic        na, nb;
          logic [9:0]  ca, cb, cab, hi;
          assign a    = aq[i*8 +: 8];
          assign b    = bq[j*8 +: 8];
          assign p_lo = a[6:0] * b[6:0];
          assign na   = a[7] & a_signed_i;
          assign nb   = b[7] & b_signed_i;
          assign ca   = a[7] ? {{3{a_signed_i}}, b_inv[j*7 +: 7]} : 10'd0;
          assign cb   = b[7] ? {{3{b_signed_i}}, a_inv[i*7 +: 7]} : 10'd0;
          assign cab  = {(a[7] & b[7]) ? ((a_signed_i ^ b_signed_i) ? 3'b111 : 3'b001) : 3'b000,
                         5'd0, na & nb, na ^ nb};
          assign hi   = {3'b0, p_lo[13:7]} + ca + cb + cab;
          assign prod_d[(Q*H*H + i*H + j)*PW +: PW] = {hi, p_lo[6:0]};
        end
      end
    end
  end

  if (MAC_PIPE == 2) begin : g_pipe2
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
    for (genvar q = 0; q < 4; q++) begin : g_preg
      logic               gclk;
      logic [H*H*PW-1:0]  pq;
      tq_cg #(.EN(CLK_GATE)) u_cg (.clk_i, .en_i(beat_i), .gclk_o(gclk));
      always_ff @(posedge gclk) begin
        if (beat_i || CLK_GATE) pq <= prod_d[q*H*H*PW +: H*H*PW];
      end
      assign prod_q[q*H*H*PW +: H*H*PW] = pq;
    end
  end else begin : g_pipe1                 // no product register: accumulate in the beat's cycle
    /* verilator lint_off UNUSEDSIGNAL */
    logic unused_rst;
    assign unused_rst = rst_ni;
    /* verilator lint_on UNUSEDSIGNAL */
    assign pv_q    = beat_i;
    assign pbank_q = bank_i;
    assign prf_q   = rfull_i;
    assign pcf_q   = cfull_i;
    assign prod_q  = prod_d;
  end

  assign prod_valid_o = pv_q;
  assign prod_bank_o  = pbank_q;

  // ---------------------------------------------------------------- accumulate cells
  function automatic logic [63:0] csa(input logic [31:0] a, input logic [31:0] b, input logic [31:0] c);
    csa = {a ^ b ^ c, ((a & b) | (a & c) | (b & c)) << 1};    // {sum, carry}
  endfunction

  logic [T*T*32-1:0] sum0, car0, sum1, car1;   // every cell's state, for the drain read port

  // bank write conditions and clock gates, per quadrant (QR, QC) = (q / 2, q % 2)
  logic clr0, clr1, acc0, acc1;
  assign clr0 = clear_all_i || (clear_bank_i && !clear_bank_sel_i);
  assign clr1 = clear_all_i || (clear_bank_i &&  clear_bank_sel_i);
  assign acc0 = pv_q && !pbank_q;
  assign acc1 = pv_q &&  pbank_q;
  // A clear and an accumulate of the same bank in one cycle: with MAC_PIPE = 2 the accumulate
  // is of a beat issued before the ZERO_C, which the clear drops (model: the beat's sums are
  // cleared). With MAC_PIPE = 1 it is of a beat issued in the ZERO_C's cycle, after it (the MAC
  // unit takes ZERO_C, then VOUTER_MAC): the products land on a zero bank.
  logic zbase;
  assign zbase = (MAC_PIPE == 1) && (pbank_q ? clr1 : clr0);
  logic [3:0] dst, keep0, keep1, we0, we1, gclk0, gclk1;
  for (genvar q = 0; q < 4; q++) begin : g_bank_cg
    // some source quadrant writes this one
    assign dst[q]   = (prf_q || q / 2 == 0) && (pcf_q || q % 2 == 0);
    assign keep0[q] = (MAC_PIPE == 1) && acc0 && dst[q];
    assign keep1[q] = (MAC_PIPE == 1) && acc1 && dst[q];
    assign we0[q]   = clr0 || (acc0 && dst[q]);
    assign we1[q]   = clr1 || (acc1 && dst[q]);
    tq_cg #(.EN(CLK_GATE)) u_cg0 (.clk_i, .en_i(we0[q]), .gclk_o(gclk0[q]));
    tq_cg #(.EN(CLK_GATE)) u_cg1 (.clk_i, .en_i(we1[q]), .gclk_o(gclk1[q]));
  end

  for (genvar I = 0; I < H; I++) begin : g_i_acc
    for (genvar J = 0; J < H; J++) begin : g_j_acc
      logic signed [PW-1:0] p0, p1, p2, p3;
      assign p0 = signed'(prod_q[(0*H*H + I*H + J)*PW +: PW]);
      assign p1 = signed'(prod_q[(1*H*H + I*H + J)*PW +: PW]);
      assign p2 = signed'(prod_q[(2*H*H + I*H + J)*PW +: PW]);
      assign p3 = signed'(prod_q[(3*H*H + I*H + J)*PW +: PW]);

      if (ACC_FORM == tq_pkg::ACC_CSA) begin : g_csa
        for (genvar QR = 0; QR < 2; QR++) begin : g_qr
          for (genvar QC = 0; QC < 2; QC++) begin : g_qc
            localparam int Q = 2 * QR + QC;
            localparam int R = QR * H + I, C = QC * H + J;
            logic [31:0] s0_q, c0_q, s1_q, c1_q, s_cur, c_cur;
            logic [63:0] x_out;
            assign s_cur = zbase ? '0 : pbank_q ? s1_q : s0_q;
            assign c_cur = zbase ? '0 : pbank_q ? c1_q : c0_q;
            if (Q == 0) begin : g_q0
              logic [63:0] x0, x1, x2;
              assign x0    = csa(s_cur, c_cur, 32'(p0));
              assign x1    = csa(x0[63:32], x0[31:0], pcf_q ? 32'd0 : 32'(p1));
              assign x2    = csa(x1[63:32], x1[31:0], prf_q ? 32'd0 : 32'(p2));
              assign x_out = csa(x2[63:32], x2[31:0], (prf_q || pcf_q) ? 32'd0 : 32'(p3));
            end else if (Q == 1) begin : g_q1
              logic [63:0] x0;
              assign x0    = csa(s_cur, c_cur, 32'(p1));
              assign x_out = csa(x0[63:32], x0[31:0], prf_q ? 32'd0 : 32'(p3));
            end else if (Q == 2) begin : g_q2
              logic [63:0] x0;
              assign x0    = csa(s_cur, c_cur, 32'(p2));
              assign x_out = csa(x0[63:32], x0[31:0], pcf_q ? 32'd0 : 32'(p3));
            end else begin : g_q3
              assign x_out = csa(s_cur, c_cur, 32'(p3));
            end
            always_ff @(posedge gclk0[Q]) begin
              if (we0[Q] || CLK_GATE) begin
                s0_q <= clr0 && !keep0[Q] ? '0 : x_out[63:32];
                c0_q <= clr0 && !keep0[Q] ? '0 : x_out[31:0];
              end
            end
            always_ff @(posedge gclk1[Q]) begin
              if (we1[Q] || CLK_GATE) begin
                s1_q <= clr1 && !keep1[Q] ? '0 : x_out[63:32];
                c1_q <= clr1 && !keep1[Q] ? '0 : x_out[31:0];
              end
            end
            assign sum0[(R*T + C)*32 +: 32] = s0_q;
            assign car0[(R*T + C)*32 +: 32] = c0_q;
            assign sum1[(R*T + C)*32 +: 32] = s1_q;
            assign car1[(R*T + C)*32 +: 32] = c1_q;
          end
        end
      end else begin : g_cpa
        // Shared narrow product sums: s23 feeds both TL (when !prf_q) and BL (when prf_q)
        logic signed [PW:0]   s01, s23, s13;
        logic signed [PW+1:0] s0123;
        assign s01   = (PW+1)'(p0) + (pcf_q ? 18'sd0 : (PW+1)'(p1));
        assign s23   = (PW+1)'(p2) + (pcf_q ? 18'sd0 : (PW+1)'(p3));
        assign s13   = (PW+1)'(p1) + (prf_q ? 18'sd0 : (PW+1)'(p3));
        assign s0123 = (PW+2)'(s01) + (prf_q ? 19'sd0 : (PW+2)'(s23));

        for (genvar QR = 0; QR < 2; QR++) begin : g_qr
          for (genvar QC = 0; QC < 2; QC++) begin : g_qc
            localparam int Q = 2 * QR + QC;
            localparam int R = QR * H + I, C = QC * H + J;
            logic [31:0] v0_q, v1_q, p_ext, v_nxt;
            if (Q == 0)      begin : g_p0 assign p_ext = 32'(s0123); end
            else if (Q == 1) begin : g_p1 assign p_ext = 32'(s13);   end
            else if (Q == 2) begin : g_p2 assign p_ext = 32'(s23);   end
            else             begin : g_p3 assign p_ext = 32'(p3);    end
            assign v_nxt = (zbase ? '0 : pbank_q ? v1_q : v0_q) + p_ext;
            always_ff @(posedge gclk0[Q]) begin
              if (we0[Q] || CLK_GATE) v0_q <= clr0 && !keep0[Q] ? '0 : v_nxt;
            end
            always_ff @(posedge gclk1[Q]) begin
              if (we1[Q] || CLK_GATE) v1_q <= clr1 && !keep1[Q] ? '0 : v_nxt;
            end
            assign sum0[(R*T + C)*32 +: 32] = v0_q;
            assign sum1[(R*T + C)*32 +: 32] = v1_q;
            assign car0[(R*T + C)*32 +: 32] = 32'd0;
            assign car1[(R*T + C)*32 +: 32] = 32'd0;
          end
        end
      end
    end
  end

  // ---------------------------------------------------------------- drain read port
  // Select one T-value tile row of (sum, carry), then T carry-propagate adders in ACC_CSA mode.
  // In the CPA form the lanes are the stored values themselves.
  localparam int BW = $clog2(T * T * 32);
  logic [BW-1:0]   base;
  logic [T*32-1:0] rs, rc;
  assign base = BW'(32'(rd_row_i) * T * 32);
  assign rs   = rd_bank_i ? sum1[base +: T * 32] : sum0[base +: T * 32];
  assign rc   = rd_bank_i ? car1[base +: T * 32] : car0[base +: T * 32];
  if (ACC_FORM == tq_pkg::ACC_CSA) begin : g_drain_cpa
    for (genvar l = 0; l < T; l++) begin : g_lane
      assign rd_val_o[l*32 +: 32] = rs[l*32 +: 32] + rc[l*32 +: 32];
    end
  end else begin : g_drain_direct
    assign rd_val_o = rs;
    /* verilator lint_off UNUSEDSIGNAL */
    logic unused_rc;
    assign unused_rc = ^rc;
    /* verilator lint_on UNUSEDSIGNAL */
  end

endmodule
