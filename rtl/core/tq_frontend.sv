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
    localparam int PCW       = $clog2(PROG_BYTES + 8)
) (
    input  logic                    clk_i,
    input  logic                    rst_ni,
    input  logic                    start_i,         // restart at pc 0 (clears all front-end state)
    input  logic                    run_i,           // core is running
    input  logic [PROG_BYTES*8-1:0] prog_i,
    // room in the queues, after this cycle's pops
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

  // front-end tile shape
  logic [31:0] fe_rows_left, fe_cols_left;
  logic        fe_rfull, fe_cfull, fe_last;
  assign fe_rows_left = m - tpos(fe_ti_q);
  assign fe_cols_left = n - tpos(fe_tj_q);
  assign fe_rfull     = fe_rows_left > 32'(H);
  assign fe_cfull     = fe_cols_left > 32'(H);
  assign fe_last      = (fe_rows_left <= 32'(T)) && (fe_cols_left <= 32'(T));

  // stream counters: exhausted once every tile has been issued
  logic        sa_exh, sb_exh;
  logic [31:0] sa_cols_left, sb_cols_left, sa_rows_left;
  assign sa_rows_left = m - tpos(sa_ti_q);
  assign sa_cols_left = n - tpos(sa_tj_q);
  assign sb_cols_left = n - tpos(sb_tj_q);
  assign sa_exh       = (tpos(sa_ti_q) >= m) || (n == 0);
  assign sb_exh       = (tpos(sb_ti_q) >= m) || (n == 0);

  // ---------------------------------------------------------------- decode
  function automatic logic [7:0] prog_byte(input logic [PCW-1:0] a);
    prog_byte = (32'(a) < PROG_BYTES) ? prog_i[32'(a) * 8 +: 8] : 8'h00;
  endfunction

  logic [7:0]  ibyte;
  logic [3:0]  op, arg;
  logic [31:0] imm;
  logic [PCW-1:0] isize;
  assign ibyte = prog_byte(pc_q);
  assign op    = ibyte[7:4];
  assign arg   = ibyte[3:0];
  assign imm   = {prog_byte(pc_q + PCW'(4)), prog_byte(pc_q + PCW'(3)),
                  prog_byte(pc_q + PCW'(2)), prog_byte(pc_q + PCW'(1))};
  assign isize = (op == tq_pkg::OP_CFG) ? PCW'(5) : PCW'(1);

  logic stall, exec;
  always_comb begin
    stall = 1'b0;
    case (op)
      tq_pkg::OP_ZERO_C, tq_pkg::OP_VOUTER, tq_pkg::OP_SWAP_C: stall = macq_full_i;
      tq_pkg::OP_STREAM_A:                     stall = scmd_a_full_i;
      tq_pkg::OP_STREAM_B:                     stall = scmd_b_full_i;
      default:                         stall = 1'b0;
    endcase
  end
  assign exec = run_i && !fence_q && !stall;

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
        tq_pkg::OP_NOP: ;
        tq_pkg::OP_CFG: begin
          if (arg[3])          begin err = 1'b1; err_code = tq_pkg::ERR_CFG_REG;  end
          else if (sp_q != 0)  begin err = 1'b1; err_code = tq_pkg::ERR_CFG_LOOP; end
          else cfg_we = 1'b1;
        end
        tq_pkg::OP_STREAM_A: begin
          if (!sa_exh) begin
            scmd_a_push_o      = 1'b1;
            scmd_a_data_o.addr = cfg_q[tq_pkg::CFG_A_BASE] + sa_off_q;
            scmd_a_data_o.vecs = (sa_rows_left > 32'(H)) ? (kp >> 1) : (kp >> 2);
            if (sa_cols_left > 32'(T)) sa_tj_n = sa_tj_q + 16'd1;
            else begin
              sa_tj_n  = '0;
              sa_ti_n  = sa_ti_q + 16'd1;
              sa_off_n = sa_off_q + panel_stride;
            end
          end
        end
        tq_pkg::OP_STREAM_B: begin
          if (!sb_exh) begin
            scmd_b_push_o      = 1'b1;
            scmd_b_data_o.addr = cfg_q[tq_pkg::CFG_B_BASE] + sb_off_q;
            scmd_b_data_o.vecs = (sb_cols_left > 32'(H)) ? (kp >> 1) : (kp >> 2);
            if (sb_cols_left > 32'(T)) begin
              sb_tj_n  = sb_tj_q + 16'd1;
              sb_off_n = sb_off_q + panel_stride;
            end else begin
              sb_tj_n  = '0;
              sb_ti_n  = sb_ti_q + 16'd1;
              sb_off_n = '0;
            end
          end
        end
        tq_pkg::OP_ZERO_C: begin
          macq_push_o      = 1'b1;
          macq_data_o.kind = tq_pkg::MC_ZERO;
        end
        tq_pkg::OP_VOUTER: begin
          macq_push_o       = 1'b1;
          macq_data_o.kind  = tq_pkg::MC_MAC;
          macq_data_o.rfull = arg[3] ? fe_rfull : arg[1];
          macq_data_o.cfull = arg[3] ? fe_cfull : arg[0];
        end
        tq_pkg::OP_SWAP_C: begin
          macq_push_o        = 1'b1;
          macq_data_o.kind   = tq_pkg::MC_SWAP;
          macq_data_o.c_addr = cfg_q[tq_pkg::CFG_C_BASE] + fe_crow_q + ({16'b0, fe_tj_q} << (L2T + 2));
          macq_data_o.rows   = (fe_rows_left >= 32'(T)) ? 8'(T) : fe_rows_left[7:0];
          macq_data_o.cols   = (fe_cols_left >= 32'(T)) ? 8'(T) : fe_cols_left[7:0];
        end
        tq_pkg::OP_LOOP_K, tq_pkg::OP_LOOP_T: begin
          if (loop_is_t)
            loop_count = (m == 0 || n == 0) ? 32'd0 : 32'd1;   // LOOP_T ends on the last tile
          else
            loop_count = kp >> (1 + 32'(!fe_rfull) + 32'(!fe_cfull));   // Kp / (2d)
          if (loop_count == 0) begin
            pc_n = pc_q + 1 + PCW'(arg);                         // skip the body
          end else if (sp_q == 2 || (loop_is_t && lt_seen_q)) begin
            // a third level, or a LOOP_T after (or inside) another: the stream counters walk
            // the tiles once per program, so a second tile loop would wait for panels forever
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
        tq_pkg::OP_FENCE: fence_n = 1'b1;
        default: begin err = 1'b1; err_code = tq_pkg::ERR_OPCODE; end
      endcase

      // zero-overhead loop-back: the inner frame may end and pop, then the outer frame may end
      // at the same pc (model: the while loop in step_front)
      if (sp_n == 2'd2 && pc_n == f1_end_n) begin
        if (f1_t_n ? !fe_last : (f1_rem_n > 32'd1)) begin
          f1_rem_n   = f1_rem_n - 32'd1;
          pc_n       = f1_start_n;
          fe_advance = f1_t_n;
        end else begin
          sp_n = 2'd1;
          if (f1_t_n) fe_reset = 1'b1;
          if (pc_n == f0_end_n) begin
            if (f0_t_n ? !fe_last : (f0_rem_n > 32'd1)) begin
              f0_rem_n   = f0_rem_n - 32'd1;
              pc_n       = f0_start_n;
              fe_advance = f0_t_n;
            end else begin
              sp_n = 2'd0;
              if (f0_t_n) fe_reset = 1'b1;
            end
          end
        end
      end else if (sp_n == 2'd1 && pc_n == f0_end_n) begin
        if (f0_t_n ? !fe_last : (f0_rem_n > 32'd1)) begin
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
      if (fe_cols_left > 32'(T)) fe_tj_n = fe_tj_q + 16'd1;
      else begin
        fe_tj_n   = '0;
        fe_ti_n   = fe_ti_q + 16'd1;
        fe_crow_n = fe_crow_q + (cfg_q[tq_pkg::CFG_C_LD] << L2T);
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
    assign we = start_i || (cfg_we && 32'(arg[2:0]) == i);
    tq_cg #(.EN(CLK_GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
    always_ff @(posedge gclk or negedge rst_ni) begin
      if (!rst_ni)              q <= '0;
      else if (we || CLK_GATE)  q <= start_i ? '0 : imm;
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
