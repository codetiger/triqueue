# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Golden test vectors for RTL: memory images, program, expected result, and the event trace.

    python3 -m tri_queue.vectors --m 16 --n 16 --k 16 -o vectors/16x16x16
    python3 -m tri_queue.vectors --m 20 --n 9 --k 33 --tile 16 --a u8 -o vectors/edge_t16

File formats are specified in docs/spec/trace_format.md.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from .core import Params, TriQueueCore, build_gemm, reference, read_c

DTYPES = {"s8": np.int8, "u8": np.uint8}


def write_words(path: Path, mem: np.ndarray, width: int):
    """$readmemh image: one `width`-byte word per line, little-endian (lowest address = LSB)."""
    assert len(mem) % width == 0
    words = mem.reshape(-1, width)[:, ::-1]
    path.write_text("".join(w.tobytes().hex() + "\n" for w in words))


def read_words(path: Path, width: int) -> np.ndarray:
    rows = [bytes.fromhex(line.strip())[::-1] for line in path.read_text().split()]
    assert all(len(r) == width for r in rows)
    return np.frombuffer(b"".join(rows), np.uint8).copy()


def export(a: np.ndarray, b: np.ndarray, params: Params, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    mem, prog, L = build_gemm(a, b, params)
    write_words(out / "mem_init.hex", mem, params.port_bytes)
    (out / "prog.hex").write_text("".join(f"{x:02x}\n" for x in prog))
    trace = []
    core = TriQueueCore(mem, prog, params, trace)
    s = core.run()
    s.useful_macs = L["m"] * L["n"] * L["k"]
    assert np.array_equal(read_c(mem, L), reference(a, b)), "model disagrees with numpy"
    write_words(out / "mem_final.hex", mem, params.port_bytes)
    (out / "trace.txt").write_text("\n".join(trace) + "\n")
    meta = {
        "format": 1,
        "params": {k: v for k, v in vars(params).items()},
        "layout": L,
        "mem_bytes": len(mem),
        "prog_bytes": len(prog),
        "expected": {
            "cycles": s.cycles, "instructions": s.instructions, "beats": s.beats,
            "read_vectors": s.read_vectors, "read_bytes": s.read_bytes,
            "write_vectors": s.write_vectors, "write_bytes": s.write_bytes,
            "mac_ops": dict(s.mac_ops), "utilization": round(s.utilization, 6),
        },
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m", type=int, required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--k", type=int, required=True)
    ap.add_argument("--a", choices=DTYPES, default="s8", help="A operand type")
    ap.add_argument("--b", choices=DTYPES, default="s8", help="B operand type")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tile", type=int, default=8)
    ap.add_argument("--port-bytes", type=int, default=0, help="0 = 2 x tile")
    ap.add_argument("--mem-latency", type=int, default=2)
    ap.add_argument("--fifo-depth", type=int, default=8)
    ap.add_argument("--shared-port", action="store_true")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(args.seed)

    def rand(shape, dt):
        lo, hi = (-128, 128) if dt == "s8" else (0, 256)
        return rng.integers(lo, hi, shape).astype(DTYPES[dt])

    a, b = rand((args.m, args.k), args.a), rand((args.k, args.n), args.b)
    p = Params(tile=args.tile, port_bytes=args.port_bytes, mem_latency=args.mem_latency,
               fifo_depth=args.fifo_depth, shared_port=args.shared_port)
    meta = export(a, b, p, args.out)
    e = meta["expected"]
    print(f"{args.out}: {e['cycles']} cycles, {e['instructions']} instructions, "
          f"utilization {100 * e['utilization']:.1f}%")


if __name__ == "__main__":
    main()
