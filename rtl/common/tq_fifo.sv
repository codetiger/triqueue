// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Ring-buffer FIFO: one push and up to two pops per cycle. The two oldest entries are visible
// combinationally. A push into a full FIFO is allowed in the same cycle as a pop (the popped
// slot is reused). The user must never pop more than `count_o` entries or overflow.
// With CLK_GATE, each entry of a FIFO at least 16 bits wide has its own clock gate (tq_cg), so a
// push clocks one entry only.

module tq_fifo #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 4,                    // >= 2
    parameter bit CLK_GATE = 1'b0,
    localparam int CW = $clog2(DEPTH + 1)
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             clear_i,           // synchronous flush
    input  logic             push_i,
    input  logic [WIDTH-1:0] data_i,
    input  logic [1:0]       pop_i,             // 0, 1 or 2 entries
    output logic [WIDTH-1:0] head0_o,           // oldest entry
    output logic [WIDTH-1:0] head1_o,           // second oldest entry
    output logic [CW-1:0]    count_o
);
  localparam int PW = $clog2(DEPTH);

  logic [DEPTH-1:0][WIDTH-1:0] mem_q;
  logic [PW-1:0]    rd_q, wr_q;
  logic [CW-1:0]    count_q;

  function automatic logic [PW-1:0] wrap_add(input logic [PW-1:0] p, input logic [1:0] inc);
    logic [PW+1:0] s;
    s = {2'b00, p} + {{PW{1'b0}}, inc};
    if (s >= (PW + 2)'(DEPTH)) s = s - (PW + 2)'(DEPTH);
    wrap_add = s[PW-1:0];
  endfunction

  assign head0_o = mem_q[rd_q];
  assign head1_o = mem_q[wrap_add(rd_q, 2'd1)];
  assign count_o = count_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rd_q    <= '0;
      wr_q    <= '0;
      count_q <= '0;
    end else if (clear_i) begin
      rd_q    <= '0;
      wr_q    <= '0;
      count_q <= '0;
    end else begin
      if (push_i) wr_q <= wrap_add(wr_q, 2'd1);
      rd_q    <= wrap_add(rd_q, pop_i);
      count_q <= count_q + CW'(push_i) - CW'(pop_i);
    end
  end

  localparam bit GATE = CLK_GATE && WIDTH >= 16;
  for (genvar i = 0; i < DEPTH; i++) begin : g_mem
    logic             we, gclk;
    logic [WIDTH-1:0] q;
    assign we = push_i && !clear_i && 32'(wr_q) == i;
    tq_cg #(.EN(GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
    always_ff @(posedge gclk) begin
      if (we || GATE) q <= data_i;
    end
    assign mem_q[i] = q;
  end

`ifdef FORMAL
`ifdef TQ_FORMAL_CORE
  // ---- core-level proof (verif/formal/tq_core.sby): legal use is a property of the core ----
  always_comb begin
    if (rst_ni && !clear_i) begin
      assert (pop_i != 2'd3);
      assert (32'(pop_i) <= 32'(count_q));                                // no underflow
      assert (32'(count_q) - 32'(pop_i) + 32'(push_i) <= DEPTH);          // no overflow
    end
  end
`else
  // ---- properties, proved by k-induction with SymbiYosys (verif/formal/tq_fifo.sby) ----
  logic past_valid = 1'b0;
  always_ff @(posedge clk_i) past_valid <= 1'b1;
  always_comb if (!past_valid) assume (!rst_ni);

  // environment: legal use (the core guarantees this through credits and queue-full checks)
  always_comb begin
    assume (pop_i != 2'd3);
    assume (32'(pop_i) <= 32'(count_q));
    assume (32'(count_q) - 32'(pop_i) + 32'(push_i) <= DEPTH);
  end

  // pointer / occupancy invariant
  always_comb begin
    if (rst_ni) begin
      assert (32'(count_q) <= DEPTH);
      assert (32'(rd_q) < DEPTH && 32'(wr_q) < DEPTH);
      assert ((32'(rd_q) + 32'(count_q)) % DEPTH == 32'(wr_q));
    end
  end

  // ordering and data integrity: an arbitrary entry (the track_n-th push, holding track_d) is
  // stored in the right slot and leaves as the track_n-th pop
  (* anyconst *) logic [4:0]       track_n;
  (* anyconst *) logic [WIDTH-1:0] track_d;
  logic [4:0] pushed_q, popped_q;
  always_ff @(posedge clk_i) begin
    if (!rst_ni || clear_i) begin
      pushed_q <= '0;
      popped_q <= '0;
    end else begin
      pushed_q <= pushed_q + 5'(push_i);
      popped_q <= popped_q + 5'(pop_i);
    end
  end
  always_comb begin
    assume (pushed_q < 5'd28);                              // counters never wrap
    if (rst_ni && !clear_i && push_i && pushed_q == track_n) assume (data_i == track_d);
    if (rst_ni) begin
      assert (32'(pushed_q) - 32'(popped_q) == 32'(count_q));
      if (popped_q <= track_n && track_n < pushed_q) begin
        assert (mem_q[(32'(rd_q) + 32'(track_n - popped_q)) % DEPTH] == track_d);
        if (popped_q == track_n)        assert (head0_o == track_d);
        if (popped_q + 5'd1 == track_n) assert (head1_o == track_d);
      end
    end
  end
`endif
`endif

endmodule
