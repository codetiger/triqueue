// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// AXI4 master for the core's two memory ports (spec 6.3). Each OBI request is one single-beat
// INCR transaction of one W-byte port word (AxLEN 0, AxSIZE log2(W), one ID), so the data
// width is the port width. The core never back-pressures responses (it reads only with room for
// the data), so RREADY and BREADY are always 1.
//
//   read:  rd_req -> AR, rd_gnt = AR handshake, R -> rd_rvalid / rd_rdata.
//   write: wr_req -> AW and W (accepted in either order), wr_gnt once both are accepted,
//          B -> wr_done (the core must be built with WR_ACK = 1, so FENCE waits for every B).
//
// AXI does not let a master withdraw a VALID, but the core drops an ungranted request at START
// or ABORT (OBI allows it). The adapter then finishes that transaction on its own ("orphan"):
// the address and data are held in registers, the core's next request waits (gnt 0) until the
// orphan is accepted, and the orphan's response is discarded. A tag queue per direction, one
// bit per accepted transaction, in order, marks which responses belong to the core.
// An error response (RRESP / BRESP SLVERR or DECERR) to one of the core's transactions pulses
// bus_err_o (tq_regs latches it in STATUS.BUS_ERR); a discarded orphan's response is ignored.

module tq_axi_mem #(
    parameter int W           = 16,                // port width in bytes (2T)
    parameter int MAX_OUT     = 16,                // transactions in flight per direction
    parameter int BURST_BEATS = 1,                 // read burst beats (power of two, >= 1)
    parameter int ID_W        = 1,                 // AXI ID width (one ID, constant 0)
    parameter logic [2:0] AXPROT  = 3'b000,
    parameter logic [3:0] AXCACHE = 4'b0000,
    parameter logic [3:0] AXQOS   = 4'b0000,
    localparam int VB  = W * 8,
    localparam int TW  = $clog2(MAX_OUT + 1)
) (
    input  logic          clk_i,
    input  logic          rst_ni,
    // core read port (OBI-style)
    input  logic          rd_req_i,
    input  logic [31:0]   rd_addr_i,
    output logic          rd_gnt_o,
    output logic          rd_rvalid_o,
    output logic [VB-1:0] rd_rdata_o,
    // core write port (OBI-style, completion on wr_done_o)
    input  logic          wr_req_i,
    input  logic [31:0]   wr_addr_i,
    input  logic [W-1:0]  wr_be_i,
    input  logic [VB-1:0] wr_wdata_i,
    output logic          wr_gnt_o,
    output logic          wr_done_o,
    output logic          bus_err_o,     // an error response to one of the core's transactions
    input  logic          flush_i = 1'b0, // pulse on START/ABORT: invalidates buffered read burst lines
    output logic          pending_o,     // any AXI read or write transaction held or in flight
    // AXI4 master
    output logic [ID_W-1:0] m_arid_o,
    output logic [31:0]   m_araddr_o,
    output logic [7:0]    m_arlen_o,
    output logic [2:0]    m_arsize_o,
    output logic [1:0]    m_arburst_o,
    output logic [2:0]    m_arprot_o,
    output logic [3:0]    m_arcache_o,
    output logic [3:0]    m_arqos_o,
    output logic [3:0]    m_arregion_o,
    output logic          m_arvalid_o,
    input  logic          m_arready_i,
    input  logic [ID_W-1:0] m_rid_i,
    input  logic [VB-1:0] m_rdata_i,
    input  logic [1:0]    m_rresp_i,
    input  logic          m_rlast_i,
    input  logic          m_rvalid_i,
    output logic          m_rready_o,
    output logic [ID_W-1:0] m_awid_o,
    output logic [31:0]   m_awaddr_o,
    output logic [7:0]    m_awlen_o,
    output logic [2:0]    m_awsize_o,
    output logic [1:0]    m_awburst_o,
    output logic [2:0]    m_awprot_o,
    output logic [3:0]    m_awcache_o,
    output logic [3:0]    m_awqos_o,
    output logic [3:0]    m_awregion_o,
    output logic          m_awvalid_o,
    input  logic          m_awready_i,
    output logic [VB-1:0] m_wdata_o,
    output logic [W-1:0]  m_wstrb_o,
    output logic          m_wlast_o,
    output logic          m_wvalid_o,
    input  logic          m_wready_i,
    input  logic [ID_W-1:0] m_bid_i,
    input  logic [1:0]    m_bresp_i,
    input  logic          m_bvalid_i,
    output logic          m_bready_o
);
  localparam logic [2:0] SIZE = 3'($clog2(W));

  generate
    if (ID_W < 1) begin : g_bad_id_w
      $error("tq_axi_mem: ID_W must be at least 1");
    end
    if (BURST_BEATS < 1 || (BURST_BEATS & (BURST_BEATS - 1)) != 0) begin : g_bad_burst
      $error("tq_axi_mem: BURST_BEATS must be a power of two, at least 1");
    end
    if (BURST_BEATS * W > 4096) begin : g_bad_burst_4k
      $error("tq_axi_mem: BURST_BEATS * W must not exceed the 4 KiB AXI boundary");
    end
    if (MAX_OUT < 1) begin : g_bad_max_out
      $error("tq_axi_mem: MAX_OUT must be at least 1");
    end
  endgenerate

  // One constant ID; the core never relies on response interleaving and the adapter keeps
  // per-direction ordering. Protection/cache/QoS/region are constants so a full AXI4 master port
  // can be connected without a wrapper.
  assign m_arid_o      = '0;
  assign m_awid_o      = '0;
  assign m_arprot_o    = AXPROT;
  assign m_awprot_o    = AXPROT;
  assign m_arcache_o   = AXCACHE;
  assign m_awcache_o   = AXCACHE;
  assign m_arqos_o     = AXQOS;
  assign m_awqos_o     = AXQOS;
  assign m_arregion_o  = '0;
  assign m_awregion_o  = '0;
  /* verilator lint_off UNUSEDSIGNAL */
  logic unused_axi_id;
  assign unused_axi_id = ^{m_rid_i, m_bid_i};
  /* verilator lint_on UNUSEDSIGNAL */

  assign m_arsize_o = SIZE;  assign m_arburst_o = 2'b01;
  assign m_awlen_o = 8'd0;   assign m_awsize_o = SIZE;  assign m_awburst_o = 2'b01;
  assign m_wlast_o = 1'b1;
  assign m_rready_o = 1'b1;
  assign m_bready_o = 1'b1;

  // ---------------------------------------------------------------- read channel
  logic rd_err, rd_pend;
  if (BURST_BEATS == 1) begin : g_rd_single
    // ar_q: a request is on AR (held: VALID stays until READY); ar_orph_q: the core dropped it.
    logic          ar_q, ar_orph_q;
    logic [31:0]   ar_addr_q;
    logic [TW-1:0] rt_cnt;
    logic          rt_full, ar_fire, rd_take;
    assign m_arlen_o   = 8'd0;
    assign rt_full     = 32'(rt_cnt) >= MAX_OUT;
    // the core's request goes straight to AR unless an earlier one is still held
    assign m_arvalid_o = ar_q || (rd_req_i && !rt_full);
    assign m_araddr_o  = ar_q ? ar_addr_q : rd_addr_i;
    assign ar_fire     = m_arvalid_o && m_arready_i;
    assign rd_take     = rd_req_i && !rt_full && (!ar_q || !ar_orph_q);   // core request on AR now
    assign rd_gnt_o    = rd_take && m_arready_i;

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        ar_q <= 1'b0; ar_orph_q <= 1'b0; ar_addr_q <= '0;
      end else if (ar_fire) begin
        ar_q <= 1'b0; ar_orph_q <= 1'b0;
      end else if (m_arvalid_o) begin
        ar_q      <= 1'b1;                                   // not accepted: hold it
        ar_addr_q <= m_araddr_o;
        ar_orph_q <= ar_orph_q || !rd_req_i;                 // the core let go of it
      end
    end

    // read tags: 1 = the response belongs to the core
    logic rt_head, ar_tag;
    logic unused_rt1;
    assign ar_tag = !(ar_q && (ar_orph_q || !rd_req_i));
    tq_fifo #(.WIDTH(1), .DEPTH(MAX_OUT)) u_rtag (
        .clk_i, .rst_ni, .clear_i(1'b0),
        .push_i(ar_fire && !(m_rvalid_i && (rt_cnt == '0))), .data_i(ar_tag),
        .pop_i({1'b0, m_rvalid_i && (rt_cnt != '0)}),
        .head0_o(rt_head), .head1_o(unused_rt1), .count_o(rt_cnt));
    assign rd_rvalid_o = m_rvalid_i && ((rt_cnt != '0) ? rt_head : (ar_fire && ar_tag));
    assign rd_rdata_o  = m_rdata_i;
    assign rd_err      = (rd_rvalid_o && m_rresp_i[1]) || (m_rvalid_i && (rt_cnt == '0) && !ar_fire);
    assign rd_pend     = ar_q || (rt_cnt != '0);
  end else begin : g_rd_burst
    // Four-slot read burst buffer: each miss issues an aligned BURST_BEATS-beat INCR AR burst,
    // subsequent beats in the line grant immediately without an AR, and u_rord returns responses
    // in exact grant order as R beats arrive.
    localparam int NSLOTS = 4;
    localparam int SW     = $clog2(NSLOTS);
    localparam int LBW    = $clog2(BURST_BEATS);
    localparam int WLOG   = $clog2(W);
    localparam int LSW    = WLOG + LBW;
    localparam int TAGW   = 32 - LSW;

    localparam int CW     = LBW + 1;
    localparam int LDATAW = BURST_BEATS * VB;

    logic [TAGW-1:0]                req_tag;
    logic [LBW-1:0]                 req_idx, ord_idx, r_idx;
    logic [NSLOTS-1:0]              lv_q, l_fill_q, lmatch, lfree;
    logic [NSLOTS*TAGW-1:0]         ltag_q;
    logic [NSLOTS*CW-1:0]           lcnt_q;
    logic [NSLOTS*TW-1:0]           lref_q;
    logic [NSLOTS*BURST_BEATS-1:0]  lerr_q;
    logic [NSLOTS*LDATAW-1:0]       ldata_q;
    logic [SW-1:0]                  hit_slot, vic, ar_vic_q, alloc_slot, rr_vic_q;
    logic [SW-1:0]                  r_slot, ord_slot, gnt_slot, unused_rf1;
    logic [SW+LBW-1:0]              ord_head, unused_ord1;
    logic [$clog2(NSLOTS+1)-1:0]    rf_cnt;
    logic [TW-1:0]                  ord_cnt;
    logic                           hit, has_free, ord_full, ar_q, ar_orph_q, ar_fire;
    logic                           hit_gnt, miss_req, miss_take, r_last, ord_ready;
    logic                           p_rd_wait_q, drop_rd;
    logic [31:0]                    ar_addr_q, req_line_addr;

    assign req_tag       = rd_addr_i[31:LSW];
    assign req_idx       = rd_addr_i[LSW-1:WLOG];
    assign req_line_addr = {req_tag, {LSW{1'b0}}};
    assign drop_rd       = p_rd_wait_q && !rd_req_i;

    for (genvar s = 0; s < NSLOTS; s++) begin : g_slot
      assign lmatch[s] = lv_q[s] && (ltag_q[s*TAGW +: TAGW] == req_tag);
      assign lfree[s]  = !l_fill_q[s] && (lref_q[s*TW +: TW] == '0);
    end

    always_comb begin
      hit      = 1'b0;
      hit_slot = '0;
      for (int s = 0; s < NSLOTS; s++) begin
        if (lmatch[s]) begin
          hit      = 1'b1;
          hit_slot = SW'(s);
        end
      end
      has_free = 1'b0;
      vic      = rr_vic_q;
      for (int i = 0; i < NSLOTS; i++) begin
        logic [SW-1:0] idx;
        idx = rr_vic_q + SW'(i);
        if (!has_free && lfree[idx] && !lv_q[idx]) begin
          has_free = 1'b1;
          vic      = idx;
        end
      end
      for (int i = 0; i < NSLOTS; i++) begin
        logic [SW-1:0] idx;
        idx = rr_vic_q + SW'(i);
        if (!has_free && lfree[idx]) begin
          has_free = 1'b1;
          vic      = idx;
        end
      end
    end

    assign ord_full    = 32'(ord_cnt) >= MAX_OUT;
    assign hit_gnt     = rd_req_i && hit && !ord_full && !ar_q;
    assign miss_req    = rd_req_i && !hit && has_free && !ord_full && (32'(rf_cnt) < NSLOTS);
    assign alloc_slot  = ar_q ? ar_vic_q : vic;
    assign m_arlen_o   = 8'(BURST_BEATS - 1);
    assign m_arvalid_o = ar_q || miss_req;
    assign m_araddr_o  = ar_q ? ar_addr_q : req_line_addr;
    assign ar_fire     = m_arvalid_o && m_arready_i;
    assign miss_take   = (ar_q ? (!ar_orph_q && rd_req_i) : miss_req) && m_arready_i;
    assign rd_gnt_o    = hit_gnt || miss_take;
    assign gnt_slot    = hit_gnt ? hit_slot : alloc_slot;

    logic [CW-1:0] ord_slot_cnt;
    assign ord_slot_cnt = lcnt_q[32'(ord_slot) * CW +: CW];
    assign r_idx        = lcnt_q[32'(r_slot) * CW +: LBW];
    assign r_last       = (r_idx == LBW'(BURST_BEATS - 1));

    tq_fifo #(.WIDTH(SW), .DEPTH(NSLOTS)) u_rfifo (
        .clk_i, .rst_ni, .clear_i(1'b0),
        .push_i(ar_fire), .data_i(alloc_slot),
        .pop_i({1'b0, m_rvalid_i && (rf_cnt != '0) && r_last}),
        .head0_o(r_slot), .head1_o(unused_rf1), .count_o(rf_cnt));

    tq_fifo #(.WIDTH(SW + LBW), .DEPTH(MAX_OUT)) u_rord (
        .clk_i, .rst_ni, .clear_i(1'b0),
        .push_i(rd_gnt_o), .data_i({gnt_slot, req_idx}),
        .pop_i({1'b0, ord_ready}),
        .head0_o(ord_head), .head1_o(unused_ord1), .count_o(ord_cnt));

    assign {ord_slot, ord_idx} = ord_head;
    assign ord_ready   = (ord_cnt != '0) && (ord_slot_cnt > {1'b0, ord_idx});
    assign rd_rvalid_o = ord_ready;
    assign rd_rdata_o  = ldata_q[(32'(ord_slot) * BURST_BEATS + 32'(ord_idx)) * VB +: VB];
    assign rd_err      = (ord_ready && lerr_q[32'(ord_slot) * BURST_BEATS + 32'(ord_idx)])
                      || (m_rvalid_i && (rf_cnt == '0));
    assign rd_pend     = ar_q || (rf_cnt != '0) || (ord_cnt != '0);

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        lv_q <= '0; l_fill_q <= '0; ltag_q <= '0; lcnt_q <= '0;
        lref_q <= '0; lerr_q <= '0; ldata_q <= '0;
        ar_vic_q <= '0; rr_vic_q <= '0; ar_q <= 1'b0; ar_orph_q <= 1'b0;
        p_rd_wait_q <= 1'b0; ar_addr_q <= '0;
      end else begin
        p_rd_wait_q <= rd_req_i && !rd_gnt_o;

        if (ar_fire) begin
          ar_q      <= 1'b0;
          ar_orph_q <= 1'b0;
          rr_vic_q  <= alloc_slot + SW'(1);
        end else if (m_arvalid_o) begin
          if (!ar_q) ar_vic_q <= vic;
          ar_q      <= 1'b1;
          ar_addr_q <= m_araddr_o;
          ar_orph_q <= ar_orph_q || !rd_req_i;
        end

        for (int s = 0; s < NSLOTS; s++) begin
          logic inc_ref, dec_ref;
          inc_ref = rd_gnt_o && (gnt_slot == SW'(s));
          dec_ref = ord_ready && (ord_slot == SW'(s));
          lref_q[s*TW +: TW] <= lref_q[s*TW +: TW] + TW'(inc_ref) - TW'(dec_ref);

          if (ar_fire && (alloc_slot == SW'(s))) begin
            l_fill_q[s]            <= 1'b1;
            ltag_q[s*TAGW +: TAGW] <= ar_q ? ar_addr_q[31:LSW] : req_tag;
            lcnt_q[s*CW +: CW]     <= '0;
            lerr_q[s*BURST_BEATS +: BURST_BEATS] <= '0;
          end else if (m_rvalid_i && (rf_cnt != '0) && (r_slot == SW'(s))) begin
            ldata_q[(s*BURST_BEATS + 32'(r_idx))*VB +: VB] <= m_rdata_i;
            lerr_q[s*BURST_BEATS + 32'(r_idx)]             <= m_rresp_i[1];
            lcnt_q[s*CW +: CW]                             <= lcnt_q[s*CW +: CW] + 1'b1;
            if (r_last) l_fill_q[s] <= 1'b0;
          end

          if (drop_rd || flush_i) begin
            lv_q[s] <= 1'b0;
          end else if (ar_fire && (alloc_slot == SW'(s))) begin
            lv_q[s] <= miss_take && (req_idx != LBW'(BURST_BEATS - 1));
          end else begin
            if (wr_req_i && (ltag_q[s*TAGW +: TAGW] == wr_addr_i[31:LSW])) lv_q[s] <= 1'b0;
            if (hit_gnt && (hit_slot == SW'(s)) && (req_idx == LBW'(BURST_BEATS - 1))) begin
              lv_q[s] <= 1'b0;
            end
          end
        end
      end
    end

    /* verilator lint_off UNUSEDSIGNAL */
    logic unused_low;
    assign unused_low = ^{rd_addr_i[WLOG-1:0], wr_addr_i[LSW-1:0]};
    /* verilator lint_on UNUSEDSIGNAL */
  end

  // ---------------------------------------------------------------- write address + data
  // AW and W are separate handshakes; wr_gnt when both are done for the core's request.
  logic        aw_ok_q, w_ok_q, wv_q, w_orph_q;            // accepted parts / held / orphan
  logic [31:0] w_addr_q;
  logic [W-1:0] w_be_q;
  logic [VB-1:0] w_data_q;
  logic [TW-1:0] wt_cnt;
  logic        wt_full, aw_fire, w_fire, wr_take, wr_all, cur_v;
  logic [31:0] cur_addr;
  logic [W-1:0] cur_be;
  logic [VB-1:0] cur_data;
  assign wt_full  = 32'(wt_cnt) >= MAX_OUT;
  // the transaction on the bus: a held one, else the core's request
  assign cur_v    = wv_q || (wr_req_i && !wt_full);
  assign cur_addr = wv_q ? w_addr_q : wr_addr_i;
  assign cur_be   = wv_q ? w_be_q   : wr_be_i;
  assign cur_data = wv_q ? w_data_q : wr_wdata_i;
  assign m_awvalid_o = cur_v && !aw_ok_q;
  assign m_wvalid_o  = cur_v && !w_ok_q;
  assign m_awaddr_o  = cur_addr;
  assign m_wdata_o   = cur_data;
  assign m_wstrb_o   = cur_be;
  assign aw_fire  = m_awvalid_o && m_awready_i;
  assign w_fire   = m_wvalid_o && m_wready_i;
  assign wr_all   = cur_v && (aw_ok_q || m_awready_i) && (w_ok_q || m_wready_i);   // both parts done
  assign wr_take  = wr_req_i && !wt_full && (!wv_q || !w_orph_q);
  assign wr_gnt_o = wr_take && wr_all;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      aw_ok_q <= 1'b0; w_ok_q <= 1'b0; wv_q <= 1'b0; w_orph_q <= 1'b0;
      w_addr_q <= '0; w_be_q <= '0; w_data_q <= '0;
    end else if (wr_all) begin
      aw_ok_q <= 1'b0; w_ok_q <= 1'b0; wv_q <= 1'b0; w_orph_q <= 1'b0;
    end else if (cur_v) begin
      aw_ok_q  <= aw_ok_q || aw_fire;
      w_ok_q   <= w_ok_q || w_fire;
      wv_q     <= 1'b1;
      w_addr_q <= cur_addr; w_be_q <= cur_be; w_data_q <= cur_data;
      w_orph_q <= w_orph_q || !wr_req_i;
    end
  end

  // write tags: one per completed AW + W pair, 1 = the B belongs to the core
  logic wt_head, w_tag;
  logic unused_wt1;
  assign w_tag = !(wv_q && (w_orph_q || !wr_req_i));
  tq_fifo #(.WIDTH(1), .DEPTH(MAX_OUT)) u_wtag (
      .clk_i, .rst_ni, .clear_i(1'b0),
      .push_i(wr_all && !(m_bvalid_i && (wt_cnt == '0))), .data_i(w_tag),
      .pop_i({1'b0, m_bvalid_i && (wt_cnt != '0)}),
      .head0_o(wt_head), .head1_o(unused_wt1), .count_o(wt_cnt));
  assign wr_done_o = m_bvalid_i && ((wt_cnt != '0) ? wt_head : (wr_all && w_tag));
  assign bus_err_o = rd_err || (wr_done_o && m_bresp_i[1]) || (m_bvalid_i && (wt_cnt == '0) && !wr_all);
  assign pending_o = rd_pend || wv_q || (wt_cnt != '0);

  /* verilator lint_off UNUSEDSIGNAL */
  logic unused;
  assign unused = ^{m_rresp_i[0], m_rlast_i, m_bresp_i[0], flush_i};
  /* verilator lint_on UNUSEDSIGNAL */

endmodule
