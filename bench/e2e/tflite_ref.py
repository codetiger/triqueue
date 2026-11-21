# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Int8 TFLite layers for the end-to-end run: lowering of CONV_2D and FULLY_CONNECTED to a GEMM
plus the core's epilogue (MODE[9]), and host reference kernels for the ops the core does not run
(DEPTHWISE_CONV_2D, ADD, AVERAGE_POOL_2D, RESHAPE). Transcribed from TFLite's reference integer
kernels (reference_integer_ops::ConvPerChannel, DepthwiseConvPerChannel, FullyConnected, Add,
AveragePool) and their Prepare steps (PopulateConvolutionQuantizationParams, the int8 Add
parameters); network.py checks every tensor against the TFLite interpreter's, bit for bit."""
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
from tri_queue import requant  # noqa: E402

HERE = Path(__file__).resolve().parent
NETS = ("dscnn", "resnet8")


@dataclass
class Net:
    ops: list
    tensors: dict
    inputs: list
    outputs: list
    arrays: dict
    seeds: list

    def const(self, i):
        return self.arrays[f"const_{i}"]

    def golden(self, seed, i):
        return self.arrays[f"in{seed}_{i}"]

    def q(self, i):
        t = self.tensors[i]
        return t["scale"], t["zero_point"]


def load(name: str = "dscnn") -> Net:
    """The golden reference bench/e2e/<name>_golden.npz (export.py)."""
    z = np.load(HERE / f"{name}_golden.npz")
    meta = json.loads(str(z["meta"]))
    tensors = {int(k): v for k, v in meta["tensors"].items()}
    return Net(meta["ops"], tensors, meta["inputs"], meta["outputs"],
               {k: z[k] for k in z.files if k != "meta"}, meta["seeds"])


def same_padding(size, k, s):
    """TFLite ComputePaddingHeightWidth for SAME: output size and the padding before."""
    out = -(-size // s)
    return out, max((out - 1) * s + k - size, 0) // 2


def act_range(activation, scale, zp):
    """CalculateActivationRangeQuantized for int8."""
    lo, hi = -128, 127
    if activation == "relu":
        lo = max(lo, zp)
    elif activation == "relu6":
        lo, hi = max(lo, zp), min(hi, zp + int(round(6.0 / scale)))
    return lo, hi


def multipliers(in_scale, w_scales, out_scale, n):
    """Per-channel (M0, shift): effective scale in double, then QuantizeMultiplier."""
    w = list(w_scales) * (n if len(w_scales) == 1 else 1)
    pairs = [requant.quantize_multiplier(float(in_scale) * float(s) / float(out_scale)) for s in w]
    return np.array([p[0] for p in pairs], np.int64), np.array([p[1] for p in pairs], np.int64)


def wrap32(x):
    return ((np.asarray(x, np.int64) + (1 << 31)) % (1 << 32) - (1 << 31)).astype(np.int32)


@dataclass
class Gemm:
    """One engine layer: C (m x n, int8) = epilogue(A (m x k) x B (k x n))."""
    a: np.ndarray
    b: np.ndarray
    epi: dict
    out_shape: list
    im2col_bytes: int          # bytes the host writes to build A beyond the layer input (0 for 1x1)


def lower(net: Net, op: dict, x: np.ndarray) -> Gemm:
    """CONV_2D / FULLY_CONNECTED with input tensor x (NHWC / NC) as a GEMM plus epilogue.
    Out-of-image taps are the input zero point, so after folding -zp_in * sum(w) into the bias they
    add nothing, as the reference kernel's skipped taps."""
    i_in, i_w, i_b = op["inputs"][:3]
    o = op["outputs"][0]
    (s_in,), (zp_in,) = net.q(i_in)
    w_scales, _ = net.q(i_w)
    (s_out,), (zp_out,) = net.q(o)
    w = net.const(i_w).astype(np.int8)
    if op["kind"] == "CONV_2D":
        assert op["options"]["dilation"] == [1, 1]
        _, h, wd, c = x.shape
        cout, kh, kw, cin = w.shape
        sh, sw = op["options"]["stride"]
        if op["options"]["padding"] == "same":
            (ho, ph), (wo, pw) = same_padding(h, kh, sh), same_padding(wd, kw, sw)
        else:
            ho, wo, ph, pw = (h - kh) // sh + 1, (wd - kw) // sw + 1, 0, 0
        if (kh, kw, sh, sw, ph, pw) == (1, 1, 1, 1, 0, 0):
            a, extra = x.reshape(h * wd, c), 0
        else:
            xp = np.full((h + kh + sh * ho, wd + kw + sw * wo, c), zp_in, np.int8)
            xp[ph:ph + h, pw:pw + wd] = x[0]
            a = np.stack([xp[oy * sh:oy * sh + kh, ox * sw:ox * sw + kw].reshape(-1)
                          for oy in range(ho) for ox in range(wo)])
            extra = a.size
        b = w.reshape(cout, kh * kw * cin).T
        out_shape = [1, ho, wo, cout]
    else:
        assert op["kind"] == "FULLY_CONNECTED"
        a, b, extra = x.reshape(-1, w.shape[1]), w.T, 0
        cout = w.shape[0]
        out_shape = net.tensors[o]["shape"]
    bias = net.const(i_b).astype(np.int64) if i_b >= 0 else np.zeros(cout, np.int64)
    bias = wrap32(bias - zp_in * b.astype(np.int64).sum(0))
    m0, shift = multipliers(s_in, w_scales, s_out, cout)
    lo, hi = act_range(op["options"]["activation"], s_out, zp_out)
    epi = dict(bias=bias, m0=m0, shift=shift, zp=zp_out, act_min=lo, act_max=hi)
    return Gemm(np.ascontiguousarray(a), np.ascontiguousarray(b), epi, out_shape, extra)


def gemm_reference(g: Gemm, rounding: str = "double") -> np.ndarray:
    """The layer's int8 output (m x n). "double" is the core's epilogue (TFLite's default
    MultiplyByQuantizedMultiplier); "single" is the TFLITE_SINGLE_ROUNDING form."""
    acc = g.a.astype(np.int64) @ g.b.astype(np.int64)
    return requant.requantize(acc, g.epi["bias"], g.epi["m0"], np.clip(g.epi["shift"], -31, 30), g.epi["zp"],
                              g.epi["act_min"], g.epi["act_max"], rounding)


def core_expected(op: dict, g: Gemm, tflite_out: np.ndarray):
    """What the core must write for a layer whose TFLite output tensor is tflite_out: that tensor
    (m x n), except for FULLY_CONNECTED. LiteRT's reference int8 FULLY_CONNECTED rounds once (one
    64-bit product, round half up; measured over 2,000 inputs per network, every element where the
    two roundings differ follows the single one) while its CONV_2D and the core round twice, so
    there TFLite's tensor must equal the single-rounded reference exactly and the core's value is
    the double-rounded one. Returns (expected, number of elements that differ from TFLite's)."""
    t = tflite_out.reshape(g.a.shape[0], g.b.shape[1])
    if op["kind"] != "FULLY_CONNECTED":
        return t, 0
    single = gemm_reference(g, "single")
    if not np.array_equal(t, single):
        raise AssertionError("TFLite FULLY_CONNECTED differs from the single-rounded reference")
    double = gemm_reference(g)
    return double, int((double != t).sum())


def depthwise(net: Net, op: dict, x: np.ndarray) -> np.ndarray:
    """reference_integer_ops::DepthwiseConvPerChannel (out-of-image taps skipped)."""
    i_in, i_w, i_b = op["inputs"][:3]
    o = op["outputs"][0]
    (s_in,), (zp_in,) = net.q(i_in)
    w_scales, _ = net.q(i_w)
    (s_out,), (zp_out,) = net.q(o)
    assert op["options"]["depth_multiplier"] == 1 and op["options"]["dilation"] == [1, 1]
    w = net.const(i_w).astype(np.int64)[0]                       # kh x kw x c
    _, h, wd, c = x.shape
    kh, kw, _ = w.shape
    sh, sw = op["options"]["stride"]
    if op["options"]["padding"] == "same":
        (ho, ph), (wo, pw) = same_padding(h, kh, sh), same_padding(wd, kw, sw)
    else:
        ho, wo, ph, pw = (h - kh) // sh + 1, (wd - kw) // sw + 1, 0, 0
    xi = x[0].astype(np.int64) - zp_in
    acc = np.zeros((ho, wo, c), np.int64)
    for dy in range(kh):
        for dx in range(kw):
            for oy in range(ho):
                iy = oy * sh - ph + dy
                if not 0 <= iy < h:
                    continue
                for ox in range(wo):
                    ix = ox * sw - pw + dx
                    if 0 <= ix < wd:
                        acc[oy, ox] += xi[iy, ix] * w[dy, dx]
    bias = net.const(i_b).astype(np.int64) if i_b >= 0 else 0
    m0, shift = multipliers(s_in, w_scales, s_out, c)
    lo, hi = act_range(op["options"]["activation"], s_out, zp_out)
    out = requant.requantize(acc.reshape(-1, c), bias, m0, shift, zp_out, lo, hi)
    return out.reshape(1, ho, wo, c)


def add(net: Net, op: dict, x1: np.ndarray, x2: np.ndarray) -> np.ndarray:
    """reference_integer_ops::Add for int8 (same shapes): both inputs rescaled to twice the larger
    input scale with 20 bits of headroom, summed, then rescaled to the output scale."""
    (s1,), (zp1,) = net.q(op["inputs"][0])
    (s2,), (zp2,) = net.q(op["inputs"][1])
    (s_out,), (zp_out,) = net.q(op["outputs"][0])
    assert x1.shape == x2.shape
    left_shift = 20
    twice_max = 2 * max(float(s1), float(s2))
    m1, sh1 = requant.quantize_multiplier(float(s1) / twice_max)
    m2, sh2 = requant.quantize_multiplier(float(s2) / twice_max)
    mo, sho = requant.quantize_multiplier(twice_max / ((1 << left_shift) * float(s_out)))
    assert max(sh1, sh2, sho) <= 0                       # QuantizeMultiplierSmallerThanOneExp
    lo, hi = act_range(op["options"]["activation"], s_out, zp_out)
    v1 = requant.mbqm((x1.astype(np.int64) - zp1) << left_shift, m1, sh1)
    v2 = requant.mbqm((x2.astype(np.int64) - zp2) << left_shift, m2, sh2)
    raw = requant.mbqm(v1.astype(np.int64) + v2, mo, sho).astype(np.int64) + zp_out
    return np.clip(raw, lo, hi).astype(np.int8)


def average_pool(net: Net, op: dict, x: np.ndarray) -> np.ndarray:
    """reference_integer_ops::AveragePool: rounded (half away from zero) mean of the valid taps."""
    fh, fw = op["options"]["filter"]
    sh, sw = op["options"]["stride"]
    _, h, wd, c = x.shape
    assert op["options"]["padding"] == "valid"
    ho, wo = (h - fh) // sh + 1, (wd - fw) // sw + 1
    out = np.zeros((1, ho, wo, c), np.int8)
    (s_out,), (zp_out,) = net.q(op["outputs"][0])
    lo, hi = act_range(op["options"]["activation"], s_out, zp_out)
    for oy in range(ho):
        for ox in range(wo):
            win = x[0, oy * sh:oy * sh + fh, ox * sw:ox * sw + fw].astype(np.int64).reshape(-1, c)
            acc, cnt = win.sum(0), win.shape[0]
            q = np.where(acc > 0, (acc + cnt // 2) // cnt, -((-acc + cnt // 2) // cnt))
            out[0, oy, ox] = np.clip(q, lo, hi)
    return out
