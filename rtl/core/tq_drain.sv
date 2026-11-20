// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Drain unit (spec 5.1, 10.1): after SWAP_C, writes the finished bank to C as aligned port words,
// T/2 int32 lanes per word (or one T-byte int8 tile row per write when out_int8_i is set) with
// byte enables, then clears the bank.
// Model: TriQueueCore.start_drain / _issue_write (model/tri_queue/core.py).
//
// One carry-propagate / requantization stage feeds a one-word staging register; the write port
// takes the staged word.
//
// TFLite epilogue (MODE[9], spec 10.1; model: _epi_plan): EPI_LANES values per cycle enter two
// stages (A: + bias, << left shift, 32 x 32 multiply by M0; B: rounding high multiply, rounding
// right shift, + zero point, clamp) into a T-byte row buffer; a row is written when its last group
// lands, and while a finished row waits for gnt the lanes hold. Groups enter from the first cycle
// the bank is stable and the tile column's parameters are valid. The first word is produced once the bank has no accumulate pending, so
// the first write lands at max(swap, last beat + 2) + 1 (model: ready = max(cycle, last_write) + cpa_latency).

module tq_drain #(
    parameter int T = 8,
    parameter bit CLK_GATE = 1'b1,      // clock gate on the staging register (tq_cg)
    parameter int EPI_LANES = T / 2,    // epilogue values requantized per cycle (divides T)
    localparam int H   = T / 2,
    localparam int W   = 2 * T,             // port width in bytes
    localparam int L2T = $clog2(T)
) (
    input  logic            clk_i,
    input  logic            rst_ni,
    input  logic            clear_i,         // abort / restart
    // start (from SWAP_C)
    input  logic            start_i,
    input  logic            start_bank_i,
    input  logic [31:0]     c_addr_i,
    input  logic [7:0]      rows_i,
    input  logic [7:0]      cols_i,
    input  logic [31:0]     c_ld_i,
    input  logic            c_base_err_i,    // C_BASE not a multiple of W
    input  logic            out_int8_i,      // MODE[2]: write C as saturating int8, T lanes per write
    input  logic [4:0]      out_shift_i,     // MODE[7:3]: right shift before int8 clamp
    // epilogue (MODE[9]): this tile column's parameters and the per-tensor values
    input  logic            epi_i,
    input  logic            par_valid_i,
    input  logic [T*32-1:0] par_bias_i,
    input  logic [T*32-1:0] par_m0_i,
    input  logic [T*8-1:0]  par_shift_i,
    input  logic [23:0]     epi_out_i,       // {act_max, act_min, zp_out}
    // accumulate pipeline status
    input  logic            prod_valid_i,
    input  logic            prod_bank_i,
    // bank read port (tq_mac_array)
    output logic            rd_bank_o,
    output logic [L2T-1:0]  rd_row_o,
    input  logic [T*32-1:0] rd_val_i,
    // write port
    output logic            wr_valid_o,
    output logic [31:0]     wr_addr_o,
    output logic [W-1:0]    wr_be_o,
    output logic [W*8-1:0]  wr_data_o,
    input  logic            wr_fire_i,       // the staged word is written this cycle
    // status
    output logic            clear_bank_o,
    output logic            clear_bank_sel_o,
    output logic            active_o,
    output logic            active_next_o,
    output logic            align_err_o
);
  localparam int RCW = L2T + 1;
  /* verilator lint_off UNUSEDSIGNAL */
  logic unused_rc_hi;
  assign unused_rc_hi = ^{rows_i[7:L2T], cols_i[7:RCW]};
  /* verilator lint_on UNUSEDSIGNAL */

  // drain state
  logic           act_q, bank_q, pdone_q, phalf_q, int8_q;
  logic [4:0]     shift_q;
  logic [L2T-1:0] rows_q, prow_q;
  logic [RCW-1:0] cols_q;
  logic [31:0]    c_ld_q, paddr_q;
  // staging register
  logic           stg_v_q, stg_last_q;
  logic [31:0]    stg_addr_q;
  logic [W-1:0]   stg_be_q;
  logic [W*8-1:0] stg_data_q;

  // current drain parameters (a drain starting this cycle may produce its first word at once)
  logic           bank, pdone, phalf, out_int8, src_act, stable, produce, last, cols_gt_h, split_row;
  logic [4:0]     out_shift;
  logic [L2T-1:0] rows, prow, lanes32;
  logic [RCW-1:0] cols;
  logic [31:0]    c_ld, paddr;
  assign src_act   = act_q || start_i;
  assign bank      = start_i ? start_bank_i       : bank_q;
  assign out_int8  = start_i ? out_int8_i         : int8_q;
  assign out_shift = start_i ? out_shift_i        : shift_q;
  assign rows      = start_i ? rows_i[L2T-1:0]    : rows_q;
  assign cols      = start_i ? cols_i[RCW-1:0]    : cols_q;
  assign c_ld      = start_i ? c_ld_i             : c_ld_q;
  assign prow      = start_i ? '0                 : prow_q;
  assign phalf     = start_i ? 1'b0               : phalf_q;
  assign paddr     = start_i ? c_addr_i           : paddr_q;
  assign pdone     = start_i ? 1'b0               : pdone_q;

  assign stable    = !(prod_valid_i && prod_bank_i == bank);
  logic epi;
  assign produce   = src_act && !epi && !pdone && stable && (!stg_v_q || wr_fire_i);
  assign cols_gt_h = cols > RCW'(H);
  assign split_row = !out_int8 && cols_gt_h;
  assign lanes32   = phalf ? cols[L2T-1:0] - L2T'(H) : (cols_gt_h ? L2T'(H) : cols[L2T-1:0]);
  assign last      = (prow == rows - L2T'(1)) && (phalf || !split_row);

  assign rd_bank_o = bank;
  // rd_row_o: the plain path's row, or the row whose group enters the epilogue lanes (below)

  logic [H*32-1:0] half_val;
  logic [T*8-1:0]  row_int8;
  logic [T-1:0]    be_int8;
  logic [W*8-1:0]  data32, data8, data;
  logic [W-1:0]    be32, be8, be;

  // Output-mode isolation: only the selected output path sees the bank values, so the other
  // path's shifters, clamps and lane muxes hold still. out_int8 is constant for a run, so the
  // gating adds no transitions, and `data` below selects the same path as before.
  logic [T*32-1:0] val8;
  assign val8     = rd_val_i & {(T*32){out_int8}};
  assign half_val = (phalf ? rd_val_i[H*32 +: H*32] : rd_val_i[0 +: H*32]) & {(H*32){!out_int8}};

  for (genvar l = 0; l < H; l++) begin : g_w32
    logic en;
    assign en                 = L2T'(l) < lanes32;
    assign be32[l*4 +: 4]     = {4{en}};
    assign data32[l*32 +: 32] = en ? half_val[l*32 +: 32] : 32'd0;
  end

  for (genvar l = 0; l < T; l++) begin : g_w8
    logic signed [31:0] sh;
    logic [7:0]         q8;
    logic               en;
    assign sh                 = $signed(val8[l*32 +: 32]) >>> out_shift;
    assign q8                 = (sh[31:7] == {25{sh[31]}}) ? sh[7:0] : (sh[31] ? 8'h80 : 8'h7f);
    assign en                 = RCW'(l) < cols;
    assign be_int8[l]         = en;
    assign row_int8[l*8 +: 8] = en ? q8 : 8'd0;
  end

  assign data8 = paddr[L2T] ? {row_int8, {(T*8){1'b0}}} : {{(T*8){1'b0}}, row_int8};
  assign be8   = paddr[L2T] ? {be_int8,  {T{1'b0}}}     : {{T{1'b0}},     be_int8};
  assign data  = out_int8 ? data8 : data32;
  assign be    = out_int8 ? be8   : be32;

  // ---------------------------------------------------------------- epilogue (MODE[9])
  localparam int L   = EPI_LANES;
  localparam int G   = T / L;                        // groups per row
  localparam int GW  = (G > 1) ? $clog2(G) : 1;
  logic          epi_q, edone_q, a_v_q, a_rowlast_q, a_final_q, erow_v_q, elast_q;
  logic [GW-1:0] eg_q, a_g_q;
  logic [L2T-1:0] erow_q;
  logic [31:0]   eaddr_q, a_addr_q, erow_addr_q;
  logic [T*8-1:0] ebuf_q;
  logic          frz, e_enter, eg_last, erow_last, e_fire_last;
  logic [GW-1:0] eg;
  logic [L2T-1:0] erow;
  logic [31:0]   eaddr;
  assign epi       = start_i ? epi_i : epi_q;
  assign eg        = start_i ? '0 : eg_q;
  assign erow      = start_i ? '0 : erow_q;
  assign eaddr     = start_i ? c_addr_i : eaddr_q;
  assign frz       = erow_v_q && !wr_fire_i;
  assign e_enter   = src_act && epi && !(start_i ? 1'b0 : edone_q) && stable && par_valid_i && !frz;
  assign eg_last   = (32'(eg) == G - 1);
  assign erow_last = (erow == rows - L2T'(1));
  assign rd_row_o  = epi ? erow : prow;

  // per-tensor values
  logic signed [7:0] zp, act_lo, act_hi;
  assign zp     = epi_out_i[7:0];
  assign act_lo = epi_out_i[15:8];
  assign act_hi = epi_out_i[23:16];

  // stage A: + bias, << left, x M0 (one group of L lanes, columns eg*L + l)
  logic [L*64-1:0] a_prod_q;
  logic [L*5-1:0]  a_right_q;
  logic [L-1:0]    a_sat_q;
  logic [L*64-1:0] a_prod_n;
  logic [L*5-1:0]  a_right_n;
  logic [L-1:0]    a_sat_n;
  for (genvar l = 0; l < L; l++) begin : g_epa
    logic [31:0]        acc, bias, m0, s, xs;
    logic signed [7:0]  sh8;
    logic signed [7:0]  shc;
    logic [4:0]         left, right;
    assign acc   = rd_val_i[(32'(eg) * L + l) * 32 +: 32];
    assign bias  = par_bias_i[(32'(eg) * L + l) * 32 +: 32];
    assign m0    = par_m0_i[(32'(eg) * L + l) * 32 +: 32];
    assign sh8   = par_shift_i[(32'(eg) * L + l) * 8 +: 8];
    assign shc   = (sh8 < -8'sd31) ? -8'sd31 : (sh8 > 8'sd30) ? 8'sd30 : sh8;
    assign left  = shc[7] ? 5'd0 : shc[4:0];
    assign right = shc[7] ? 5'(-shc) : 5'd0;
    assign s     = acc + bias;                                   // int32 wrap, as TFLite
    assign xs    = s << left;
    assign a_prod_n[l*64 +: 64] = 64'($signed(xs) * $signed(m0));
    assign a_right_n[l*5 +: 5]  = right;
    assign a_sat_n[l]           = (xs == 32'h8000_0000) && (m0 == 32'h8000_0000);
  end

  // stage B: SaturatingRoundingDoublingHighMul, RoundingDivideByPOT, + zp, clamp (model: requant)
  logic [L*8-1:0] b_q8;
  for (genvar l = 0; l < L; l++) begin : g_epb
    logic signed [63:0] ab, q;
    logic signed [31:0] hi, hs, res;
    logic [31:0]        mask, rem, thr;
    logic signed [32:0] o;
    logic signed [32:0] lo_c;
    logic [4:0]         right;
    assign ab    = $signed(a_prod_q[l*64 +: 64]);
    assign q     = ab + (ab[63] ? (64'sd1 - 64'sd1073741824) : 64'sd1073741824);
    assign hi    = a_sat_q[l] ? 32'sh7fff_ffff
                 : (q[63] ? -(32'(64'(-q) >> 31)) : 32'(q >> 31));   // truncate toward zero
    assign right = a_right_q[l*5 +: 5];
    assign mask  = (32'd1 << right) - 32'd1;
    assign rem   = hi & mask;
    assign thr   = (mask >> 1) + 32'(hi[31]);
    assign hs    = hi >>> right;                                    // arithmetic (hi is signed)
    assign res   = hs + $signed({31'd0, rem > thr});
    assign o     = 33'(res) + 33'(zp);
    assign lo_c  = (o < 33'(act_lo)) ? 33'(act_lo) : o;
    assign b_q8[l*8 +: 8] = (lo_c > 33'(act_hi)) ? act_hi : lo_c[7:0];
  end

  logic gclk_ea, gclk_eb;
  tq_cg #(.EN(CLK_GATE)) u_cg_ea (.clk_i, .en_i(e_enter), .gclk_o(gclk_ea));
  always_ff @(posedge gclk_ea) begin
    if (e_enter || CLK_GATE) begin
      a_prod_q  <= a_prod_n;
      a_right_q <= a_right_n;
      a_sat_q   <= a_sat_n;
      a_g_q     <= eg;
      a_addr_q  <= eaddr;
    end
  end
  tq_cg #(.EN(CLK_GATE)) u_cg_eb (.clk_i, .en_i(a_v_q && !frz), .gclk_o(gclk_eb));
  always_ff @(posedge gclk_eb) begin
    if ((a_v_q && !frz) || CLK_GATE) begin
      for (int g = 0; g < G; g++)
        if (32'(a_g_q) == g) ebuf_q[g * L * 8 +: L * 8] <= b_q8;
      if (a_rowlast_q) erow_addr_q <= a_addr_q;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      epi_q <= 1'b0; edone_q <= 1'b0; a_v_q <= 1'b0; a_rowlast_q <= 1'b0;
      a_final_q <= 1'b0; erow_v_q <= 1'b0; elast_q <= 1'b0; eg_q <= '0; erow_q <= '0; eaddr_q <= '0;
    end else if (clear_i) begin
      edone_q <= 1'b0; a_v_q <= 1'b0; erow_v_q <= 1'b0; elast_q <= 1'b0;
    end else begin
      if (start_i) begin
        epi_q   <= epi_i;
        edone_q <= 1'b0;
        eg_q    <= '0;
        erow_q  <= '0;
        eaddr_q <= c_addr_i;
      end
      if (e_enter) begin
        if (!eg_last) eg_q <= eg + GW'(1);
        else begin
          eg_q <= '0;
          if (erow_last) edone_q <= 1'b1;
          else begin
            erow_q  <= erow + L2T'(1);
            eaddr_q <= eaddr + c_ld;
          end
        end
      end
      if (!frz) begin
        a_v_q       <= e_enter;
        a_rowlast_q <= e_enter && eg_last;
        a_final_q   <= e_enter && eg_last && erow_last;
      end
      // a finished row is staged when its last group lands; written when granted
      if (a_v_q && !frz && a_rowlast_q) begin
        erow_v_q <= 1'b1;
        elast_q  <= a_final_q;
      end else if (wr_fire_i) begin
        erow_v_q <= 1'b0;
      end
    end
  end
  assign e_fire_last = wr_fire_i && erow_v_q && elast_q;

  // the epilogue's write word: one T-byte row, in the half of the word its address selects
  logic [W*8-1:0] edata;
  logic [W-1:0]   ebe;
  logic [T-1:0]   ebe_row;
  for (genvar l = 0; l < T; l++) begin : g_ebe
    assign ebe_row[l] = RCW'(l) < cols_q;
  end
  assign edata = erow_addr_q[L2T] ? {ebuf_q, {(T*8){1'b0}}} : {{(T*8){1'b0}}, ebuf_q};
  assign ebe   = erow_addr_q[L2T] ? {ebe_row, {T{1'b0}}}    : {{T{1'b0}},    ebe_row};

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      act_q <= 1'b0; bank_q <= 1'b0; pdone_q <= 1'b0; phalf_q <= 1'b0; int8_q <= 1'b0;
      rows_q <= '0; cols_q <= '0; prow_q <= '0;
      stg_v_q <= 1'b0; stg_last_q <= 1'b0;
    end else if (clear_i) begin
      act_q <= 1'b0; pdone_q <= 1'b0; stg_v_q <= 1'b0; stg_last_q <= 1'b0;
    end else begin
      if (start_i) begin
        act_q  <= 1'b1;
        bank_q <= start_bank_i;
        int8_q <= out_int8_i;
        rows_q <= rows_i[L2T-1:0];
        cols_q <= cols_i[RCW-1:0];
      end
      if (start_i || produce) begin
        prow_q  <= prow;
        phalf_q <= phalf;
        pdone_q <= pdone;
      end
      if (produce) begin
        stg_v_q    <= 1'b1;
        stg_last_q <= last;
        if (last) pdone_q <= 1'b1;
        else if (!phalf && split_row) phalf_q <= 1'b1;
        else begin
          phalf_q <= 1'b0;
          prow_q  <= prow + L2T'(1);
        end
      end else if (wr_fire_i) begin
        stg_v_q <= 1'b0;
      end
      if ((wr_fire_i && stg_last_q && stg_v_q) || e_fire_last) act_q <= 1'b0;
    end
  end

  always_ff @(posedge clk_i) begin
    if (start_i) begin
      c_ld_q  <= c_ld_i;
      shift_q <= out_shift_i;
    end
    if (start_i || produce) paddr_q <= paddr;
    if (produce && !last && (phalf || !split_row)) paddr_q <= paddr + c_ld;
  end

  // staged word: written when a word is produced, read only while stg_v_q
  logic gclk_stg;
  tq_cg #(.EN(CLK_GATE)) u_cg_stg (.clk_i, .en_i(produce), .gclk_o(gclk_stg));
  always_ff @(posedge gclk_stg) begin
    if (produce || CLK_GATE) begin
      stg_addr_q <= (paddr & ~(out_int8 ? 32'(T) : 32'd0)) + (phalf ? 32'(W) : 32'd0);
      stg_be_q   <= be;
      stg_data_q <= data;
    end
  end

  assign wr_valid_o       = stg_v_q || erow_v_q;
  assign wr_addr_o        = erow_v_q ? (erow_addr_q & ~32'(T)) : stg_v_q ? stg_addr_q : 32'd0;
  assign wr_be_o          = erow_v_q ? ebe : stg_v_q ? stg_be_q : '0;
  // The data holds the last staged word between writes (it is valid only with wr_req, spec 6.3),
  // so the W-byte bus and the SRAM's data inputs do not swing to zero and back between writes.
  assign wr_data_o        = epi_q ? edata : stg_data_q;
  assign clear_bank_o     = (wr_fire_i && stg_v_q && stg_last_q) || e_fire_last;
  assign clear_bank_sel_o = bank_q;
  assign active_o         = act_q;
  assign active_next_o    = src_act && !((wr_fire_i && stg_v_q && stg_last_q) || e_fire_last);
  assign align_err_o      = start_i && (c_base_err_i || (c_ld_i[L2T:0] != '0));

endmodule
