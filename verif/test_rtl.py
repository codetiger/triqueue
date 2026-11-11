# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""RTL regression: build tq_top with Verilator for each configuration and run the cocotb cases.

    python3 -m pytest verif -q                 # all configurations
    python3 -m pytest verif -q -k t8_default   # one configuration

Each configuration is built once (build/rtl/<name>), then its cases run in one simulation.
"""
import itertools
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / line for line in (ROOT / "rtl" / "files.f").read_text().split()]
sys.path.insert(0, str(ROOT / "verif" / "tb"))     # the runner passes sys.path to the simulator


def case(m, n, k, a="s8", b="s8", seed=None, lat=2, stall=0.0):
    if seed is None:
        seed = zlib.crc32(f"{m},{n},{k},{a},{b},{lat},{stall}".encode()) % 10_000    # deterministic across runs
    return dict(m=m, n=n, k=k, a=a, b=b, seed=seed, lat=lat, stall=stall)


def edge_cases(t, ks):
    h = t // 2
    sizes = sorted({1, h, h + 1, t, t + 1, t + h + 1})
    out = []
    for i, (m, n, k) in enumerate(itertools.product(sizes, sizes, ks)):
        out.append(case(m, n, k, a="s8" if i % 3 else "u8", b="u8" if i % 4 == 1 else "s8"))
    return out


def signedness(t):
    return [case(t + 5, 2 * t + 3, 40, a, b) for a, b in itertools.product(("s8", "u8"), repeat=2)]


def latency_sweep(t, lats=(1, 2, 3, 5, 8, 13)):
    return [case(t + 3, 2 * t + 1, 27, lat=lat) for lat in lats]


CONFIGS = {
    # name: (RTL parameters, model Params fields, cases)
    "t8_default": (
        dict(T=8),
        dict(tile=8),
        edge_cases(8, (1, 7, 8, 9, 33)) + signedness(8) + latency_sweep(8)
        + [case(64, 64, 64), case(60, 60, 60), case(64, 64, 8), case(64, 64, 16),
           case(1, 64, 64), case(64, 1, 64), case(3, 3, 3), case(20, 24, 27, "s8", "u8")]
        + [dict(c, shift=sh) for c, sh in zip(edge_cases(8, (1, 9)), itertools.cycle((0, 1, 6, 12, 31)))]
        + [dict(case(64, 64, 8), shift=5), dict(case(64, 64, 16), shift=6), dict(case(20, 24, 27, "s8", "u8"), shift=7)],
    ),
    "t8_shared_port": (
        dict(T=8, SHARED_PORT=1),
        dict(tile=8, shared_port=True),
        edge_cases(8, (9,)) + latency_sweep(8, (1, 2, 6)) + [case(64, 64, 64), case(32, 32, 8)]
        + [dict(case(64, 64, 64), shift=8), dict(case(20, 24, 27), shift=6), dict(case(9, 13, 9), shift=4)],
    ),
    "t8_small_queues": (
        dict(T=8, FIFO_DEPTH=2, MACQ_DEPTH=2, SCMD_DEPTH=2),
        dict(tile=8, fifo_depth=2, mac_queue_depth=2, stream_queue_depth=2),
        edge_cases(8, (9, 33)) + latency_sweep(8, (1, 2, 4, 9)) + [case(40, 40, 40)],
    ),
    "t8_backpressure": (
        dict(T=8),
        dict(tile=8),
        [dict(c, stall=s) for c in edge_cases(8, (9, 33))[::3] for s in (0.3,)]
        + [case(m, n, k, lat=lat, stall=s) for (m, n, k) in ((20, 24, 27), (33, 17, 40), (64, 64, 32))
           for lat in (1, 2, 6) for s in (0.1, 0.5, 0.8)]
        + [dict(case(20, 24, 27, lat=2, stall=0.4), shift=6), dict(case(33, 17, 16, lat=3, stall=0.5), shift=5)],
    ),
    "t8_shared_backpressure": (
        dict(T=8, SHARED_PORT=1),
        dict(tile=8, shared_port=True),
        [case(m, n, k, lat=lat, stall=s) for (m, n, k) in ((20, 24, 27), (64, 64, 32), (9, 9, 9))
         for lat in (1, 3) for s in (0.2, 0.6)],
    ),
    "t8_acc_csa": (
        dict(T=8, ACC_FORM=0),
        dict(tile=8),
        edge_cases(8, (1, 9, 33)) + signedness(8)
        + [case(64, 64, 64), case(64, 64, 8), case(1, 64, 64), case(20, 24, 27, lat=3, stall=0.4)]
        + [dict(case(64, 64, 8), shift=5), dict(case(20, 24, 27, lat=3, stall=0.4), shift=6)],
    ),
    "t8_reference_queues": (
        dict(T=8, FIFO_DEPTH=4, MACQ_DEPTH=3),
        dict(tile=8, fifo_depth=4, mac_queue_depth=3),
        edge_cases(8, (9,)) + latency_sweep(8, (1, 2, 3, 5)) + [case(64, 64, 64), case(256, 32, 288)],
    ),
    "t8_edge_cases": (
        # illegal programs stop with the model's error code at the model's cycle; ZERO_C in a
        # drain waits; K = 0 writes C = 0; M = 0 / N = 0 are no-ops; PROG writes while busy are ignored
        dict(T=8),
        dict(tile=8),
        [dict(case(20, 24, 27), prog=pr) for pr in
         ("opcode", "reserved_arg", "reserved_vouter", "cfg_reg", "cfg_loop", "loop_depth", "loop_zero_body",
          "loop_nest_over", "second_loop_t", "pc", "pc_skip_loop", "cfg_val_k", "cfg_val_m",
          "cfg_val_mode", "cfg_val_cld", "operand_leftover", "operand_starved", "cfg_after_swap",
          "zero_in_drain")]
        + [dict(case(14, 15, 27), prog="explicit_8x8"),
           dict(case(8, 4, 27), prog="explicit_8x4"),
           dict(case(4, 8, 27), prog="explicit_4x8"),
           dict(case(4, 4, 27), prog="explicit_4x4")]
        + [dict(case(20, 24, 27, lat=lat, stall=0.3), prog="zero_in_drain") for lat in (1, 4)]
        + [dict(case(20, 24, 27), misalign=f) for f in ("a_base", "b_base", "c_base", "c_ld")]
        + [dict(case(20, 24, 27), misalign="c_base", shift=6), dict(case(20, 24, 27), misalign="c_ld", shift=6)]
        + [case(9, 13, 0), dict(case(9, 13, 0), shift=4), case(16, 16, 0, lat=3), case(1, 1, 0),
           case(0, 8, 8), case(8, 0, 8)]
        + [dict(case(64, 64, 64), prog_poke=True), dict(case(20, 24, 27, stall=0.4), prog_poke=True)]
        + [case(20, 24, 27)],                          # a normal run after the errors
    ),
    "t8_edge_shared": (
        dict(T=8, SHARED_PORT=1, WR_ACK=1, WR_OUT_MAX=2),
        dict(tile=8, shared_port=True, wr_ack=True, wr_out_max=2),
        [dict(case(20, 24, 27, lat=2, stall=0.3), prog=pr, wr_lat=3)
         for pr in ("opcode", "second_loop_t", "pc", "operand_leftover", "operand_starved",
                    "cfg_after_swap", "zero_in_drain")]
        + [dict(case(14, 15, 27, lat=2, stall=0.3), prog="explicit_8x8", wr_lat=3)]
        + [dict(case(20, 24, 27, lat=3), misalign="c_base", wr_lat=2),
           dict(case(9, 13, 0), wr_lat=1), dict(case(0, 8, 8), wr_lat=1)],
    ),
    "t8_mcu_custom": (
        dict(T=8, PERF=0, PROG_BYTES=120, MACQ_DEPTH=1, SCMD_DEPTH=4),
        dict(tile=8, prog_bytes=120, mac_queue_depth=1, stream_queue_depth=4, perf_off=True),
        [case(16, 16, 16), case(20, 24, 27, lat=3, stall=0.3),
         dict(case(20, 24, 27), prog="zero_in_drain"), dict(case(14, 15, 27), prog="explicit_8x8")],
    ),
    "t8_panel": (
        # A panel buffer (spec 10.9): 1 KiB holds panels up to K = 128 (8 x 128); larger K streams
        dict(T=8, PANEL_BYTES=1024),
        dict(tile=8, panel_bytes=1024),
        edge_cases(8, (1, 9, 64, 200)) + latency_sweep(8, (1, 2, 3, 6, 13))
        + [case(64, 64, 64), case(1, 256, 128), case(4, 512, 64), case(1, 64, 200), case(37, 41, 64, lat=1)]
        + [case(m, n, k, lat=lat, stall=s) for (m, n, k) in ((20, 24, 27), (33, 40, 64), (1, 128, 96))
           for lat in (1, 2, 7) for s in (0.3, 0.7)]
        + [dict(case(20, 24, 27), prog=pr) for pr in ("opcode", "zero_in_drain")] + [case(9, 13, 0)],
    ),
    "t8_panel_shared": (
        dict(T=8, PANEL_BYTES=512, SHARED_PORT=1, WR_ACK=1, WR_OUT_MAX=2),
        dict(tile=8, panel_bytes=512, shared_port=True, wr_ack=True, wr_out_max=2),
        [dict(case(m, n, k, lat=lat, stall=s), wr_lat=wl) for (m, n, k) in ((20, 24, 27), (1, 96, 64), (40, 40, 40))
         for lat, s, wl in ((1, 0.0, 0), (2, 0.4, 3), (5, 0.6, 9))],
    ),
    "t16_panel": (
        dict(T=16, PANEL_BYTES=2048),
        dict(tile=16, panel_bytes=2048),
        edge_cases(16, (9, 64)) + latency_sweep(16, (1, 2, 7)) + [case(64, 64, 64), case(1, 256, 128)],
    ),
    "t4_panel": (
        dict(T=4, PANEL_BYTES=256),
        dict(tile=4, panel_bytes=256),
        edge_cases(4, (1, 8, 64, 100)) + latency_sweep(4, (1, 2, 5)) + [case(16, 16, 16), case(1, 64, 48, stall=0.5)],
    ),
    "t8_mac1": (
        # MAC_PIPE = 1: no product register, multiply and accumulate in the beat's cycle
        dict(T=8, MAC_PIPE=1),
        dict(tile=8, mac_pipe=1),
        edge_cases(8, (1, 9, 33)) + latency_sweep(8, (1, 2, 5)) + signedness(8)
        + [case(64, 64, 64), case(64, 64, 8), case(20, 24, 27, lat=3, stall=0.4)]
        + [dict(case(20, 24, 27), prog="zero_in_drain")],
    ),
    "t8_mac1_csa_shared": (
        dict(T=8, MAC_PIPE=1, ACC_FORM=0, SHARED_PORT=1, PANEL_BYTES=512),
        dict(tile=8, mac_pipe=1, shared_port=True, panel_bytes=512),
        [case(m, n, k, lat=lat, stall=s) for (m, n, k) in ((20, 24, 27), (64, 64, 16), (1, 64, 64))
         for lat, s in ((1, 0.0), (2, 0.5), (6, 0.3))],
    ),
    "t8_no_clock_gating": (
        dict(T=8, CLK_GATE=0),
        dict(tile=8),
        edge_cases(8, (1, 9)) + [case(64, 64, 64), case(20, 24, 27, lat=3, stall=0.4)],
    ),
    "t8_axil": (
        dict(T=8),
        dict(tile=8),
        edge_cases(8, (9,))[::4] + [case(64, 64, 64), case(20, 24, 27, lat=3, stall=0.4)],
    ),
    "t8_xif": (
        dict(T=8),
        dict(tile=8),
        edge_cases(8, (9,))[::4] + [case(64, 64, 64), case(20, 24, 27, lat=3, stall=0.4)],
    ),
    "t8_write_ack": (
        dict(T=8, WR_ACK=1, WR_OUT_MAX=2),
        dict(tile=8, wr_ack=True, wr_out_max=2),
        [dict(c, wr_lat=wl) for c in edge_cases(8, (9,))[::5] for wl in (0, 1, 4)]
        + [dict(case(m, n, k, lat=lat, stall=s), wr_lat=wl) for (m, n, k) in ((20, 24, 27), (64, 64, 16))
           for lat, s, wl in ((1, 0.0, 9), (2, 0.4, 3), (3, 0.6, 12))],
    ),
    "t8_write_ack_shared": (
        dict(T=8, WR_ACK=1, WR_OUT_MAX=1, SHARED_PORT=1),
        dict(tile=8, wr_ack=True, wr_out_max=1, shared_port=True),
        [dict(case(m, n, k, lat=lat, stall=s), wr_lat=wl) for (m, n, k) in ((20, 24, 27), (9, 9, 9), (64, 64, 16))
         for lat, s, wl in ((1, 0.0, 0), (2, 0.3, 2), (3, 0.5, 7))],
    ),
    "t8_axi": (
        dict(T=8),
        dict(tile=8, wr_ack=True, wr_out_max=16),
        [dict(c, wr_lat=wl) for c in edge_cases(8, (9,))[::4] for wl in (1, 3)]
        + [dict(case(m, n, k, lat=lat, stall=s), wr_lat=wl) for (m, n, k) in ((64, 64, 64), (20, 24, 27))
           for lat, s, wl in ((2, 0.0, 1), (3, 0.5, 6), (1, 0.8, 20))]
        + [dict(case(20, 24, 27, lat=2, stall=0.3), wr_lat=3, shift=6), dict(case(64, 64, 8), wr_lat=1, shift=5)]
        # SLVERR write or read response sets STATUS.BUS_ERR, and the next START clears it
        + [dict(case(20, 24, 27), wr_lat=2, bus_err=3),
           dict(case(20, 24, 27), wr_lat=2, rresp_err=2),
           dict(case(9, 9, 9), wr_lat=1)],
    ),
    "t8_axi_burst": (
        dict(T=8, BURST_BEATS=4, ID_W=4),
        dict(tile=8, wr_ack=True, wr_out_max=16),
        [dict(c, wr_lat=2) for c in edge_cases(8, (9,))[::4]]
        + [dict(case(1, 16, 8, lat=2), wr_lat=1),
           dict(case(4, 16, 8, lat=2), wr_lat=1),
           dict(case(64, 64, 64, lat=8), wr_lat=2),
           dict(case(20, 24, 27, lat=4, stall=0.3), wr_lat=3, shift=6),
           dict(case(64, 64, 8, lat=2), wr_lat=1, shift=5),
           dict(case(20, 24, 27, lat=2), wr_lat=2, bus_err=1),
           dict(case(20, 24, 27, lat=2), wr_lat=2, rresp_err=1),
           dict(case(9, 9, 9, lat=2), wr_lat=1)],
    ),
    "t32": (
        dict(T=32),
        dict(tile=32),
        edge_cases(32, (1, 40)) + signedness(32) + latency_sweep(32, (1, 4))
        + [case(64, 64, 64), case(128, 96, 72), case(40, 40, 40, stall=0.3)],
    ),
    "t16": (
        dict(T=16),
        dict(tile=16),
        edge_cases(16, (1, 9, 40)) + signedness(16) + latency_sweep(16, (1, 2, 7)) + [case(64, 64, 64)]
        + [dict(case(37, 41, 16), shift=6), dict(case(64, 64, 8), shift=5)],
    ),
    "t4": (
        dict(T=4),
        dict(tile=4),
        edge_cases(4, (1, 8, 9, 33)) + latency_sweep(4, (1, 2, 5)) + [case(16, 16, 16)]
        + [dict(case(9, 13, 8), shift=4), dict(case(16, 16, 8), shift=5)],
    ),
}


# configurations whose top level is not tq_top (the testbench host driver follows it)
TOPS = {"t8_axil": ("tq_top_axil", "axil"), "t8_axi": ("tq_top_axi", "axi"),
        "t8_axi_burst": ("tq_top_axi", "axi_burst"), "t8_xif": ("tq_top_xif", "xif")}


@pytest.mark.parametrize("name", list(CONFIGS))
def test_rtl(name):
    from cocotb_tools.runner import get_runner

    rtl_params, model_params, cases = CONFIGS[name]
    top, host = TOPS.get(name, ("tq_top", "native"))
    build_dir = ROOT / "build" / "rtl" / name
    runner = get_runner("verilator")
    x_mode = "unique" if name in ("t8_no_clock_gating", "t8_mcu_custom") else "fast"
    runner.build(
        sources=SOURCES,
        hdl_toplevel=top,
        parameters=rtl_params,
        build_dir=build_dir,
        build_args=["-Wno-fatal", "--assert", "-O3", "--x-assign", x_mode, "--x-initial", x_mode],
    )
    extra_env = {
        "TQ_PARAMS": json.dumps(model_params),
        "TQ_CASES": json.dumps(cases),
        "TQ_HOST": host,
    }
    if "TQ_CASE" in os.environ:
        extra_env["TQ_CASE"] = os.environ["TQ_CASE"]
    results = runner.test(
        hdl_toplevel=top,
        test_module="tq_cocotb",
        test_dir=build_dir,
        build_dir=build_dir,
        extra_env=extra_env,
    )
    from cocotb_tools.runner import get_results
    num, failed = get_results(results)
    assert num > 0 and failed == 0, f"{failed} cocotb test(s) failed, see {results}"


def test_vectors_readmemh(tmp_path):
    """Export golden vectors and consume prog.hex / mem_init.hex / mem_final.hex with $readmemh in SV."""
    sys.path.insert(0, str(ROOT / "model"))
    from tri_queue.vectors import main as vectors_main

    vectors_main(["--m", "16", "--n", "16", "--k", "16", "--seed", "7", "-o", str(tmp_path)])
    meta = json.loads((tmp_path / "meta.json").read_text())
    mem_words = meta["mem_bytes"] // 16
    tb_sv = tmp_path / "tb_vectors.sv"
    tb_sv.write_text(f"""\
module tb_vectors;
  localparam int MEM_WORDS = {mem_words};
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  logic [7:0]   prog [0:63];
  logic [127:0] mem  [0:MEM_WORDS-1];
  logic [127:0] exp  [0:MEM_WORDS-1];

  logic         reg_req = 1'b0, reg_we = 1'b0, reg_gnt, reg_rvalid;
  logic [11:0]  reg_addr = '0;
  logic [3:0]   reg_be = 4'hF;
  logic [31:0]  reg_wdata = '0, reg_rdata;
  logic         rd_req, rd_rvalid = 1'b0, wr_req, irq, flush;
  logic [31:0]  rd_addr, wr_addr;
  logic [127:0] rd_rdata = '0, wr_wdata;
  logic [15:0]  wr_be;

  tq_top #(.T(8)) dut (
    .clk_i(clk), .rst_ni(rst_n),
    .reg_req_i(reg_req), .reg_we_i(reg_we), .reg_addr_i(reg_addr),
    .reg_be_i(reg_be), .reg_wdata_i(reg_wdata),
    .reg_gnt_o(reg_gnt), .reg_rvalid_o(reg_rvalid), .reg_rdata_o(reg_rdata),
    .rd_req_o(rd_req), .rd_addr_o(rd_addr), .rd_gnt_i(1'b1),
    .rd_rvalid_i(rd_rvalid), .rd_rdata_i(rd_rdata),
    .wr_req_o(wr_req), .wr_addr_o(wr_addr), .wr_be_o(wr_be), .wr_wdata_o(wr_wdata),
    .wr_gnt_i(1'b1), .wr_done_i(1'b0), .bus_err_i(1'b0),
    .irq_o(irq), .flush_o(flush), .ext_pending_i(1'b0)
  );

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_rvalid <= 1'b0;
      rd_rdata  <= '0;
    end else begin
      rd_rvalid <= rd_req;
      rd_rdata  <= rd_req ? mem[rd_addr >> 4] : '0;
      if (wr_req) begin
        for (int b = 0; b < 16; b++)
          if (wr_be[b]) mem[wr_addr >> 4][8*b +: 8] <= wr_wdata[8*b +: 8];
      end
    end
  end

  initial begin
    for (int i = 0; i < 64; i++) prog[i] = 8'h00;
    $readmemh("prog.hex", prog);
    $readmemh("mem_init.hex", mem);
    $readmemh("mem_final.hex", exp);
    repeat (3) @(posedge clk);
    @(negedge clk) rst_n = 1'b1;
    for (int i = 0; i < 64; i += 4) begin
      @(negedge clk);
      reg_req = 1'b1; reg_we = 1'b1; reg_addr = 12'h100 + 12'(i);
      reg_wdata = {{prog[i+3], prog[i+2], prog[i+1], prog[i]}};
    end
    @(negedge clk);
    reg_req = 1'b1; reg_we = 1'b1; reg_addr = 12'h008; reg_wdata = 32'd1;
    @(negedge clk);
    reg_req = 1'b0; reg_we = 1'b0;
    for (int c = 0; c < 10000; c++) begin
      @(negedge clk);
      reg_req = 1'b1; reg_we = 1'b0; reg_addr = 12'h00C;
      if (reg_rvalid && reg_rdata[1]) break;
    end
    for (int w = 0; w < MEM_WORDS; w++) begin
      if (mem[w] !== exp[w]) begin
        $display("FAIL word %0d: got %h want %h", w, mem[w], exp[w]);
        $fatal(1);
      end
    end
    $display("PASS");
    $finish;
  end
endmodule
""")
    obj_dir = tmp_path / "obj"
    cmd = ["verilator", "--binary", "-Wno-fatal", "--top-module", "tb_vectors",
           "-Mdir", str(obj_dir), *(str(s) for s in SOURCES), str(tb_sv)]
    subprocess.run(cmd, check=True, cwd=tmp_path)
    res = subprocess.run([str(obj_dir / "Vtb_vectors")], check=True, cwd=tmp_path, capture_output=True, text=True)
    assert "PASS" in res.stdout
