// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// AXI4 master for the core's two memory ports (spec 6.3). Each OBI request is one single-beat
// INCR transaction of one W-byte port word (AxLEN 0, AxSIZE log2(W), one ID), so the data
// width is the port width. The core never back-pressures responses (it reads only with room for
// the data), so RREADY and BREADY are always 1.
//
//   read:  rd_req -> AR, rd_gnt = AR handshake, R -> rd_rvalid / rd_rdata.
//   write: wr_req -> AW and W (accepted in either order), wr_gnt once both are accepted,
//          B -> wr_done (the core must be built with WR_ACK = 1, so FENCE waits for every B).
//
// AXI does not let a master withdraw a VALID, but the core drops an ungranted request at START
// or ABORT (OBI allows it). The adapter then finishes that transaction on its own ("orphan"):
// the address and data are held in registers, the core's next request waits (gnt 0) until the
// orphan is accepted, and the orphan's response is discarded. A tag queue per direction, one
// bit per accepted transaction, in order, marks which responses belong to the core.
// RRESP / BRESP errors are not reported to the core (a later revision may add an error bit).

module tq_axi_mem #(
    parameter int W       = 16,                // port width in bytes (2T)
    parameter int MAX_OUT = 16,                // transactions in flight per direction
    localparam int VB  = W * 8,
    localparam int TW  = $clog2(MAX_OUT + 1)
) (
    input  logic          clk_i,
    input  logic          rst_ni,
    // core read port (OBI-style)
    input  logic          rd_req_i,
    input  logic [31:0]   rd_addr_i,
    output logic          rd_gnt_o,
    output logic          rd_rvalid_o,
    output logic [VB-1:0] rd_rdata_o,
    // core write port (OBI-style, completion on wr_done_o)
    input  logic          wr_req_i,
    input  logic [31:0]   wr_addr_i,
    input  logic [W-1:0]  wr_be_i,
    input  logic [VB-1:0] wr_wdata_i,
    output logic          wr_gnt_o,
    output logic          wr_done_o,
    // AXI4 master
    output logic [31:0]   m_araddr_o,
    output logic [7:0]    m_arlen_o,
    output logic [2:0]    m_arsize_o,
    output logic [1:0]    m_arburst_o,
    output logic          m_arvalid_o,
    input  logic          m_arready_i,
    input  logic [VB-1:0] m_rdata_i,
    input  logic [1:0]    m_rresp_i,
    input  logic          m_rlast_i,
    input  logic          m_rvalid_i,
    output logic          m_rready_o,
    output logic [31:0]   m_awaddr_o,
    output logic [7:0]    m_awlen_o,
    output logic [2:0]    m_awsize_o,
    output logic [1:0]    m_awburst_o,
    output logic          m_awvalid_o,
    input  logic          m_awready_i,
    output logic [VB-1:0] m_wdata_o,
    output logic [W-1:0]  m_wstrb_o,
    output logic          m_wlast_o,
    output logic          m_wvalid_o,
    input  logic          m_wready_i,
    input  logic [1:0]    m_bresp_i,
    input  logic          m_bvalid_i,
    output logic          m_bready_o
);
  localparam logic [2:0] SIZE = 3'($clog2(W));

  assign m_arlen_o = 8'd0;  assign m_arsize_o = SIZE;  assign m_arburst_o = 2'b01;
  assign m_awlen_o = 8'd0;  assign m_awsize_o = SIZE;  assign m_awburst_o = 2'b01;
  assign m_wlast_o = 1'b1;
  assign m_rready_o = 1'b1;
  assign m_bready_o = 1'b1;

  // ---------------------------------------------------------------- read address
  // ar_q: a request is on AR (held: VALID stays until READY); ar_orph_q: the core dropped it.
  logic        ar_q, ar_orph_q;
  logic [31:0] ar_addr_q;
  logic [TW-1:0] rt_cnt;
  logic        rt_full, ar_fire, rd_take;
  assign rt_full     = 32'(rt_cnt) >= MAX_OUT;
  // the core's request goes straight to AR unless an earlier one is still held
  assign m_arvalid_o = ar_q || (rd_req_i && !rt_full);
  assign m_araddr_o  = ar_q ? ar_addr_q : rd_addr_i;
  assign ar_fire     = m_arvalid_o && m_arready_i;
  assign rd_take     = rd_req_i && !rt_full && (!ar_q || !ar_orph_q);   // core request on AR now
  assign rd_gnt_o    = rd_take && m_arready_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ar_q <= 1'b0; ar_orph_q <= 1'b0; ar_addr_q <= '0;
    end else if (ar_fire) begin
      ar_q <= 1'b0; ar_orph_q <= 1'b0;
    end else if (m_arvalid_o) begin
      ar_q      <= 1'b1;                                   // not accepted: hold it
      ar_addr_q <= m_araddr_o;
      ar_orph_q <= ar_orph_q || !rd_req_i;                 // the core let go of it
    end
  end

  // read tags: 1 = the response belongs to the core
  logic rt_head;
  logic unused_rt1;
  tq_fifo #(.WIDTH(1), .DEPTH(MAX_OUT)) u_rtag (
      .clk_i, .rst_ni, .clear_i(1'b0),
      .push_i(ar_fire), .data_i(!(ar_q && (ar_orph_q || !rd_req_i))),
      .pop_i({1'b0, m_rvalid_i}), .head0_o(rt_head), .head1_o(unused_rt1), .count_o(rt_cnt));
  assign rd_rvalid_o = m_rvalid_i && rt_head;
  assign rd_rdata_o  = m_rdata_i;

  // ---------------------------------------------------------------- write address + data
  // AW and W are separate handshakes; wr_gnt when both are done for the core's request.
  logic        aw_ok_q, w_ok_q, wv_q, w_orph_q;            // accepted parts / held / orphan
  logic [31:0] w_addr_q;
  logic [W-1:0] w_be_q;
  logic [VB-1:0] w_data_q;
  logic [TW-1:0] wt_cnt;
  logic        wt_full, aw_fire, w_fire, wr_take, wr_all, cur_v;
  logic [31:0] cur_addr;
  logic [W-1:0] cur_be;
  logic [VB-1:0] cur_data;
  assign wt_full  = 32'(wt_cnt) >= MAX_OUT;
  // the transaction on the bus: a held one, else the core's request
  assign cur_v    = wv_q || (wr_req_i && !wt_full);
  assign cur_addr = wv_q ? w_addr_q : wr_addr_i;
  assign cur_be   = wv_q ? w_be_q   : wr_be_i;
  assign cur_data = wv_q ? w_data_q : wr_wdata_i;
  assign m_awvalid_o = cur_v && !aw_ok_q;
  assign m_wvalid_o  = cur_v && !w_ok_q;
  assign m_awaddr_o  = cur_addr;
  assign m_wdata_o   = cur_data;
  assign m_wstrb_o   = cur_be;
  assign aw_fire  = m_awvalid_o && m_awready_i;
  assign w_fire   = m_wvalid_o && m_wready_i;
  assign wr_all   = cur_v && (aw_ok_q || m_awready_i) && (w_ok_q || m_wready_i);   // both parts done
  assign wr_take  = wr_req_i && !wt_full && (!wv_q || !w_orph_q);
  assign wr_gnt_o = wr_take && wr_all;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      aw_ok_q <= 1'b0; w_ok_q <= 1'b0; wv_q <= 1'b0; w_orph_q <= 1'b0;
      w_addr_q <= '0; w_be_q <= '0; w_data_q <= '0;
    end else if (wr_all) begin
      aw_ok_q <= 1'b0; w_ok_q <= 1'b0; wv_q <= 1'b0; w_orph_q <= 1'b0;
    end else if (cur_v) begin
      aw_ok_q  <= aw_ok_q || aw_fire;
      w_ok_q   <= w_ok_q || w_fire;
      wv_q     <= 1'b1;
      w_addr_q <= cur_addr; w_be_q <= cur_be; w_data_q <= cur_data;
      w_orph_q <= w_orph_q || !wr_req_i;
    end
  end

  // write tags: one per completed AW + W pair, 1 = the B belongs to the core
  logic wt_head;
  logic unused_wt1;
  tq_fifo #(.WIDTH(1), .DEPTH(MAX_OUT)) u_wtag (
      .clk_i, .rst_ni, .clear_i(1'b0),
      .push_i(wr_all), .data_i(!(wv_q && (w_orph_q || !wr_req_i))),
      .pop_i({1'b0, m_bvalid_i}), .head0_o(wt_head), .head1_o(unused_wt1), .count_o(wt_cnt));
  assign wr_done_o = m_bvalid_i && wt_head;

  /* verilator lint_off UNUSEDSIGNAL */
  logic unused;
  assign unused = ^{m_rresp_i, m_rlast_i, m_bresp_i};
  /* verilator lint_on UNUSEDSIGNAL */

endmodule
