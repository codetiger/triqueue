# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""RTL regression: build tq_top with Verilator for each configuration and run the cocotb cases.

    python3 -m pytest verif -q                 # all configurations
    python3 -m pytest verif -q -k t8_default   # one configuration

Each configuration is built once (build/rtl/<name>), then its cases run in one simulation.
"""
import itertools
import json
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
           case(1, 64, 64), case(64, 1, 64), case(3, 3, 3), case(20, 24, 27, "s8", "u8")],
    ),
    "t8_shared_port": (
        dict(T=8, SHARED_PORT=1),
        dict(tile=8, shared_port=True),
        edge_cases(8, (9,)) + latency_sweep(8, (1, 2, 6)) + [case(64, 64, 64), case(32, 32, 8)],
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
           for lat in (1, 2, 6) for s in (0.1, 0.5, 0.8)],
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
        + [case(64, 64, 64), case(64, 64, 8), case(1, 64, 64), case(20, 24, 27, lat=3, stall=0.4)],
    ),
    "t8_reference_queues": (
        dict(T=8, FIFO_DEPTH=4, MACQ_DEPTH=3),
        dict(tile=8, fifo_depth=4, mac_queue_depth=3),
        edge_cases(8, (9,)) + latency_sweep(8, (1, 2, 3, 5)) + [case(64, 64, 64), case(256, 32, 288)],
    ),
    "t8_edge_cases": (
        # illegal programs stop with the model's error code at the model's cycle; ZERO_C in a
        # drain waits; K = 0 writes C = 0; PROG writes while busy are ignored
        dict(T=8),
        dict(tile=8),
        [dict(case(20, 24, 27), prog=pr) for pr in
         ("opcode", "cfg_reg", "cfg_loop", "loop_depth", "second_loop_t", "pc", "zero_in_drain")]
        + [dict(case(20, 24, 27, lat=lat, stall=0.3), prog="zero_in_drain") for lat in (1, 4)]
        + [dict(case(20, 24, 27), misalign=f) for f in ("a_base", "b_base", "c_base", "c_ld")]
        + [case(9, 13, 0), case(16, 16, 0, lat=3), case(1, 1, 0)]
        + [dict(case(64, 64, 64), prog_poke=True), dict(case(20, 24, 27, stall=0.4), prog_poke=True)]
        + [case(20, 24, 27)],                          # a normal run after the errors
    ),
    "t8_edge_shared": (
        dict(T=8, SHARED_PORT=1, WR_ACK=1, WR_OUT_MAX=2),
        dict(tile=8, shared_port=True, wr_ack=True, wr_out_max=2),
        [dict(case(20, 24, 27, lat=2, stall=0.3), prog=pr, wr_lat=3)
         for pr in ("opcode", "second_loop_t", "pc", "zero_in_drain")]
        + [dict(case(20, 24, 27, lat=3), misalign="c_base", wr_lat=2), dict(case(9, 13, 0), wr_lat=1)],
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
        # an SLVERR write response sets STATUS.BUS_ERR, and the next START clears it
        + [dict(case(20, 24, 27), wr_lat=2, bus_err=3), dict(case(9, 9, 9), wr_lat=1)],
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
        edge_cases(16, (1, 9, 40)) + signedness(16) + latency_sweep(16, (1, 2, 7)) + [case(64, 64, 64)],
    ),
    "t4": (
        dict(T=4),
        dict(tile=4),
        edge_cases(4, (1, 8, 9, 33)) + latency_sweep(4, (1, 2, 5)) + [case(16, 16, 16)],
    ),
}


# configurations whose top level is not tq_top (the testbench host driver follows it)
TOPS = {"t8_axil": ("tq_top_axil", "axil"), "t8_axi": ("tq_top_axi", "axi"), "t8_xif": ("tq_top_xif", "xif")}


@pytest.mark.parametrize("name", list(CONFIGS))
def test_rtl(name):
    from cocotb_tools.runner import get_runner

    rtl_params, model_params, cases = CONFIGS[name]
    top, host = TOPS.get(name, ("tq_top", "native"))
    build_dir = ROOT / "build" / "rtl" / name
    runner = get_runner("verilator")
    runner.build(
        sources=SOURCES,
        hdl_toplevel=top,
        parameters=rtl_params,
        build_dir=build_dir,
        build_args=["-Wno-fatal", "--assert", "-O3", "--x-assign", "fast", "--x-initial", "fast"],
    )
    results = runner.test(
        hdl_toplevel=top,
        test_module="tq_cocotb",
        test_dir=build_dir,
        build_dir=build_dir,
        extra_env={
            "TQ_PARAMS": json.dumps(model_params),
            "TQ_CASES": json.dumps(cases),
            "TQ_HOST": host,
        },
    )
    from cocotb_tools.runner import get_results
    num, failed = get_results(results)
    assert num > 0 and failed == 0, f"{failed} cocotb test(s) failed, see {results}"
