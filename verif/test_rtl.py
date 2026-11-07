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


@pytest.mark.parametrize("name", list(CONFIGS))
def test_rtl(name):
    from cocotb_tools.runner import get_runner

    rtl_params, model_params, cases = CONFIGS[name]
    build_dir = ROOT / "build" / "rtl" / name
    runner = get_runner("verilator")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="tq_top",
        parameters=rtl_params,
        build_dir=build_dir,
        build_args=["-Wno-fatal", "--assert", "-O3", "--x-assign", "fast", "--x-initial", "fast"],
    )
    results = runner.test(
        hdl_toplevel="tq_top",
        test_module="tq_cocotb",
        test_dir=build_dir,
        build_dir=build_dir,
        extra_env={
            "TQ_PARAMS": json.dumps(model_params),
            "TQ_CASES": json.dumps(cases),
        },
    )
    from cocotb_tools.runner import get_results
    num, failed = get_results(results)
    assert num > 0 and failed == 0, f"{failed} cocotb test(s) failed, see {results}"
