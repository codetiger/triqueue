# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""End-to-end int8 networks (MLPerf Tiny DS-CNN and ResNet-8) on the Tri-Queue cycle model.

The convolutions and the fully connected layers run on the core: the host builds A (im2col, or the
layer input itself for a stride-1 1x1 convolution) and packs it, and the core does the GEMM and the
TFLite epilogue (MODE[9]) from a parameter table built offline with the packed weights. Depthwise
convolutions, residual adds, average pools and reshapes run on the host (spec: they are out of
scope for the outer-product engine). Each op consumes the tensors computed here, and every tensor
is checked against the TFLite interpreter's (<net>_golden.npz, from export.py), bit for bit.
Softmax is left to the host; its input is checked.

    python3 bench/e2e/network.py            # rewrites bench/e2e/results.md (~2 min)
"""
import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "model"))
import tflite_ref as tr  # noqa: E402
from tri_queue.core import Params, run_gemm  # noqa: E402
from tri_queue.packing import kp, pack_a  # noqa: E402

ENGINE = ("CONV_2D", "FULLY_CONNECTED")
TILES = (8, 4)
TITLE = {"dscnn": "DS-CNN keyword spotting (`kws_ref_model.tflite`)",
         "resnet8": "ResNet-8 image classification (`pretrainedResnet_quant.tflite`)"}


def walk_cols(m: int, tile: int) -> bool:
    """The driver's choice (sw/tq.c tq_layer_prepare): column walk with more than one tile row."""
    return m > tile


def host_op(net: tr.Net, op: dict, x: list) -> np.ndarray:
    """One op the core does not run, on the host reference kernels."""
    if op["kind"] == "DEPTHWISE_CONV_2D":
        return tr.depthwise(net, op, x[0])
    if op["kind"] == "ADD":
        return tr.add(net, op, x[0], x[1])
    if op["kind"] == "AVERAGE_POOL_2D":
        return tr.average_pool(net, op, x[0])
    if op["kind"] == "RESHAPE":
        return x[0].reshape(net.tensors[op["outputs"][0]]["shape"])
    raise NotImplementedError(op["kind"])


def activations(net: tr.Net, op: dict) -> list:
    """The op's non-constant inputs (ADD has two; the others one, then weights, bias or shape)."""
    return op["inputs"][:2] if op["kind"] == "ADD" else op["inputs"][:1]


def run(net: tr.Net, seed: int, tile: int):
    """Runs one input through the network. Returns (rows, mismatches): one row per op."""
    val = {net.inputs[0]: net.golden(seed, net.inputs[0])}
    rows, bad = [], []
    for k, op in enumerate(net.ops):
        o = op["outputs"][0]
        x = [val[i] for i in activations(net, op)]
        row = dict(idx=k, kind=op["kind"], host_bytes=0, host_macs=0, host_elems=0)
        if op["kind"] == "SOFTMAX":
            rows.append(dict(row, where="host", macs=0))
            break
        if op["kind"] in ENGINE:
            g = tr.lower(net, op, x[0])
            m, kk = g.a.shape
            n = g.b.shape[1]
            p = Params(tile=tile, out_bytes=1, out_shift=0, walk_cols=walk_cols(m, tile))
            c, s = run_gemm(g.a, g.b, p, epi=g.epi)
            assert s.error == 0
            if not np.array_equal(c, tr.gemm_reference(g)):
                bad.append((k, "core vs requant reference"))
            want, rdiff = tr.core_expected(op, g, net.golden(seed, o))
            if not np.array_equal(c, want):
                bad.append((k, f"{op['kind']} vs TFLite"))
            y = c.reshape(g.out_shape)
            row.update(where="core", m=m, n=n, k=kk, macs=m * n * kk, cycles=s.cycles, rounding=rdiff,
                       util=m * n * kk / (s.cycles * tile * tile), walk="cols" if p.walk_cols else "rows",
                       host_bytes=g.im2col_bytes + len(pack_a(g.a, kp(kk), tile)))
        else:
            y = host_op(net, op, x)
            row.update(where="host", macs=0)
            if op["kind"] == "DEPTHWISE_CONV_2D":
                kh, kw = net.tensors[op["inputs"][1]]["shape"][1:3]
                row.update(macs=y.size * kh * kw, host_macs=y.size * kh * kw)
            elif op["kind"] == "AVERAGE_POOL_2D":
                row.update(host_elems=x[0].size)
            elif op["kind"] == "ADD":
                row.update(host_elems=y.size)
            if not np.array_equal(y, net.golden(seed, o)):
                bad.append((k, f"{op['kind']} vs TFLite"))
        rows.append(row)
        val[o] = y
    return rows, bad


LABEL = {"CONV_2D": "conv", "DEPTHWISE_CONV_2D": "depthwise", "AVERAGE_POOL_2D": "average pool",
         "RESHAPE": "reshape", "FULLY_CONNECTED": "fully connected", "SOFTMAX": "softmax", "ADD": "add"}


def conv_label(net: tr.Net, op: dict) -> str:
    if op["kind"] != "CONV_2D":
        return LABEL[op["kind"]]
    _, kh, kw, _ = net.tensors[op["inputs"][1]]["shape"]
    s = op["options"]["stride"][0]
    return f"conv {kh}×{kw}" + (f" s{s}" if s > 1 else "")


def host_text(hb: int, hm: int, he: int, cyc: int) -> str:
    """The per-inference host work and its largest term."""
    does = ([f"{hm:,} depthwise MACs"] if hm else []) + [f"{he:,} element ops (residual adds, average pool)"]
    text = (f"writes {hb:,} bytes (im2col and A packing; weights, B packing and the epilogue tables are "
            f"offline) and does {' and '.join(does)}")
    host = hb + hm + he
    top = max((hb, "im2col and A packing"), (hm, "the depthwise convolutions"), (he, "the element ops"))
    return (f"Per inference the host {text}. At an assumed scalar host "
            f"rate of one byte, MAC or element op per cycle that is about {host:,} host cycles, "
            f"{host / cyc:.1f}× the core's, most of it {top[1]} ({100 * top[0] / host:.0f}%).")


def section(name: str, net: tr.Net) -> list:
    out = [f"## {TITLE[name]}", ""]
    for tile in TILES:
        results = [run(net, s, tile) for s in net.seeds]
        bad = [b for _, bb in results for b in bb]
        rows = results[0][0]
        fc = [r for rr, _ in results for r in rr if r["kind"] == "FULLY_CONNECTED"]
        rdiff, fc_out = sum(r["rounding"] for r in fc), sum(r["m"] * r["n"] for r in fc)
        tensors = sum(len(r) for r, _ in results)
        if bad:
            status = f"**NO: {bad}**"
        elif rdiff:
            status = (f"**yes**, {tensors} tensors over {len(net.seeds)} inputs, except {rdiff} of the {fc_out} "
                      "fully connected outputs, one LSB apart: TFLite's reference FULLY_CONNECTED rounds "
                      "once, the core twice (see Notes).")
        else:
            status = f"**yes**, {tensors} tensors over {len(net.seeds)} inputs."
        out += [f"### T = {tile} ({tile * tile} MACs/cycle), default build", "",
                f"Bit-exact with TFLite: {status}", "",
                "| # | Op | Runs on | GEMM m × n × k | MACs | Walk | Core cycles | Utilization "
                "| Host bytes (im2col + pack A) | Host MACs | Host element ops |",
                "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
        for r in rows:
            core = r["where"] == "core"
            out.append(f"| {r['idx']} | {conv_label(net, net.ops[r['idx']])} | {r['where']} | "
                       + (f"{r['m']} × {r['n']} × {r['k']}" if core else "–")
                       + f" | {r['macs']:,} | {r.get('walk', '–')} | "
                       + (f"{r['cycles']:,} | {100 * r['util']:.1f}%" if core else "– | –")
                       + f" | {r['host_bytes']:,} | {r['host_macs']:,} | {r['host_elems']:,} |")
        cyc = sum(r.get("cycles", 0) for r in rows)
        cmacs = sum(r["macs"] for r in rows if r["where"] == "core")
        hb, hm, he = (sum(r[f] for r in rows) for f in ("host_bytes", "host_macs", "host_elems"))
        out += [f"| | **total** | | | {cmacs + hm:,} | | **{cyc:,}** | "
                f"**{100 * cmacs / (cyc * tile * tile):.1f}%** | {hb:,} | {hm:,} | {he:,} |", "",
                f"The core does {100 * cmacs / (cmacs + hm):.1f}% of the network's MACs in {cyc:,} cycles. "
                + host_text(hb, hm, he, cyc), ""]
    return out


def report(names=tr.NETS) -> str:
    out = ["<!-- Generated by bench/e2e/network.py; do not edit. -->", "",
           "# End-to-end int8 networks", "",
           "MLPerf Tiny reference models (int8, per-channel quantization) run layer by layer: "
           "convolutions and fully connected layers on the Tri-Queue cycle model (host im2col and "
           "packing of A, then GEMM + TFLite epilogue, `MODE[9]`), depthwise convolutions, residual "
           "adds and average pools on the host. Each op consumes this run's tensors, and every "
           "tensor is compared with the TFLite interpreter's (reference kernels).", "",
           "Host im2col and packing are counted as two passes (build the im2col rows, then pack them "
           "into panels); a host that writes im2col rows straight into the panel layout writes only "
           "the packed bytes.", ""]
    for name in names:
        out += section(name, tr.load(name))
    out += ["## Notes", "",
            "**Rounding.** The core's epilogue rounds like TFLite's default "
            "`MultiplyByQuantizedMultiplier` (a rounding doubling high multiply, then a rounding "
            "right shift), as the reference `CONV_2D` kernel does: every convolution output above is "
            "TFLite's. The reference int8 `FULLY_CONNECTED` kernel of the LiteRT build used here "
            "(ai-edge-litert 2.1.6) rounds once instead (one 64-bit product, round half up, the "
            "`TFLITE_SINGLE_ROUNDING` form): over 2,000 random inputs per network, every classifier "
            "output where the two roundings differ (82 for DS-CNN, 284 for ResNet-8) follows the "
            "single rounding. Those outputs differ by one LSB. The check is exact either way: TFLite's "
            "tensor must equal the single-rounded reference and the core's the double-rounded one, "
            "element for element. TFLite's other kernel paths round differently again (the optimized "
            "`CONV_2D` rounds once; XNNPACK matches neither form exactly), so a bit-exact claim names "
            "the kernel.", "",
            "Inputs are uniform random int8 (`export.py`, seeds 1, 2, 3), not audio features or "
            "images: bit-exactness does not depend on the data, and the cycle counts depend only on "
            "the shapes. The model's `mem_latency`, FIFO and queue depths are the defaults; no panel "
            "buffer. Host costs are counts, not a host simulation.", ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=Path, default=HERE / "results.md")
    args = ap.parse_args(argv)
    text = report()
    args.out.write_text(text)
    print(text)
    if "NO:" in text:
        raise SystemExit("mismatch against TFLite")


if __name__ == "__main__":
    main()
