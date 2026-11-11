// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Front end: decodes one instruction per cycle from the program buffer, runs the zero-overhead
// loop stack (2 levels), walks output tiles, and issues commands to the MAC and stream queues.
// Spec sections 5.2 and 6. Model: TriQueueCore.step_front (model/tri_queue/core.py).
//
// Addresses that depend on the tile position are kept as running offsets, so no multipliers
// are needed: tile (ti, tj) of C starts at C_BASE + ti*T*C_LD + tj*4T, A panel ti at
// A_BASE + ti*T*Kp, B panel tj at B_BASE + tj*T*Kp.

module tq_frontend #(
    parameter int T          = 8,
    parameter int PROG_BYTES = 64,
    parameter bit CLK_GATE   = 1'b1,    // one clock gate per CFG register (tq_cg)
    localparam int L2T       = $clog2(T),
    localparam int H         = T / 2,
    localparam int PCW       = $clog2(PROG_BYTES + 16)
) (
    input  logic                    clk_i,
    input  logic                    rst_ni,
    input  logic                    start_i,         // restart at pc 0 (clears front-end control state)
    input  logic                    cfg_clear_i,     // clear CFG registers (on START only, not ABORT)
    input  logic                    run_i,           // core is running
    input  logic [PROG_BYTES*8-1:0] prog_i,
    // room in the queues, after this cycle's pops
    input  logic                    queues_idle_i,
    input  logic                    macq_full_i,
    input  logic                    scmd_a_full_i,
    input  logic                    scmd_b_full_i,
    // commands
    output logic                    macq_push_o,
    output tq_pkg::mcmd_t           macq_data_o,
    output logic                    scmd_a_push_o,
    output tq_pkg::scmd_t           scmd_a_data_o,
    output logic                    scmd_b_push_o,
    output tq_pkg::scmd_t           scmd_b_data_o,
    // configuration registers
    output logic [31:0]             cfg_a_base_o,
    output logic [31:0]             cfg_b_base_o,
    output logic [31:0]             cfg_c_base_o,
    output logic [31:0]             cfg_c_ld_o,
    output logic [31:0]             cfg_m_o,
    output logic [31:0]             cfg_n_o,
    output logic [31:0]             cfg_k_o,
    output logic [31:0]             cfg_mode_o,
    // status
    output logic                    fence_o,         // a FENCE has retired
    output logic                    macq_stall_o,    // front end stalled on MAC command queue full
    output logic                    retire_o,        // an instruction retired this cycle
    output logic                    error_o,         // pulse: illegal program
    output logic [3:0]              err_code_o
);
  // ---------------------------------------------------------------- state
  logic [PCW-1:0] pc_q;
  logic           fence_q;
  logic [7:0][31:0] cfg_q;

  // loop stack: frame 0 is the outer frame, frame 1 the inner
  logic [1:0]     sp_q;                 // number of valid frames
  logic           f0_t_q, f1_t_q;       // frame is a LOOP_T
  logic [PCW-1:0] f0_start_q, f0_end_q, f1_start_q, f1_end_q;
  logic [31:0]    f0_rem_q, f1_rem_q;   // LOOP_K iterations left (unused for LOOP_T)

  // tile walker for the front end (AUTO shapes, SWAP_C tile, LOOP_K counts)
  logic [15:0]    fe_ti_q, fe_tj_q;
  logic [31:0]    fe_crow_q;            // ti * T * C_LD

  // stream command counters
  logic [15:0]    sa_ti_q, sa_tj_q, sb_ti_q, sb_tj_q;
  logic [31:0]    sa_off_q, sb_off_q;   // panel offsets from A_BASE / B_BASE

  // ---------------------------------------------------------------- derived configuration
  logic [31:0] m, n, k, kp, panel_stride;
  assign m            = cfg_q[tq_pkg::CFG_M];
  assign n            = cfg_q[tq_pkg::CFG_N];
  assign k            = cfg_q[tq_pkg::CFG_K];
  assign kp           = (k + 32'd7) & ~32'd7;
  assign panel_stride = kp << L2T;

  function automatic logic [31:0] tpos(input logic [15:0] t);    // t * T
    tpos = {16'b0, t} << L2T;
  endfunction

  // ---------------------------------------------------------------- decode
  localparam int PSHW = $clog2(PROG_BYTES);
  localparam int THW  = 32 - L2T;
  logic [PROG_BYTES*8+31:0] prog_pad;
  logic [159:0]             win160;
  logic [63:0]              win64;
  logic [39:0]              iwin;
  logic [7:0]               ibyte;
  logic [3:0]               op, arg;
  logic [31:0]              imm;
  logic [PCW-1:0]           isize;
  assign prog_pad = {32'h0, prog_i};
  assign win160   = prog_pad[32'(pc_q[PSHW-1:4]) * 128 +: 160];
  assign win64    = win160[32'(pc_q[3:2]) * 32 +: 64];
  assign iwin     = (32'(pc_q) < PROG_BYTES) ? win64[32'(pc_q[1:0]) * 8 +: 40] : 40'h0;
  assign ibyte    = iwin[7:0];
  assign op       = ibyte[7:4];
  assign arg      = ibyte[3:0];
  assign imm      = iwin[39:8];
  assign isize    = (op == tq_pkg::OP_CFG) ? PCW'(5) : PCW'(1);

  // front-end tile shape (since tpos(t) has low L2T bits == 0, m - tpos(t) needs no subtractor)
  logic [THW-1:0] m_hi, n_hi;
  logic [L2T-1:0] m_lo, n_lo;
  logic           m_lo_gt_h, n_lo_gt_h, m_lo_z, n_lo_z;
  logic [16:0]    fe_ti_inc, fe_tj_inc, s_tj_inc;
  logic [15:0]    s_ti_inc;
  logic           fe_r_ge_t, fe_c_ge_t, fe_r_le_t, fe_c_le_t, fe_rfull, fe_cfull, fe_last;
  assign m_hi      = m[31:L2T];
  assign m_lo      = m[L2T-1:0];
  assign n_hi      = n[31:L2T];
  assign n_lo      = n[L2T-1:0];
  assign m_lo_gt_h = m_lo > L2T'(H);
  assign n_lo_gt_h = n_lo > L2T'(H);
  assign m_lo_z    = (m_lo == '0);
  assign n_lo_z    = (n_lo == '0);
  assign fe_ti_inc = {1'b0, fe_ti_q} + 17'd1;
  assign fe_tj_inc = {1'b0, fe_tj_q} + 17'd1;
  assign fe_r_ge_t = (m_hi != THW'(fe_ti_q));
  assign fe_c_ge_t = (n_hi != THW'(fe_tj_q));
  assign fe_rfull  = fe_r_ge_t || m_lo_gt_h;
  assign fe_cfull  = fe_c_ge_t || n_lo_gt_h;
  assign fe_r_le_t = !fe_r_ge_t || ((m_hi == THW'(fe_ti_inc)) && m_lo_z);
  assign fe_c_le_t = !fe_c_ge_t || ((n_hi == THW'(fe_tj_inc)) && n_lo_z);
  assign fe_last   = fe_r_le_t && fe_c_le_t;

  // stream counters: shared datapath for STREAM_A and STREAM_B
  logic        is_sb, s_exh, s_cols_more, s_full;
  logic [15:0] s_ti, s_tj;
  logic [31:0] s_base, s_off, s_addr, s_vecs, s_off_inc;
  assign is_sb       = (op == tq_pkg::OP_STREAM_B);
  assign s_ti        = is_sb ? sb_ti_q  : sa_ti_q;
  assign s_tj        = is_sb ? sb_tj_q  : sa_tj_q;
  assign s_ti_inc    = s_ti + 16'd1;
  assign s_tj_inc    = {1'b0, s_tj} + 17'd1;
  assign s_base      = is_sb ? cfg_q[tq_pkg::CFG_B_BASE] : cfg_q[tq_pkg::CFG_A_BASE];
  assign s_off       = {is_sb ? sb_off_q[31:L2T+3] : sa_off_q[31:L2T+3], {(L2T+3){1'b0}}};
  assign s_exh       = (tpos(s_ti) >= m) || (n == 0);
  assign s_cols_more = (n_hi != THW'(s_tj)) && !((n_hi == THW'(s_tj_inc)) && n_lo_z);
  assign s_full      = is_sb ? ((n_hi != THW'(s_tj)) || n_lo_gt_h)
                             : ((m_hi != THW'(s_ti)) || m_lo_gt_h);
  assign s_addr      = s_base + s_off;
  assign s_off_inc   = s_off + panel_stride;
  assign s_vecs      = s_full ? (kp >> 1) : (kp >> 2);

  logic stall, exec;
  always_comb begin
    stall = 1'b0;
    case (op)
      tq_pkg::OP_CFG:                                          stall = !queues_idle_i;
      tq_pkg::OP_ZERO_C, tq_pkg::OP_VOUTER, tq_pkg::OP_SWAP_C: stall = macq_full_i;
      tq_pkg::OP_STREAM_A:                                     stall = scmd_a_full_i;
      tq_pkg::OP_STREAM_B:                                     stall = scmd_b_full_i;
      default:                                                 stall = 1'b0;
    endcase
  end
  assign exec = run_i && !fence_q && !stall;
  assign macq_stall_o = run_i && !fence_q &&
                        (op == tq_pkg::OP_ZERO_C || op == tq_pkg::OP_VOUTER || op == tq_pkg::OP_SWAP_C) &&
                        macq_full_i;

  // ---------------------------------------------------------------- next state
  logic [PCW-1:0] pc_n;
  logic           fence_n;
  logic [1:0]     sp_n;
  logic           f0_t_n, f1_t_n;
  logic [PCW-1:0] f0_start_n, f0_end_n, f1_start_n, f1_end_n;
  logic [31:0]    f0_rem_n, f1_rem_n;
  logic [15:0]    fe_ti_n, fe_tj_n, sa_ti_n, sa_tj_n, sb_ti_n, sb_tj_n;
  logic [31:0]    fe_crow_n, sa_off_n, sb_off_n;
  logic           cfg_we;
  logic           err;
  logic [3:0]     err_code;
  logic [31:0]    loop_count;
  logic           loop_is_t;
  logic           fe_advance, fe_reset;
  logic           lt_seen_q, lt_seen_n;   // a LOOP_T has run: a second one is ERR_LOOP (spec 6.1)

  always_comb begin
    pc_n       = pc_q;
    fence_n    = fence_q;
    sp_n       = sp_q;
    f0_t_n     = f0_t_q;     f1_t_n     = f1_t_q;
    f0_start_n = f0_start_q; f1_start_n = f1_start_q;
    f0_end_n   = f0_end_q;   f1_end_n   = f1_end_q;
    f0_rem_n   = f0_rem_q;   f1_rem_n   = f1_rem_q;
    sa_ti_n    = sa_ti_q;    sa_tj_n    = sa_tj_q;    sa_off_n = sa_off_q;
    sb_ti_n    = sb_ti_q;    sb_tj_n    = sb_tj_q;    sb_off_n = sb_off_q;
    cfg_we     = 1'b0;
    err        = 1'b0;
    err_code   = tq_pkg::ERR_NONE;
    fe_advance = 1'b0;
    fe_reset   = 1'b0;
    loop_is_t  = (op == tq_pkg::OP_LOOP_T);
    loop_count = 32'd0;
    lt_seen_n  = lt_seen_q;

    macq_push_o   = 1'b0;
    macq_data_o   = '0;
    scmd_a_push_o = 1'b0;
    scmd_a_data_o = '0;
    scmd_b_push_o = 1'b0;
    scmd_b_data_o = '0;

    if (exec) begin
      pc_n = pc_q + isize;
      if (32'(pc_q) + 32'(isize) > PROG_BYTES) begin
        err = 1'b1; err_code = tq_pkg::ERR_PC;
      end
      case (op)
        tq_pkg::OP_NOP: begin
          if (arg != 4'h0) begin err = 1'b1; err_code = tq_pkg::ERR_OPCODE; end
        end
        tq_pkg::OP_CFG: begin
          if (arg[3])          begin err = 1'b1; err_code = tq_pkg::ERR_CFG_REG;  end
          else if (sp_q != 0)  begin err = 1'b1; err_code = tq_pkg::ERR_CFG_LOOP; end
          else if ((arg == 4'(tq_pkg::CFG_K) && imm > ((32'hFFFF_FFFF >> L2T) & ~32'd7)) ||
                   ((arg == 4'(tq_pkg::CFG_M) || arg == 4'(tq_pkg::CFG_N)) && imm > (32'hFFFF << L2T)) ||
                   (arg == 4'(tq_pkg::CFG_MODE) && imm[31:8] != 24'd0)) begin
            err = 1'b1; err_code = tq_pkg::ERR_CFG_VAL;
          end else begin
            cfg_we = !err;
          end
        end
        tq_pkg::OP_STREAM_A: begin
          if (arg != 4'h0) begin
            err = 1'b1; err_code = tq_pkg::ERR_OPCODE;
          end else if (!s_exh) begin
            scmd_a_push_o      = !err;
            scmd_a_data_o.addr = s_addr;
            scmd_a_data_o.vecs = s_vecs;
            if (s_cols_more) sa_tj_n = s_tj_inc[15:0];
            else begin
              sa_tj_n  = '0;
              sa_ti_n  = s_ti_inc;
              sa_off_n = s_off_inc;
            end
          end
        end
        tq_pkg::OP_STREAM_B: begin
          if (arg != 4'h0) begin
            err = 1'b1; err_code = tq_pkg::ERR_OPCODE;
          end else if (!s_exh) begin
            scmd_b_push_o      = !err;
            scmd_b_data_o.addr = s_addr;
            scmd_b_data_o.vecs = s_vecs;
            if (s_cols_more) begin
              sb_tj_n  = s_tj_inc[15:0];
              sb_off_n = s_off_inc;
            end else begin
              sb_tj_n  = '0;
              sb_ti_n  = s_ti_inc;
              sb_off_n = '0;
            end
          end
        end
        tq_pkg::OP_ZERO_C: begin
          if (arg != 4'h0) begin
            err = 1'b1; err_code = tq_pkg::ERR_OPCODE;
          end else begin
            macq_push_o      = !err;
            macq_data_o.kind = tq_pkg::MC_ZERO;
          end
        end
        tq_pkg::OP_VOUTER: begin
          if (arg[3] ? (arg[2:0] != 3'b0) : arg[2]) begin
            err = 1'b1; err_code = tq_pkg::ERR_OPCODE;
          end else begin
            macq_push_o       = !err;
            macq_data_o.kind  = tq_pkg::MC_MAC;
            macq_data_o.rfull = arg[3] ? fe_rfull : arg[1];
            macq_data_o.cfull = arg[3] ? fe_cfull : arg[0];
          end
        end
        tq_pkg::OP_SWAP_C: begin
          if (arg != 4'h0) begin
            err = 1'b1; err_code = tq_pkg::ERR_OPCODE;
          end else if (m != 32'd0 && n != 32'd0 &&
                       cfg_q[tq_pkg::CFG_C_LD] < (cfg_q[tq_pkg::CFG_MODE][tq_pkg::MODE_OUT_INT8] ? n : (n << 2))) begin
            err = 1'b1; err_code = tq_pkg::ERR_CFG_VAL;
          end else begin
            macq_push_o        = !err;
            macq_data_o.kind   = tq_pkg::MC_SWAP;
            macq_data_o.c_addr = cfg_q[tq_pkg::CFG_C_BASE] + {fe_crow_q[31:L2T], {L2T{1'b0}}}
                               + (cfg_q[tq_pkg::CFG_MODE][tq_pkg::MODE_OUT_INT8]
                                  ? ({16'b0, fe_tj_q} << L2T) : ({16'b0, fe_tj_q} << (L2T + 2)));
            macq_data_o.rows   = fe_r_ge_t ? 8'(T) : 8'(m_lo);
            macq_data_o.cols   = fe_c_ge_t ? 8'(T) : 8'(n_lo);
          end
        end
        tq_pkg::OP_LOOP_K, tq_pkg::OP_LOOP_T: begin
          if (loop_is_t)
            loop_count = (m == 0 || n == 0) ? 32'd0 : 32'd1;   // LOOP_T ends on the last tile
          else
            loop_count = kp >> (1 + 32'(!fe_rfull) + 32'(!fe_cfull));   // Kp / (2d)
          if (loop_count == 0) begin
            pc_n = pc_q + 1 + PCW'(arg);                         // skip the body
          end else if (arg == 4'h0 || sp_q == 2 || (loop_is_t && lt_seen_q) ||
                       (sp_q == 2'd1 && (pc_q + 1 + PCW'(arg)) > f0_end_q)) begin
            // empty body, third level, second LOOP_T, or inner loop extending past outer loop
            err = 1'b1; err_code = tq_pkg::ERR_LOOP;
          end else if (sp_q == 0) begin
            lt_seen_n = lt_seen_q || loop_is_t;
            sp_n = 2'd1; f0_t_n = loop_is_t;
            f0_start_n = pc_q + 1; f0_end_n = pc_q + 1 + PCW'(arg); f0_rem_n = loop_count;
            fe_reset = loop_is_t;
          end else begin
            lt_seen_n = lt_seen_q || loop_is_t;
            sp_n = 2'd2; f1_t_n = loop_is_t;
            f1_start_n = pc_q + 1; f1_end_n = pc_q + 1 + PCW'(arg); f1_rem_n = loop_count;
            fe_reset = loop_is_t;
          end
        end
        tq_pkg::OP_FENCE: begin
          if (arg != 4'h0) begin err = 1'b1; err_code = tq_pkg::ERR_OPCODE; end
          else fence_n = !err;
        end
        default: begin err = 1'b1; err_code = tq_pkg::ERR_OPCODE; end
      endcase

      // zero-overhead loop-back: the inner frame may end and pop, then the outer frame may end
      // at the same pc (model: the while loop in step_front)
      if (sp_n == 2'd2 && pc_n == f1_end_n) begin
        if (f1_t_n ? !fe_last : (|f1_rem_n[31:1])) begin
          f1_rem_n   = f1_rem_n - 32'd1;
          pc_n       = f1_start_n;
          fe_advance = f1_t_n;
        end else begin
          sp_n = 2'd1;
          if (f1_t_n) fe_reset = 1'b1;
        end
      end
      if (sp_n == 2'd1 && pc_n == f0_end_n) begin
        if (f0_t_n ? !fe_last : (|f0_rem_n[31:1])) begin
          f0_rem_n   = f0_rem_n - 32'd1;
          pc_n       = f0_start_n;
          fe_advance = f0_t_n;
        end else begin
          sp_n = 2'd0;
          if (f0_t_n) fe_reset = 1'b1;
        end
      end
    end
  end

  // front-end tile walker
  always_comb begin
    fe_ti_n   = fe_ti_q;
    fe_tj_n   = fe_tj_q;
    fe_crow_n = fe_crow_q;
    if (fe_reset) begin
      fe_ti_n   = '0;
      fe_tj_n   = '0;
      fe_crow_n = '0;
    end else if (fe_advance) begin
      if (!fe_c_le_t) fe_tj_n = fe_tj_inc[15:0];
      else begin
        fe_tj_n   = '0;
        fe_ti_n   = fe_ti_inc[15:0];
        fe_crow_n = {fe_crow_q[31:L2T] + cfg_q[tq_pkg::CFG_C_LD][31-L2T:0], {L2T{1'b0}}};
      end
    end
  end

  // ---------------------------------------------------------------- registers
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      pc_q <= '0; fence_q <= 1'b0; sp_q <= '0; lt_seen_q <= 1'b0;
      f0_t_q <= 1'b0; f1_t_q <= 1'b0;
      f0_start_q <= '0; f0_end_q <= '0; f1_start_q <= '0; f1_end_q <= '0;
      f0_rem_q <= '0; f1_rem_q <= '0;
      fe_ti_q <= '0; fe_tj_q <= '0; fe_crow_q <= '0;
      sa_ti_q <= '0; sa_tj_q <= '0; sa_off_q <= '0;
      sb_ti_q <= '0; sb_tj_q <= '0; sb_off_q <= '0;
    end else if (start_i) begin
      pc_q <= '0; fence_q <= 1'b0; sp_q <= '0; lt_seen_q <= 1'b0;
      f0_t_q <= 1'b0; f1_t_q <= 1'b0;
      fe_ti_q <= '0; fe_tj_q <= '0; fe_crow_q <= '0;
      sa_ti_q <= '0; sa_tj_q <= '0; sa_off_q <= '0;
      sb_ti_q <= '0; sb_tj_q <= '0; sb_off_q <= '0;
    end else begin
      pc_q <= pc_n; fence_q <= fence_n; sp_q <= sp_n; lt_seen_q <= lt_seen_n;
      f0_t_q <= f0_t_n; f1_t_q <= f1_t_n;
      f0_start_q <= f0_start_n; f0_end_q <= f0_end_n; f1_start_q <= f1_start_n; f1_end_q <= f1_end_n;
      f0_rem_q <= f0_rem_n; f1_rem_q <= f1_rem_n;
      fe_ti_q <= fe_ti_n; fe_tj_q <= fe_tj_n; fe_crow_q <= fe_crow_n;
      sa_ti_q <= sa_ti_n; sa_tj_q <= sa_tj_n; sa_off_q <= sa_off_n;
      sb_ti_q <= sb_ti_n; sb_tj_q <= sb_tj_n; sb_off_q <= sb_off_n;
    end
  end

  // CFG registers: written by CFG, cleared by START; static during the GEMM, so each is gated
  for (genvar i = 0; i < 8; i++) begin : g_cfg
    logic        we, gclk;
    logic [31:0] q;
    assign we = cfg_clear_i || (cfg_we && 32'(arg[2:0]) == i);
    tq_cg #(.EN(CLK_GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
    always_ff @(posedge gclk or negedge rst_ni) begin
      if (!rst_ni)              q <= '0;
      else if (we || CLK_GATE)  q <= cfg_clear_i ? '0 : imm;
    end
    assign cfg_q[i] = q;
  end

  assign cfg_a_base_o = cfg_q[tq_pkg::CFG_A_BASE];
  assign cfg_b_base_o = cfg_q[tq_pkg::CFG_B_BASE];
  assign cfg_c_base_o = cfg_q[tq_pkg::CFG_C_BASE];
  assign cfg_c_ld_o   = cfg_q[tq_pkg::CFG_C_LD];
  assign cfg_k_o      = cfg_q[tq_pkg::CFG_K];
  assign cfg_m_o      = cfg_q[tq_pkg::CFG_M];
  assign cfg_n_o      = cfg_q[tq_pkg::CFG_N];
  assign cfg_mode_o   = cfg_q[tq_pkg::CFG_MODE];
  assign fence_o      = fence_q;
  assign retire_o     = exec && !err;
  assign error_o      = exec && err;
  assign err_code_o   = err_code;

endmodule
