// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Formal environment for tq_axi_mem (verif/formal/tq_axi_mem.sby), W = 4 bytes.
// Core side: any request sequence that keeps OBI rules, except that a request waiting for gnt
// may be dropped (START / ABORT; tq_core then has no request in that cycle, which tq_core.sby
// proves). AXI side: any READY pattern, in-order R and B at any latency (R in the AR cycle's
// next cycle or later, B after its AW and W), and R data equal to the AR address, so data can
// be followed through the adapter (in burst mode, through one arbitrary tracked address, below).
// Properties: AXI VALID and payload held until READY; the core receives exactly the responses
// of its own granted requests, in order and with the right data (a dropped request's response
// never reaches it); wr_done only for the core's granted writes. CHECK_RDATA = 0 leaves out only
// the read-data equality (tq_axi_mem.sby burst_prove; burst_deep checks it to a bounded depth).

module tq_axi_mem_env #(
    parameter int MAX_OUT     = 3,
    parameter int BURST_BEATS = 1,
    parameter bit CHECK_RDATA = 1'b1
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        flush_i,
    input  logic        rd_req_i,
    input  logic [31:0] rd_addr_i,
    input  logic        wr_req_i,
    input  logic [31:0] wr_addr_i,
    input  logic [3:0]  wr_be_i,
    input  logic [31:0] wr_wdata_i,
    input  logic        m_arready_i,
    input  logic        m_rvalid_i,
    input  logic        m_rerr_i,
    input  logic        m_awready_i,
    input  logic        m_wready_i,
    input  logic        m_bvalid_i,
    input  logic        m_berr_i
);
  logic        rd_gnt, rd_rvalid, wr_gnt, wr_done, pending, bus_err;
  logic [31:0] rd_rdata;
  logic [31:0] araddr, awaddr, wdata;
  logic [7:0]  arlen, awlen;
  logic [2:0]  arsize, awsize;
  logic [1:0]  arburst, awburst;
  logic        arvalid, rready, awvalid, wvalid, wlast, bready;
  logic [3:0]  wstrb;
  logic [31:0] rdata;

  // Address bits that carry information. In burst mode the core-side assumptions below fix
  // rd_addr_i / wr_addr_i [31:6] to 0, so the DUT sees those bits as constants and the queues
  // keep only {epoch, addr[DAW-1:0]}: the same values, without hundreds of state bits that the
  // prover would otherwise have to show are zero. AR addresses are asserted to stay in range.
  localparam int DAW = (BURST_BEATS > 1) ? 6 : 31;
  // Burst mode follows one arbitrary word address sym_a (data independence): the slave returns
  // {epoch, 30'b0, beat address == sym_a}, and a response must carry exactly that bit for the
  // request it answers. For every sym_a this is the full data check, so wrong-beat, wrong-line,
  // stale or reordered data still fails, with 2 bits per queued beat instead of 7.
  localparam int QW  = (BURST_BEATS > 1) ? 1 : DAW;
  (* anyconst *) logic [DAW-1:2] sym_a;
  function automatic logic [QW-1:0] qval(input logic [DAW-1:0] a);
    if (BURST_BEATS > 1) qval = QW'(a[DAW-1:2] == sym_a);
    else                 qval = QW'(a);
  endfunction
  function automatic logic [31:0] qdata(input logic [QW:0] e);
    if (BURST_BEATS > 1) qdata = {e[QW], 30'b0, e[0]};
    else                 qdata = {e[QW], (31 - DAW)'(0), e[QW-1:0]};
  endfunction
  logic [31:0] rd_addr_c, wr_addr_c;
  assign rd_addr_c = (BURST_BEATS > 1) ? {26'b0, rd_addr_i[5:0]} : rd_addr_i;
  assign wr_addr_c = (BURST_BEATS > 1) ? {26'b0, wr_addr_i[5:0]} : wr_addr_i;

  tq_axi_mem #(.W(4), .MAX_OUT(MAX_OUT), .BURST_BEATS(BURST_BEATS)) dut (
      .clk_i, .rst_ni, .flush_i, .pending_o(pending),
      .rd_req_i, .rd_addr_i(rd_addr_c), .rd_gnt_o(rd_gnt), .rd_rvalid_o(rd_rvalid), .rd_rdata_o(rd_rdata),
      .wr_req_i, .wr_addr_i(wr_addr_c), .wr_be_i, .wr_wdata_i, .wr_gnt_o(wr_gnt), .wr_done_o(wr_done), .bus_err_o(bus_err),
      .m_araddr_o(araddr), .m_arlen_o(arlen), .m_arsize_o(arsize), .m_arburst_o(arburst),
      .m_arvalid_o(arvalid), .m_arready_i,
      .m_rdata_i(rdata), .m_rresp_i({m_rerr_i, 1'b0}), .m_rlast_i(1'b1), .m_rvalid_i, .m_rready_o(rready),
      .m_awaddr_o(awaddr), .m_awlen_o(awlen), .m_awsize_o(awsize), .m_awburst_o(awburst),
      .m_awvalid_o(awvalid), .m_awready_i,
      .m_wdata_o(wdata), .m_wstrb_o(wstrb), .m_wlast_o(wlast), .m_wvalid_o(wvalid), .m_wready_i,
      .m_bresp_i({m_berr_i, 1'b0}), .m_bvalid_i, .m_bready_o(bready));

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
    p_rd_wait <= rst_ni && rd_req_i && !rd_gnt;   p_rd_addr <= rd_addr_c;
    p_wr_wait <= rst_ni && wr_req_i && !wr_gnt;   p_wr_addr <= wr_addr_c;
    p_wr_wdata <= wr_wdata_i;                     p_wr_be <= wr_be_i;
  end
  always_comb begin
    assume (rd_addr_i[31] == 1'b0);
    if (BURST_BEATS > 1) assume (rd_addr_i[1:0] == 2'b00 && rd_addr_i[31:6] == '0 && wr_addr_i[31:6] == '0);
    if (flush_i) assume (!rd_req_i && !wr_req_i);
    if (run && p_rd_wait) assume (!rd_req_i || rd_addr_c == p_rd_addr);           // held or dropped
    if (run && p_wr_wait) assume (!wr_req_i || (wr_addr_c == p_wr_addr && wr_wdata_i == p_wr_wdata
                                                && wr_be_i == p_wr_be));
  end

  // ---------------------------------------------------------------- AXI slave (assumptions)
  // R: a FIFO of accepted AR beat addresses; R carries the oldest one, never in the AR cycle.
  // epoch_q toggles on flush_i when no read is in flight (host rewrote operand memory before START).
  localparam int RQ_MAX = ((BURST_BEATS > 1) ? 4 : MAX_OUT) * BURST_BEATS;
  logic [RQ_MAX:0][QW:0] ar_q;                    // {epoch, qval(beat address)}
  logic [3:0]  ar_n;
  logic [2:0]  b_n, cr_n, cw_n;
  logic        ar_fire, aw_fire, w_fire;
  logic        aw_open_q, w_open_q;                 // AW / W of the current write accepted
  logic        epoch_q;
  assign ar_fire = arvalid && m_arready_i;
  assign aw_fire = awvalid && m_awready_i;
  assign w_fire  = wvalid && m_wready_i;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ar_n <= '0; b_n <= '0; aw_open_q <= 1'b0; w_open_q <= 1'b0; epoch_q <= 1'b0;
    end else begin
      if (flush_i && ar_n == 0 && !arvalid && cr_n == 0) epoch_q <= ~epoch_q;
      for (int i = 0; i < RQ_MAX; i++)
        ar_q[i] <= (m_rvalid_i ? ar_q[i + 1] : ar_q[i]);
      if (ar_fire) begin
        for (int b = 0; b < BURST_BEATS; b++)
          ar_q[32'(ar_n) - 32'(m_rvalid_i) + b] <= {epoch_q, qval(araddr[DAW-1:0] + DAW'(b * 4))};
      end
      ar_n <= ar_n + (ar_fire ? 4'(BURST_BEATS) : 4'd0) - 4'(m_rvalid_i);
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
  assign rdata = qdata(ar_q[0]);
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
      assert (arlen == 8'(BURST_BEATS - 1) && awlen == 0 && arsize == 3'd2 && awsize == 3'd2
              && arburst == 2'b01 && awburst == 2'b01);
      assert (!rd_gnt || rd_req_i);
      if (BURST_BEATS > 1 && arvalid) assert (araddr[31:DAW] == '0 && araddr % (4 * BURST_BEATS) == 0);
      assert (!wr_gnt || wr_req_i);
    end
  end

  // The core's reads: a FIFO of granted reads, {epoch, qval(address)}; each response carries the
  // data of the oldest one (hits on a buffered burst line included).
  logic [MAX_OUT:0][QW:0] cr_q;                   // {epoch, qval(address)}
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      cr_n <= '0; cw_n <= '0;
    end else begin
      for (int i = 0; i < MAX_OUT; i++)
        cr_q[i] <= (rd_rvalid ? cr_q[i + 1] : cr_q[i]);
      if (rd_gnt) cr_q[32'(cr_n) - 32'(rd_rvalid)] <= {epoch_q, qval(rd_addr_c[DAW-1:0])};
      cr_n <= cr_n + 3'(rd_gnt) - 3'(rd_rvalid);
      cw_n <= cw_n + 3'(wr_gnt) - 3'(wr_done);
    end
  end
  always_comb begin
    if (run) begin
      if (rd_rvalid) assert (cr_n != 0);
      if (rd_rvalid && CHECK_RDATA) assert (rd_rdata == qdata(cr_q[0]));
      if (wr_done) assert (cw_n != 0);
      assert (32'(cw_n) <= MAX_OUT);
      assert (!bus_err || rd_rvalid || wr_done);
      if (wr_done && m_berr_i) assert (bus_err);
      if (BURST_BEATS == 1) assert (bus_err == ((rd_rvalid && m_rerr_i) || (wr_done && m_berr_i)));
      if (cr_n != 0 || cw_n != 0 || ar_n != 0 || b_n != 0) assert (pending);
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
      cover (bus_err);
      cover (epoch_q && rd_rvalid);
    end
  end

endmodule
