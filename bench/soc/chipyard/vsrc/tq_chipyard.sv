// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tq_top_axi for a Chipyard SoC (bench/soc/chipyard): active-high reset, and a full AXI4 slave
// port for the registers. The AXI4-Lite slave takes one transaction at a time, so the ID of the
// accepted request is registered and echoed on its response, and every R beat is the last.
// The memory master reads 4-beat bursts (one 64-byte line); LINES, WR_IDS, FIFO_DEPTH and MAX_OUT
// come from TriQueue.scala (WR_IDS > 0: an AXI ID per read line slot and per write).
// Simulation only: after each burst of memory traffic the shim prints a "tqbus:" line with the
// AXI transactions in flight on average and the mean AR-to-first-R latency.

module tq_chipyard #(
    parameter int T     = 8,
    parameter int ID_W  = 4,
    parameter int MID_W = 1,
    parameter int LINES      = 4,
    parameter int WR_IDS     = 0,
    parameter int FIFO_DEPTH = 8,
    parameter int MAX_OUT    = 16,
    localparam int W  = 2 * T,
    localparam int VB = W * 8
) (
    input  logic            clock,
    input  logic            reset,
    output logic            irq,
    // AXI4 slave (registers), 32-bit
    input  logic [ID_W-1:0] s_awid,
    input  logic [31:0]     s_awaddr,
    input  logic [2:0]      s_awprot,
    input  logic            s_awvalid,
    output logic            s_awready,
    input  logic [31:0]     s_wdata,
    input  logic [3:0]      s_wstrb,
    input  logic            s_wvalid,
    output logic            s_wready,
    output logic [ID_W-1:0] s_bid,
    output logic [1:0]      s_bresp,
    output logic            s_bvalid,
    input  logic            s_bready,
    input  logic [ID_W-1:0] s_arid,
    input  logic [31:0]     s_araddr,
    input  logic [2:0]      s_arprot,
    input  logic            s_arvalid,
    output logic            s_arready,
    output logic [ID_W-1:0] s_rid,
    output logic [31:0]     s_rdata,
    output logic [1:0]      s_rresp,
    output logic            s_rlast,
    output logic            s_rvalid,
    input  logic            s_rready,
    // AXI4 master (memory), W bytes
    output logic [MID_W-1:0] m_arid,
    output logic [31:0]     m_araddr,
    output logic [7:0]      m_arlen,
    output logic [2:0]      m_arsize,
    output logic [1:0]      m_arburst,
    output logic [2:0]      m_arprot,
    output logic [3:0]      m_arcache,
    output logic [3:0]      m_arqos,
    output logic [3:0]      m_arregion,
    output logic            m_arvalid,
    input  logic            m_arready,
    input  logic [MID_W-1:0] m_rid,
    input  logic [VB-1:0]   m_rdata,
    input  logic [1:0]      m_rresp,
    input  logic            m_rlast,
    input  logic            m_rvalid,
    output logic            m_rready,
    output logic [MID_W-1:0] m_awid,
    output logic [31:0]     m_awaddr,
    output logic [7:0]      m_awlen,
    output logic [2:0]      m_awsize,
    output logic [1:0]      m_awburst,
    output logic [2:0]      m_awprot,
    output logic [3:0]      m_awcache,
    output logic [3:0]      m_awqos,
    output logic [3:0]      m_awregion,
    output logic            m_awvalid,
    input  logic            m_awready,
    output logic [VB-1:0]   m_wdata,
    output logic [W-1:0]    m_wstrb,
    output logic            m_wlast,
    output logic            m_wvalid,
    input  logic            m_wready,
    input  logic [MID_W-1:0] m_bid,
    input  logic [1:0]      m_bresp,
    input  logic            m_bvalid,
    output logic            m_bready
);
  logic [ID_W-1:0] bid_q, rid_q;
  always_ff @(posedge clock) begin
    if (s_awvalid && s_awready) bid_q <= s_awid;
    if (s_arvalid && s_arready) rid_q <= s_arid;
  end
  assign s_bid   = bid_q;
  assign s_rid   = rid_q;
  assign s_rlast = 1'b1;

  localparam int BEATS = 4;
  tq_top_axi #(.T(T), .BURST_BEATS(BEATS), .ADDR_W(12), .ID_W(MID_W), .LINES(LINES), .WR_IDS(WR_IDS),
               .FIFO_DEPTH(FIFO_DEPTH), .MAX_OUT(MAX_OUT)) u_tq (
      .clk_i(clock), .rst_ni(!reset),
      .s_awaddr_i(s_awaddr[11:0]), .s_awprot_i(s_awprot), .s_awvalid_i(s_awvalid), .s_awready_o(s_awready),
      .s_wdata_i(s_wdata), .s_wstrb_i(s_wstrb), .s_wvalid_i(s_wvalid), .s_wready_o(s_wready),
      .s_bresp_o(s_bresp), .s_bvalid_o(s_bvalid), .s_bready_i(s_bready),
      .s_araddr_i(s_araddr[11:0]), .s_arprot_i(s_arprot), .s_arvalid_i(s_arvalid), .s_arready_o(s_arready),
      .s_rdata_o(s_rdata), .s_rresp_o(s_rresp), .s_rvalid_o(s_rvalid), .s_rready_i(s_rready),
      .irq_o(irq),
      .m_arid_o(m_arid), .m_araddr_o(m_araddr), .m_arlen_o(m_arlen), .m_arsize_o(m_arsize),
      .m_arburst_o(m_arburst), .m_arprot_o(m_arprot), .m_arcache_o(m_arcache), .m_arqos_o(m_arqos),
      .m_arregion_o(m_arregion), .m_arvalid_o(m_arvalid), .m_arready_i(m_arready), .m_rid_i(m_rid),
      .m_rdata_i(m_rdata), .m_rresp_i(m_rresp), .m_rlast_i(m_rlast), .m_rvalid_i(m_rvalid), .m_rready_o(m_rready),
      .m_awid_o(m_awid), .m_awaddr_o(m_awaddr), .m_awlen_o(m_awlen), .m_awsize_o(m_awsize),
      .m_awburst_o(m_awburst), .m_awprot_o(m_awprot), .m_awcache_o(m_awcache), .m_awqos_o(m_awqos),
      .m_awregion_o(m_awregion), .m_awvalid_o(m_awvalid), .m_awready_i(m_awready),
      .m_wdata_o(m_wdata), .m_wstrb_o(m_wstrb), .m_wlast_o(m_wlast), .m_wvalid_o(m_wvalid), .m_wready_i(m_wready),
      .m_bid_i(m_bid), .m_bresp_i(m_bresp), .m_bvalid_i(m_bvalid), .m_bready_o(m_bready));

  // Bus statistics over each active stretch (until 3,000 idle cycles): reads and writes in flight
  // per cycle on average, cycles with both in flight, the most in flight, and the mean latency from
  // AR to the first R beat (the sum of per-burst latencies does not depend on the pairing, so
  // pairing ARs with last beats in order gives the right mean with out-of-order responses too).
  int unsigned st_cyc, st_ar, st_aw, st_b, st_rl, st_lat, st_idle, st_rd, st_wr, st_both, st_max;
  int unsigned rd_of, wr_of, ar_t [$];
  logic st_act;
  assign st_act = m_arvalid || m_rvalid || m_awvalid || m_wvalid || m_bvalid || rd_of != 0 || wr_of != 0;
  always_ff @(posedge clock) begin
    if (reset) begin
      st_cyc <= 0; st_ar <= 0; st_aw <= 0; st_b <= 0; st_rl <= 0; st_lat <= 0; st_idle <= 0;
      st_rd <= 0; st_wr <= 0; st_both <= 0; st_max <= 0; rd_of <= 0; wr_of <= 0;
    end else begin
      rd_of <= rd_of + (m_arvalid && m_arready) - (m_rvalid && m_rready && m_rlast);
      wr_of <= wr_of + (m_awvalid && m_awready) - (m_bvalid && m_bready);
      if (st_act) begin
        st_idle <= 0; st_cyc <= st_cyc + 1;
        st_rd <= st_rd + rd_of; st_wr <= st_wr + wr_of;
        if (rd_of != 0 && wr_of != 0) st_both <= st_both + 1;
        if (rd_of + wr_of > st_max) st_max <= rd_of + wr_of;
      end else if (st_cyc != 0) begin
        st_idle <= st_idle + 1;
        if (st_idle == 3000) begin
          $display("tqbus: cycles=%0d ar=%0d aw=%0d b=%0d mean_rd_inflight=%0d.%02d mean_wr_inflight=%0d.%02d both=%0d max=%0d ar_to_r=%0d.%01d",
                   st_cyc, st_ar, st_aw, st_b, st_rd / st_cyc, (100 * st_rd / st_cyc) % 100, st_wr / st_cyc,
                   (100 * st_wr / st_cyc) % 100, st_both, st_max, st_lat / (st_rl == 0 ? 1 : st_rl),
                   (10 * st_lat / (st_rl == 0 ? 1 : st_rl)) % 10);
          st_cyc <= 0; st_ar <= 0; st_aw <= 0; st_b <= 0; st_rl <= 0; st_lat <= 0; st_rd <= 0; st_wr <= 0;
          st_both <= 0; st_max <= 0;
        end
      end
      if (m_arvalid && m_arready) begin st_ar <= st_ar + 1; ar_t.push_back(st_cyc); end
      if (m_awvalid && m_awready) st_aw <= st_aw + 1;
      if (m_bvalid && m_bready) st_b <= st_b + 1;
      if (m_rvalid && m_rready && m_rlast) begin
        st_rl <= st_rl + 1; st_lat <= st_lat + (st_cyc - ar_t.pop_front()) - (BEATS - 1);
      end
    end
  end

  /* verilator lint_off UNUSEDSIGNAL */
  logic unused;
  assign unused = ^{s_awaddr[31:12], s_araddr[31:12]};
  /* verilator lint_on UNUSEDSIGNAL */
endmodule
