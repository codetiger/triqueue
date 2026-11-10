// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tq_top_axi for a Chipyard SoC (bench/soc/chipyard): active-high reset, and a full AXI4 slave
// port for the registers. The AXI4-Lite slave takes one transaction at a time, so the ID of the
// accepted request is registered and echoed on its response, and every R beat is the last.
// The memory master always uses ID 0 (responses in order).

module tq_chipyard #(
    parameter int T     = 8,
    parameter int ID_W  = 4,
    parameter int MID_W = 1,
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

  tq_top_axi #(.T(T), .BURST_BEATS(4), .ADDR_W(12), .ID_W(MID_W)) u_tq (
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

  /* verilator lint_off UNUSEDSIGNAL */
  logic unused;
  assign unused = ^{s_awaddr[31:12], s_araddr[31:12]};
  /* verilator lint_on UNUSEDSIGNAL */
endmodule
