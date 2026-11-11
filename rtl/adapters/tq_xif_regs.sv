// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// CV-X-IF coprocessor adapter to the Tri-Queue register port (spec 6.4): two custom
// instructions give a CVA6 / CV32E40X-class core register access without a bus.
//
//   tq.wr rs1, rs2    register[rs1[11:0]] = rs2         (R-type, funct3 = 0, no rd)
//   tq.rd rd, rs1     rd = register[rs1[11:0]]          (R-type, funct3 = 1)
//
// Both use major opcode OPCODE (custom-0 by default) and funct7 = FUNCT7; any other
// instruction is not accepted. Signal names follow the CV-X-IF issue, register, commit and
// result interfaces, flattened to the fields this adapter uses (connect the struct fields of
// the core's x-interface to them; issue_resp fields not listed here are 0: no load/store, no
// exceptions, no dual read/write).
//
// One instruction at a time: issue_ready is low until the previous one has finished. The
// register access is made only after the instruction is committed (commit_kill = 0), so a
// speculative or killed instruction has no effect; register reads have no side effects, but
// writes do (CTRL.START). Register and commit transactions may come in the issue cycle or
// later, in either order. Every accepted, committed instruction gets one result (we = 1 for
// tq.rd), after the register port's response, so the result of tq.wr also means the write
// has reached the core.

module tq_xif_regs #(
    parameter int        X_ID_WIDTH     = 4,
    parameter int        X_HARTID_WIDTH = 1,
    parameter logic [6:0] OPCODE        = 7'b0001011,   // custom-0
    parameter logic [6:0] FUNCT7        = 7'b0000000
) (
    input  logic                      clk_i,
    input  logic                      rst_ni,
    // issue
    input  logic                      x_issue_valid_i,
    output logic                      x_issue_ready_o,
    input  logic [31:0]               x_issue_req_instr_i,
    input  logic [X_HARTID_WIDTH-1:0] x_issue_req_hartid_i,
    input  logic [X_ID_WIDTH-1:0]     x_issue_req_id_i,
    output logic                      x_issue_resp_accept_o,
    output logic                      x_issue_resp_writeback_o,
    output logic [1:0]                x_issue_resp_register_read_o,   // source registers read (rs1, rs2)
    // register (source operands)
    input  logic                      x_register_valid_i,
    output logic                      x_register_ready_o,
    input  logic [X_HARTID_WIDTH-1:0] x_register_hartid_i,
    input  logic [X_ID_WIDTH-1:0]     x_register_id_i,
    input  logic [31:0]               x_register_rs0_i,
    input  logic [31:0]               x_register_rs1_i,
    input  logic [1:0]                x_register_rs_valid_i,
    // commit
    input  logic                      x_commit_valid_i,
    input  logic [X_HARTID_WIDTH-1:0] x_commit_hartid_i,
    input  logic [X_ID_WIDTH-1:0]     x_commit_id_i,
    input  logic                      x_commit_kill_i,
    // result
    output logic                      x_result_valid_o,
    input  logic                      x_result_ready_i,
    output logic [X_HARTID_WIDTH-1:0] x_result_hartid_o,
    output logic [X_ID_WIDTH-1:0]     x_result_id_o,
    output logic [31:0]               x_result_data_o,
    output logic [4:0]                x_result_rd_o,
    output logic                      x_result_we_o,
    // register port (tq_top)
    output logic                      reg_req_o,
    input  logic                      reg_gnt_i,
    output logic [11:0]               reg_addr_o,
    output logic                      reg_we_o,
    output logic [3:0]                reg_be_o,
    output logic [31:0]               reg_wdata_o,
    input  logic                      reg_rvalid_i,
    input  logic [31:0]               reg_rdata_i
);
  typedef enum logic [2:0] {S_IDLE, S_WAIT, S_REQ, S_RESP, S_RESULT} state_e;
  state_e state_q;

  logic                      rd_q, have_rs_q, have_commit_q;
  logic [X_ID_WIDTH-1:0]     id_q;
  logic [X_HARTID_WIDTH-1:0] hartid_q;
  logic [4:0]                rdst_q;
  logic [11:0]               addr_q;
  logic [31:0]               wdata_q, rdata_q;

  // decode
  logic ours, is_rd;
  assign is_rd = x_issue_req_instr_i[14:12] == 3'd1;
  assign ours  = x_issue_req_instr_i[6:0] == OPCODE && x_issue_req_instr_i[31:25] == FUNCT7
              && x_issue_req_instr_i[14:13] == 2'd0;

  logic issue_fire, accept;
  assign x_issue_ready_o              = state_q == S_IDLE;
  assign issue_fire                   = x_issue_valid_i && x_issue_ready_o;
  assign accept                       = ours;
  assign x_issue_resp_accept_o        = accept;
  assign x_issue_resp_writeback_o     = accept && is_rd;
  assign x_issue_resp_register_read_o = accept ? {!is_rd, 1'b1} : 2'b00;

  // the instruction whose operands and commit are being collected: the one in flight, or the
  // one being issued this cycle
  logic                      cur, cur_rd, cur_rs, cur_commit;
  logic [X_ID_WIDTH-1:0]     cur_id;
  logic [X_HARTID_WIDTH-1:0] cur_hart;
  assign cur        = state_q == S_WAIT || (issue_fire && accept);
  assign cur_rd     = state_q == S_WAIT ? rd_q : is_rd;
  assign cur_id     = state_q == S_WAIT ? id_q : x_issue_req_id_i;
  assign cur_hart   = state_q == S_WAIT ? hartid_q : x_issue_req_hartid_i;
  assign cur_rs     = state_q == S_WAIT && have_rs_q;
  assign cur_commit = state_q == S_WAIT && have_commit_q;

  // A register transaction for this instruction waits (ready low) until the operands it needs
  // are valid; any other one (an instruction not accepted here) is taken and ignored.
  // CORE-V-XIF commit rules: commit_kill = 0 commits commit_id and all older instructions;
  // commit_kill = 1 kills commit_id and all younger instructions (modular ID comparison).
  logic rs_ok, rs_mine, rs_fire, commit_fire, kill;
  logic [X_ID_WIDTH-1:0] commit_delta;
  logic                  commit_ge, commit_le, commit_match;
  assign rs_ok        = x_register_rs_valid_i[0] && (cur_rd || x_register_rs_valid_i[1]);
  assign rs_mine      = cur && !cur_rs && x_register_id_i == cur_id && x_register_hartid_i == cur_hart;
  assign x_register_ready_o = !rs_mine || rs_ok;
  assign rs_fire      = x_register_valid_i && rs_mine && rs_ok;
  assign commit_delta = x_commit_id_i - cur_id;
  assign commit_ge    = !commit_delta[X_ID_WIDTH-1];
  assign commit_le    = (commit_delta == '0) || commit_delta[X_ID_WIDTH-1];
  assign commit_match = x_commit_kill_i ? commit_le : commit_ge;
  assign commit_fire  = cur && !cur_commit && x_commit_valid_i
                     && x_commit_hartid_i == cur_hart && commit_match;
  assign kill         = commit_fire && x_commit_kill_i;

  logic rs_now, commit_now;
  assign rs_now     = cur_rs || rs_fire;
  assign commit_now = cur_commit || (commit_fire && !x_commit_kill_i);

  assign reg_req_o   = state_q == S_REQ;
  assign reg_we_o    = !rd_q;
  assign reg_addr_o  = addr_q;
  assign reg_be_o    = rd_q ? 4'h0 : 4'hF;
  assign reg_wdata_o = wdata_q;

  assign x_result_valid_o  = state_q == S_RESULT;
  assign x_result_hartid_o = hartid_q;
  assign x_result_id_o     = id_q;
  assign x_result_data_o   = rdata_q;
  assign x_result_rd_o     = rdst_q;
  assign x_result_we_o     = rd_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= S_IDLE; rd_q <= 1'b0; have_rs_q <= 1'b0; have_commit_q <= 1'b0;
      id_q <= '0; hartid_q <= '0; rdst_q <= '0; addr_q <= '0; wdata_q <= '0; rdata_q <= '0;
    end else begin
      if (issue_fire && accept) begin
        rd_q     <= is_rd;
        id_q     <= x_issue_req_id_i;
        hartid_q <= x_issue_req_hartid_i;
        rdst_q   <= x_issue_req_instr_i[11:7];
      end
      if (rs_fire) begin
        addr_q  <= x_register_rs0_i[11:0];
        wdata_q <= x_register_rs1_i;
      end
      if (cur) begin
        have_rs_q     <= rs_now;
        have_commit_q <= commit_now;
      end
      case (state_q)
        S_IDLE:   if (issue_fire && accept) state_q <= kill ? S_IDLE : (rs_now && commit_now) ? S_REQ : S_WAIT;
        S_WAIT:   if (kill) state_q <= S_IDLE;
                  else if (rs_now && commit_now) state_q <= S_REQ;
        S_REQ:    if (reg_gnt_i) state_q <= S_RESP;
        S_RESP:   if (reg_rvalid_i) state_q <= S_RESULT;
        S_RESULT: if (x_result_ready_i) state_q <= S_IDLE;
        default:  state_q <= S_IDLE;
      endcase
      if (state_q == S_RESP && reg_rvalid_i) rdata_q <= rd_q ? reg_rdata_i : 32'h0;
    end
  end

`ifdef FORMAL
  // ---- properties, proved by k-induction (verif/formal/tq_xif_regs.sby) ----
  localparam int N_IDS = 1 << X_ID_WIDTH;
  logic past_valid = 1'b0;
  always_ff @(posedge clk_i) past_valid <= 1'b1;
  always_comb if (!past_valid) assume (!rst_ni);

  // Independent per-id tracker for the active instruction's hart (V6): records register operands,
  // batched commits (commit_id and older), and range kills (commit_id and younger).
  logic                      open_q;
  logic [X_ID_WIDTH-1:0]     f_id_q, f_id;
  logic [X_HARTID_WIDTH-1:0] f_hart_q, f_hart;
  logic                      f_rd_q, f_rd;
  logic [11:0]               f_addr_q;
  logic [31:0]               f_wdata_q;
  logic [N_IDS-1:0]          f_comm_q, f_kill_q, f_rs_q, f_comm_n, f_kill_n, f_rs_n;

  assign f_id   = open_q ? f_id_q   : x_issue_req_id_i;
  assign f_hart = open_q ? f_hart_q : x_issue_req_hartid_i;
  assign f_rd   = open_q ? f_rd_q   : (x_issue_req_instr_i[14:12] == 3'd1);

  always_comb begin
    f_comm_n = (issue_fire && accept) ? '0 : f_comm_q;
    f_kill_n = (issue_fire && accept) ? '0 : f_kill_q;
    f_rs_n   = (issue_fire && accept) ? '0 : f_rs_q;
    if (open_q || (issue_fire && accept)) begin
      if (x_register_valid_i && x_register_ready_o && x_register_hartid_i == f_hart
          && x_register_rs_valid_i[0] && (f_rd || x_register_rs_valid_i[1])) begin
        f_rs_n[x_register_id_i] = 1'b1;
      end
      if (x_commit_valid_i && x_commit_hartid_i == f_hart) begin
        for (int k = 0; k < N_IDS; k++) begin
          logic [X_ID_WIDTH-1:0] d;
          d = x_commit_id_i - X_ID_WIDTH'(k);
          if (!x_commit_kill_i && !d[X_ID_WIDTH-1] && !f_kill_n[k]) f_comm_n[k] = 1'b1;
          if (x_commit_kill_i && (d == '0 || d[X_ID_WIDTH-1]) && !f_comm_n[k]) f_kill_n[k] = 1'b1;
        end
      end
    end
  end

  // CPU side: an offered issue stays (with its instruction) until ready; a committed instruction
  // is not later killed, and operands are not re-sent after they have been accepted.
  always_comb begin
    if (rst_ni && (open_q || (issue_fire && accept))) begin
      if (f_comm_q[f_id]) assume (!(x_commit_valid_i && x_commit_hartid_i == f_hart && x_commit_kill_i));
      if (f_rs_q[f_id])   assume (!(x_register_valid_i && x_register_hartid_i == f_hart && x_register_id_i == f_id));
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      open_q <= 1'b0; f_id_q <= '0; f_hart_q <= '0; f_rd_q <= 1'b0;
      f_addr_q <= '0; f_wdata_q <= '0;
      f_comm_q <= '0; f_kill_q <= '0; f_rs_q <= '0;
    end else begin
      f_comm_q <= f_comm_n;
      f_kill_q <= f_kill_n;
      f_rs_q   <= f_rs_n;
      if (issue_fire && accept) begin
        f_id_q   <= x_issue_req_id_i;
        f_hart_q <= x_issue_req_hartid_i;
        f_rd_q   <= (x_issue_req_instr_i[14:12] == 3'd1);
      end
      if ((open_q || (issue_fire && accept)) && !f_rs_q[f_id]
          && x_register_valid_i && x_register_ready_o
          && x_register_hartid_i == f_hart && x_register_id_i == f_id) begin
        f_addr_q  <= x_register_rs0_i[11:0];
        f_wdata_q <= x_register_rs1_i;
      end
      if (issue_fire && accept && !f_kill_n[x_issue_req_id_i]) open_q <= 1'b1;
      else if (f_kill_n[f_id_q] || (x_result_valid_o && x_result_ready_i)) open_q <= 1'b0;
    end
  end
  always_ff @(posedge clk_i) begin
    if (past_valid && $past(rst_ni) && rst_ni) begin
      if ($past(x_issue_valid_i && !x_issue_ready_o))
        assume (x_issue_valid_i && x_issue_req_instr_i == $past(x_issue_req_instr_i)
                && x_issue_req_id_i == $past(x_issue_req_id_i));
    end
  end
  // register port (OBI): one response per accepted request, one or more cycles later
  logic obi_out_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) obi_out_q <= 1'b0;
    else if (reg_req_o && reg_gnt_i) obi_out_q <= 1'b1;
    else if (reg_rvalid_i) obi_out_q <= 1'b0;
  end
  always_comb if (rst_ni && reg_rvalid_i) assume (obi_out_q);

  always_ff @(posedge clk_i) begin
    if (past_valid && $past(rst_ni) && rst_ni) begin
      // result stays valid and unchanged until taken
      if ($past(x_result_valid_o && !x_result_ready_i))
        assert (x_result_valid_o && x_result_data_o == $past(x_result_data_o)
                && x_result_id_o == $past(x_result_id_o) && x_result_we_o == $past(x_result_we_o));
      // OBI: a request without gnt is held unchanged
      if ($past(reg_req_o && !reg_gnt_i))
        assert (reg_req_o && reg_we_o == $past(reg_we_o) && reg_addr_o == $past(reg_addr_o)
                && reg_wdata_o == $past(reg_wdata_o));
    end
  end
  always_comb begin
    if (rst_ni) begin
      // checked against the independent per-id tracker, not only the adapter's internal flags
      assert (!reg_req_o || (f_comm_q[f_id_q] && !f_kill_q[f_id_q] && f_rs_q[f_id_q]));
      assert (!reg_req_o || (reg_addr_o == f_addr_q && (!reg_we_o || reg_wdata_o == f_wdata_q)));
      // nothing is issued while an instruction is open; a result only for an open committed one
      assert (!x_issue_ready_o || !open_q);
      assert (!x_result_valid_o || (open_q && f_comm_q[f_id_q] && !f_kill_q[f_id_q] && f_rs_q[f_id_q]));
      assert (open_q == (state_q != S_IDLE));
      assert (!open_q || (id_q == f_id_q && hartid_q == f_hart_q && rd_q == f_rd_q));
      assert (!open_q || (have_rs_q == f_rs_q[f_id_q] && have_commit_q == f_comm_q[f_id_q] && !f_kill_q[f_id_q]));
      assert (!have_rs_q || (addr_q == f_addr_q && wdata_q == f_wdata_q));
      assert (obi_out_q == (state_q == S_RESP));
      assert (!(state_q == S_REQ || state_q == S_RESP || state_q == S_RESULT) || (have_rs_q && have_commit_q));
      assert (!x_result_valid_o || x_result_we_o || x_result_data_o == 32'h0);
    end
  end
  always_ff @(posedge clk_i) begin
    if (past_valid && rst_ni) begin
      cover (x_result_valid_o && x_result_ready_i && x_result_we_o && x_result_data_o == 32'h5451_0001);
      cover (x_result_valid_o && x_result_ready_i && !x_result_we_o);
      cover (issue_fire && accept && kill);
      cover (state_q == S_WAIT && commit_fire && !kill && !have_rs_q);   // commit before operands
      cover (state_q == S_WAIT && commit_fire && !kill && x_commit_id_i != cur_id); // batched commit
      cover (state_q == S_WAIT && commit_fire &&  kill && x_commit_id_i != cur_id); // older-id kill
    end
  end
`endif

  /* verilator lint_off UNUSEDSIGNAL */
  logic unused;
  assign unused = ^{x_register_rs0_i[31:12], x_issue_req_instr_i[24:15]};
  /* verilator lint_on UNUSEDSIGNAL */

endmodule
