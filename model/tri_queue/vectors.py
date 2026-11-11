# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Golden test vectors for RTL: memory images, program, expected result, and the event trace.

    python3 -m tri_queue.vectors --m 16 --n 16 --k 16 -o vectors/16x16x16
    python3 -m tri_queue.vectors --m 20 --n 9 --k 33 --tile 16 --a u8 -o vectors/edge_t16

File formats are specified in docs/golden-vectors.md.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from . import __version__
from .core import GrantSource, Params, TriQueueCore, build_gemm, reference, read_c

DTYPES = {"s8": np.int8, "u8": np.uint8}


def write_words(path: Path, mem: np.ndarray, width: int):
    """$readmemh image: one `width`-byte word per line, little-endian (lowest address = LSB)."""
    if len(mem) % width != 0:
        raise ValueError(f"memory length {len(mem)} is not a multiple of port width {width}")
    words = mem.reshape(-1, width)[:, ::-1]
    path.write_text("".join(w.tobytes().hex() + "\n" for w in words))


def read_words(path: Path, width: int) -> np.ndarray:
    rows = [bytes.fromhex(line.strip())[::-1] for line in path.read_text().split()]
    if not all(len(r) == width for r in rows):
        raise ValueError(f"corrupt word width in {path}")
    return np.frombuffer(b"".join(rows), np.uint8).copy()


def export(a: np.ndarray, b: np.ndarray, params: Params, out: Path, seed: int | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    mem, prog, L = build_gemm(a, b, params)
    write_words(out / "mem_init.hex", mem, int(params.port_bytes))
    (out / "prog.hex").write_text("".join(f"{x:02x}\n" for x in prog))
    trace = []
    core = TriQueueCore(mem, prog, params, trace)
    s = core.run()
    s.useful_macs = L["m"] * L["n"] * L["k"]
    ref = reference(a, b)
    if params.out_bytes == 1:
        ref = np.clip(ref >> params.out_shift, -128, 127).astype(np.int8)
    if not np.array_equal(read_c(mem, L), ref):
        raise ValueError("model disagrees with numpy")
    write_words(out / "mem_final.hex", mem, int(params.port_bytes))
    (out / "trace.txt").write_text("\n".join(trace) + "\n")
    gnt_src = GrantSource(params.gnt_stall, params.gnt_seed)
    gnt_lines = []
    for _ in range(s.cycles):
        rg, wg = gnt_src.next()
        gnt_lines.append(f"{int(rg)} {int(wg)}\n")
    (out / "gnt.txt").write_text("".join(gnt_lines))
    meta = {
        "format": 1,
        "version": __version__,
        "a_dtype": "s8" if a.dtype == np.int8 else "u8",
        "b_dtype": "s8" if b.dtype == np.int8 else "u8",
        "seed": seed,
        "params": {k: (int(v) if isinstance(v, int) and not isinstance(v, bool) else v)
                   for k, v in vars(params).items() if not k.startswith("_")},
        "layout": L,
        "mem_bytes": len(mem),
        "prog_bytes": len(prog),
        "expected": {
            "cycles": s.cycles, "instructions": s.instructions, "beats": s.beats,
            "read_vectors": s.read_vectors, "read_bytes": s.read_bytes,
            "write_vectors": s.write_vectors, "write_bytes": s.write_bytes,
            "panel_hits": s.panel_hits, "panel_vectors": s.panel_vectors,
            "port_stall": dict(s.port_stall), "error": s.error,
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
    ap.add_argument("--mac-pipe", type=int, default=2, choices=(1, 2))
    ap.add_argument("--shared-port", action="store_true")
    ap.add_argument("--panel-bytes", type=int, default=0)
    ap.add_argument("--out-int8", action="store_true", help="requantize C to int8")
    ap.add_argument("--out-shift", type=int, default=8)
    ap.add_argument("--wr-ack", action="store_true")
    ap.add_argument("--wr-resp-latency", type=int, default=0)
    ap.add_argument("--wr-out-max", type=int, default=15)
    ap.add_argument("--gnt-stall", type=float, default=0.0, help="probability a port's gnt is withheld")
    ap.add_argument("--gnt-seed", type=int, default=0)
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(args.seed)

    def rand(shape, dt):
        lo, hi = (-128, 128) if dt == "s8" else (0, 256)
        return rng.integers(lo, hi, shape).astype(DTYPES[dt])

    a, b = rand((args.m, args.k), args.a), rand((args.k, args.n), args.b)
    p = Params(tile=args.tile, port_bytes=args.port_bytes, mem_latency=args.mem_latency,
               fifo_depth=args.fifo_depth, mac_pipe=args.mac_pipe, shared_port=args.shared_port,
               panel_bytes=args.panel_bytes, out_bytes=1 if args.out_int8 else 4,
               out_shift=args.out_shift, wr_ack=args.wr_ack,
               wr_resp_latency=args.wr_resp_latency, wr_out_max=args.wr_out_max,
               gnt_stall=args.gnt_stall, gnt_seed=args.gnt_seed)
    meta = export(a, b, p, args.out, seed=args.seed)
    e = meta["expected"]
    print(f"{args.out}: {e['cycles']} cycles, {e['instructions']} instructions, "
          f"utilization {100 * e['utilization']:.1f}%")


if __name__ == "__main__":
    main()
