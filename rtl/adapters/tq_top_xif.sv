// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tq_top attached to a CPU through CV-X-IF (tq_xif_regs): registers are reached with the
// tq.wr / tq.rd custom instructions. The memory ports stay native (OBI-style, W bytes,
// spec 6.3), for tightly coupled SRAM or an AXI master (tq_axi_mem).

module tq_top_xif #(
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
    parameter int PANEL_BYTES = 0,
    parameter int X_ID_WIDTH  = 4,
    parameter int X_HARTID_WIDTH = 1,
    localparam int W  = 2 * T,
    localparam int VB = W * 8
) (
    input  logic              clk_i,
    input  logic              rst_ni,
    // CV-X-IF (tq_xif_regs: tq.wr / tq.rd custom instructions)
    input  logic              x_issue_valid_i,
    output logic              x_issue_ready_o,
    input  logic [31:0]       x_issue_req_instr_i,
    input  logic [X_HARTID_WIDTH-1:0] x_issue_req_hartid_i,
    input  logic [X_ID_WIDTH-1:0]     x_issue_req_id_i,
    output logic              x_issue_resp_accept_o,
    output logic              x_issue_resp_writeback_o,
    output logic [1:0]        x_issue_resp_register_read_o,
    input  logic              x_register_valid_i,
    output logic              x_register_ready_o,
    input  logic [X_HARTID_WIDTH-1:0] x_register_hartid_i,
    input  logic [X_ID_WIDTH-1:0]     x_register_id_i,
    input  logic [31:0]       x_register_rs0_i,
    input  logic [31:0]       x_register_rs1_i,
    input  logic [1:0]        x_register_rs_valid_i,
    input  logic              x_commit_valid_i,
    input  logic [X_HARTID_WIDTH-1:0] x_commit_hartid_i,
    input  logic [X_ID_WIDTH-1:0]     x_commit_id_i,
    input  logic              x_commit_kill_i,
    output logic              x_result_valid_o,
    input  logic              x_result_ready_i,
    output logic [X_HARTID_WIDTH-1:0] x_result_hartid_o,
    output logic [X_ID_WIDTH-1:0]     x_result_id_o,
    output logic [31:0]       x_result_data_o,
    output logic [4:0]        x_result_rd_o,
    output logic              x_result_we_o,
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

  tq_xif_regs #(.X_ID_WIDTH(X_ID_WIDTH), .X_HARTID_WIDTH(X_HARTID_WIDTH)) u_xif (
      .clk_i, .rst_ni,
      .x_issue_valid_i, .x_issue_ready_o, .x_issue_req_instr_i, .x_issue_req_hartid_i,
      .x_issue_req_id_i, .x_issue_resp_accept_o, .x_issue_resp_writeback_o,
      .x_issue_resp_register_read_o,
      .x_register_valid_i, .x_register_ready_o, .x_register_hartid_i, .x_register_id_i,
      .x_register_rs0_i, .x_register_rs1_i, .x_register_rs_valid_i,
      .x_commit_valid_i, .x_commit_hartid_i, .x_commit_id_i, .x_commit_kill_i,
      .x_result_valid_o, .x_result_ready_i, .x_result_hartid_o, .x_result_id_o,
      .x_result_data_o, .x_result_rd_o, .x_result_we_o,
      .reg_req_o(reg_req), .reg_gnt_i(reg_gnt), .reg_addr_o(reg_addr), .reg_we_o(reg_we),
      .reg_be_o(reg_be), .reg_wdata_o(reg_wdata), .reg_rvalid_i(reg_rvalid), .reg_rdata_i(reg_rdata));

  tq_top #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .MACQ_DEPTH(MACQ_DEPTH), .SCMD_DEPTH(SCMD_DEPTH),
           .SHARED_PORT(SHARED_PORT), .PROG_BYTES(PROG_BYTES), .ACC_FORM(ACC_FORM),
           .CLK_GATE(CLK_GATE), .WR_ACK(WR_ACK), .WR_OUT_MAX(WR_OUT_MAX),
           .PANEL_BYTES(PANEL_BYTES)) u_tq (
      .clk_i, .rst_ni,
      .reg_req_i(reg_req), .reg_gnt_o(reg_gnt), .reg_addr_i(reg_addr), .reg_we_i(reg_we),
      .reg_be_i(reg_be), .reg_wdata_i(reg_wdata), .reg_rvalid_o(reg_rvalid), .reg_rdata_o(reg_rdata),
      .irq_o,
      .rd_req_o, .rd_addr_o, .rd_gnt_i, .rd_rvalid_i, .rd_rdata_i,
      .wr_req_o, .wr_addr_o, .wr_be_o, .wr_wdata_o, .wr_gnt_i, .wr_done_i, .bus_err_i);

endmodule
