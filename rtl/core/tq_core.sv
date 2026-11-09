// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Tri-Queue core: front end, MAC command queue, operand FIFOs, stream engine, MAC array, drain.
// Spec: docs/specification.md. Golden model: model/tri_queue/core.py.
//
// The core matches the cycle model cycle for cycle when the memory always grants and returns
// read data with a fixed latency (model mem_latency = SRAM latency + 1). The order of evaluation
// inside a cycle follows the model: memory responses land -> MAC unit -> memory port(s) -> front end,
// so later stages see the earlier stages' pops in the same cycle.
//
// Interfaces: spec 6.4 (host, via tq_regs) and 6.3 (memory: OBI-style, W = 2T bytes).

module tq_core #(
    parameter int T           = 8,     // tile: T x T, T*T multipliers (power of two, >= 4)
    parameter int FIFO_DEPTH  = 8,     // QA / QB depth in port words
    parameter int MACQ_DEPTH  = 4,     // MAC command queue
    parameter int SCMD_DEPTH  = 2,     // stream command queues
    parameter bit SHARED_PORT = 1'b0,  // drain writes share the read port
    parameter int PROG_BYTES  = 64,
    parameter int ACC_FORM    = tq_pkg::ACC_CPA,  // accumulator form (tq_mac_array, spec 10.2)
    parameter bit CLK_GATE    = 1'b1,  // clock gates on the wide register groups (tq_cg)
    parameter bit WR_ACK      = 1'b0,  // 1: a write completes at wr_done_i, not at its grant (AXI)
    parameter int WR_OUT_MAX  = 15,    // WR_ACK: most writes granted and not yet complete
    parameter int PANEL_BYTES = 0,     // A panel buffer (tq_stream, spec 10.9); 0 = none
    parameter int MAC_PIPE    = 2,     // MAC stages (tq_mac_array): 2, or 1 without the product register
    localparam int W  = 2 * T,
    localparam int VB = W * 8
) (
    input  logic                    clk_i,
    input  logic                    rst_ni,
    // control
    input  logic                    start_i,     // ignored while busy
    input  logic                    abort_i,
    input  logic [PROG_BYTES*8-1:0] prog_i,
    output logic                    busy_o,
    output logic                    done_o,      // pulse when a run ends (normally or on error)
    output logic                    error_o,     // sticky until the next start
    output logic [3:0]              err_code_o,
    output logic [8*32-1:0]         cfg_o,       // configuration registers, CFG[0] in the low bits
    // performance counters (cleared at start)
    output logic [31:0]             perf_cycles_o,
    output logic [31:0]             perf_instret_o,
    output logic [31:0]             perf_beats_o,
    output logic [31:0]             perf_reads_o,
    output logic [31:0]             perf_writes_o,
    // read port
    output logic                    rd_req_o,
    output logic [31:0]             rd_addr_o,
    input  logic                    rd_gnt_i,
    input  logic                    rd_rvalid_i,
    input  logic [VB-1:0]           rd_rdata_i,
    // write port
    output logic                    wr_req_o,
    output logic [31:0]             wr_addr_o,
    output logic [W-1:0]            wr_be_o,
    output logic [VB-1:0]           wr_wdata_o,
    input  logic                    wr_gnt_i,
    input  logic                    wr_done_i    // WR_ACK: one pulse per completed write, in order
);
  // ---------------------------------------------------------------- run control
  logic running_q, err_q;
  logic [3:0] err_code_q;
  logic clear, start;
  assign start = start_i && !running_q;
  assign clear = start || abort_i;

  // ---------------------------------------------------------------- front end
  logic          macq_push, scmd_a_push, scmd_b_push, macq_full, scmd_a_full, scmd_b_full;
  tq_pkg::mcmd_t macq_in;
  tq_pkg::scmd_t scmd_a_in, scmd_b_in;
  logic [31:0]   a_base, b_base, c_base, c_ld, cfg_m, cfg_n, cfg_k, mode;
  logic          fence, retire, fe_err;
  logic [3:0]    fe_err_code;

  tq_frontend #(.T(T), .PROG_BYTES(PROG_BYTES), .CLK_GATE(CLK_GATE)) u_fe (
      .clk_i, .rst_ni, .start_i(clear), .run_i(running_q), .prog_i,
      .macq_full_i(macq_full), .scmd_a_full_i(scmd_a_full), .scmd_b_full_i(scmd_b_full),
      .macq_push_o(macq_push), .macq_data_o(macq_in),
      .scmd_a_push_o(scmd_a_push), .scmd_a_data_o(scmd_a_in),
      .scmd_b_push_o(scmd_b_push), .scmd_b_data_o(scmd_b_in),
      .cfg_a_base_o(a_base), .cfg_b_base_o(b_base), .cfg_c_base_o(c_base), .cfg_c_ld_o(c_ld),
      .cfg_m_o(cfg_m), .cfg_n_o(cfg_n), .cfg_k_o(cfg_k), .cfg_mode_o(mode),
      .fence_o(fence), .retire_o(retire), .error_o(fe_err), .err_code_o(fe_err_code));

  assign cfg_o = {mode, cfg_n, cfg_m, cfg_k, c_ld, c_base, b_base, a_base};

  // ---------------------------------------------------------------- MAC command queue
  localparam int MCW = $clog2(MACQ_DEPTH + 1);
  tq_pkg::mcmd_t e0;
  /* verilator lint_off UNUSEDSIGNAL */
  tq_pkg::mcmd_t e1;                              // only kind and shape are used
  /* verilator lint_on UNUSEDSIGNAL */
  logic [MCW-1:0] macq_cnt;
  logic [1:0]     macq_pop;

  tq_fifo #(.WIDTH(tq_pkg::MCMD_W), .DEPTH(MACQ_DEPTH), .CLK_GATE(CLK_GATE)) u_macq (
      .clk_i, .rst_ni, .clear_i(clear),
      .push_i(macq_push), .data_i(macq_in), .pop_i(macq_pop),
      .head0_o(e0), .head1_o(e1), .count_o(macq_cnt));
  assign macq_full = (32'(macq_cnt) - 32'(macq_pop)) >= MACQ_DEPTH;

  // ---------------------------------------------------------------- operand FIFOs
  localparam int QCW = $clog2(FIFO_DEPTH + 1);
  logic          qa_push, qb_push;
  logic [VB-1:0] q_data, qa_data, qa0, qa1, qb0, qb1;
  logic [QCW-1:0] qa_cnt, qb_cnt;
  logic [1:0]    qa_pop, qb_pop;

  tq_fifo #(.WIDTH(VB), .DEPTH(FIFO_DEPTH), .CLK_GATE(CLK_GATE)) u_qa (
      .clk_i, .rst_ni, .clear_i(clear), .push_i(qa_push), .data_i(qa_data), .pop_i(qa_pop),
      .head0_o(qa0), .head1_o(qa1), .count_o(qa_cnt));
  tq_fifo #(.WIDTH(VB), .DEPTH(FIFO_DEPTH), .CLK_GATE(CLK_GATE)) u_qb (
      .clk_i, .rst_ni, .clear_i(clear), .push_i(qb_push), .data_i(q_data), .pop_i(qb_pop),
      .head0_o(qb0), .head1_o(qb1), .count_o(qb_cnt));

  // ---------------------------------------------------------------- MAC unit control
  // Per cycle: finish beat 1 of the current VOUTER_MAC, or consume at most one ZERO_C/SWAP_C and
  // then start at most one VOUTER_MAC whose operands are all present (model: step_mac).
  logic          active_q;                        // bank being accumulated
  logic          b1_q, b1_rf_q, b1_cf_q, b1_bank_q;
  logic [VB-1:0] opa_q, opb_q;
  logic          drain_act, drain_act_next;

  logic               has0, has1, is_zero, is_swap, ctl0, swap_wait, zero_wait, mac_start;
  tq_pkg::mcmd_kind_e mc_kind;
  logic               mc_rf, mc_cf, mc_has, e0_ctl, sh_rf, sh_cf;
  logic [1:0]         na, nb;
  logic               active_n;

  always_comb begin
    has0      = macq_cnt >= MCW'(1);
    has1      = macq_cnt >= MCW'(2);
    // ZERO_C clears both banks, so like SWAP_C it waits while a drain reads one
    is_zero   = !b1_q && has0 && e0.kind == tq_pkg::MC_ZERO && !drain_act;
    zero_wait = !b1_q && has0 && e0.kind == tq_pkg::MC_ZERO && drain_act;
    is_swap   = !b1_q && has0 && e0.kind == tq_pkg::MC_SWAP && !drain_act;
    swap_wait = !b1_q && has0 && e0.kind == tq_pkg::MC_SWAP && drain_act;
    ctl0      = is_zero || is_swap;
    mc_kind   = ctl0 ? e1.kind  : e0.kind;
    mc_rf     = ctl0 ? e1.rfull : e0.rfull;
    mc_cf     = ctl0 ? e1.cfull : e0.cfull;
    mc_has    = ctl0 ? has1 : has0;
    na        = mc_cf ? 2'd1 : 2'd2;              // A vectors: 2*d*r bytes / 2T
    nb        = mc_rf ? 2'd1 : 2'd2;
    mac_start = !b1_q && !swap_wait && mc_has && mc_kind == tq_pkg::MC_MAC
             && 32'(qa_cnt) >= 32'(na) && 32'(qb_cnt) >= 32'(nb);
    macq_pop  = 2'(ctl0) + 2'(mac_start);
    qa_pop    = mac_start ? na : 2'd0;
    qb_pop    = mac_start ? nb : 2'd0;
    active_n  = is_swap ? !active_q : active_q;
    // Shape of a starting VOUTER_MAC for the operand select. It only matters when mac_start, and
    // then the MAC is e1 exactly when e0 is a control command, so the select needs only e0's
    // kind, not ctl0 (has0, b1_q, drain_act): this keeps ctl0 off the multiplier's input path.
    e0_ctl    = e0.kind != tq_pkg::MC_MAC;
    sh_rf     = e0_ctl ? e1.rfull : e0.rfull;
    sh_cf     = e0_ctl ? e1.cfull : e0.cfull;
  end

  logic beat, beat_rf, beat_cf, beat_bank;
  logic [VB-1:0] beat_a, beat_b;
  assign beat      = b1_q || mac_start;
  assign beat_rf   = b1_q ? b1_rf_q   : sh_rf;
  assign beat_cf   = b1_q ? b1_cf_q   : sh_cf;
  assign beat_bank = b1_q ? b1_bank_q : active_n;
  assign beat_a    = b1_q ? opa_q : qa0;
  assign beat_b    = b1_q ? opb_q : qb0;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      active_q <= 1'b0; b1_q <= 1'b0; b1_rf_q <= 1'b0; b1_cf_q <= 1'b0; b1_bank_q <= 1'b0;
    end else if (clear) begin
      active_q <= 1'b0; b1_q <= 1'b0;
    end else begin
      active_q <= active_n;
      b1_q     <= mac_start;
      if (mac_start) begin
        b1_rf_q   <= mc_rf;
        b1_cf_q   <= mc_cf;
        b1_bank_q <= active_n;
      end
    end
  end

  // beat-1 operands: pre-select the second beat's VB-bit slice when a VOUTER_MAC starts
  logic gclk_op;
  tq_cg #(.EN(CLK_GATE)) u_cg_op (.clk_i, .en_i(mac_start), .gclk_o(gclk_op));
  always_ff @(posedge gclk_op) begin
    if (mac_start || CLK_GATE) begin
      opa_q <= {qa1[VB-1 : VB/2], sh_cf ? qa0[VB-1 : VB/2] : qa1[VB/2-1 : 0]};
      opb_q <= {qb1[VB-1 : VB/2], sh_rf ? qb0[VB-1 : VB/2] : qb1[VB/2-1 : 0]};
    end
  end

  // ---------------------------------------------------------------- MAC array
  logic             prod_valid, prod_bank, clear_bank, clear_bank_sel, rd_bank, rd_half;
  logic [$clog2(T)-1:0] rd_row;
  logic [T/2*32-1:0] rd_val;

  tq_mac_array #(.T(T), .ACC_FORM(ACC_FORM), .CLK_GATE(CLK_GATE), .MAC_PIPE(MAC_PIPE)) u_array (
      .clk_i, .rst_ni,
      .beat_i(beat), .rfull_i(beat_rf), .cfull_i(beat_cf),
      .bank_i(beat_bank), .a_i(beat_a), .b_i(beat_b),
      .a_signed_i(mode[0]), .b_signed_i(mode[1]),
      .clear_all_i(is_zero || clear), .clear_bank_i(clear_bank), .clear_bank_sel_i(clear_bank_sel),
      .rd_bank_i(rd_bank), .rd_row_i(rd_row), .rd_half_i(rd_half), .rd_val_o(rd_val),
      .prod_valid_o(prod_valid), .prod_bank_o(prod_bank));

  // ---------------------------------------------------------------- drain
  logic wr_pending, wr_fire, drain_align_err;
  tq_drain #(.T(T), .CLK_GATE(CLK_GATE)) u_drain (
      .clk_i, .rst_ni, .clear_i(clear),
      .start_i(is_swap), .start_bank_i(active_q), .c_addr_i(e0.c_addr),
      .rows_i(e0.rows), .cols_i(e0.cols), .c_ld_i(c_ld),
      .prod_valid_i(prod_valid), .prod_bank_i(prod_bank),
      .rd_bank_o(rd_bank), .rd_row_o(rd_row), .rd_half_o(rd_half), .rd_val_i(rd_val),
      .wr_valid_o(wr_pending), .wr_addr_o(wr_addr_o), .wr_be_o(wr_be_o), .wr_data_o(wr_wdata_o),
      .wr_fire_i(wr_fire),
      .clear_bank_o(clear_bank), .clear_bank_sel_o(clear_bank_sel),
      .active_o(drain_act), .active_next_o(drain_act_next), .align_err_o(drain_align_err));

  // ---------------------------------------------------------------- write completion
  // With WR_ACK, a granted write stays outstanding until its wr_done_i pulse (same cycle or
  // later). FENCE waits for none outstanding, and no new write is requested while WR_OUT_MAX
  // are; a request already waiting for gnt is held (OBI). Not cleared by START / ABORT: writes
  // in flight still complete. Model: wr_ack, wr_resp_latency, wr_out_max.
  localparam int OW = $clog2(WR_OUT_MAX + 2);
  logic [OW-1:0] wr_out_q, wr_out_n;
  logic          wr_hold_q, wr_room, wr_pending_room;
  assign wr_room         = !WR_ACK || wr_hold_q || 32'(wr_out_q) < WR_OUT_MAX;
  assign wr_pending_room = wr_pending && wr_room;
  assign wr_out_n        = WR_ACK ? wr_out_q + OW'(wr_fire) - OW'(wr_done_i) : '0;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      wr_out_q <= '0; wr_hold_q <= 1'b0;
    end else begin
      wr_out_q  <= wr_out_n;
      wr_hold_q <= wr_req_o && !wr_gnt_i;
    end
  end

  // ---------------------------------------------------------------- stream engine + ports
  logic stream_idle, stream_align_err, rd_fire, wr_req;
  tq_stream #(.T(T), .FIFO_DEPTH(FIFO_DEPTH), .SCMD_DEPTH(SCMD_DEPTH), .SHARED_PORT(SHARED_PORT),
              .CLK_GATE(CLK_GATE), .PANEL_BYTES(PANEL_BYTES)) u_stream (
      .clk_i, .rst_ni, .clear_i(clear), .run_i(running_q),
      .scmd_a_push_i(scmd_a_push), .scmd_a_i(scmd_a_in),
      .scmd_b_push_i(scmd_b_push), .scmd_b_i(scmd_b_in),
      .scmd_a_full_o(scmd_a_full), .scmd_b_full_o(scmd_b_full),
      .qa_pop_i(qa_pop), .qb_pop_i(qb_pop),
      .wr_pending_i(wr_pending_room), .swap_wait_i(swap_wait || zero_wait), .wr_gnt_i, .wr_req_o(wr_req), .wr_fire_o(wr_fire),
      .rd_req_o, .rd_addr_o, .rd_gnt_i, .rd_rvalid_i, .rd_rdata_i, .rd_fire_o(rd_fire),
      .qa_push_o(qa_push), .qb_push_o(qb_push), .q_data_o(q_data), .qa_data_o(qa_data),
      .idle_o(stream_idle), .align_err_o(stream_align_err));

  assign wr_req_o = wr_req;

  // ---------------------------------------------------------------- halt and errors
  // FENCE completes when, after this cycle's MAC and port activity, nothing is queued, streaming,
  // in flight, mid-instruction or draining (model: all_idle, evaluated in the front-end stage).
  logic idle, halt, err_now;
  logic [3:0] err_now_code;
  assign idle = (32'(macq_cnt) - 32'(macq_pop) == 0) && !mac_start && stream_idle && !drain_act_next
             && wr_out_n == '0;
  assign halt = running_q && fence && idle;

  always_comb begin
    err_now      = 1'b0;
    err_now_code = tq_pkg::ERR_NONE;
    if (running_q) begin
      if (fe_err) begin
        err_now = 1'b1; err_now_code = fe_err_code;
      end else if (drain_align_err || stream_align_err) begin
        err_now = 1'b1; err_now_code = tq_pkg::ERR_ALIGN;
      end
    end
  end

  logic done_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      running_q <= 1'b0; err_q <= 1'b0; err_code_q <= '0; done_q <= 1'b0;
    end else begin
      done_q <= 1'b0;
      if (start) begin
        running_q <= 1'b1; err_q <= 1'b0; err_code_q <= '0;
      end else if (abort_i) begin
        running_q <= 1'b0;
      end else if (running_q && (halt || err_now)) begin
        running_q <= 1'b0;
        done_q    <= 1'b1;
        if (err_now) begin
          err_q <= 1'b1; err_code_q <= err_now_code;
        end
      end
    end
  end

  assign busy_o     = running_q;
  assign done_o     = done_q;
  assign error_o    = err_q;
  assign err_code_o = err_code_q;

  // ---------------------------------------------------------------- performance counters
  logic [31:0] pc_cyc_q, pc_ret_q, pc_beat_q, pc_rd_q, pc_wr_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      pc_cyc_q <= '0; pc_ret_q <= '0; pc_beat_q <= '0; pc_rd_q <= '0; pc_wr_q <= '0;
    end else if (start) begin
      pc_cyc_q <= '0; pc_ret_q <= '0; pc_beat_q <= '0; pc_rd_q <= '0; pc_wr_q <= '0;
    end else if (running_q) begin
      pc_cyc_q  <= pc_cyc_q  + 32'd1;
      pc_ret_q  <= pc_ret_q  + 32'(retire);
      pc_beat_q <= pc_beat_q + 32'(beat);
      pc_rd_q   <= pc_rd_q   + 32'(rd_fire);
      pc_wr_q   <= pc_wr_q   + 32'(wr_fire);
    end
  end
  assign perf_cycles_o  = pc_cyc_q;
  assign perf_instret_o = pc_ret_q;
  assign perf_beats_o   = pc_beat_q;
  assign perf_reads_o   = pc_rd_q;
  assign perf_writes_o  = pc_wr_q;

`ifndef SYNTHESIS
  // ---------------------------------------------------------------- simulation checks
  always_comb begin
    if (rst_ni && running_q) begin
      // an accumulate never targets the bank being drained
      assert (!(prod_valid && drain_act && prod_bank == rd_bank))
        else $error("accumulate into draining bank");
      // FENCE leaves no operands behind (model: assert in step_front)
      if (halt) assert (qa_cnt == 0 && qb_cnt == 0) else $error("operands left in FIFOs at FENCE");
      // ZERO_C only while nothing drains (model: assert in step_mac)
      if (is_zero) assert (!drain_act) else $error("ZERO_C during a drain");
    end
  end
  // write responses, checked at the clock edge (inputs may change between edges)
  /* verilator lint_off SYNCASYNCNET */
  always_ff @(posedge clk_i) begin
    if (rst_ni && WR_ACK) begin
      assert (!(wr_done_i && 32'(wr_out_q) + 32'(wr_fire) == 0)) else $error("wr_done without a write");
      assert (32'(wr_out_n) <= WR_OUT_MAX) else $error("more than WR_OUT_MAX writes outstanding");
    end
  end
  /* verilator lint_on SYNCASYNCNET */
`endif

`ifdef FORMAL
  // ---------------------------------------------------------------- formal (verif/formal/tq_core.sby)
  // For any program: an accumulate never targets the bank being drained.
  always_comb begin
    if (rst_ni && running_q) assert (!(prod_valid && drain_act && prod_bank == rd_bank));
  end
  // A request dropped at START / ABORT is followed by a cycle without requests, so an adapter
  // that must finish a dropped transaction (tq_axi_mem) can tell it from a new one.
  logic past_clear_q = 1'b0;
  always_ff @(posedge clk_i) past_clear_q <= rst_ni && clear;
  always_comb begin
    if (rst_ni && past_clear_q) assert (!rd_req_o && !wr_req_o);
  end
`endif

endmodule
