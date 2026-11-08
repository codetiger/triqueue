// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tq_top with an AXI4-Lite register port (tq_axil_regs). The memory ports stay native
// (OBI-style, W bytes, spec 6.3), for tightly coupled SRAM.

module tq_top_axil #(
    parameter int T           = 8,
    parameter int FIFO_DEPTH  = 8,
    parameter int MACQ_DEPTH  = 4,
    parameter int SCMD_DEPTH  = 2,
    parameter bit SHARED_PORT = 1'b0,
    parameter int PROG_BYTES  = 64,
    parameter int ACC_FORM    = tq_pkg::ACC_CPA,
    parameter bit CLK_GATE    = 1'b1,
    parameter bit WR_ACK      = 1'b0,
    parameter int WR_OUT_MAX  = 15,
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
    // memory read port (W bytes)
    output logic              rd_req_o,
    output logic [31:0]       rd_addr_o,
    input  logic              rd_gnt_i,
    input  logic              rd_rvalid_i,
    input  logic [VB-1:0]     rd_rdata_i,
    // memory write port (W bytes, byte enables)
    output logic              wr_req_o,
    output logic [31:0]       wr_addr_o,
    output logic [W-1:0]      wr_be_o,
    output logic [VB-1:0]     wr_wdata_o,
    input  logic              wr_gnt_i,
    input  logic              wr_done_i,
    input  logic              bus_err_i
);
  logic        reg_req, reg_gnt, reg_we, reg_rvalid;
  logic [11:0] reg_addr;
  logic [3:0]  reg_be;
  logic [31:0] reg_wdata, reg_rdata;

  tq_axil_regs #(.ADDR_W(ADDR_W)) u_axil (
      .clk_i, .rst_ni,
      .s_awaddr_i, .s_awprot_i, .s_awvalid_i, .s_awready_o,
      .s_wdata_i, .s_wstrb_i, .s_wvalid_i, .s_wready_o,
      .s_bresp_o, .s_bvalid_o, .s_bready_i,
      .s_araddr_i, .s_arprot_i, .s_arvalid_i, .s_arready_o,
      .s_rdata_o, .s_rresp_o, .s_rvalid_o, .s_rready_i,
      .reg_req_o(reg_req), .reg_gnt_i(reg_gnt), .reg_addr_o(reg_addr), .reg_we_o(reg_we),
      .reg_be_o(reg_be), .reg_wdata_o(reg_wdata), .reg_rvalid_i(reg_rvalid), .reg_rdata_i(reg_rdata));

  tq_top #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .MACQ_DEPTH(MACQ_DEPTH), .SCMD_DEPTH(SCMD_DEPTH),
           .SHARED_PORT(SHARED_PORT), .PROG_BYTES(PROG_BYTES), .ACC_FORM(ACC_FORM),
           .CLK_GATE(CLK_GATE), .WR_ACK(WR_ACK), .WR_OUT_MAX(WR_OUT_MAX)) u_tq (
      .clk_i, .rst_ni,
      .reg_req_i(reg_req), .reg_gnt_o(reg_gnt), .reg_addr_i(reg_addr), .reg_we_i(reg_we),
      .reg_be_i(reg_be), .reg_wdata_i(reg_wdata), .reg_rvalid_o(reg_rvalid), .reg_rdata_o(reg_rdata),
      .irq_o,
      .rd_req_o, .rd_addr_o, .rd_gnt_i, .rd_rvalid_i, .rd_rdata_i,
      .wr_req_o, .wr_addr_o, .wr_be_o, .wr_wdata_o, .wr_gnt_i, .wr_done_i, .bus_err_i);

endmodule
