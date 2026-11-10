// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
/**
 * Visual Controller for the Tri-Queue GEMM RTL-to-Silicon Showcase.
 * Streamlined low-text, high-visual density across:
 *   1. Full-Width Interactive Logical RTL Schematic + On-Demand Popover
 *   2. Visual Tri-Queue Slots, 8x8 Outer-Product Matrix & Draggable Timeline
 *   3. WebGL 2.0 3D Silicon Die (`6_final.def`) with Verified Orientation & Compass
 */
(function () {
  "use strict";

  const SHORT_SCENARIO_NAMES = {
    overlap_16x16x32: "16×16×32 Overlap",
    arity_12x12x16: "12×12×16 Arity",
    panel_8x16x16: "8×16×16 Panel",
    backpressure_8x16x16: "8×16×16 Stall",
  };

  const RTL_BLOCKS = {
    regs_prog: {
      id: "regs_prog",
      modKey: "regs_prog",
      title: "tq_regs · 64B Program Buf",
      file: "rtl/core/tq_regs.sv",
      flops: "768 FFs",
      areaPct: "6.4%",
      cgCount: "64 ICGs",
      formal: "Formal Proved",
      tag: "32b CSR port · 64B instruction buffer (1 ICG/byte) · 5 perf counters",
      svCode: `for (genvar i = 0; i < PROG_BYTES; i++) begin : g_prog
  assign we = prog_we && (pidx == PW'(i)) && reg_be_i[i[1:0]];
  tq_cg #(.EN(CLK_GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
  always_ff @(posedge gclk or negedge rst_ni)
    if (!rst_ni) q <= 8'h0; else if (we || CLK_GATE) q <= reg_wdata_i[i[1:0]*8 +: 8];
end`,
    },
    frontend: {
      id: "frontend",
      modKey: "frontend",
      title: "tq_frontend · Decoder & Loops",
      file: "rtl/core/tq_frontend.sv",
      flops: "412 FFs",
      areaPct: "8.1%",
      cgCount: "8 ICGs",
      formal: "PDR Proved",
      tag: "1 instr/cyc decode · 2-level hardware loop stack (LOOP_T, LOOP_K) · 0 multipliers",
      svCode: `if (next_col) begin
  tj_d = tj_q + 1'b1; b_off_d = b_off_q + b_step; c_col_d = c_col_q + 32'(4*T);
end else begin
  ti_d = ti_q + 1'b1; tj_d = '0; a_off_d = a_off_q + a_step; c_row_d = c_row_q + c_row_step;
end`,
    },
    stream_eng: {
      id: "stream_eng",
      modKey: "stream_eng",
      title: "tq_stream · Credit Streamer",
      file: "rtl/core/tq_stream.sv",
      flops: "594 FFs",
      areaPct: "5.2%",
      cgCount: "4 ICGs",
      formal: "OBI Stable",
      tag: "Round-robin A/B panel walker · credit counters (occupancy + inflight <= depth)",
      svCode: `assign can_a = act_a && !a_buf && !pb_wait_a && (cred_a < CRW'(FIFO_DEPTH));
assign can_b = act_b && (cred_b < CRW'(FIFO_DEPTH));
assign rd_sel_b = rd_hold_q ? rd_hold_b_q : (rr_q ? (can_b || !can_a) : (!can_a && can_b));`,
    },
    qa_fifo: {
      id: "qa_fifo",
      modKey: "qa_fifo",
      title: "u_qa · Queue 1 (A FIFO)",
      file: "rtl/common/tq_fifo.sv",
      flops: "512 FFs",
      areaPct: "4.6%",
      cgCount: "4 ICGs",
      formal: "FIFO Proved",
      tag: "4 × 128b ring FIFO · 16B A-column slices (8 rows × 2 k-steps) · per-slot ICG",
      svCode: `for (genvar i = 0; i < DEPTH; i++) begin : g_mem
  assign we = push_i && (wr_q == PtrW'(i));
  tq_cg #(.EN(CG)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
  always_ff @(posedge gclk) if (we || CG) q <= data_i;
end`,
    },
    qb_fifo: {
      id: "qb_fifo",
      modKey: "qb_fifo",
      title: "u_qb · Queue 2 (B FIFO)",
      file: "rtl/common/tq_fifo.sv",
      flops: "512 FFs",
      areaPct: "4.6%",
      cgCount: "4 ICGs",
      formal: "FIFO Proved",
      tag: "4 × 128b ring FIFO · 16B B-row slices (2 k-steps × 8 cols) · 1 or 2 vec pop",
      svCode: `tq_fifo #(.WIDTH(VB), .DEPTH(FIFO_DEPTH), .CLK_GATE(CLK_GATE)) u_qb (
  .clk_i, .rst_ni, .clear_i(clear), .push_i(qb_push), .data_i(q_data),
  .pop_i(qb_pop), .head0_o(qb0), .head1_o(qb1), .count_o(qb_cnt));`,
    },
    mac_cmd_q: {
      id: "mac_cmd_q",
      modKey: "mac_cmd_q",
      title: "u_macq · Queue 3 (MAC CmdQ)",
      file: "rtl/core/tq_core.sv",
      flops: "156 FFs",
      areaPct: "1.8%",
      cgCount: "3 ICGs",
      formal: "PDR Proved",
      tag: "3 × 52b command FIFO · folds ZERO_C / SWAP_C into first VOUTER_MAC beat in 0 cycles",
      svCode: `is_zero = !b1_q && has0 && e0.kind == tq_pkg::MC_ZERO && !drain_act;
is_swap = !b1_q && has0 && e0.kind == tq_pkg::MC_SWAP && !drain_act;
mc      = (is_zero || is_swap) ? e1 : e0; // 0-cycle swap + beat start!`,
    },
    mac_prod: {
      id: "mac_prod",
      modKey: "mac_prod",
      title: "tq_mac_array · 64× (9×9) MUL",
      file: "rtl/core/tq_mac_array.sv",
      flops: "1,156 FFs",
      areaPct: "38.2%",
      cgCount: "6 ICGs",
      formal: "Bit-Exact",
      tag: "4 quadrants (TL, TR, BL, BR) · 64 × (9×9 → 18b) signed multipliers + pipeline reg",
      svCode: `d   = (rfull_i && cfull_i) ? 3'd1 : (rfull_i || cfull_i) ? 3'd2 : 3'd4;
off = (rfull_i ? 3'd0 : (cfull_i ? 3'(qi) : 3'(2*qi))) + (cfull_i ? 3'd0 : 3'(qj));
assign av = {a_signed_i & aq[i*8+7], aq[i*8 +: 8]};
assign bv = {b_signed_i & bq[j*8+7], bq[j*8 +: 8]};
assign p  = av * bv;`,
    },
    mac_banks: {
      id: "mac_banks",
      modKey: "mac_bank0",
      title: "tq_mac_array · Banks X & Y",
      file: "rtl/core/tq_mac_array.sv",
      flops: "4,096 FFs",
      areaPct: "27.4%",
      cgCount: "8 ICGs",
      formal: "Arity Proved",
      tag: "Ping-pong 8×8 int32 tiles · fixed quadrant arity (TL=4, TR=2, BL=2, BR=1)",
      svCode: `s = base_v + p_self
  + ((Q == 0) ? (((!prf_q) ? p_bl : 32'd0) + ((!pcf_q) ? p_tr : 32'd0) + ((!prf_q && !pcf_q) ? p_br : 32'd0))
   : (Q == 1) ? ((!prf_q && pcf_q) ? p_br : 32'd0)
   : (Q == 2) ? ((prf_q && !pcf_q) ? p_br : 32'd0) : 32'd0);`,
    },
    drain_unit: {
      id: "drain_unit",
      modKey: "drain_unit",
      title: "tq_drain · Background Drain",
      file: "rtl/core/tq_drain.sv",
      flops: "184 FFs",
      areaPct: "3.7%",
      cgCount: "1 ICG",
      formal: "Write-Ack Proved",
      tag: "16B/cyc background writeout of idle bank under active MAC · auto-clears bank",
      svCode: `if (rd_valid_q && (!stg_v_q || wr_fire_i) && rd_last_q) begin
  clear_bank_o     = 1'b1;
  clear_bank_sel_o = bank_q;
end`,
    },
  };

  const state = {
    scenarios: window.TRIQUEUE_SCENARIOS || [],
    scenIdx: 0,
    cycleIdx: 0,
    playing: false,
    speedMs: 220,
    timer: null,
    selectedRtlBlock: null,
    showSvCode: false,
    pdk: "asap7",
    chip3d: null,
    wfHoverX: null,
  };

  function currentScenario() {
    return state.scenarios[state.scenIdx] || state.scenarios[0];
  }

  function currentCycleData() {
    const sc = currentScenario();
    if (!sc || !sc.cycles || sc.cycles.length === 0) return null;
    const idx = Math.max(0, Math.min(sc.cycles.length - 1, state.cycleIdx));
    return sc.cycles[idx];
  }

  function init() {
    renderScenarioTabs();
    bindTransportControls();
    bindWaterfallTimeline();
    initChip3DSection();
    selectScenario(0);
  }

  function renderScenarioTabs() {
    const container = document.getElementById("scenario-tabs");
    container.innerHTML = "";
    state.scenarios.forEach((sc, idx) => {
      const btn = document.createElement("button");
      btn.className = "sc-tab" + (idx === state.scenIdx ? " active" : "");
      const shortTitle = SHORT_SCENARIO_NAMES[sc.id] || sc.title.split(" ").slice(0, 2).join(" ");
      btn.innerHTML = `<span>${shortTitle}</span><span class="sc-badge">${Math.round(sc.stats.utilization * 100)}%</span>`;
      btn.addEventListener("click", () => selectScenario(idx));
      container.appendChild(btn);
    });
  }

  function selectScenario(idx) {
    state.scenIdx = idx;
    const sc = currentScenario();
    const firstBeatIdx = sc.cycles.findIndex((c) => c.mac_beat !== null);
    state.cycleIdx = firstBeatIdx >= 0 ? firstBeatIdx : 0;

    renderScenarioTabs();
    renderKernelTrack(sc);
    updateAllViews();
  }

  function setCycle(newIdx) {
    const sc = currentScenario();
    if (!sc) return;
    state.cycleIdx = Math.max(0, Math.min(sc.cycles.length - 1, newIdx));
    updateAllViews();
  }

  const SVG_PLAY  = `<svg class="ui-icon" viewBox="0 0 16 16"><path d="M4.5 2.8l8.5 5.2-8.5 5.2V2.8z"/></svg><span>Play</span>`;
  const SVG_PAUSE = `<svg class="ui-icon" viewBox="0 0 16 16"><path d="M4.5 3h2.4v10H4.5V3zm4.6 0h2.4v10H9.1V3z"/></svg><span>Pause</span>`;

  function stopPlay() {
    if (state.playing) {
      state.playing = false;
      document.getElementById("btn-play").innerHTML = SVG_PLAY;
      if (state.timer) clearInterval(state.timer);
    }
  }

  function togglePlay() {
    state.playing = !state.playing;
    const btn = document.getElementById("btn-play");
    btn.innerHTML = state.playing ? SVG_PAUSE : SVG_PLAY;
    if (state.timer) clearInterval(state.timer);
    if (state.playing) {
      state.timer = setInterval(() => {
        const sc = currentScenario();
        state.cycleIdx = state.cycleIdx >= sc.cycles.length - 1 ? 0 : state.cycleIdx + 1;
        updateAllViews();
      }, state.speedMs);
    }
  }

  function bindTransportControls() {
    document.getElementById("btn-reset").addEventListener("click", () => { stopPlay(); setCycle(0); });
    document.getElementById("btn-prev").addEventListener("click", () => { stopPlay(); setCycle(state.cycleIdx - 1); });
    document.getElementById("btn-next").addEventListener("click", () => { stopPlay(); setCycle(state.cycleIdx + 1); });
    document.getElementById("btn-play").addEventListener("click", togglePlay);

    document.getElementById("btn-next-beat").addEventListener("click", () => {
      stopPlay();
      const sc = currentScenario();
      for (let i = state.cycleIdx + 1; i < sc.cycles.length; i++) {
        if (sc.cycles[i].mac_beat !== null) {
          setCycle(i);
          return;
        }
      }
      setCycle(0);
    });

    document.getElementById("btn-next-swap").addEventListener("click", () => {
      stopPlay();
      const sc = currentScenario();
      for (let i = state.cycleIdx + 1; i < sc.cycles.length; i++) {
        if (sc.cycles[i].events.some((e) => e.startsWith("mac swap"))) {
          setCycle(i);
          return;
        }
      }
      setCycle(sc.cycles.length - 1);
    });

    document.getElementById("speed-select").addEventListener("change", (e) => {
      state.speedMs = parseInt(e.target.value, 10);
      if (state.playing) {
        togglePlay();
        togglePlay();
      }
    });

    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      if (e.code === "ArrowRight") {
        e.preventDefault();
        stopPlay();
        setCycle(state.cycleIdx + 1);
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        stopPlay();
        setCycle(state.cycleIdx - 1);
      } else if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "Escape" && state.selectedRtlBlock) {
        state.selectedRtlBlock = null;
        updateAllViews();
      }
    });
  }

  function updateAllViews() {
    const sc = currentScenario();
    const cyc = currentCycleData();
    if (!sc || !cyc) return;

    document.getElementById("cycle-readout").textContent = `CYC ${cyc.cycle} / ${sc.cycles.length - 1}`;

    renderLogicalSchematic(sc, cyc);
    renderRtlInspector();
    renderHowItWorks(sc, cyc);
    renderWaterfall(sc, cyc);

    if (state.chip3d) {
      state.chip3d.setActiveCycleState(cyc);
    }
  }

  // =========================================================================
  // COMPONENT 1: VISUAL LOGICAL ARCHITECTURE DIAGRAM & POPOVER
  // =========================================================================
  function renderLogicalSchematic(sc, cyc) {
    const container = document.getElementById("logical-svg-container");
    const evs = cyc.events || [];
    const hasFeIssue = evs.some((e) => e.startsWith("fe issue"));
    const hasRdPort  = evs.some((e) => e.startsWith("port rd"));
    const hasPbHit   = evs.some((e) => e.startsWith("pb rd"));
    const hasWrPort  = evs.some((e) => e.startsWith("port wr"));
    const hasBeat    = cyc.mac_beat !== null;
    const hasSwap    = evs.some((e) => e.startsWith("mac swap"));
    const qaArr      = cyc.arrived_a > 0;
    const qbArr      = cyc.arrived_b > 0;
    const activeBank = cyc.active_bank;
    const isDraining = cyc.drain !== null;

    const sel = state.selectedRtlBlock;
    const nodeClass = (id) => `rtl-node ${sel === id ? "selected" : ""}`;

    const drawSlots = (count, max, x0, y0, w, h, color) => {
      let out = "";
      const gap = 4;
      const sw = (w - gap * (max - 1)) / max;
      for (let i = 0; i < max; i++) {
        const filled = i < count;
        out += `<rect x="${x0 + i * (sw + gap)}" y="${y0}" width="${sw}" height="${h}" rx="4" fill="${filled ? color : "rgba(21,24,32,0.85)"}" fill-opacity="${filled ? "0.78" : "1"}" stroke="${color}" stroke-opacity="${filled ? "0.9" : "0.28"}"/>`;
      }
      return out;
    };

    const mb = cyc.mac_beat;
    const qColors = ["#81c995", "#8ab4f8", "#fdd663", "#c58af9"];
    const qNames = ["TL", "TR", "BL", "BR"];
    let quadBoxesSvg = "";
    for (let qi = 0; qi < 2; qi++) {
      for (let qj = 0; qj < 2; qj++) {
        const idx = qi * 2 + qj;
        const qx = 404 + qj * 116;
        const qy = 362 + qi * 42;
        const qInfo = mb && mb.quadrants ? mb.quadrants[idx] : null;
        const lbl = qInfo ? `${qNames[idx]}→${qInfo.dest_q} (k+${qInfo.k_step})` : `${qNames[idx]} (4×4)`;
        quadBoxesSvg += `
          <rect x="${qx}" y="${qy}" width="108" height="34" rx="6" fill="${mb ? "rgba(197,138,249,0.18)" : "rgba(25,28,36,0.85)"}" stroke="${qColors[idx]}" stroke-width="${mb ? "1.5" : "1"}" stroke-opacity="0.75"/>
          <text x="${qx + 54}" y="${qy + 21}" text-anchor="middle" fill="${mb ? "#e3e2e6" : "#a9abb4"}" font-family="monospace" font-size="10" font-weight="600">${lbl}</text>
        `;
      }
    }

    container.innerHTML = `
      <svg viewBox="0 0 940 462" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <marker id="arr-cyan" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#8ab4f8"/>
          </marker>
          <marker id="arr-amber" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#fdd663"/>
          </marker>
          <marker id="arr-violet" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#c58af9"/>
          </marker>
          <marker id="arr-coral" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#f28b82"/>
          </marker>
          <marker id="arr-yellow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#fde293"/>
          </marker>
        </defs>

        <!-- Memory Bar -->
        <rect x="20" y="10" width="900" height="36" rx="9" fill="#1e222b" stroke="#78d9ec" stroke-opacity="0.55" stroke-width="1.2" stroke-dasharray="4 3"/>
        <text x="36" y="32" fill="#78d9ec" font-family="monospace" font-size="11.5" font-weight="600">SoC Memory / Scratchpad (128b Read &amp; Write Ports · OBI / AXI4)</text>
        <text x="904" y="32" text-anchor="end" fill="#757780" font-family="monospace" font-size="10.5">A Panel · B Panel · C Tile</text>

        <!-- tq_top Shell -->
        <rect x="20" y="58" width="900" height="394" rx="12" fill="rgba(25,28,34,0.65)" stroke="rgba(196,198,208,0.16)" stroke-width="1.2"/>
        <text x="34" y="76" fill="#757780" font-family="monospace" font-size="10" font-weight="600">tq_top (T=${sc.params.tile}, W=${sc.params.port_bytes}B)</text>

        <!-- ================= ANIMATED BUS WIRES ================= -->
        <path d="M 180 136 L 180 172" stroke="#a8dab5" stroke-opacity="0.75" stroke-width="2" marker-end="url(#arr-yellow)" class="${hasFeIssue ? "bus-wire-active" : ""}"/>
        <path d="M 296 208 L 392 130" stroke="#fde293" stroke-opacity="0.75" stroke-width="2" fill="none" marker-end="url(#arr-yellow)" class="${hasFeIssue ? "bus-wire-active" : ""}"/>
        <path d="M 180 254 L 180 310" stroke="#d7aefb" stroke-opacity="0.75" stroke-width="2.2" marker-end="url(#arr-violet)" class="${cyc.cg.macq ? "bus-wire-active" : ""}"/>

        <!-- SRAM -> Stream -->
        <path d="M 525 46 L 525 88" stroke="${hasRdPort ? "#8ab4f8" : "#383d49"}" stroke-width="3" marker-end="url(#arr-cyan)" class="${hasRdPort ? "bus-wire-active" : ""}"/>
        <text x="534" y="73" fill="#8ab4f8" font-family="monospace" font-size="10" font-weight="600">16 B/cyc</text>

        <!-- Stream -> QA / QB -->
        <path d="M 465 156 L 428 206" stroke="${qaArr || hasPbHit ? "#8ab4f8" : "#383d49"}" stroke-width="2.5" marker-end="url(#arr-cyan)" class="${qaArr || hasPbHit ? "bus-wire-active" : ""}"/>
        <path d="M 585 156 L 615 206" stroke="${qbArr ? "#fdd663" : "#383d49"}" stroke-width="2.5" marker-end="url(#arr-amber)" class="${qbArr ? "bus-wire-active" : ""}"/>

        <!-- QA / QB / MACQ -> Multiplier Array -->
        <path d="M 428 286 L 472 326" stroke="${hasBeat ? "#8ab4f8" : "#383d49"}" stroke-width="2.6" marker-end="url(#arr-cyan)" class="${hasBeat ? "bus-wire-active" : ""}"/>
        <path d="M 615 286 L 572 326" stroke="${hasBeat ? "#fdd663" : "#383d49"}" stroke-width="2.6" marker-end="url(#arr-amber)" class="${hasBeat ? "bus-wire-active" : ""}"/>
        <path d="M 296 364 L 388 378" stroke="${hasBeat || hasSwap ? "#d7aefb" : "#383d49"}" stroke-width="2.2" marker-end="url(#arr-violet)" class="${hasBeat || hasSwap ? "bus-wire-active" : ""}"/>

        <!-- Multiplier Array -> Accumulator Banks -->
        <path d="M 652 380 L 700 380" stroke="${hasBeat ? "#c58af9" : "#383d49"}" stroke-width="3" marker-end="url(#arr-violet)" class="${hasBeat ? "bus-wire-active" : ""}"/>
        <text x="676" y="370" text-anchor="middle" fill="#d7aefb" font-family="monospace" font-size="9.5" font-weight="600">64×18b</text>

        <!-- Idle Bank -> Drain -> SRAM Write -->
        <path d="M 802 300 L 802 224" stroke="${isDraining ? "#f28b82" : "#383d49"}" stroke-width="2.6" marker-end="url(#arr-coral)" class="${isDraining ? "bus-wire-active" : ""}"/>
        <path d="M 802 148 L 802 46" stroke="${hasWrPort ? "#f28b82" : "#383d49"}" stroke-width="3" marker-end="url(#arr-coral)" class="${hasWrPort ? "bus-wire-active" : ""}"/>
        <text x="812" y="73" fill="#f28b82" font-family="monospace" font-size="10" font-weight="600">16 B/cyc</text>

        <!-- ================= CLICKABLE HARDWARE BLOCKS (M3 TONAL SURFACES) ================= -->

        <!-- 1. tq_regs -->
        <g class="${nodeClass("regs_prog")}" data-block="regs_prog" transform="translate(38, 88)">
          <rect class="node-box" width="258" height="48" rx="10" fill="#20242c" stroke="#a8dab5" stroke-opacity="0.65" stroke-width="1.5"/>
          <text x="12" y="20" fill="#a8dab5" font-family="monospace" font-size="11.5" font-weight="600">tq_regs · 64B Prog</text>
          <text x="12" y="37" fill="#a9abb4" font-family="monospace" font-size="10">OBI / AXI-Lite / CV-X-IF</text>
          <rect x="192" y="13" width="54" height="20" rx="6" fill="rgba(168,218,181,0.14)" stroke="#a8dab5" stroke-opacity="0.5"/>
          <text x="219" y="27" text-anchor="middle" fill="#a8dab5" font-family="monospace" font-size="9.5" font-weight="600">64 ICG</text>
        </g>

        <!-- 2. tq_frontend -->
        <g class="${nodeClass("frontend")}" data-block="frontend" transform="translate(38, 172)">
          <rect class="node-box" width="258" height="82" rx="10" fill="#20242c" stroke="#fde293" stroke-opacity="0.65" stroke-width="1.5"/>
          <text x="12" y="21" fill="#fde293" font-family="monospace" font-size="11.5" font-weight="600">tq_frontend · Decoder</text>
          <rect x="176" y="8" width="70" height="19" rx="6" fill="rgba(253,226,147,0.16)"/>
          <text x="211" y="21" text-anchor="middle" fill="#fde293" font-family="monospace" font-size="10" font-weight="600">PC 0x${cyc.pc.toString(16).padStart(2, "0")}</text>
          <text x="12" y="45" fill="#e3e2e6" font-family="monospace" font-size="10.5">Tile #${cyc.front_tile} · Loops: ${cyc.loops.length}/2</text>
          <text x="12" y="66" fill="#8ab4f8" font-family="monospace" font-size="10">M=${cyc.cfg[5]} N=${cyc.cfg[6]} K=${cyc.cfg[4]}</text>
        </g>

        <!-- 3. tq_stream -->
        <g class="${nodeClass("stream_eng")}" data-block="stream_eng" transform="translate(392, 88)">
          <rect class="node-box" width="262" height="68" rx="10" fill="#20242c" stroke="#78d9ec" stroke-opacity="0.65" stroke-width="1.5"/>
          <text x="12" y="21" fill="#78d9ec" font-family="monospace" font-size="11.5" font-weight="600">tq_stream · Credits</text>
          <rect x="178" y="8" width="74" height="19" rx="6" fill="${hasPbHit ? "rgba(168,218,181,0.22)" : "rgba(120,217,236,0.14)"}"/>
          <text x="215" y="21" text-anchor="middle" fill="${hasPbHit ? "#a8dab5" : "#78d9ec"}" font-family="monospace" font-size="9.5" font-weight="600">${hasPbHit ? "PANEL HIT" : `PIPE ${cyc.inflight.length}`}</text>
          <text x="12" y="46" fill="#e3e2e6" font-family="monospace" font-size="10.5">A-Cmd: ${cyc.scmd_a.length}/2 · B-Cmd: ${cyc.scmd_b.length}/2</text>
        </g>

        <!-- 4. Queue 1: QA FIFO (with visual slot bars) -->
        <g class="${nodeClass("qa_fifo")}" data-block="qa_fifo" transform="translate(348, 206)">
          <rect class="node-box" width="158" height="80" rx="10" fill="#1e232d" stroke="#8ab4f8" stroke-opacity="0.7" stroke-width="1.6"/>
          <text x="12" y="21" fill="#8ab4f8" font-family="monospace" font-size="11.5" font-weight="600">QA FIFO</text>
          <text x="146" y="21" text-anchor="end" fill="#e3e2e6" font-family="monospace" font-size="11" font-weight="600">${cyc.qa.vectors.length}/${sc.params.fifo_depth}</text>
          ${drawSlots(cyc.qa.vectors.length, sc.params.fifo_depth, 12, 32, 134, 18, "#8ab4f8")}
          <text x="12" y="68" fill="#a9abb4" font-family="monospace" font-size="9.5">${cyc.qa.bytes} B · A-Cols</text>
        </g>

        <!-- 5. Queue 2: QB FIFO (with visual slot bars) -->
        <g class="${nodeClass("qb_fifo")}" data-block="qb_fifo" transform="translate(538, 206)">
          <rect class="node-box" width="158" height="80" rx="10" fill="#232229" stroke="#fdd663" stroke-opacity="0.7" stroke-width="1.6"/>
          <text x="12" y="21" fill="#fdd663" font-family="monospace" font-size="11.5" font-weight="600">QB FIFO</text>
          <text x="146" y="21" text-anchor="end" fill="#e3e2e6" font-family="monospace" font-size="11" font-weight="600">${cyc.qb.vectors.length}/${sc.params.fifo_depth}</text>
          ${drawSlots(cyc.qb.vectors.length, sc.params.fifo_depth, 12, 32, 134, 18, "#fdd663")}
          <text x="12" y="68" fill="#a9abb4" font-family="monospace" font-size="9.5">${cyc.qb.bytes} B · B-Rows</text>
        </g>

        <!-- 6. Queue 3: MAC Command Queue (with visual slot bars) -->
        <g class="${nodeClass("mac_cmd_q")}" data-block="mac_cmd_q" transform="translate(38, 310)">
          <rect class="node-box" width="258" height="106" rx="10" fill="#21202c" stroke="#d7aefb" stroke-opacity="0.7" stroke-width="1.6"/>
          <text x="12" y="22" fill="#d7aefb" font-family="monospace" font-size="11.5" font-weight="600">MAC_CMD_Q</text>
          <text x="244" y="22" text-anchor="end" fill="#e3e2e6" font-family="monospace" font-size="11" font-weight="600">${cyc.macq.length}/${sc.params.mac_queue_depth}</text>
          ${drawSlots(cyc.macq.length, sc.params.mac_queue_depth, 12, 34, 232, 20, "#d7aefb")}
          <text x="12" y="76" fill="#e8c6ff" font-family="monospace" font-size="10.5" font-weight="600">
            ${cyc.macq[0] ? `HEAD: ${cyc.macq[0].kind} ${cyc.macq[0].shape || ""}` : "HEAD: EMPTY"}
          </text>
          <text x="12" y="94" fill="#a9abb4" font-family="monospace" font-size="9.5">52-bit Cmds · 0-Cycle Swap</text>
        </g>

        <!-- 7. 64x (9x9) Multiplier Array & 4 Quadrants -->
        <g class="${nodeClass("mac_prod")}" data-block="mac_prod" transform="translate(388, 326)">
          <rect class="node-box" width="264" height="114" rx="10" fill="#211f2d" stroke="#c58af9" stroke-opacity="0.75" stroke-width="1.8"/>
          <text x="12" y="21" fill="#d7aefb" font-family="monospace" font-size="11.5" font-weight="600">64× (9×9) Multipliers</text>
          <text x="252" y="21" text-anchor="end" fill="${mb ? "#a8dab5" : "#757780"}" font-family="monospace" font-size="10.5" font-weight="600">
            ${mb ? `${mb.shape} · B${mb.beat}` : "IDLE"}
          </text>
        </g>
        ${quadBoxesSvg}

        <!-- 8. Ping-Pong Accumulator Banks X & Y -->
        <g class="${nodeClass("mac_banks")}" data-block="mac_banks" transform="translate(700, 300)">
          <rect class="node-box" width="204" height="140" rx="10" fill="#1d2428" stroke="#81c995" stroke-opacity="0.75" stroke-width="1.8"/>
          <text x="12" y="21" fill="#e3e2e6" font-family="monospace" font-size="11.5" font-weight="600">8×8 int32 Banks</text>
          <text x="192" y="21" text-anchor="end" fill="#a9abb4" font-family="monospace" font-size="9.5">4:2:2:1</text>

          <!-- Bank X -->
          <rect x="10" y="34" width="88" height="94" rx="8" fill="${activeBank === 0 ? "rgba(129,201,149,0.20)" : "rgba(21,24,32,0.8)"}" stroke="#81c995" stroke-opacity="${activeBank === 0 ? "0.9" : "0.4"}" stroke-width="${activeBank === 0 ? "1.8" : "1"}"/>
          <text x="54" y="56" text-anchor="middle" fill="#81c995" font-family="monospace" font-size="11" font-weight="600">BANK X</text>
          <text x="54" y="78" text-anchor="middle" fill="${activeBank === 0 ? "#81c995" : isDraining && cyc.drain.bank === 0 ? "#f6aea9" : "#757780"}" font-family="monospace" font-size="10" font-weight="600">
            ${activeBank === 0 ? "MAC" : isDraining && cyc.drain.bank === 0 ? "DRAIN" : "IDLE"}
          </text>
          <circle cx="54" cy="104" r="5.5" fill="${cyc.cg.bank0 ? "#a8dab5" : "#2a2f3a"}"/>

          <!-- Bank Y -->
          <rect x="106" y="34" width="88" height="94" rx="8" fill="${activeBank === 1 ? "rgba(242,139,130,0.20)" : "rgba(21,24,32,0.8)"}" stroke="#f28b82" stroke-opacity="${activeBank === 1 ? "0.9" : "0.4"}" stroke-width="${activeBank === 1 ? "1.8" : "1"}"/>
          <text x="150" y="56" text-anchor="middle" fill="#f28b82" font-family="monospace" font-size="11" font-weight="600">BANK Y</text>
          <text x="150" y="78" text-anchor="middle" fill="${activeBank === 1 ? "#f28b82" : isDraining && cyc.drain.bank === 1 ? "#f6aea9" : "#757780"}" font-family="monospace" font-size="10" font-weight="600">
            ${activeBank === 1 ? "MAC" : isDraining && cyc.drain.bank === 1 ? "DRAIN" : "IDLE"}
          </text>
          <circle cx="150" cy="104" r="5.5" fill="${cyc.cg.bank1 ? "#a8dab5" : "#2a2f3a"}"/>
        </g>

        <!-- 9. Drain Unit -->
        <g class="${nodeClass("drain_unit")}" data-block="drain_unit" transform="translate(700, 148)">
          <rect class="node-box" width="204" height="76" rx="10" fill="#251f26" stroke="#f6aea9" stroke-opacity="0.7" stroke-width="1.5"/>
          <text x="12" y="22" fill="#f6aea9" font-family="monospace" font-size="11.5" font-weight="600">tq_drain</text>
          <text x="192" y="22" text-anchor="end" fill="${isDraining ? "#f6aea9" : "#757780"}" font-family="monospace" font-size="10" font-weight="600">
            ${isDraining ? `${cyc.drain.writes_left} WR LEFT` : "IDLE"}
          </text>
          <rect x="12" y="38" width="180" height="8" rx="4" fill="#151820"/>
          <rect x="12" y="38" width="${isDraining ? Math.max(12, ((16 - cyc.drain.writes_left) / 16) * 180) : 0}" height="8" rx="4" fill="#f28b82"/>
          <text x="12" y="64" fill="#a9abb4" font-family="monospace" font-size="9.5">128b Stage · Auto-Clear</text>
        </g>
      </svg>
    `;

    container.querySelectorAll(".rtl-node").forEach((node) => {
      node.addEventListener("click", (e) => {
        e.stopPropagation();
        const blkId = node.getAttribute("data-block");
        state.selectedRtlBlock = state.selectedRtlBlock === blkId ? null : blkId;
        renderLogicalSchematic(sc, cyc);
        renderRtlInspector();
      });
    });
  }

  function renderRtlInspector() {
    const panel = document.getElementById("rtl-inspector-panel");
    if (!state.selectedRtlBlock || !RTL_BLOCKS[state.selectedRtlBlock]) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    const blk = RTL_BLOCKS[state.selectedRtlBlock];

    panel.innerHTML = `
      <div class="pop-hdr">
        <h3>${blk.title}</h3>
        <div style="display:flex; gap:6px; align-items:center;">
          <span class="ev-pill swap">✓ ${blk.formal}</span>
          <button class="pop-close" id="btn-close-popover" title="Close (Esc)">×</button>
        </div>
      </div>

      <div class="inspector-metrics">
        <div class="insp-stat"><div class="st-v">${blk.flops}</div><div class="st-k">State</div></div>
        <div class="insp-stat"><div class="st-v">${blk.areaPct}</div><div class="st-k">Area</div></div>
        <div class="insp-stat"><div class="st-v">${blk.cgCount}</div><div class="st-k">Clock Gates</div></div>
      </div>

      <div style="font-size: 11.5px; color: var(--text-muted);">${blk.tag}</div>

      <div style="display:flex; justify-content:space-between; gap:6px;">
        <button class="btn-ctrl" id="btn-toggle-sv">${state.showSvCode ? "Hide RTL" : "RTL (" + blk.file.split("/").pop() + ")"}</button>
        <button class="btn-ctrl primary" id="btn-fly-from-inspector">
          <svg class="ui-icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
          <span>Locate in 3D</span>
        </button>
      </div>

      ${state.showSvCode ? `<div class="sv-code-box">${blk.svCode.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>` : ""}
    `;

    document.getElementById("btn-close-popover").addEventListener("click", () => {
      state.selectedRtlBlock = null;
      updateAllViews();
    });

    document.getElementById("btn-toggle-sv").addEventListener("click", () => {
      state.showSvCode = !state.showSvCode;
      renderRtlInspector();
    });

    document.getElementById("btn-fly-from-inspector").addEventListener("click", () => {
      if (state.chip3d) {
        state.chip3d.flyToModule(blk.modKey);
        document.getElementById("section-chip3d").scrollIntoView({ behavior: "smooth" });
      }
    });
  }

  // =========================================================================
  // COMPONENT 2: VISUAL QUEUES, OUTER-PRODUCT MATRIX & DRAGGABLE TIMELINE
  // =========================================================================
  function renderKernelTrack(sc) {
    const track = document.getElementById("kernel-track");
    const cfgInstrs = sc.program.filter((i) => !i.is_kernel);
    const kernInstrs = sc.program.filter((i) => i.is_kernel);

    let html = "";
    if (cfgInstrs.length > 0) {
      html += `<div class="op-pill" id="op-cfg-group" title="8 × 40-bit Host Setup CFG Instructions (PC 0x00..0x23)"><span>CFG ×${cfgInstrs.length}</span></div>`;
    }
    kernInstrs.forEach((ins) => {
      const shortLbl = ins.label
        .replace("VOUTER_MAC ", "MAC ")
        .replace("LOAD_A", "LD_A")
        .replace("LOAD_B", "LD_B")
        .replace("LOOP_", "LP_");
      html += `<div class="op-pill kernel" data-pc="${ins.pc}" title="PC 0x${ins.pc.toString(16).padStart(2, "0")}: ${ins.label} (${ins.bytes_hex})"><span>${shortLbl}</span></div>`;
    });
    track.innerHTML = html;
  }

  function renderHowItWorks(sc, cyc) {
    document.getElementById("hiw-stats-pills").innerHTML = `
      <span class="sub-tag"><strong>${sc.stats.cycles}</strong> cyc · RD <strong>${sc.stats.read_vectors}</strong> · WR <strong>${sc.stats.write_vectors}</strong></span>
    `;

    document.getElementById("fe-pc-badge").textContent =
      `PC 0x${cyc.pc.toString(16).padStart(2, "0")}${cyc.halted ? " · HALT" : cyc.fence ? " · FENCE" : ""}`;

    const cfgPill = document.getElementById("op-cfg-group");
    if (cfgPill) {
      cfgPill.classList.toggle("active-pc", cyc.pc < 40);
    }
    document.querySelectorAll("#kernel-track .op-pill.kernel").forEach((el) => {
      const p = parseInt(el.getAttribute("data-pc"), 10);
      el.classList.toggle("active-pc", p === cyc.pc);
    });

    const loopK = cyc.loops.find((l) => l.kind === "K");
    const ntm = Math.ceil(sc.shape.m / sc.params.tile);
    const ntn = Math.ceil(sc.shape.n / sc.params.tile);
    const drainTile = cyc.drain ? cyc.drain.tile : -1;

    let tileDots = "";
    for (let r = 0; r < ntm; r++) {
      for (let c = 0; c < ntn; c++) {
        const tid = r * ntn + c;
        const isCur = tid === cyc.front_tile;
        const isDrn = tid === drainTile;
        tileDots += `<div class="tile-cell-dot ${isCur ? "active-tile" : ""} ${isDrn ? "draining-tile" : ""}" title="Tile ${tid} (${r},${c})">T${tid}</div>`;
      }
    }

    const kTotal = loopK ? loopK.iter + loopK.rem : 1;
    const kPct = loopK ? Math.round(((loopK.iter + 1) / Math.max(1, kTotal)) * 100) : 0;

    document.getElementById("fe-loop-boxes").innerHTML = `
      <div class="tile-minimap" style="grid-template-columns: repeat(${ntn}, 24px);">${tileDots}</div>
      <div style="display:flex; flex-direction:column; gap:6px; font-family:var(--font-mono); font-size:10.5px;">
        <div style="display:flex; justify-content:space-between;">
          <span style="color:var(--text-muted);">LOOP_T</span>
          <strong style="color:#fff;">T${cyc.front_tile} / ${ntm * ntn - 1}</strong>
        </div>
        <div>
          <div style="display:flex; justify-content:space-between;">
            <span style="color:var(--text-muted);">LOOP_K</span>
            <strong style="color:var(--yellow-fe);">${loopK ? `${loopK.iter + 1}/${kTotal}` : "—"}</strong>
          </div>
          <div class="meter-bar"><div class="meter-fill" style="width:${kPct}%; background:var(--yellow-fe);"></div></div>
        </div>
      </div>
    `;

    const stA = cyc.stream_a;
    const stB = cyc.stream_b;
    const panelBytes = sc.params.tile * sc.shape.kp;
    const pctA = stA ? Math.round(((panelBytes - stA.left) / Math.max(1, panelBytes)) * 100) : 100;
    const pctB = stB ? Math.round(((panelBytes - stB.left) / Math.max(1, panelBytes)) * 100) : 100;

    document.getElementById("stream-walkers-box").innerHTML = `
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:7px;">
        <div class="mini-panel" style="border-left: 2px solid var(--cyan-qa);">
          <div style="display:flex; justify-content:space-between;">
            <span style="color:var(--cyan-qa); font-weight:700;">A</span>
            <span>${stA ? `${stA.left}B` : "0B"}</span>
          </div>
          <div class="meter-bar"><div class="meter-fill" style="width:${pctA}%; background:var(--cyan-qa);"></div></div>
        </div>
        <div class="mini-panel" style="border-left: 2px solid var(--amber-qb);">
          <div style="display:flex; justify-content:space-between;">
            <span style="color:var(--amber-qb); font-weight:700;">B</span>
            <span>${stB ? `${stB.left}B` : "0B"}</span>
          </div>
          <div class="meter-bar"><div class="meter-fill" style="width:${pctB}%; background:var(--amber-qb);"></div></div>
        </div>
      </div>
    `;

    renderFifoBox("qa-queue-box", "QA FIFO", cyc.qa, sc.params.fifo_depth, "qa", cyc.arrived_a);
    renderFifoBox("qb-queue-box", "QB FIFO", cyc.qb, sc.params.fifo_depth, "qb", cyc.arrived_b);

    const macqHtml = [];
    for (let i = 0; i < sc.params.mac_queue_depth; i++) {
      const entry = cyc.macq[i];
      if (entry) {
        macqHtml.push(`
          <div class="fifo-slot occupied macq">
            <strong>#${i} ${entry.kind}</strong>
            <span style="color:#e9d5ff;">${entry.shape || entry.detail}</span>
          </div>
        `);
      } else {
        macqHtml.push(`<div class="fifo-slot"><span style="color:var(--text-dim);">#${i}</span><span>—</span></div>`);
      }
    }
    document.getElementById("macq-queue-box").innerHTML = `
      <div class="queue-hdr">
        <span style="color:var(--violet-mac);">MAC_CMD_Q</span>
        <span>${cyc.macq.length}/${sc.params.mac_queue_depth}</span>
      </div>
      <div class="fifo-slots">${macqHtml.join("")}</div>
    `;

    renderOuterProductAndBanks(sc, cyc);
  }

  function renderFifoBox(elId, title, fifoState, depth, themeCls, arrivedBytes) {
    const slots = [];
    for (let i = 0; i < depth; i++) {
      const vec = fifoState.vectors[i];
      if (vec) {
        const bCells = vec
          .map((v, bi) => `<span class="byte-cell ${bi < 8 ? "b0" : "b1"}">${v}</span>`)
          .join("");
        slots.push(`
          <div class="fifo-slot occupied ${themeCls}">
            <strong style="min-width:24px;">V${i}</strong>
            <div class="vec-bytes">${bCells}</div>
          </div>
        `);
      } else {
        slots.push(`<div class="fifo-slot"><span style="color:var(--text-dim);">V${i}</span><span style="color:var(--text-dim);">—</span></div>`);
      }
    }
    const colorVar = themeCls === "qa" ? "var(--cyan-qa)" : "var(--amber-qb)";
    document.getElementById(elId).innerHTML = `
      <div class="queue-hdr">
        <span style="color:${colorVar};">${title}</span>
        <span>${fifoState.vectors.length}/${depth} (${fifoState.bytes}B) ${arrivedBytes > 0 ? `<strong style="color:var(--lime-cg);">+${arrivedBytes}B</strong>` : ""}</span>
      </div>
      <div class="fifo-slots">${slots.join("")}</div>
    `;
  }

  function renderOuterProductAndBanks(sc, cyc) {
    const mb = cyc.mac_beat;
    const arityBadge = document.getElementById("arity-badge");
    const badge = document.getElementById("beat-status-badge");

    if (mb) {
      if (arityBadge) {
        arityBadge.textContent = `Arity ${mb.d}×`;
        arityBadge.style.color = "var(--violet-mac)";
      }
      badge.textContent = `${mb.shape} · B${mb.beat} → Bank ${mb.bank === 0 ? "X" : "Y"}`;
      badge.style.color = "var(--cyan-qa)";
    } else {
      if (arityBadge) arityBadge.textContent = "";
      badge.textContent = "Idle";
      badge.style.color = "var(--text-muted)";
    }

    const activeBankMat = cyc.active_bank === 0 ? cyc.bank0 : cyc.bank1;
    const aAxis = new Array(8).fill("·");
    const bAxis = new Array(8).fill("·");
    const prodGrid = Array.from({ length: 8 }, () => new Array(8).fill(null));
    const quadMap = Array.from({ length: 8 }, () => new Array(8).fill(""));

    if (mb) {
      for (const q of mb.quadrants) {
        const r0 = q.qi * 4;
        const c0 = q.qj * 4;
        for (let i = 0; i < 4; i++) {
          if (q.qj === 0) aAxis[r0 + i] = q.a_vec[i];
          if (q.qi === 0) bAxis[c0 + i] = q.b_vec[i];
          for (let j = 0; j < 4; j++) {
            prodGrid[r0 + i][c0 + j] = q.products[i][j];
            quadMap[r0 + i][c0 + j] = `${q.name}→${q.dest_q} (k+${q.k_step})`;
          }
        }
      }
    }

    let gridHtml = `<div class="op-grid-8x8">`;
    gridHtml += `<div style="display:grid;place-items:center;color:var(--text-dim);font-size:8.5px;">A\\B</div>`;
    for (let c = 0; c < 8; c++) {
      gridHtml += `<div class="op-hdr-b">${bAxis[c]}</div>`;
    }

    for (let r = 0; r < 8; r++) {
      gridHtml += `<div class="op-hdr-a">${aAxis[r]}</div>`;
      for (let c = 0; c < 8; c++) {
        const qCls = r < 4 ? (c < 4 ? "quad-tl" : "quad-tr") : c < 4 ? "quad-bl" : "quad-br";
        const pVal = prodGrid[r][c];
        const accVal = activeBankMat[r][c];
        const pStr = pVal !== null ? (pVal >= 0 ? `+${pVal}` : `${pVal}`) : "·";
        gridHtml += `
          <div class="op-cell ${qCls} ${pVal !== null ? "active-beat" : ""}" title="[${r},${c}] ${quadMap[r][c]} | Sum=${accVal}">
            <span class="prod-v">${pStr}</span>
            <span class="acc-v">${accVal}</span>
          </div>
        `;
      }
    }
    gridHtml += `</div>`;
    document.getElementById("outer-product-grid-container").innerHTML = gridHtml;

    function renderBankCard(bankIdx, name, cls, mat) {
      const isCompute = cyc.active_bank === bankIdx;
      const isDrain = cyc.drain && cyc.drain.bank === bankIdx;
      const roleBadge = isCompute
        ? `<span class="ev-pill swap">MAC</span>`
        : isDrain
        ? `<span class="ev-pill wr">DRAIN (${cyc.drain.writes_left})</span>`
        : `<span class="sub-tag">IDLE</span>`;

      let cells = "";
      for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
          const v = mat[r][c];
          cells += `<div class="bank-cell ${v !== 0 ? "nonzero" : ""}">${v}</div>`;
        }
      }
      return `
        <div class="bank-card ${cls} ${isCompute || isDrain ? "active" : ""}">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong style="font-family:var(--font-mono); font-size:11px; color:${bankIdx === 0 ? "var(--emerald-bx)" : "var(--coral-by)"};">${name}</strong>
            ${roleBadge}
          </div>
          <div class="bank-mini-grid">${cells}</div>
        </div>
      `;
    }

    document.getElementById("banks-dual-container").innerHTML =
      renderBankCard(0, "BANK X", "bank-x", cyc.bank0) +
      renderBankCard(1, "BANK Y", "bank-y", cyc.bank1);
  }

  // =========================================================================
  // SMOOTH DRAGGABLE 3-WAY OVERLAP TIMELINE (`#waterfall-canvas`)
  // =========================================================================
  const WF_PAD_L = 96;
  const WF_PAD_R = 16;

  function bindWaterfallTimeline() {
    const canvas = document.getElementById("waterfall-canvas");
    if (!canvas) return;

    let dragging = false;

    function scrubFromEvent(e) {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const plotW = Math.max(1, rect.width - WF_PAD_L - WF_PAD_R);
      const scNow = currentScenario();
      if (!scNow || !scNow.cycles.length) return;
      const ratio = Math.max(0, Math.min(1, (x - WF_PAD_L) / plotW));
      const targetCycle = Math.round(ratio * (scNow.cycles.length - 1));
      if (targetCycle !== state.cycleIdx) {
        setCycle(targetCycle);
      }
    }

    canvas.addEventListener("pointerdown", (e) => {
      stopPlay();
      dragging = true;
      if (canvas.setPointerCapture) {
        try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
      }
      scrubFromEvent(e);
    });

    canvas.addEventListener("pointermove", (e) => {
      const rect = canvas.getBoundingClientRect();
      state.wfHoverX = e.clientX - rect.left;
      if (dragging) {
        scrubFromEvent(e);
      } else {
        renderWaterfall(currentScenario(), currentCycleData());
      }
    });

    const endDrag = (e) => {
      if (!dragging) return;
      dragging = false;
      if (canvas.releasePointerCapture) {
        try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
      }
    };

    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", () => {
      state.wfHoverX = null;
      if (!dragging) renderWaterfall(currentScenario(), currentCycleData());
    });
  }

  function renderWaterfall(sc, cyc) {
    const canvas = document.getElementById("waterfall-canvas");
    if (!canvas || !sc || !cyc) return;
    const ctx = canvas.getContext("2d");
    const total = sc.cycles.length;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.parentElement.clientWidth || 1200;
    const cssH = 142;
    if (canvas.width !== Math.floor(cssW * dpr) || canvas.height !== Math.floor(cssH * dpr)) {
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
    }
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const plotW = Math.max(10, cssW - WF_PAD_L - WF_PAD_R);
    const colW = Math.max(2, plotW / Math.max(1, total));

    const lanes = [
      { label: "Decode PC",  y: 22, h: 17 },
      { label: "Read A / B", y: 44, h: 17 },
      { label: "MAC Bank X", y: 66, h: 17 },
      { label: "MAC Bank Y", y: 88, h: 17 },
      { label: "Drain WR",   y: 110, h: 17 },
    ];

    ctx.font = "500 10px 'Roboto Mono', 'JetBrains Mono', monospace";
    lanes.forEach((ln) => {
      ctx.fillStyle = "rgba(32, 36, 44, 0.9)";
      ctx.fillRect(WF_PAD_L, ln.y, plotW, ln.h);
      ctx.fillStyle = "#a9abb4";
      ctx.fillText(ln.label, 8, ln.y + 12);
    });

    for (let i = 0; i < total; i++) {
      const c = sc.cycles[i];
      const x = WF_PAD_L + (i / Math.max(1, total - 1)) * plotW;
      const w = Math.max(2.2, colW - 0.4);
      const evs = c.events || [];

      if (evs.some((e) => e.startsWith("fe issue"))) {
        ctx.fillStyle = "#fde293";
        ctx.fillRect(x, lanes[0].y + 2, w, lanes[0].h - 4);
      }

      if (evs.some((e) => e.startsWith("port rd s=A"))) {
        ctx.fillStyle = "#8ab4f8";
        ctx.fillRect(x, lanes[1].y + 2, w, lanes[1].h - 4);
      } else if (evs.some((e) => e.startsWith("port rd s=B"))) {
        ctx.fillStyle = "#fdd663";
        ctx.fillRect(x, lanes[1].y + 2, w, lanes[1].h - 4);
      } else if (evs.some((e) => e.startsWith("pb rd"))) {
        ctx.fillStyle = "#a8dab5";
        ctx.fillRect(x, lanes[1].y + 2, w, lanes[1].h - 4);
      }

      if (c.mac_beat) {
        const laneIdx = c.mac_beat.bank === 0 ? 2 : 3;
        ctx.fillStyle = c.mac_beat.bank === 0 ? "#81c995" : "#f28b82";
        ctx.fillRect(x, lanes[laneIdx].y + 2, w, lanes[laneIdx].h - 4);
      }

      if (evs.some((e) => e.startsWith("port wr"))) {
        ctx.fillStyle = "#f6aea9";
        ctx.fillRect(x, lanes[4].y + 2, w, lanes[4].h - 4);
      }
    }

    if (state.wfHoverX !== null && state.wfHoverX >= WF_PAD_L && state.wfHoverX <= WF_PAD_L + plotW) {
      ctx.strokeStyle = "rgba(138, 180, 248, 0.35)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(state.wfHoverX, 18);
      ctx.lineTo(state.wfHoverX, cssH - 8);
      ctx.stroke();
    }

    const curX = WF_PAD_L + (cyc.cycle / Math.max(1, total - 1)) * plotW;
    ctx.strokeStyle = "#8ab4f8";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(curX, 16);
    ctx.lineTo(curX, cssH - 8);
    ctx.stroke();

    const pillW = 54;
    const pillX = Math.max(WF_PAD_L, Math.min(WF_PAD_L + plotW - pillW, curX - pillW * 0.5));
    ctx.fillStyle = "#8ab4f8";
    ctx.beginPath();
    ctx.roundRect(pillX, 2, pillW, 14, 7);
    ctx.fill();
    ctx.fillStyle = "#0b1426";
    ctx.font = "600 9.5px 'Roboto Mono', 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.fillText(`C${cyc.cycle}`, pillX + pillW * 0.5, 12);

    ctx.restore();
  }

  // =========================================================================
  // COMPONENT 3: 3D SILICON DIE & LAYER STACKUP SECTION
  // =========================================================================
  function initChip3DSection() {
    const canvas = document.getElementById("chip3d-canvas");
    const hoverReadout = document.getElementById("chip3d-hover-readout");

    state.chip3d = new window.TriQueueChip3D.Chip3DRenderer(canvas, (info) => {
      if (info && info.module) {
        hoverReadout.innerHTML = `<strong style="color:var(--cyan-qa);">(${info.x_um}, ${info.y_um}) µm</strong> · <strong style="color:${info.module.color};">${info.module.name.split("(")[0]}</strong> <span class="compass-tag">${info.module.compass || ""}</span>`;
      }
    });

    loadPdkChip("asap7");

    const btnAsap7 = document.getElementById("pdk-asap7");
    const btnSky130 = document.getElementById("pdk-sky130");
    btnAsap7.addEventListener("click", () => {
      btnAsap7.classList.add("active");
      btnSky130.classList.remove("active");
      loadPdkChip("asap7");
    });
    btnSky130.addEventListener("click", () => {
      btnSky130.classList.add("active");
      btnAsap7.classList.remove("active");
      loadPdkChip("sky130hd");
    });

    document.querySelectorAll("#color-lens-buttons [data-lens]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#color-lens-buttons [data-lens]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.chip3d.setColorMode(btn.getAttribute("data-lens"));
      });
    });

    const expSlider = document.getElementById("explode-slider");
    const expLabel = document.getElementById("explode-val-label");
    expSlider.addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      expLabel.textContent = `${v.toFixed(1)}×`;
      state.chip3d.setExplode(v);
    });

    document.querySelectorAll("[data-cam]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.chip3d.flyToPreset(btn.getAttribute("data-cam"));
      });
    });
    document.getElementById("btn-clear-isolate").addEventListener("click", () => {
      state.chip3d.setHighlightModule(-1);
      document.querySelectorAll(".mod-legend-btn").forEach((b) => b.classList.remove("active"));
    });
  }

  function loadPdkChip(pdk) {
    state.pdk = pdk;
    const chipData = pdk === "asap7" ? window.TRIQUEUE_CHIP_ASAP7 : window.TRIQUEUE_CHIP_SKY130HD;
    if (!chipData || !state.chip3d) return;

    state.chip3d.setChipData(chipData);
    state.chip3d.setActiveCycleState(currentCycleData());

    const rec = chipData.record || {};
    const fmaxMhz = rec.fmax_hz ? (rec.fmax_hz / 1e6).toFixed(0) : "976";
    const densPct = rec.placement_density ? `${(rec.placement_density * 100).toFixed(1)}% Dens` : "";
    const relTag = chipData.release_version ? ` · <span style="color:var(--lime-cg);">${chipData.release_version}</span>` : "";
    const areaStr =
      pdk === "asap7"
        ? `${Math.round(rec.cell_area_um2 || 12702).toLocaleString()} µm²`
        : `${((rec.cell_area_um2 || 825600) / 1e6).toFixed(2)} mm²`;

    document.getElementById("chip3d-hud-info").innerHTML = `
      <strong>${pdk.toUpperCase()}</strong> · ${chipData.die.width_um}×${chipData.die.height_um} µm · ${areaStr}${densPct ? " · " + densPct : ""} · <strong style="color:var(--cyan-qa);">${fmaxMhz} MHz</strong> · 0 DRC${relTag}
    `;

    renderLayerStackToggles();
    renderModuleFlyToList(chipData);
  }

  function renderLayerStackToggles() {
    const container = document.getElementById("layer-stack-toggles");
    const vis = state.chip3d.vis;
    const metalsOn = vis.layers.some(Boolean);

    const groups = [
      { id: "cells",   label: "Cells",   swatch: "#8ab4f8", active: vis.cells },
      { id: "filler",  label: "Fill",    swatch: "#38404f", active: vis.filler },
      { id: "metals",  label: "Metals",  swatch: "#c58af9", active: metalsOn },
      { id: "pdn",     label: "Power/IO",swatch: "#fdd663", active: vis.pdn },
      { id: "regions", label: "Regions", swatch: "#81c995", active: vis.regions },
    ];

    container.innerHTML = groups
      .map(
        (g) => `
        <button class="layer-toggle-pill ${g.active ? "active" : ""}" data-lgrp="${g.id}">
          <span class="toggle-check">${g.active ? "✓" : "○"}</span>
          <span class="layer-swatch" style="background:${g.swatch};margin:0;"></span>
          <span>${g.label}</span>
        </button>
      `
      )
      .join("");

    container.querySelectorAll("[data-lgrp]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-lgrp");
        if (id === "regions") {
          vis.regions = !vis.regions;
        } else if (id === "cells") {
          vis.cells = !vis.cells;
        } else if (id === "filler") {
          vis.filler = !vis.filler;
        } else if (id === "metals") {
          const next = !vis.layers.some(Boolean);
          vis.layers = vis.layers.map(() => next);
          vis.vias = next;
        } else if (id === "pdn") {
          vis.pdn = !vis.pdn;
          vis.pins = vis.pdn;
        }
        state.chip3d.rebuildInstances();
        renderLayerStackToggles();
      });
    });
  }

  function renderModuleFlyToList(chipData) {
    const container = document.getElementById("module-flyto-list");
    const bms = chipData.bookmarks || {};
    const items = window.TriQueueChip3D.MODULE_META.filter((m) => bms[m.key]);

    container.innerHTML = items
      .map((m) => {
        const bm = bms[m.key];
        return `
          <button class="mod-legend-btn" data-modkey="${m.key}" data-modid="${m.id}" title="${m.name} (${bm.cx}, ${bm.cy} µm)">
            <span><span class="layer-swatch" style="background:${m.hex};"></span>${m.short}</span>
            <span class="compass-tag">${bm.compass || ""} ›</span>
          </button>
        `;
      })
      .join("");

    container.querySelectorAll(".mod-legend-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-modkey");
        const id = parseInt(btn.getAttribute("data-modid"), 10);
        if (state.chip3d.highlightMod === id) {
          state.chip3d.setHighlightModule(-1);
          btn.classList.remove("active");
        } else {
          container.querySelectorAll(".mod-legend-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          state.chip3d.flyToModule(key);
        }
      });
    });
  }

  window.addEventListener("DOMContentLoaded", init);
})();
