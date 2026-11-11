// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// AXI4-Lite slave to the Tri-Queue register port (spec 6.4, decision: register port + thin
// adapters). 32-bit data; the low 12 address bits select the register.
//
// One transaction at a time: AW and W may arrive together or in either order (each channel has a
// one-entry skid buffer), a read is accepted when no write is buffered or in flight, and a new
// transaction starts only after the previous response has been taken. When a write and a read are
// both waiting, they alternate. By default every response is OKAY (unmapped registers read as 0
// and ignore writes in tq_regs); with CHECK_PROT = 1, unprivileged (AxPROT[0] = 0) or non-secure
// (AxPROT[1] = 1) accesses are rejected with SLVERR without asserting reg_req_o.
//
// The register port side is OBI-style: the request is held, unchanged, until reg_gnt_i (tq_regs
// always grants), and every transaction, read or write, gets one reg_rvalid_i response one or
// more cycles later. The B or R response follows it, so B carries the write's completion.
// AWREADY / WREADY / ARREADY depend combinationally on the VALIDs, which AXI allows a slave.

module tq_axil_regs #(
    parameter int ADDR_W     = 12,
    parameter bit CHECK_PROT = 1'b0
) (
    input  logic              clk_i,
    input  logic              rst_ni,
    // AXI4-Lite slave
    input  logic [ADDR_W-1:0] s_awaddr_i,
    input  logic [2:0]        s_awprot_i,
    input  logic              s_awvalid_i,
    output logic              s_awready_o,
    input  logic [31:0]       s_wdata_i,
    input  logic [3:0]        s_wstrb_i,
    input  logic              s_wvalid_i,
    output logic              s_wready_o,
    output logic [1:0]        s_bresp_o,
    output logic              s_bvalid_o,
    input  logic              s_bready_i,
    input  logic [ADDR_W-1:0] s_araddr_i,
    input  logic [2:0]        s_arprot_i,
    input  logic              s_arvalid_i,
    output logic              s_arready_o,
    output logic [31:0]       s_rdata_o,
    output logic [1:0]        s_rresp_o,
    output logic              s_rvalid_o,
    input  logic              s_rready_i,
    // register port (tq_top)
    output logic              reg_req_o,
    input  logic              reg_gnt_i,
    output logic [11:0]       reg_addr_o,
    output logic              reg_we_o,
    output logic [3:0]        reg_be_o,
    output logic [31:0]       reg_wdata_o,
    input  logic              reg_rvalid_i,
    input  logic [31:0]       reg_rdata_i
);
  generate
    if (ADDR_W < 12) begin : g_bad_addr_w
      $error("tq_axil_regs: ADDR_W must be at least 12");
    end
  endgenerate

  logic        wwait_q, bvalid_q, rwait_q, rvalid_q, rd_turn_q, hold_q, hold_wr_q;
  logic        aw_v_q, w_v_q;
  logic [11:0] aw_addr_q;
  logic [2:0]  aw_prot_q;
  logic [31:0] w_data_q, rdata_q;
  logic [3:0]  w_strb_q;
  logic [1:0]  bresp_q, rresp_q;

  // An ungranted request keeps its kind (hold_q): AXI keeps the VALIDs up meanwhile, so the
  // address and data stay the same as well.
  logic        busy, have_aw, have_w, wr_ok, rd_ok, sel_wr, sel_rd, aw_only, w_only;
  logic [11:0] wr_addr;
  logic [2:0]  wr_prot;
  logic [31:0] wr_data;
  logic [3:0]  wr_strb;
  logic        wr_prot_err, rd_prot_err, wr_accept, rd_accept;

  assign busy        = wwait_q || bvalid_q || rwait_q || rvalid_q;
  assign have_aw     = aw_v_q || s_awvalid_i;
  assign have_w      = w_v_q  || s_wvalid_i;
  assign wr_ok       = !busy && have_aw && have_w;
  assign rd_ok       = !busy && !aw_v_q && !w_v_q && s_arvalid_i;
  assign sel_wr      = hold_q ? hold_wr_q  : wr_ok && !(rd_ok && rd_turn_q);
  assign sel_rd      = hold_q ? !hold_wr_q : rd_ok && !sel_wr;
  assign aw_only     = !busy && !hold_q && !sel_rd && !aw_v_q && s_awvalid_i && !have_w;
  assign w_only      = !busy && !hold_q && !sel_rd && !w_v_q  && s_wvalid_i  && !have_aw;

  assign wr_addr     = aw_v_q ? aw_addr_q : 12'(s_awaddr_i);
  assign wr_prot     = aw_v_q ? aw_prot_q : s_awprot_i;
  assign wr_data     = w_v_q  ? w_data_q  : s_wdata_i;
  assign wr_strb     = w_v_q  ? w_strb_q  : s_wstrb_i;
  assign wr_prot_err = CHECK_PROT && (!wr_prot[0] || wr_prot[1]);
  assign rd_prot_err = CHECK_PROT && (!s_arprot_i[0] || s_arprot_i[1]);

  assign reg_req_o   = (sel_wr && !wr_prot_err) || (sel_rd && !rd_prot_err);
  assign reg_we_o    = sel_wr;
  assign reg_addr_o  = sel_wr ? wr_addr : 12'(s_araddr_i);
  assign reg_be_o    = sel_wr ? wr_strb : 4'h0;
  assign reg_wdata_o = wr_data;

  assign wr_accept   = sel_wr && (wr_prot_err || reg_gnt_i);
  assign rd_accept   = sel_rd && (rd_prot_err || reg_gnt_i);
  assign s_awready_o = aw_only || (!aw_v_q && wr_accept);
  assign s_wready_o  = w_only  || (!w_v_q  && wr_accept);
  assign s_arready_o = rd_accept;
  assign s_bresp_o   = bresp_q;
  assign s_bvalid_o  = bvalid_q;
  assign s_rresp_o   = rresp_q;
  assign s_rvalid_o  = rvalid_q;
  assign s_rdata_o   = rdata_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      wwait_q <= 1'b0; bvalid_q <= 1'b0; rwait_q <= 1'b0; rvalid_q <= 1'b0; rd_turn_q <= 1'b0;
      hold_q <= 1'b0; hold_wr_q <= 1'b0; rdata_q <= '0;
      aw_v_q <= 1'b0; w_v_q <= 1'b0; aw_addr_q <= '0; aw_prot_q <= '0;
      w_data_q <= '0; w_strb_q <= '0; bresp_q <= 2'b00; rresp_q <= 2'b00;
    end else begin
      hold_q    <= reg_req_o && !reg_gnt_i;
      hold_wr_q <= sel_wr;
      if (aw_only) begin
        aw_v_q    <= 1'b1;
        aw_addr_q <= 12'(s_awaddr_i);
        aw_prot_q <= s_awprot_i;
      end else if (wr_accept) begin
        aw_v_q <= 1'b0;
      end
      if (w_only) begin
        w_v_q    <= 1'b1;
        w_data_q <= s_wdata_i;
        w_strb_q <= s_wstrb_i;
      end else if (wr_accept) begin
        w_v_q <= 1'b0;
      end
      if (wr_accept) begin
        rd_turn_q <= 1'b1;                        // the other kind goes first next time
        if (wr_prot_err) begin
          bvalid_q <= 1'b1;
          bresp_q  <= 2'b10;
        end else begin
          wwait_q  <= 1'b1;
          bresp_q  <= 2'b00;
        end
      end else if (rd_accept) begin
        rd_turn_q <= 1'b0;
        if (rd_prot_err) begin
          rvalid_q <= 1'b1;
          rresp_q  <= 2'b10;
          rdata_q  <= 32'd0;
        end else begin
          rwait_q  <= 1'b1;
          rresp_q  <= 2'b00;
        end
      end
      if (wwait_q && reg_rvalid_i) begin
        wwait_q  <= 1'b0;
        bvalid_q <= 1'b1;
      end
      if (bvalid_q && s_bready_i) bvalid_q <= 1'b0;
      if (rwait_q && reg_rvalid_i) begin
        rwait_q  <= 1'b0;
        rvalid_q <= 1'b1;
        rdata_q  <= reg_rdata_i;
      end
      if (rvalid_q && s_rready_i) rvalid_q <= 1'b0;
    end
  end

`ifdef FORMAL
  // ---- properties, proved by k-induction (verif/formal/tq_axil_regs.sby) ----
  logic past_valid = 1'b0;
  always_ff @(posedge clk_i) past_valid <= 1'b1;
  always_comb if (!past_valid) assume (!rst_ni);

  // master: AXI handshake rules (a VALID with its payload stays until READY)
  always_ff @(posedge clk_i) begin
    if (past_valid && $past(rst_ni) && rst_ni) begin
      if ($past(s_awvalid_i && !s_awready_o))
        assume (s_awvalid_i && s_awaddr_i == $past(s_awaddr_i) && s_awprot_i == $past(s_awprot_i));
      if ($past(s_wvalid_i && !s_wready_o))
        assume (s_wvalid_i && s_wdata_i == $past(s_wdata_i) && s_wstrb_i == $past(s_wstrb_i));
      if ($past(s_arvalid_i && !s_arready_o))
        assume (s_arvalid_i && s_araddr_i == $past(s_araddr_i) && s_arprot_i == $past(s_arprot_i));
    end
  end
  // register port (OBI): one response per accepted request, one or more cycles later
  logic obi_out_q;                                 // an accepted request awaits its response
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) obi_out_q <= 1'b0;
    else if (reg_req_o && reg_gnt_i) obi_out_q <= 1'b1;
    else if (reg_rvalid_i) obi_out_q <= 1'b0;
  end
  always_comb if (rst_ni && reg_rvalid_i) assume (obi_out_q);

  // slave: AXI rules
  always_ff @(posedge clk_i) begin
    if (past_valid && $past(rst_ni) && rst_ni) begin
      if ($past(s_bvalid_o && !s_bready_i)) assert (s_bvalid_o && s_bresp_o == $past(s_bresp_o));
      if ($past(s_rvalid_o && !s_rready_i))
        assert (s_rvalid_o && s_rdata_o == $past(s_rdata_o) && s_rresp_o == $past(s_rresp_o));
      // OBI: a request without gnt is held unchanged
      if ($past(reg_req_o && !reg_gnt_i))
        assert (reg_req_o && reg_we_o == $past(reg_we_o) && reg_addr_o == $past(reg_addr_o)
                && reg_be_o == $past(reg_be_o) && (!reg_we_o || reg_wdata_o == $past(reg_wdata_o)));
    end
  end
  // One response per request, and nothing new while one is open
  logic [1:0] open_aw, open_w, open_r;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      open_aw <= '0; open_w <= '0; open_r <= '0;
    end else begin
      open_aw <= open_aw + 2'(s_awvalid_i && s_awready_o) - 2'(s_bvalid_o && s_bready_i);
      open_w  <= open_w  + 2'(s_wvalid_i  && s_wready_o)  - 2'(s_bvalid_o && s_bready_i);
      open_r  <= open_r  + 2'(s_arvalid_i && s_arready_o) - 2'(s_rvalid_o && s_rready_i);
    end
  end
  always_comb begin
    if (rst_ni) begin
      assert (32'(open_aw) + 32'(open_r) <= 1);
      assert (32'(open_w)  + 32'(open_r) <= 1);
      assert (!s_bvalid_o || (open_aw == 2'd1 && open_w == 2'd1));
      assert (!s_rvalid_o || open_r == 2'd1);
      assert (!s_awready_o || (open_aw == 2'd0 && open_r == 2'd0));
      assert (!s_wready_o  || (open_w  == 2'd0 && open_r == 2'd0));
      assert (!s_arready_o || (open_aw == 2'd0 && open_w == 2'd0 && open_r == 2'd0));
      assert (!(reg_req_o && reg_we_o) || ((aw_v_q || s_awvalid_i) && (w_v_q || s_wvalid_i)));
      assert (!(reg_req_o && !reg_we_o) || s_arvalid_i);
      // inductive invariants
      assert (!(aw_v_q && w_v_q));
      assert (!((aw_v_q || w_v_q) && busy));
      assert (32'(wwait_q) + 32'(bvalid_q) + 32'(rwait_q) + 32'(rvalid_q) <= 1);
      assert (open_aw == 2'(aw_v_q || wwait_q || bvalid_q));
      assert (open_w  == 2'(w_v_q  || wwait_q || bvalid_q));
      assert (open_r  == 2'(rwait_q || rvalid_q));
      assert (obi_out_q == (wwait_q || rwait_q));
      assert (!hold_q || (reg_req_o && !busy));
      assert (!(hold_q && !hold_wr_q) || (!aw_v_q && !w_v_q));
      if (CHECK_PROT) begin
        assert (!reg_req_o || (reg_we_o ? (wr_prot[0] && !wr_prot[1])
                                        : (s_arprot_i[0] && !s_arprot_i[1])));
      end
    end
  end
  // reachability: a write and a read complete, also after a held (ungranted) request
  logic held_wr_q, held_rd_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      held_wr_q <= 1'b0; held_rd_q <= 1'b0;
    end else begin
      if (hold_q &&  hold_wr_q) held_wr_q <= 1'b1;
      if (hold_q && !hold_wr_q) held_rd_q <= 1'b1;
    end
  end
  always_ff @(posedge clk_i) begin
    if (past_valid && rst_ni) begin
      cover (s_bvalid_o && s_bready_i);
      cover (s_rvalid_o && s_rready_i && s_rdata_o == 32'h5451_0001);
      cover (held_wr_q && s_bvalid_o && s_bready_i);
      cover (held_rd_q && s_rvalid_o && s_rready_i);
    end
  end
`endif

  /* verilator lint_off UNUSEDSIGNAL */
  logic unused;
  assign unused = ^{s_awprot_i, s_arprot_i, s_awaddr_i, s_araddr_i, wr_prot};
  /* verilator lint_on UNUSEDSIGNAL */

endmodule
