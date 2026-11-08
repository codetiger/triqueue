// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Host register port (spec 6.4). 32-bit OBI-style slave: req/gnt, response one cycle later.
//
//   0x000 ID          RO  0x5451_0001 ("TQ", register-map version 1)
//   0x004 HW_CONFIG   RO  [7:0] T, [15:8] FIFO_DEPTH, [19:16] MACQ_DEPTH, [20] SHARED_PORT,
//                         [31:24] PROG_BYTES / 4.  Port width W = 2T bytes.
//   0x008 CTRL        WO  bit 0 START, bit 1 ABORT
//   0x00C STATUS      RO  bit 0 BUSY, bit 1 DONE (sticky until START), bit 2 ERROR, bit 3 BUS_ERR
//                         (an interconnect error response since START, sticky), [11:8] error code
//   0x010 IRQ_EN      RW  bit 0 done, bit 1 error, bit 2 bus error
//   0x014 IRQ_STATUS  RW1C bit 0 done, bit 1 error, bit 2 bus error
//   0x020 PERF_CYCLES, 0x024 PERF_INSTRET, 0x028 PERF_BEATS, 0x02C PERF_READS, 0x030 PERF_WRITES   RO
//   0x040-0x05C CFG[0..7]   RO  configuration registers as last written by the program
//   0x100-       PROG        WO  program buffer (PROG_BYTES bytes, byte enables honoured)

module tq_regs #(
    parameter int T           = 8,
    parameter int FIFO_DEPTH  = 8,
    parameter int MACQ_DEPTH  = 4,
    parameter bit SHARED_PORT = 1'b0,
    parameter int PROG_BYTES  = 64,
    parameter bit CLK_GATE    = 1'b1     // one clock gate per program-buffer byte (tq_cg)
) (
    input  logic                    clk_i,
    input  logic                    rst_ni,
    // register port
    input  logic                    reg_req_i,
    output logic                    reg_gnt_o,
    input  logic [11:0]             reg_addr_i,
    input  logic                    reg_we_i,
    input  logic [3:0]              reg_be_i,
    input  logic [31:0]             reg_wdata_i,
    output logic                    reg_rvalid_o,
    output logic [31:0]             reg_rdata_o,
    output logic                    irq_o,
    // core
    output logic                    start_o,
    output logic                    abort_o,
    output logic [PROG_BYTES*8-1:0] prog_o,
    input  logic                    busy_i,
    input  logic                    done_i,
    input  logic                    error_i,
    input  logic [3:0]              err_code_i,
    input  logic                    bus_err_i,   // pulse: an error response from the interconnect
    input  logic [8*32-1:0]         cfg_i,
    input  logic [5*32-1:0]         perf_i       // {writes, reads, beats, instret, cycles}
);
  localparam logic [31:0] ID = 32'h5451_0001;
  localparam logic [31:0] HW_CONFIG = {8'(PROG_BYTES / 4), 3'b0, SHARED_PORT, 4'(MACQ_DEPTH),
                                       8'(FIFO_DEPTH), 8'(T)};

  logic [PROG_BYTES*8-1:0] prog_q;
  logic       done_q, bus_err_q;
  logic [2:0] irq_en_q, irq_st_q;
  logic       rvalid_q;
  logic [31:0] rdata_q;

  logic wr, rd;
  assign reg_gnt_o = 1'b1;
  assign wr = reg_req_i && reg_we_i;
  assign rd = reg_req_i && !reg_we_i;

  assign start_o = wr && reg_addr_i == 12'h008 && reg_be_i[0] && reg_wdata_i[0];
  assign abort_o = wr && reg_addr_i == 12'h008 && reg_be_i[0] && reg_wdata_i[1];

  logic [31:0] rdata;
  always_comb begin
    rdata = 32'd0;
    case (reg_addr_i)
      12'h000: rdata = ID;
      12'h004: rdata = HW_CONFIG;
      12'h00C: rdata = {20'd0, err_code_i, 4'd0, bus_err_q, error_i, done_q, busy_i};
      12'h010: rdata = {29'd0, irq_en_q};
      12'h014: rdata = {29'd0, irq_st_q};
      12'h020: rdata = perf_i[0*32 +: 32];
      12'h024: rdata = perf_i[1*32 +: 32];
      12'h028: rdata = perf_i[2*32 +: 32];
      12'h02C: rdata = perf_i[3*32 +: 32];
      12'h030: rdata = perf_i[4*32 +: 32];
      default: begin
        if (reg_addr_i >= 12'h040 && reg_addr_i < 12'h060) rdata = cfg_i[32'(reg_addr_i[4:2]) * 32 +: 32];
      end
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_q <= 1'b0; bus_err_q <= 1'b0; irq_en_q <= '0; irq_st_q <= '0;
      rvalid_q <= 1'b0; rdata_q <= '0;
    end else begin
      rvalid_q <= reg_req_i;
      if (rd) rdata_q <= rdata;
      if (start_o && !busy_i) begin
        done_q    <= 1'b0;
        bus_err_q <= 1'b0;
      end
      if (bus_err_i) begin
        bus_err_q   <= 1'b1;
        irq_st_q[2] <= 1'b1;
      end
      if (done_i) begin
        done_q      <= 1'b1;
        irq_st_q[0] <= 1'b1;
        if (error_i) irq_st_q[1] <= 1'b1;
      end
      if (wr && reg_addr_i == 12'h010 && reg_be_i[0]) irq_en_q <= reg_wdata_i[2:0];
      if (wr && reg_addr_i == 12'h014 && reg_be_i[0]) irq_st_q <= irq_st_q & ~reg_wdata_i[2:0];
    end
  end

  // program buffer: byte i is written by a PROG write to word i / 4 with byte enable i % 4
  logic prog_wr;
  assign prog_wr = wr && reg_addr_i >= 12'h100 && 32'(reg_addr_i) < 32'h100 + PROG_BYTES;
  for (genvar i = 0; i < PROG_BYTES; i++) begin : g_prog
    logic       we, gclk;
    logic [7:0] q;
    assign we = prog_wr && 32'(reg_addr_i[11:2]) == 32'h40 + i / 4 && reg_be_i[i % 4];
    tq_cg #(.EN(CLK_GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
    always_ff @(posedge gclk or negedge rst_ni) begin
      if (!rst_ni)              q <= '0;
      else if (we || CLK_GATE)  q <= reg_wdata_i[(i % 4) * 8 +: 8];
    end
    assign prog_q[i*8 +: 8] = q;
  end

  assign prog_o       = prog_q;
  assign reg_rvalid_o = rvalid_q;
  assign reg_rdata_o  = rdata_q;
  assign irq_o        = |(irq_st_q & irq_en_q);

endmodule
