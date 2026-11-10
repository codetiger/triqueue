// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tq_top for an AXI SoC: AXI4-Lite register slave (tq_axil_regs) and an AXI4 memory master
// (tq_axi_mem) with a 2T-byte data bus. The core runs with WR_ACK = 1, so DONE (FENCE) comes
// only after the last write's B response: once software sees DONE, C is in memory.

module tq_top_axi #(
    parameter int T           = 8,
    parameter int FIFO_DEPTH  = 8,
    parameter int MACQ_DEPTH  = 4,
    parameter int SCMD_DEPTH  = 2,
    parameter bit SHARED_PORT = 1'b0,
    parameter int PROG_BYTES  = 64,
    parameter int ACC_FORM    = tq_pkg::ACC_CPA,
    parameter bit CLK_GATE    = 1'b1,
    parameter int MAX_OUT     = 16,      // AXI transactions in flight per direction (also the core's write limit)
    parameter int PANEL_BYTES = 0,
    parameter int BURST_BEATS = 1,       // read burst beats (power of two, >= 1)
    parameter int ID_W        = 1,       // AXI ID width (constant 0)
    parameter logic [2:0] AXPROT  = 3'b000,
    parameter logic [3:0] AXCACHE = 4'b0000,
    parameter logic [3:0] AXQOS   = 4'b0000,
    parameter int ADDR_W      = 12,
    localparam int W  = 2 * T,
    localparam int VB = W * 8
) (
    input  logic              clk_i,
    input  logic              rst_ni,
    // AXI4-Lite slave (registers, spec 6.4)
    input  logic [ADDR_W-1:0] s_awaddr_i,
    input  logic [2:0]        s_awprot_i,
    input  logic              s_awvalid_i,
    output logic              s_awready_o,
    input  logic [31:0]       s_wdata_i,
    input  logic [3:0]        s_wstrb_i,
    input  logic              s_wvalid_i,
    output logic              s_wready_o,
    output logic [1:0]        s_bresp_o,
    output logic              s_bvalid_o,
    input  logic              s_bready_i,
    input  logic [ADDR_W-1:0] s_araddr_i,
    input  logic [2:0]        s_arprot_i,
    input  logic              s_arvalid_i,
    output logic              s_arready_o,
    output logic [31:0]       s_rdata_o,
    output logic [1:0]        s_rresp_o,
    output logic              s_rvalid_o,
    input  logic              s_rready_i,
    output logic              irq_o,
    // AXI4 master (memory)
    output logic [ID_W-1:0]   m_arid_o,
    output logic [31:0]       m_araddr_o,
    output logic [7:0]        m_arlen_o,
    output logic [2:0]        m_arsize_o,
    output logic [1:0]        m_arburst_o,
    output logic [2:0]        m_arprot_o,
    output logic [3:0]        m_arcache_o,
    output logic [3:0]        m_arqos_o,
    output logic [3:0]        m_arregion_o,
    output logic              m_arvalid_o,
    input  logic              m_arready_i,
    input  logic [ID_W-1:0]   m_rid_i,
    input  logic [VB-1:0]     m_rdata_i,
    input  logic [1:0]        m_rresp_i,
    input  logic              m_rlast_i,
    input  logic              m_rvalid_i,
    output logic              m_rready_o,
    output logic [ID_W-1:0]   m_awid_o,
    output logic [31:0]       m_awaddr_o,
    output logic [7:0]        m_awlen_o,
    output logic [2:0]        m_awsize_o,
    output logic [1:0]        m_awburst_o,
    output logic [2:0]        m_awprot_o,
    output logic [3:0]        m_awcache_o,
    output logic [3:0]        m_awqos_o,
    output logic [3:0]        m_awregion_o,
    output logic              m_awvalid_o,
    input  logic              m_awready_i,
    output logic [VB-1:0]     m_wdata_o,
    output logic [W-1:0]      m_wstrb_o,
    output logic              m_wlast_o,
    output logic              m_wvalid_o,
    input  logic              m_wready_i,
    input  logic [ID_W-1:0]   m_bid_i,
    input  logic [1:0]        m_bresp_i,
    input  logic              m_bvalid_i,
    output logic              m_bready_o
);
  logic          rd_req, rd_gnt, rd_rvalid, wr_req, wr_gnt, wr_done, bus_err;
  logic [31:0]   rd_addr, wr_addr;
  logic [VB-1:0] rd_rdata, wr_wdata;
  logic [W-1:0]  wr_be;

  tq_top_axil #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .MACQ_DEPTH(MACQ_DEPTH), .SCMD_DEPTH(SCMD_DEPTH),
                .SHARED_PORT(SHARED_PORT), .PROG_BYTES(PROG_BYTES), .ACC_FORM(ACC_FORM),
                .CLK_GATE(CLK_GATE), .WR_ACK(1'b1), .WR_OUT_MAX(MAX_OUT), .PANEL_BYTES(PANEL_BYTES),
                .ADDR_W(ADDR_W)) u_tq (
      .clk_i, .rst_ni,
      .s_awaddr_i, .s_awprot_i, .s_awvalid_i, .s_awready_o,
      .s_wdata_i, .s_wstrb_i, .s_wvalid_i, .s_wready_o,
      .s_bresp_o, .s_bvalid_o, .s_bready_i,
      .s_araddr_i, .s_arprot_i, .s_arvalid_i, .s_arready_o,
      .s_rdata_o, .s_rresp_o, .s_rvalid_o, .s_rready_i,
      .irq_o,
      .rd_req_o(rd_req), .rd_addr_o(rd_addr), .rd_gnt_i(rd_gnt), .rd_rvalid_i(rd_rvalid), .rd_rdata_i(rd_rdata),
      .wr_req_o(wr_req), .wr_addr_o(wr_addr), .wr_be_o(wr_be), .wr_wdata_o(wr_wdata), .wr_gnt_i(wr_gnt),
      .wr_done_i(wr_done), .bus_err_i(bus_err));

  tq_axi_mem #(.W(W), .MAX_OUT(MAX_OUT), .BURST_BEATS(BURST_BEATS), .ID_W(ID_W),
                .AXPROT(AXPROT), .AXCACHE(AXCACHE), .AXQOS(AXQOS)) u_axi (
      .clk_i, .rst_ni,
      .rd_req_i(rd_req), .rd_addr_i(rd_addr), .rd_gnt_o(rd_gnt), .rd_rvalid_o(rd_rvalid), .rd_rdata_o(rd_rdata),
      .wr_req_i(wr_req), .wr_addr_i(wr_addr), .wr_be_i(wr_be), .wr_wdata_i(wr_wdata), .wr_gnt_o(wr_gnt),
      .wr_done_o(wr_done), .bus_err_o(bus_err),
      .m_arid_o, .m_araddr_o, .m_arlen_o, .m_arsize_o, .m_arburst_o,
      .m_arprot_o, .m_arcache_o, .m_arqos_o, .m_arregion_o,
      .m_arvalid_o, .m_arready_i, .m_rid_i,
      .m_rdata_i, .m_rresp_i, .m_rlast_i, .m_rvalid_i, .m_rready_o,
      .m_awid_o, .m_awaddr_o, .m_awlen_o, .m_awsize_o, .m_awburst_o,
      .m_awprot_o, .m_awcache_o, .m_awqos_o, .m_awregion_o,
      .m_awvalid_o, .m_awready_i,
      .m_wdata_o, .m_wstrb_o, .m_wlast_o, .m_wvalid_o, .m_wready_i,
      .m_bid_i, .m_bresp_i, .m_bvalid_i, .m_bready_o);

endmodule
