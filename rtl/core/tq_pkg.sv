// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Tri-Queue shared definitions: opcodes, config register indices, internal command formats.
// Spec: docs/specification.md section 6.

package tq_pkg;

  // ---- opcodes (instruction byte = opcode[7:4] | arg[3:0]) ----
  localparam logic [3:0] OP_NOP      = 4'h0;
  localparam logic [3:0] OP_CFG      = 4'h1;  // followed by imm32, little-endian
  localparam logic [3:0] OP_STREAM_A = 4'h2;
  localparam logic [3:0] OP_STREAM_B = 4'h3;
  localparam logic [3:0] OP_ZERO_C   = 4'h4;
  localparam logic [3:0] OP_VOUTER   = 4'h5;  // arg[3] AUTO, else arg[1] full rows, arg[0] full cols
  localparam logic [3:0] OP_SWAP_C   = 4'h6;
  localparam logic [3:0] OP_LOOP_K   = 4'h7;
  localparam logic [3:0] OP_LOOP_T   = 4'h8;
  localparam logic [3:0] OP_FENCE    = 4'h9;

  // ---- accumulator form (tq_mac_array ACC_FORM, spec 10.2) ----
  localparam int ACC_CSA = 0;   // carry-save (sum, carry): 64 bits per cell and bank, CPA at drain
  localparam int ACC_CPA = 1;   // plain 32-bit value, carry-propagate add in the loop (default)

  // ---- config registers ----
  localparam int CFG_A_BASE = 0;
  localparam int CFG_B_BASE = 1;
  localparam int CFG_C_BASE = 2;
  localparam int CFG_C_LD   = 3;
  localparam int CFG_K      = 4;
  localparam int CFG_M      = 5;
  localparam int CFG_N      = 6;
  localparam int CFG_MODE   = 7;

  // ---- MODE register bits (CFG_MODE, spec 6.1, 10.1) ----
  localparam int MODE_A_SIGNED  = 0;
  localparam int MODE_B_SIGNED  = 1;
  localparam int MODE_OUT_INT8  = 2;   // 1: C written as int8, clip(acc >> shift, -128, 127), T lanes
  localparam int MODE_SHIFT_LSB = 3;   // MODE[7:3]: right shift (0..31) for int8 output

  // ---- error codes (STATUS.err_code) ----
  localparam logic [3:0] ERR_NONE      = 4'd0;
  localparam logic [3:0] ERR_OPCODE    = 4'd1;  // undefined opcode
  localparam logic [3:0] ERR_CFG_REG   = 4'd2;  // CFG to a register index >= 8
  localparam logic [3:0] ERR_LOOP      = 4'd3;  // a third loop level, or a second LOOP_T
  localparam logic [3:0] ERR_CFG_LOOP  = 4'd4;  // CFG inside a loop body
  localparam logic [3:0] ERR_PC        = 4'd5;  // instruction runs past the program buffer
  localparam logic [3:0] ERR_ALIGN     = 4'd6;  // base address or C_LD not a multiple of the port width

  // ---- MAC command queue entry (front end -> MAC unit) ----
  typedef enum logic [1:0] {
    MC_ZERO = 2'd0,
    MC_SWAP = 2'd1,
    MC_MAC  = 2'd2
  } mcmd_kind_e;

  typedef struct packed {
    mcmd_kind_e  kind;
    logic        rfull;    // MAC: T rows (else T/2)
    logic        cfull;    // MAC: T cols (else T/2)
    logic [31:0] c_addr;   // SWAP: address of the finished tile's C[0][0]
    logic [7:0]  rows;     // SWAP: valid rows in the finished tile (1..T)
    logic [7:0]  cols;     // SWAP: valid cols in the finished tile (1..T)
  } mcmd_t;
  localparam int MCMD_W = 2 + 1 + 1 + 32 + 8 + 8;   // $bits(mcmd_t); Yosys can't take $bits of a type

  // ---- stream command queue entry (front end -> stream engine) ----
  typedef struct packed {
    logic [31:0] addr;     // first byte of the panel
    logic [31:0] vecs;     // panel length in port words
  } scmd_t;
  localparam int SCMD_W = 64;                       // $bits(scmd_t)

endpackage
