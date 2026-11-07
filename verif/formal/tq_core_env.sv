// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Formal environment for tq_core (verif/formal/tq_core.sby). START and ABORT may arrive at any
// time; gnt and read data are free on every cycle. The program is one arbitrary constant:
//   KERNEL = 0: any bytes, so any instruction sequence and any CFG values;
//   KERNEL = 1: CFG K, M, N with arbitrary 1 <= M, N <= 2T + 1 and 1 <= K <= 16, then the
//               reference GEMM kernel (spec 6.2, model isa.gemm_kernel()). Bases and C_LD stay 0.
// The other constraints are:
//   * reset is asserted in the first cycle;
//   * the memory answers only reads it has granted, in order, at any latency including zero
//     (spec 6.3), so rd_rvalid needs an outstanding or same-cycle granted read.
// The asserted properties live in the RTL under `ifdef FORMAL`: tq_core (no accumulate into the
// draining bank) and tq_fifo with TQ_FORMAL_CORE (no queue overflow or underflow).
// The register port (tq_regs) is outside the cone of these properties and is left out.

module tq_core_env #(
    parameter int  T           = 4,
    parameter int  FIFO_DEPTH  = 2,
    parameter int  MACQ_DEPTH  = 2,
    parameter int  SCMD_DEPTH  = 2,
    parameter bit  SHARED_PORT = 1'b0,
    parameter int  ACC_FORM    = 0,
    parameter int  PROG_BYTES  = 32,
    parameter bit  KERNEL      = 1'b0,
    localparam int W  = 2 * T,
    localparam int VB = W * 8
) (
    input  logic          clk_i,
    input  logic          rst_ni,
    input  logic          start_i,
    input  logic          abort_i,
    input  logic          rd_gnt_i,
    input  logic          rd_rvalid_i,
    input  logic [VB-1:0] rd_rdata_i,
    input  logic          wr_gnt_i
);
  (* anyconst *) logic [PROG_BYTES*8-1:0] any_prog;
  (* anyconst *) logic [4:0] k_dim, m_dim, n_dim;
  logic [PROG_BYTES*8-1:0] prog;
  localparam logic [79:0] GEMM_KERNEL = 80'h90_60_58_71_30_20_85_30_20_40;   // byte 0 in the low bits
  always_comb begin
    if (KERNEL) begin
      prog = '0;                                                       // NOP padding
      prog[0   +: 40] = {27'd0, k_dim, 8'h14};                         // CFG K, imm32 little-endian
      prog[40  +: 40] = {27'd0, m_dim, 8'h15};                         // CFG M
      prog[80  +: 40] = {27'd0, n_dim, 8'h16};                         // CFG N
      prog[120 +: 80] = GEMM_KERNEL;
    end else begin
      prog = any_prog;
    end
  end

  logic          busy, done, error, rd_req, wr_req;
  logic [3:0]    err_code;
  logic [255:0]  cfg;
  logic [31:0]   p_cyc, p_ret, p_beat, p_rd, p_wr, rd_addr, wr_addr;
  logic [W-1:0]  wr_be;
  logic [VB-1:0] wr_wdata;

  tq_core #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .MACQ_DEPTH(MACQ_DEPTH), .SCMD_DEPTH(SCMD_DEPTH),
            .SHARED_PORT(SHARED_PORT), .PROG_BYTES(PROG_BYTES), .ACC_FORM(ACC_FORM)) dut (
      .clk_i, .rst_ni, .start_i, .abort_i, .prog_i(prog),
      .busy_o(busy), .done_o(done), .error_o(error), .err_code_o(err_code), .cfg_o(cfg),
      .perf_cycles_o(p_cyc), .perf_instret_o(p_ret), .perf_beats_o(p_beat),
      .perf_reads_o(p_rd), .perf_writes_o(p_wr),
      .rd_req_o(rd_req), .rd_addr_o(rd_addr), .rd_gnt_i, .rd_rvalid_i, .rd_rdata_i,
      .wr_req_o(wr_req), .wr_addr_o(wr_addr), .wr_be_o(wr_be), .wr_wdata_o(wr_wdata), .wr_gnt_i);

  logic past_valid = 1'b0;
  always_ff @(posedge clk_i) past_valid <= 1'b1;

  logic [5:0] outstanding_q;          // granted reads not yet answered
  logic       fire;
  assign fire = rd_req && rd_gnt_i;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) outstanding_q <= '0;
    else         outstanding_q <= outstanding_q + 6'(fire) - 6'(rd_rvalid_i);
  end

  always_comb begin
    if (!past_valid) assume (!rst_ni);
    if (KERNEL) begin
      assume (m_dim >= 1 && 32'(m_dim) <= 2 * T + 1);
      assume (n_dim >= 1 && 32'(n_dim) <= 2 * T + 1);
      assume (k_dim >= 1 && k_dim <= 5'd16);
    end
    if (!rst_ni) assume (!rd_rvalid_i);
    assume (!rd_rvalid_i || outstanding_q != 0 || fire);
  end
endmodule
