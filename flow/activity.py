# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Switching activity for power analysis: run one workload on tq_top in Verilator and dump SAIF.

    python3 flow/activity.py t8_csa gemm64          # -> build/activity/t8_csa/gemm64.saif
    python3 flow/activity.py list

The run goes through the same cocotb test as the RTL regression, so the result is checked
against the cycle model (memory, transactions, cycles) before the activity is used.

The SAIF covers the whole simulation: reset, program load, the run, and the counter reads after
it. The run is all but a few dozen of those cycles; flow/power.py charges the energy of the whole
window to the workload, which can only overstate energy per MAC.

Operands are uniform random int8 (seeded), a high-activity case, except *_relu workloads:
synthetic post-ReLU uint8 activations (about half zeros) and Gaussian int8 weights. Neither is
data from a real network; uniform is the upper bound, the ReLU case a more typical one.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flow"))
sys.path.insert(0, str(ROOT / "verif"))
sys.path.insert(0, str(ROOT / "verif" / "tb"))

from ppa import CONFIGS  # noqa: E402
from test_rtl import SOURCES, case  # noqa: E402

# name: case (shapes from bench/workloads/gemm.csv)
WORKLOADS = {
    "gemm64":   case(64, 64, 64, lat=2),                 # dense, ~99% utilization at T = 8
    "resnet8_conv32": case(256, 32, 288, lat=2),         # ic_resnet8 conv 3x3 32->32 (16x16)
    "mbv1_pw8_16":    case(2304, 16, 8, lat=2),          # vww_mbv1 pointwise 8->16, low K
    "fcae_fc640_128": case(1, 128, 640, lat=2),         # ad_fcae fc 640->128: GEMV, port-bound
    # the ResNet-8 shape with synthetic inference-like operands (tq_cocotb.operands, dist="relu")
    "resnet8_conv32_relu": dict(case(256, 32, 288, "u8", "s8", lat=2), dist="relu"),
}

TB_PERIOD_PS = 10_000                                   # clock in verif/tb/tq_tb.py

MODEL_FIELDS = {"T": "tile", "SHARED_PORT": "shared_port", "FIFO_DEPTH": "fifo_depth",
                "MACQ_DEPTH": "mac_queue_depth", "SCMD_DEPTH": "stream_queue_depth",
                "PANEL_BYTES": "panel_bytes"}


def model_params(rtl):
    out = {MODEL_FIELDS[k]: (bool(v) if k == "SHARED_PORT" else v)
           for k, v in rtl.items() if k in MODEL_FIELDS}
    return out


def run(config, workload):
    from cocotb_tools.runner import get_results, get_runner

    rtl = CONFIGS[config]
    build_dir = ROOT / "build" / "activity" / config
    saif = build_dir / f"{workload}.saif"
    runner = get_runner("verilator")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="tq_top",
        parameters=rtl,
        build_dir=build_dir,
        waves=True,                                     # makes the runner keep VM_TRACE on
        build_args=["-Wno-fatal", "--assert", "-O3", "--x-assign", "fast", "--x-initial", "fast",
                    "--trace-saif"],
    )
    results = runner.test(
        hdl_toplevel="tq_top",
        test_module="tq_cocotb",
        test_dir=build_dir,
        build_dir=build_dir,
        test_args=["--trace", "--trace-file", str(saif)],
        extra_env={
            "TQ_PARAMS": json.dumps(model_params(rtl)),
            "TQ_CASES": json.dumps([WORKLOADS[workload]]),
        },
    )
    num, failed = get_results(results)
    if num == 0 or failed:
        print(f"[activity] {config} {workload}: run failed, see {results}")
        return 1
    print(f"[activity] {config} {workload}: checked against the model, {saif.relative_to(ROOT)}")
    return 0


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "list":
        for k, v in WORKLOADS.items():
            print(f"{k:16s} {v['m']}x{v['n']}x{v['k']}")
        return 0
    if len(sys.argv) != 3 or sys.argv[1] not in CONFIGS or sys.argv[2] not in WORKLOADS:
        print(__doc__)
        return 2
    return run(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    sys.exit(main())
