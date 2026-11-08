// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Drain unit (spec 5.1): after SWAP_C, writes the finished bank to C as aligned port words,
// T/2 int32 lanes per word with byte enables, then clears the bank.
// Model: TriQueueCore.start_drain / _issue_write (model/tri_queue/core.py).
//
// One carry-propagate stage feeds a one-word staging register; the write port takes the staged
// word. The first word is produced once the bank has no accumulate pending, so the first write
// lands at max(swap, last beat + 2) + 1 (model: ready = max(cycle, last_write) + cpa_latency).

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
    // accumulate pipeline status
    input  logic            prod_valid_i,
    input  logic            prod_bank_i,
    // bank read port (tq_mac_array)
    output logic            rd_bank_o,
    output logic [L2T-1:0]  rd_row_o,
    output logic            rd_half_o,
    input  logic [H*32-1:0] rd_val_i,
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
  // drain state
  logic        act_q, bank_q, pdone_q, phalf_q;
  logic [7:0]  rows_q, cols_q, prow_q;
  logic [31:0] c_ld_q, paddr_q;
  // staging register
  logic           stg_v_q, stg_last_q;
  logic [31:0]    stg_addr_q;
  logic [W-1:0]   stg_be_q;
  logic [W*8-1:0] stg_data_q;

  // current drain parameters (a drain starting this cycle may produce its first word at once)
  logic        bank, pdone, phalf, src_act, stable, produce, last;
  logic [7:0]  rows, cols, prow, lanes;
  logic [31:0] c_ld, paddr;
  assign src_act = act_q || start_i;
  assign bank    = start_i ? start_bank_i : bank_q;
  assign rows    = start_i ? rows_i       : rows_q;
  assign cols    = start_i ? cols_i       : cols_q;
  assign c_ld    = start_i ? c_ld_i       : c_ld_q;
  assign prow    = start_i ? 8'd0         : prow_q;
  assign phalf   = start_i ? 1'b0         : phalf_q;
  assign paddr   = start_i ? c_addr_i     : paddr_q;
  assign pdone   = start_i ? 1'b0         : pdone_q;

  assign stable  = !(prod_valid_i && prod_bank_i == bank);
  assign produce = src_act && !pdone && stable && (!stg_v_q || wr_fire_i);
  assign lanes   = phalf ? (cols - 8'(H)) : ((cols > 8'(H)) ? 8'(H) : cols);
  assign last    = (prow == rows - 8'd1) && (phalf || cols <= 8'(H));

  assign rd_bank_o = bank;
  assign rd_row_o  = prow[L2T-1:0];
  assign rd_half_o = phalf;

  logic [W-1:0]   be;
  logic [W*8-1:0] data;
  always_comb begin
    for (int l = 0; l < H; l++) begin
      be[l*4 +: 4]    = (8'(l) < lanes) ? 4'hf : 4'h0;
      data[l*32 +: 32] = (8'(l) < lanes) ? rd_val_i[l*32 +: 32] : 32'd0;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      act_q <= 1'b0; bank_q <= 1'b0; pdone_q <= 1'b0; phalf_q <= 1'b0;
      rows_q <= '0; cols_q <= '0; prow_q <= '0; c_ld_q <= '0; paddr_q <= '0;
      stg_v_q <= 1'b0; stg_last_q <= 1'b0;
    end else if (clear_i) begin
      act_q <= 1'b0; pdone_q <= 1'b0; stg_v_q <= 1'b0; stg_last_q <= 1'b0;
    end else begin
      if (start_i) begin
        act_q  <= 1'b1;
        bank_q <= start_bank_i;
        rows_q <= rows_i;
        cols_q <= cols_i;
        c_ld_q <= c_ld_i;
      end
      if (start_i || produce) begin
        prow_q  <= prow;
        phalf_q <= phalf;
        paddr_q <= paddr;
        pdone_q <= pdone;
      end
      if (produce) begin
        stg_v_q    <= 1'b1;
        stg_last_q <= last;
        if (last) pdone_q <= 1'b1;
        else if (!phalf && cols > 8'(H)) phalf_q <= 1'b1;
        else begin
          phalf_q <= 1'b0;
          prow_q  <= prow + 8'd1;
          paddr_q <= paddr + c_ld;
        end
      end else if (wr_fire_i) begin
        stg_v_q <= 1'b0;
      end
      if (wr_fire_i && stg_last_q) act_q <= 1'b0;
    end
  end

  // staged word: written when a word is produced, read only while stg_v_q
  logic gclk_stg;
  tq_cg #(.EN(CLK_GATE)) u_cg_stg (.clk_i, .en_i(produce), .gclk_o(gclk_stg));
  always_ff @(posedge gclk_stg) begin
    if (produce || CLK_GATE) begin
      stg_addr_q <= paddr + (phalf ? 32'(W) : 32'd0);
      stg_be_q   <= be;
      stg_data_q <= data;
    end
  end

  assign wr_valid_o       = stg_v_q;
  assign wr_addr_o        = stg_addr_q;
  assign wr_be_o          = stg_be_q;
  assign wr_data_o        = stg_data_q;
  assign clear_bank_o     = wr_fire_i && stg_last_q;
  assign clear_bank_sel_o = bank_q;
  assign active_o         = act_q;
  assign active_next_o    = src_act && !(wr_fire_i && stg_last_q);
  assign align_err_o      = start_i && ((c_addr_i[L2T:0] != '0) || (c_ld_i[L2T:0] != '0));

endmodule
