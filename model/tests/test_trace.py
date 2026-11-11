# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Trace and golden-vector export: the files the RTL testbench consumes."""
import json
from collections import Counter

import numpy as np

from _helpers import gemm
from tri_queue.core import run_gemm, reference, Params, build_gemm, read_c
from tri_queue.vectors import export, read_words, main


def parse(line):
    cyc, unit, event, *kv = line.split()
    return int(cyc), unit, event, dict(x.split("=", 1) for x in kv)


def test_tracing_does_not_change_timing():
    a, b = gemm(20, 13, 30)
    _, s0 = run_gemm(a, b)
    tr = []
    _, s1 = run_gemm(a, b, trace=tr)
    assert s0.cycles == s1.cycles and tr


def test_trace_events_match_stats():
    a, b = gemm(20, 13, 30)
    tr = []
    _, s = run_gemm(a, b, trace=tr)
    ev = [parse(x) for x in tr]
    count = Counter((u, e) for _, u, e, _ in ev)
    assert count[("port", "rd")] == s.read_vectors
    assert count[("port", "wr")] == s.write_vectors
    assert count[("mac", "beat")] == s.beats
    assert count[("mac", "start")] == sum(s.mac_ops.values())
    assert count[("fe", "issue")] == s.instructions
    assert count[("mac", "drain_start")] == count[("mac", "drain_done")] == 3 * 2   # 3x2 tiles
    cycles = [c for c, *_ in ev]
    assert cycles == sorted(cycles)
    assert ev[-1][1:3] == ("fe", "halt")


def test_trace_writes_reproduce_c():
    """Replaying only the trace's write events onto the initial image must give the reference C."""
    a, b = gemm(9, 17, 11, seed=3)
    tr = []
    c, _ = run_gemm(a, b, trace=tr)
    mem, _, L = build_gemm(a, b)
    for line in tr:
        _, u, e, kv = parse(line)
        if (u, e) == ("port", "wr"):
            addr, data = int(kv["addr"]), bytes.fromhex(kv["data"])
            assert addr % 16 == 0 and len(data) <= 16      # one aligned port word per write
            mem[addr:addr + len(data)] = np.frombuffer(data, np.uint8)
    np.testing.assert_array_equal(read_c(mem, L), reference(a, b))


def test_export_files(tmp_path):
    a, b = gemm(12, 20, 17, seed=5)
    p = Params(tile=8)
    meta = export(a, b, p, tmp_path, seed=5)
    assert {f.name for f in tmp_path.iterdir()} == {
        "mem_init.hex", "mem_final.hex", "prog.hex", "trace.txt", "gnt.txt", "meta.json"
    }
    assert json.loads((tmp_path / "meta.json").read_text()) == json.loads(json.dumps(meta))
    assert "version" in meta and meta["seed"] == 5 and meta["a_dtype"] == "s8"
    assert len((tmp_path / "gnt.txt").read_text().splitlines()) == meta["expected"]["cycles"]
    final = read_words(tmp_path / "mem_final.hex", p.port_bytes)
    L = meta["layout"]
    np.testing.assert_array_equal(read_c(final, L), reference(a, b))
    init = read_words(tmp_path / "mem_init.hex", p.port_bytes)
    assert len(init) == meta["mem_bytes"] and not init[L["c_base"]:].any()
    assert len((tmp_path / "prog.hex").read_text().split()) == meta["prog_bytes"] == 50


def test_export_int8_and_cli(tmp_path, capsys):
    a, b = gemm(12, 20, 17, seed=5)
    p8 = Params(tile=8, out_bytes=1, out_shift=6)
    meta8 = export(a, b, p8, tmp_path / "int8", seed=5)
    final8 = read_words(tmp_path / "int8" / "mem_final.hex", p8.port_bytes)
    want8 = np.clip(reference(a, b) >> 6, -128, 127).astype(np.int8)
    np.testing.assert_array_equal(read_c(final8, meta8["layout"]), want8)

    main(["--m", "16", "--n", "8", "--k", "24", "--tile", "16", "--a", "u8",
          "--out-int8", "--out-shift", "5", "--panel-bytes", "512", "-o", str(tmp_path / "v")])
    assert "cycles" in capsys.readouterr().out
    vmeta = json.loads((tmp_path / "v" / "meta.json").read_text())
    assert vmeta["params"]["port_bytes"] == 32 and vmeta["params"]["out_bytes"] == 1
    assert vmeta["expected"]["panel_hits"] == 0
