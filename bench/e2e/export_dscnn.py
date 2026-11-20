# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Export the TFLite golden reference for the end-to-end DS-CNN run (bench/e2e/README.md).

Runs the MLPerf Tiny keyword-spotting DS-CNN (kws_ref_model.tflite, int8) in the TFLite
interpreter with the reference kernels (no XNNPACK delegate) and writes dscnn_golden.npz:
the graph (ops, options, tensor shapes and quantization), every constant tensor, and every
intermediate tensor for a few int8 inputs. dscnn.py and the tests only need numpy and this file.

Needs ai-edge-litert and tflite (flatbuffer schema), which are not in requirements.txt:

    python3 -m venv build/venv-litert
    build/venv-litert/bin/pip install ai-edge-litert==2.1.6 tflite==2.18.0 numpy
    build/venv-litert/bin/python bench/e2e/export_dscnn.py
"""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
URL = ("https://github.com/mlcommons/tiny/raw/master/benchmark/training/keyword_spotting/"
       "trained_models/kws_ref_model.tflite")
SHA256 = "aeea436800704fce17b17292e4412630ad856e9d777c044c64ef748a880bd0ae"
SEEDS = (1, 2, 3)


def fetch(path: Path) -> bytes:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, path)
    buf = path.read_bytes()
    if hashlib.sha256(buf).hexdigest() != SHA256:
        raise SystemExit(f"{path}: unexpected SHA-256 (expected {SHA256})")
    return buf


def graph(buf: bytes):
    """Ops with their options and the tensors they use, from the flatbuffer."""
    import tflite
    m = tflite.Model.GetRootAsModel(buf, 0)
    g = m.Subgraphs(0)
    names = {v: k for k, v in tflite.BuiltinOperator.__dict__.items() if not k.startswith("_")}
    pad = {tflite.Padding.SAME: "same", tflite.Padding.VALID: "valid"}
    act = {tflite.ActivationFunctionType.NONE: "none", tflite.ActivationFunctionType.RELU: "relu",
           tflite.ActivationFunctionType.RELU6: "relu6"}
    ops = []
    for i in range(g.OperatorsLength()):
        op = g.Operators(i)
        code = m.OperatorCodes(op.OpcodeIndex())
        kind = names[max(code.BuiltinCode(), code.DeprecatedBuiltinCode())]
        raw, opt = op.BuiltinOptions(), {}
        if kind in ("CONV_2D", "DEPTHWISE_CONV_2D"):
            o = (tflite.Conv2DOptions if kind == "CONV_2D" else tflite.DepthwiseConv2DOptions)()
            o.Init(raw.Bytes, raw.Pos)
            opt = dict(padding=pad[o.Padding()], stride=[o.StrideH(), o.StrideW()],
                       dilation=[o.DilationHFactor(), o.DilationWFactor()],
                       activation=act[o.FusedActivationFunction()])
            if kind == "DEPTHWISE_CONV_2D":
                opt["depth_multiplier"] = o.DepthMultiplier()
        elif kind == "AVERAGE_POOL_2D":
            o = tflite.Pool2DOptions()
            o.Init(raw.Bytes, raw.Pos)
            opt = dict(padding=pad[o.Padding()], stride=[o.StrideH(), o.StrideW()],
                       filter=[o.FilterHeight(), o.FilterWidth()], activation=act[o.FusedActivationFunction()])
        elif kind == "FULLY_CONNECTED":
            o = tflite.FullyConnectedOptions()
            o.Init(raw.Bytes, raw.Pos)
            opt = dict(activation=act[o.FusedActivationFunction()], keep_num_dims=bool(o.KeepNumDims()))
        ops.append(dict(kind=kind, inputs=[int(x) for x in op.InputsAsNumpy()],
                        outputs=[int(x) for x in op.OutputsAsNumpy()], options=opt))
    tensors = {}
    for i in range(g.TensorsLength()):
        t = g.Tensors(i)
        q = t.Quantization()
        tensors[i] = dict(
            name=t.Name().decode(), shape=[int(x) for x in t.ShapeAsNumpy()] if t.ShapeLength() else [],
            scale=[float(x) for x in q.ScaleAsNumpy()] if q and q.ScaleLength() else [],
            zero_point=[int(x) for x in q.ZeroPointAsNumpy()] if q and q.ZeroPointLength() else [])
    return ops, tensors, [int(x) for x in g.InputsAsNumpy()], [int(x) for x in g.OutputsAsNumpy()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, default=HERE.parents[1] / "build" / "kws" / "kws_ref_model.tflite")
    ap.add_argument("-o", "--out", type=Path, default=HERE / "dscnn_golden.npz")
    args = ap.parse_args(argv)
    from ai_edge_litert.interpreter import Interpreter, OpResolverType

    buf = fetch(args.model)
    ops, tensors, g_in, g_out = graph(buf)
    it = Interpreter(model_content=buf, experimental_preserve_all_tensors=True,
                     experimental_op_resolver_type=OpResolverType.BUILTIN_REF)
    it.allocate_tensors()
    const = {i for op in ops for i in op["inputs"] if i >= 0} - {o for op in ops for o in op["outputs"]} - set(g_in)
    arrays = {f"const_{i}": it.get_tensor(i) for i in sorted(const)}
    shape = tensors[g_in[0]]["shape"]
    for s in SEEDS:
        x = np.random.default_rng(s).integers(-128, 128, shape).astype(np.int8)
        it.set_tensor(g_in[0], x)
        it.invoke()
        arrays[f"in{s}_{g_in[0]}"] = x
        for op in ops:
            for o in op["outputs"]:
                arrays[f"in{s}_{o}"] = it.get_tensor(o)
    meta = dict(source=URL, sha256=SHA256, runtime="ai-edge-litert, BUILTIN_REF op resolver",
                seeds=list(SEEDS), ops=ops, tensors=tensors, inputs=g_in, outputs=g_out)
    np.savez_compressed(args.out, meta=np.array(json.dumps(meta)), **arrays)
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes, {len(ops)} ops, {len(SEEDS)} inputs)")


if __name__ == "__main__":
    main()
