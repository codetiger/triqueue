// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Tri-Queue top level: register port + core. This is the unit an SoC integrates, through an
// adapter for its bus (spec 6.4) and its memory (spec 6.3).

module tq_top #(
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
    parameter int MAC_PIPE    = 2,
    parameter bit PERF        = 1'b1,
    localparam int W  = 2 * T,
    localparam int VB = W * 8
) (
    input  logic          clk_i,
    input  logic          rst_ni,
    // register port (32-bit)
    input  logic          reg_req_i,
    output logic          reg_gnt_o,
    input  logic [11:0]   reg_addr_i,
    input  logic          reg_we_i,
    input  logic [3:0]    reg_be_i,
    input  logic [31:0]   reg_wdata_i,
    output logic          reg_rvalid_o,
    output logic [31:0]   reg_rdata_o,
    output logic          irq_o,
    // memory read port (W bytes)
    output logic          rd_req_o,
    output logic [31:0]   rd_addr_o,
    input  logic          rd_gnt_i,
    input  logic          rd_rvalid_i,
    input  logic [VB-1:0] rd_rdata_i,
    // memory write port (W bytes, byte enables)
    output logic          wr_req_o,
    output logic [31:0]   wr_addr_o,
    output logic [W-1:0]  wr_be_o,
    output logic [VB-1:0] wr_wdata_o,
    input  logic          wr_gnt_i,
    input  logic          wr_done_i,     // write response (WR_ACK = 1); tie 0 for an SRAM
    input  logic          bus_err_i,     // pulse: an interconnect error response (STATUS.BUS_ERR); tie 0 for an SRAM
    output logic          flush_o,       // pulse on START or ABORT (flushes external read-burst buffers)
    input  logic          ext_pending_i = 1'b0  // external adapter transactions still in flight (STATUS.PENDING); tie 0 for an SRAM
);
  logic                    start, abort, busy, core_pending, done, error;
  logic [3:0]              err_code;
  logic [PROG_BYTES*8-1:0] prog;
  logic [8*32-1:0]         cfg;
  logic [31:0]             p_cyc, p_ret, p_beat, p_rd, p_wr;

  tq_regs #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .MACQ_DEPTH(MACQ_DEPTH), .SHARED_PORT(SHARED_PORT),
            .PROG_BYTES(PROG_BYTES), .CLK_GATE(CLK_GATE)) u_regs (
      .clk_i, .rst_ni,
      .reg_req_i, .reg_gnt_o, .reg_addr_i, .reg_we_i, .reg_be_i, .reg_wdata_i,
      .reg_rvalid_o, .reg_rdata_o, .irq_o,
      .start_o(start), .abort_o(abort), .prog_o(prog),
      .busy_i(busy), .pending_i(core_pending || ext_pending_i),
      .done_i(done), .error_i(error), .err_code_i(err_code), .bus_err_i, .cfg_i(cfg),
      .perf_i({p_wr, p_rd, p_beat, p_ret, p_cyc}));

  tq_core #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .MACQ_DEPTH(MACQ_DEPTH), .SCMD_DEPTH(SCMD_DEPTH),
            .SHARED_PORT(SHARED_PORT), .PROG_BYTES(PROG_BYTES), .ACC_FORM(ACC_FORM),
            .CLK_GATE(CLK_GATE), .WR_ACK(WR_ACK), .WR_OUT_MAX(WR_OUT_MAX),
            .PANEL_BYTES(PANEL_BYTES), .MAC_PIPE(MAC_PIPE), .PERF(PERF)) u_core (
      .clk_i, .rst_ni,
      .start_i(start), .abort_i(abort), .prog_i(prog),
      .clear_o(flush_o), .busy_o(busy), .pending_o(core_pending),
      .done_o(done), .error_o(error), .err_code_o(err_code), .cfg_o(cfg),
      .perf_cycles_o(p_cyc), .perf_instret_o(p_ret), .perf_beats_o(p_beat),
      .perf_reads_o(p_rd), .perf_writes_o(p_wr),
      .rd_req_o, .rd_addr_o, .rd_gnt_i, .rd_rvalid_i, .rd_rdata_i,
      .wr_req_o, .wr_addr_o, .wr_be_o, .wr_wdata_o, .wr_gnt_i, .wr_done_i);

  generate
    if (T < 4 || T > 128 || (T & (T - 1)) != 0) begin : g_bad_t
      $error("tq_top: T must be a power of two in [4, 128]");
    end
    if (FIFO_DEPTH < 2 || FIFO_DEPTH > 255) begin : g_bad_fifo
      $error("tq_top: FIFO_DEPTH must be in [2, 255]");
    end
    if (MACQ_DEPTH < 1 || MACQ_DEPTH > 15) begin : g_bad_macq
      $error("tq_top: MACQ_DEPTH must be in [1, 15]");
    end
    if (SCMD_DEPTH < 1) begin : g_bad_scmd
      $error("tq_top: SCMD_DEPTH must be at least 1");
    end
    if (PROG_BYTES < 50 || PROG_BYTES > 1020 || (PROG_BYTES % 4) != 0) begin : g_bad_prog
      $error("tq_top: PROG_BYTES must be in [50, 1020] and a multiple of 4");
    end
    if (PANEL_BYTES < 0 || (PANEL_BYTES % (2 * T)) != 0) begin : g_bad_panel
      $error("tq_top: PANEL_BYTES must be a non-negative multiple of 2T");
    end
    if (WR_OUT_MAX < 1) begin : g_bad_wrout
      $error("tq_top: WR_OUT_MAX must be at least 1");
    end
    if (MAC_PIPE != 1 && MAC_PIPE != 2) begin : g_bad_macpipe
      $error("tq_top: MAC_PIPE must be 1 or 2");
    end
    if (ACC_FORM != tq_pkg::ACC_CSA && ACC_FORM != tq_pkg::ACC_CPA) begin : g_bad_acc
      $error("tq_top: ACC_FORM must be tq_pkg::ACC_CSA or tq_pkg::ACC_CPA");
    end
  endgenerate

endmodule
