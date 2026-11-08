// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Formal environment for tq_axi_mem (verif/formal/tq_axi_mem.sby), W = 4 bytes.
// Core side: any request sequence that keeps OBI rules, except that a request waiting for gnt
// may be dropped (START / ABORT; tq_core then has no request in that cycle, which tq_core.sby
// proves). AXI side: any READY pattern, in-order R and B at any latency (R in the AR cycle's
// next cycle or later, B after its AW and W), and R data equal to the AR address, so data can
// be followed through the adapter.
// Properties: AXI VALID and payload held until READY; the core receives exactly the responses
// of its own granted requests, in order and with the right data (a dropped request's response
// never reaches it); wr_done only for the core's granted writes.

module tq_axi_mem_env #(
    parameter int MAX_OUT = 3
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        rd_req_i,
    input  logic [31:0] rd_addr_i,
    input  logic        wr_req_i,
    input  logic [31:0] wr_addr_i,
    input  logic [3:0]  wr_be_i,
    input  logic [31:0] wr_wdata_i,
    input  logic        m_arready_i,
    input  logic        m_rvalid_i,
    input  logic        m_awready_i,
    input  logic        m_wready_i,
    input  logic        m_bvalid_i
);
  logic        rd_gnt, rd_rvalid, wr_gnt, wr_done;
  logic [31:0] rd_rdata;
  logic [31:0] araddr, awaddr, wdata;
  logic [7:0]  arlen, awlen;
  logic [2:0]  arsize, awsize;
  logic [1:0]  arburst, awburst;
  logic        arvalid, rready, awvalid, wvalid, wlast, bready;
  logic [3:0]  wstrb;
  logic [31:0] rdata;

  tq_axi_mem #(.W(4), .MAX_OUT(MAX_OUT)) dut (
      .clk_i, .rst_ni,
      .rd_req_i, .rd_addr_i, .rd_gnt_o(rd_gnt), .rd_rvalid_o(rd_rvalid), .rd_rdata_o(rd_rdata),
      .wr_req_i, .wr_addr_i, .wr_be_i, .wr_wdata_i, .wr_gnt_o(wr_gnt), .wr_done_o(wr_done),
      .m_araddr_o(araddr), .m_arlen_o(arlen), .m_arsize_o(arsize), .m_arburst_o(arburst),
      .m_arvalid_o(arvalid), .m_arready_i,
      .m_rdata_i(rdata), .m_rresp_i(2'b00), .m_rlast_i(1'b1), .m_rvalid_i, .m_rready_o(rready),
      .m_awaddr_o(awaddr), .m_awlen_o(awlen), .m_awsize_o(awsize), .m_awburst_o(awburst),
      .m_awvalid_o(awvalid), .m_awready_i,
      .m_wdata_o(wdata), .m_wstrb_o(wstrb), .m_wlast_o(wlast), .m_wvalid_o(wvalid), .m_wready_i,
      .m_bresp_i(2'b00), .m_bvalid_i, .m_bready_o(bready));

  logic past_valid = 1'b0;
  always_ff @(posedge clk_i) past_valid <= 1'b1;
  always_comb if (!past_valid) assume (!rst_ni);
  logic run;
  assign run = past_valid && rst_ni;

  // ---------------------------------------------------------------- core side (assumptions)
  logic [31:0] p_rd_addr, p_wr_addr, p_wr_wdata;
  logic [3:0]  p_wr_be;
  logic        p_rd_wait, p_wr_wait;
  always_ff @(posedge clk_i) begin
    p_rd_wait <= rst_ni && rd_req_i && !rd_gnt;   p_rd_addr <= rd_addr_i;
    p_wr_wait <= rst_ni && wr_req_i && !wr_gnt;   p_wr_addr <= wr_addr_i;
    p_wr_wdata <= wr_wdata_i;                     p_wr_be <= wr_be_i;
  end
  always_comb begin
    if (run && p_rd_wait) assume (!rd_req_i || rd_addr_i == p_rd_addr);           // held or dropped
    if (run && p_wr_wait) assume (!wr_req_i || (wr_addr_i == p_wr_addr && wr_wdata_i == p_wr_wdata
                                                && wr_be_i == p_wr_be));
  end

  // ---------------------------------------------------------------- AXI slave (assumptions)
  // R: a FIFO of accepted AR addresses; R carries the oldest one, never in the AR cycle.
  logic [MAX_OUT:0][31:0] ar_q;
  logic [2:0]  ar_n;
  logic [2:0]  b_n;
  logic        ar_fire, aw_fire, w_fire;
  logic        aw_open_q, w_open_q;                 // AW / W of the current write accepted
  assign ar_fire = arvalid && m_arready_i;
  assign aw_fire = awvalid && m_awready_i;
  assign w_fire  = wvalid && m_wready_i;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ar_n <= '0; b_n <= '0; aw_open_q <= 1'b0; w_open_q <= 1'b0;
    end else begin
      for (int i = 0; i < MAX_OUT; i++)
        ar_q[i] <= (m_rvalid_i ? ar_q[i + 1] : ar_q[i]);
      if (ar_fire) ar_q[32'(ar_n) - 32'(m_rvalid_i)] <= araddr;
      ar_n <= ar_n + 3'(ar_fire) - 3'(m_rvalid_i);
      // a write is complete in the slave when both AW and W are in
      if ((aw_fire || aw_open_q) && (w_fire || w_open_q)) begin
        aw_open_q <= 1'b0; w_open_q <= 1'b0;
        b_n <= b_n + 3'd1 - 3'(m_bvalid_i);
      end else begin
        aw_open_q <= aw_open_q || aw_fire;
        w_open_q  <= w_open_q || w_fire;
        b_n <= b_n - 3'(m_bvalid_i);
      end
    end
  end
  assign rdata = ar_q[0];
  always_comb begin
    if (rst_ni) begin
      if (m_rvalid_i) assume (ar_n != 0);
      if (m_bvalid_i) assume (b_n != 0);
    end
  end

  // ---------------------------------------------------------------- properties
  // AXI master rules
  logic [31:0] p_araddr, p_awaddr, p_wdata;
  logic [3:0]  p_wstrb;
  logic        p_ar_wait, p_aw_wait, p_w_wait;
  always_ff @(posedge clk_i) begin
    p_ar_wait <= rst_ni && arvalid && !m_arready_i;   p_araddr <= araddr;
    p_aw_wait <= rst_ni && awvalid && !m_awready_i;   p_awaddr <= awaddr;
    p_w_wait  <= rst_ni && wvalid && !m_wready_i;     p_wdata <= wdata;   p_wstrb <= wstrb;
  end
  always_comb begin
    if (run) begin
      if (p_ar_wait) assert (arvalid && araddr == p_araddr);
      if (p_aw_wait) assert (awvalid && awaddr == p_awaddr);
      if (p_w_wait)  assert (wvalid && wdata == p_wdata && wstrb == p_wstrb);
      assert (rready && bready && wlast);
      assert (arlen == 0 && awlen == 0 && arsize == 3'd2 && awsize == 3'd2 && arburst == 2'b01 && awburst == 2'b01);
      assert (!rd_gnt || rd_req_i);
      assert (!wr_gnt || wr_req_i);
    end
  end

  // The core's reads: a FIFO of granted read addresses; every response the core gets must be
  // the oldest one (R data is the AR address), and none may come with none outstanding.
  logic [MAX_OUT:0][31:0] cr_q;
  logic [2:0]  cr_n, cw_n;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      cr_n <= '0; cw_n <= '0;
    end else begin
      for (int i = 0; i < MAX_OUT; i++)
        cr_q[i] <= (rd_rvalid ? cr_q[i + 1] : cr_q[i]);
      if (rd_gnt) cr_q[32'(cr_n) - 32'(rd_rvalid)] <= rd_addr_i;
      cr_n <= cr_n + 3'(rd_gnt) - 3'(rd_rvalid);
      cw_n <= cw_n + 3'(wr_gnt) - 3'(wr_done);
    end
  end
  always_comb begin
    if (run) begin
      if (rd_rvalid) assert (cr_n != 0 && rd_rdata == cr_q[0]);
      if (wr_done) assert (cw_n != 0);
      assert (32'(cw_n) <= MAX_OUT);
    end
  end

  // reachability: a dropped (orphan) read and write complete, and the core still gets later data
  logic orph_r_q, orph_w_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      orph_r_q <= 1'b0; orph_w_q <= 1'b0;
    end else begin
      if (p_rd_wait && !rd_req_i) orph_r_q <= 1'b1;
      if (p_wr_wait && !wr_req_i) orph_w_q <= 1'b1;
    end
  end
  always_ff @(posedge clk_i) begin
    if (run) begin
      cover (orph_r_q && rd_rvalid);
      cover (orph_w_q && wr_done);
    end
  end

endmodule
