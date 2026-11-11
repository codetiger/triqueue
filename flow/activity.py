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
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flow"))
sys.path.insert(0, str(ROOT / "bench" / "workloads"))
sys.path.insert(0, str(ROOT / "verif"))
sys.path.insert(0, str(ROOT / "verif" / "tb"))

from ppa import CONFIGS  # noqa: E402
from test_rtl import SOURCES, case  # noqa: E402
from workloads import shapes as workload_shapes  # noqa: E402

_PPA_NAMES = {
    (64, 64, 64): ("gemm64", 0),                  # dense, ~99% utilization at T = 8
    (256, 32, 288): ("resnet8_conv32", 1),        # ic_resnet8 conv 3x3 32->32 (16x16)
    (2304, 16, 8): ("mbv1_pw8_16", 2),            # vww_mbv1 pointwise 8->16, low K
    (1, 128, 640): ("fcae_fc640_128", 3),         # ad_fcae fc 640->128: GEMV, port-bound
}


def _build_workloads() -> dict[str, dict]:
    found = {}
    for m, n, k in workload_shapes("ppa"):
        if (m, n, k) not in _PPA_NAMES:
            raise ValueError(f"Unmapped 'ppa' workload shape in gemm.csv: {(m, n, k)}")
        name, order = _PPA_NAMES[(m, n, k)]
        found[order] = (name, case(m, n, k, lat=2))
    out = {name: c for _, (name, c) in sorted(found.items())}
    if "resnet8_conv32" in out:
        c = out["resnet8_conv32"]
        out["resnet8_conv32_relu"] = dict(case(c["m"], c["n"], c["k"], "u8", "s8", lat=2), dist="relu")
    return out


# Selected by tag `ppa` in bench/workloads/gemm.csv
WORKLOADS = _build_workloads()

TB_PERIOD_PS = 10_000                                   # clock in verif/tb/tq_tb.py

MODEL_FIELDS = {"T": "tile", "SHARED_PORT": "shared_port", "FIFO_DEPTH": "fifo_depth",
                "MACQ_DEPTH": "mac_queue_depth", "SCMD_DEPTH": "stream_queue_depth",
                "PANEL_BYTES": "panel_bytes", "MAC_PIPE": "mac_pipe"}


def model_params(rtl):
    out = {MODEL_FIELDS[k]: (bool(v) if k == "SHARED_PORT" else v)
           for k, v in rtl.items() if k in MODEL_FIELDS}
    return out


def saif_source_digest(config: str, workload: str) -> str:
    """SHA-256 over RTL sources, TB sources, config parameters, and workload specification."""
    h = hashlib.sha256()
    rtl_files = [ROOT / f for f in (ROOT / "rtl" / "files.f").read_text().split()]
    tb_files = [ROOT / "verif" / "tb" / "tq_tb.py", ROOT / "verif" / "tb" / "tq_cocotb.py"]
    for p in rtl_files + tb_files:
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    h.update(json.dumps(CONFIGS[config], sort_keys=True).encode())
    h.update(b"\0")
    h.update(json.dumps(WORKLOADS[workload], sort_keys=True).encode())
    return h.hexdigest()


def run(config, workload):
    from cocotb_tools.runner import get_results, get_runner

    rtl = CONFIGS[config]
    build_dir = ROOT / "build" / "activity" / config
    saif = build_dir / f"{workload}.saif"
    meta = build_dir / f"{workload}.saif.meta.json"
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
    meta.write_text(json.dumps({
        "config": config,
        "workload": workload,
        "source_sha256": saif_source_digest(config, workload),
    }, indent=2) + "\n")
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
