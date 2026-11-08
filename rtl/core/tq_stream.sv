// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Stream engine and memory-port arbitration (spec 5.1, 5.2, 6.3).
// Model: TriQueueCore._read_candidate / _issue_read / step_port (model/tri_queue/core.py).
//
// Two panel walkers (A, B) share one read port, alternating round-robin. A read is issued only
// when the destination FIFO has room for it after everything already in flight ("credits"),
// so read responses never need backpressure. Responses return in order; a tag FIFO records
// which operand FIFO each one belongs to.
//
// clear (START / ABORT) can arrive with reads in flight. The memory still answers them, so the
// number outstanding is kept in drop_q: those responses are discarded, and no new read is issued
// until they are all back (found by the core-level formal check, verif/formal/tq_core.sby).
//
// OBI request stability: a request that is not granted is held unchanged until it is. A held
// read keeps its stream (no re-arbitration, no panel loads); on a shared port a held read or
// write keeps the port. clear (START / ABORT) drops a held request.

module tq_stream #(
    parameter int T           = 8,
    parameter int FIFO_DEPTH  = 8,
    parameter int SCMD_DEPTH  = 2,
    parameter bit SHARED_PORT = 1'b0,
    parameter bit CLK_GATE    = 1'b1,
    localparam int W   = 2 * T,
    localparam int VB  = W * 8,
    localparam int L2T = $clog2(T),
    localparam int CRW = $clog2(FIFO_DEPTH + 1),
    localparam int IFW = $clog2(2 * FIFO_DEPTH + 1)
) (
    input  logic          clk_i,
    input  logic          rst_ni,
    input  logic          clear_i,
    input  logic          run_i,           // 0: no new requests (stopped by an error until START)
    // stream commands from the front end
    input  logic          scmd_a_push_i,
    input  tq_pkg::scmd_t scmd_a_i,
    input  logic          scmd_b_push_i,
    input  tq_pkg::scmd_t scmd_b_i,
    output logic          scmd_a_full_o,   // no room after this cycle's pop
    output logic          scmd_b_full_o,
    // operand FIFO pops by the MAC unit this cycle (vectors)
    input  logic [1:0]    qa_pop_i,
    input  logic [1:0]    qb_pop_i,
    // drain write request (shared-port arbitration)
    input  logic          wr_pending_i,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic          swap_wait_i,     // SWAP_C is waiting for the drain: writes win (shared port)
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic          wr_gnt_i,
    output logic          wr_req_o,        // drain write request after arbitration (never depends on gnt)
    output logic          wr_fire_o,
    // read port
    output logic          rd_req_o,
    output logic [31:0]   rd_addr_o,
    input  logic          rd_gnt_i,
    input  logic          rd_rvalid_i,
    input  logic [VB-1:0] rd_rdata_i,
    output logic          rd_fire_o,
    // operand FIFO pushes
    output logic          qa_push_o,
    output logic          qb_push_o,
    output logic [VB-1:0] q_data_o,
    // status
    output logic          idle_o,          // no commands, panels or reads outstanding after this cycle
    output logic          align_err_o
);
  // ---------------------------------------------------------------- command queues
  tq_pkg::scmd_t ha, hb, unused_a1, unused_b1;
  logic [$clog2(SCMD_DEPTH+1)-1:0] cnt_a, cnt_b;
  logic load_a, load_b;

  tq_fifo #(.WIDTH(tq_pkg::SCMD_W), .DEPTH(SCMD_DEPTH), .CLK_GATE(CLK_GATE)) u_scmd_a (
      .clk_i, .rst_ni, .clear_i,
      .push_i(scmd_a_push_i), .data_i(scmd_a_i), .pop_i({1'b0, load_a}),
      .head0_o(ha), .head1_o(unused_a1), .count_o(cnt_a));
  tq_fifo #(.WIDTH(tq_pkg::SCMD_W), .DEPTH(SCMD_DEPTH), .CLK_GATE(CLK_GATE)) u_scmd_b (
      .clk_i, .rst_ni, .clear_i,
      .push_i(scmd_b_push_i), .data_i(scmd_b_i), .pop_i({1'b0, load_b}),
      .head0_o(hb), .head1_o(unused_b1), .count_o(cnt_b));

  assign scmd_a_full_o = (32'(cnt_a) - 32'(load_a)) >= SCMD_DEPTH;
  assign scmd_b_full_o = (32'(cnt_b) - 32'(load_b)) >= SCMD_DEPTH;

  // ---------------------------------------------------------------- panel walkers
  logic        act_a_q, act_b_q, rr_q;               // rr_q: 0 = A first, 1 = B first
  logic [31:0] addr_a_q, addr_b_q, vecs_a_q, vecs_b_q;
  logic [CRW-1:0] cred_a_q, cred_b_q;                // FIFO occupancy + reads in flight
  logic [IFW-1:0] infl_q, drop_q;                   // reads in flight; stale reads to discard
  logic           drop;
  assign drop = rd_rvalid_i && (drop_q != 0);
  logic           rd_hold_q, rd_hold_b_q;            // read request held for gnt, and its stream
  /* verilator lint_off UNUSEDSIGNAL */
  logic           wr_hold_q;                         // write request held for gnt (shared port only)
  /* verilator lint_on UNUSEDSIGNAL */

  logic avail_a, avail_b, space_a, space_b, cand_a, cand_b;
  // a zero-length panel (K = 0) is loaded and completes at once, without a read
  assign avail_a = act_a_q || (cnt_a != 0 && ha.vecs != 0);
  assign avail_b = act_b_q || (cnt_b != 0 && hb.vecs != 0);
  assign space_a = (32'(cred_a_q) - 32'(qa_pop_i)) < FIFO_DEPTH;
  assign space_b = (32'(cred_b_q) - 32'(qb_pop_i)) < FIFO_DEPTH;
  assign cand_a  = avail_a && space_a && (drop_q == 0);
  assign cand_b  = avail_b && space_b && (drop_q == 0);

  // the first stream in round-robin order is always evaluated; the second only if the first
  // is not a candidate (model: the loop in _read_candidate returns early)
  logic sel_valid, sel_b, cand_first;
  assign cand_first = rr_q ? cand_b : cand_a;
  assign sel_valid  = rd_hold_q || cand_a || cand_b;
  assign sel_b      = rd_hold_q ? rd_hold_b_q : rr_q ? cand_b : !cand_a;
  assign load_a     = !rd_hold_q && !act_a_q && (cnt_a != 0) && (!rr_q || !cand_first);
  assign load_b     = !rd_hold_q && !act_b_q && (cnt_b != 0) && ( rr_q || !cand_first);

  logic [31:0] cur_addr_a, cur_addr_b, cur_vecs_a, cur_vecs_b;
  assign cur_addr_a = act_a_q ? addr_a_q : ha.addr;
  assign cur_vecs_a = act_a_q ? vecs_a_q : ha.vecs;
  assign cur_addr_b = act_b_q ? addr_b_q : hb.addr;
  assign cur_vecs_b = act_b_q ? vecs_b_q : hb.vecs;

  // ---------------------------------------------------------------- port arbitration
  // Shared port: the drain write wins when SWAP_C waits on it or no read is ready (model: step_port).
  logic rd_want;
  if (SHARED_PORT) begin : g_shared
    assign wr_req_o = run_i && wr_pending_i && (wr_hold_q || (!rd_hold_q && (swap_wait_i || !sel_valid)));
    assign rd_want  = run_i && sel_valid && !wr_req_o;
  end else begin : g_split
    assign wr_req_o = run_i && wr_pending_i;
    assign rd_want  = run_i && sel_valid;
  end
  assign wr_fire_o = wr_req_o && wr_gnt_i;
  assign rd_req_o  = rd_want;
  assign rd_addr_o = sel_b ? cur_addr_b : cur_addr_a;
  assign rd_fire_o = rd_want && rd_gnt_i;

  logic fire_a, fire_b;
  assign fire_a = rd_fire_o && !sel_b;
  assign fire_b = rd_fire_o && sel_b;

  // ---------------------------------------------------------------- response routing
  // A zero-latency memory answers in the request cycle: the response then bypasses the tag FIFO.
  logic tag, tag_head, unused_tag1, bypass;
  logic [$clog2(2*FIFO_DEPTH+1)-1:0] tag_cnt;
  assign bypass = (tag_cnt == 0) && (drop_q == 0);
  assign tag    = bypass ? sel_b : tag_head;
  tq_fifo #(.WIDTH(1), .DEPTH(2 * FIFO_DEPTH)) u_tag (
      .clk_i, .rst_ni, .clear_i,
      .push_i(rd_fire_o && !(bypass && rd_rvalid_i)), .data_i(sel_b),
      .pop_i({1'b0, rd_rvalid_i && !bypass && !drop}),
      .head0_o(tag_head), .head1_o(unused_tag1), .count_o(tag_cnt));

  assign qa_push_o = rd_rvalid_i && !drop && !tag;
  assign qb_push_o = rd_rvalid_i && !drop && tag;
  assign q_data_o  = rd_rdata_i;

  // ---------------------------------------------------------------- state update
  logic act_a_n, act_b_n;
  always_comb begin
    act_a_n = act_a_q || (load_a && ha.vecs != 0);
    act_b_n = act_b_q || (load_b && hb.vecs != 0);
    if (fire_a && cur_vecs_a == 32'd1) act_a_n = 1'b0;
    if (fire_b && cur_vecs_b == 32'd1) act_b_n = 1'b0;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      act_a_q <= 1'b0; act_b_q <= 1'b0; rr_q <= 1'b0;
      addr_a_q <= '0; addr_b_q <= '0; vecs_a_q <= '0; vecs_b_q <= '0;
      cred_a_q <= '0; cred_b_q <= '0; infl_q <= '0; drop_q <= '0;
      rd_hold_q <= 1'b0; rd_hold_b_q <= 1'b0; wr_hold_q <= 1'b0;
    end else if (clear_i) begin
      act_a_q <= 1'b0; act_b_q <= 1'b0; rr_q <= 1'b0;
      rd_hold_q <= 1'b0; wr_hold_q <= 1'b0;
      cred_a_q <= '0; cred_b_q <= '0; infl_q <= '0;
      // everything still outstanding after this cycle (reads are blocked while drop_q != 0, so
      // drop_q and infl_q are never both non-zero)
      drop_q <= drop_q + infl_q + IFW'(rd_fire_o) - IFW'(rd_rvalid_i);
    end else begin
      act_a_q <= act_a_n;
      act_b_q <= act_b_n;
      if (load_a || fire_a) begin
        addr_a_q <= fire_a ? cur_addr_a + 32'(W) : cur_addr_a;
        vecs_a_q <= fire_a ? cur_vecs_a - 32'd1  : cur_vecs_a;
      end
      if (load_b || fire_b) begin
        addr_b_q <= fire_b ? cur_addr_b + 32'(W) : cur_addr_b;
        vecs_b_q <= fire_b ? cur_vecs_b - 32'd1  : cur_vecs_b;
      end
      if (rd_fire_o) rr_q <= !sel_b;
      rd_hold_q   <= rd_req_o && !rd_gnt_i;
      rd_hold_b_q <= sel_b;
      wr_hold_q   <= wr_req_o && !wr_gnt_i;
      cred_a_q <= cred_a_q - CRW'(qa_pop_i) + CRW'(fire_a);
      cred_b_q <= cred_b_q - CRW'(qb_pop_i) + CRW'(fire_b);
      infl_q   <= infl_q + IFW'(rd_fire_o) - IFW'(rd_rvalid_i && !drop);
      drop_q   <= drop_q - IFW'(drop);
    end
  end

  assign idle_o = (32'(cnt_a) - 32'(load_a) == 0) && (32'(cnt_b) - 32'(load_b) == 0)
               && !act_a_n && !act_b_n && (infl_q == 0) && (drop_q == 0) && !rd_fire_o;
`ifdef FORMAL
  // OBI: a request without gnt stays asserted with the same address next cycle (unless cleared,
  // or the run stopped on an error)
  logic past_ok_q, past_rd_q, past_wr_q;
  logic [31:0] past_addr_q;
  always_ff @(posedge clk_i) begin
    past_ok_q   <= rst_ni && !clear_i;
    past_rd_q   <= rd_req_o && !rd_gnt_i;
    past_wr_q   <= wr_req_o && !wr_gnt_i;
    past_addr_q <= rd_addr_o;
  end
  always_comb begin
    if (rst_ni && !clear_i && run_i && past_ok_q) begin
      if (past_rd_q) assert (rd_req_o && rd_addr_o == past_addr_q);
      if (past_wr_q) assert (wr_req_o);
    end
  end
`endif

  assign align_err_o = (load_a && ha.addr[L2T:0] != '0) || (load_b && hb.addr[L2T:0] != '0);

endmodule
