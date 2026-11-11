// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Drain unit (spec 5.1, 10.1): after SWAP_C, writes the finished bank to C as aligned port words,
// T/2 int32 lanes per word (or one T-byte int8 tile row per write when out_int8_i is set) with
// byte enables, then clears the bank.
// Model: TriQueueCore.start_drain / _issue_write (model/tri_queue/core.py).
//
// One carry-propagate / requantization stage feeds a one-word staging register; the write port
// takes the staged word. The first word is produced once the bank has no accumulate pending, so
// the first write lands at max(swap, last beat + 2) + 1 (model: ready = max(cycle, last_write) + cpa_latency).

module tq_drain #(
    parameter int T = 8,
    parameter bit CLK_GATE = 1'b1,      // clock gate on the staging register (tq_cg)
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
  assign produce   = src_act && !pdone && stable && (!stg_v_q || wr_fire_i);
  assign cols_gt_h = cols > RCW'(H);
  assign split_row = !out_int8 && cols_gt_h;
  assign lanes32   = phalf ? cols[L2T-1:0] - L2T'(H) : (cols_gt_h ? L2T'(H) : cols[L2T-1:0]);
  assign last      = (prow == rows - L2T'(1)) && (phalf || !split_row);

  assign rd_bank_o = bank;
  assign rd_row_o  = prow;

  logic [H*32-1:0] half_val;
  logic [T*8-1:0]  row_int8;
  logic [T-1:0]    be_int8;
  logic [W*8-1:0]  data32, data8, data;
  logic [W-1:0]    be32, be8, be;

  assign half_val = phalf ? rd_val_i[H*32 +: H*32] : rd_val_i[0 +: H*32];

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
    assign sh                 = $signed(rd_val_i[l*32 +: 32]) >>> out_shift;
    assign q8                 = (sh[31:7] == {25{sh[31]}}) ? sh[7:0] : (sh[31] ? 8'h80 : 8'h7f);
    assign en                 = RCW'(l) < cols;
    assign be_int8[l]         = en;
    assign row_int8[l*8 +: 8] = en ? q8 : 8'd0;
  end

  assign data8 = paddr[L2T] ? {row_int8, {(T*8){1'b0}}} : {{(T*8){1'b0}}, row_int8};
  assign be8   = paddr[L2T] ? {be_int8,  {T{1'b0}}}     : {{T{1'b0}},     be_int8};
  assign data  = out_int8 ? data8 : data32;
  assign be    = out_int8 ? be8   : be32;

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
      if (wr_fire_i && stg_last_q) act_q <= 1'b0;
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

  assign wr_valid_o       = stg_v_q;
  assign wr_addr_o        = stg_v_q ? stg_addr_q : 32'd0;
  assign wr_be_o          = stg_v_q ? stg_be_q   : '0;
  assign wr_data_o        = stg_v_q ? stg_data_q : '0;
  assign clear_bank_o     = wr_fire_i && stg_last_q;
  assign clear_bank_sel_o = bank_q;
  assign active_o         = act_q;
  assign active_next_o    = src_act && !(wr_fire_i && stg_last_q);
  assign align_err_o      = start_i && (c_base_err_i || (c_ld_i[L2T:0] != '0));

endmodule
