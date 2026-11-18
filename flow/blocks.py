# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Functional blocks of tq_top, shared by the toggle census (flow/census.py) and the per-block
power split (flow/power.py).

A name is an RTL hierarchical path (`u_core.u_array.g_pipe2.g_preg[0].pq[5]`): a net from the
Verilator SAIF, or a register or clock-gate cell of the flattened netlist, whose names keep the RTL
path. Combinational cells lose their names in synthesis (ABC maps them to `_1234_`); power.py
assigns each to the block of the nearest register or clock gate it drives (`attribute`). The cone
of the product register is then the operand select and the multipliers, the cone of the
accumulator banks the accumulate adders, and the cone of the drain's staging register the drain
read mux and requantization.
"""
import re
from collections import deque

# (block, description), in report order
BLOCKS = [
    ("array_mult", "operand select + multipliers (comb), product register (seq)"),
    ("array_acc", "accumulate adders (comb), accumulator banks (seq)"),
    ("array_ctl", "MAC array control"),
    ("drain", "array read mux, requantization, staging register"),
    ("stream", "stream engine, address generation, port arbitration"),
    ("operand_fifos", "QA / QB"),
    ("cmd_queues", "MAC and stream command queues"),
    ("frontend", "decoder, loop stack, tile walker, CFG registers"),
    ("regs", "register port, program buffer"),
    ("core_ctl", "MAC unit control, beat-1 operands, run control, perf counters"),
    ("clock_tree", "clock buffers (CTS)"),
    ("io", "port buffers and logic driving only outputs"),
]
BLOCK_NAMES = [b for b, _ in BLOCKS]

_RULES = [
    (r"^u_core\.u_array\.(g_qi|g_pipe2|g_pipe1|prod_)", "array_mult"),
    (r"^u_core\.u_array\.(g_i_acc|g_bank_cg|sum[01]|car[01])", "array_acc"),
    (r"^u_core\.u_array\.(rs|rc|rd_val|base|g_drain)", "drain"),
    (r"^u_core\.u_array\.", "array_ctl"),
    (r"^u_core\.u_drain\.", "drain"),
    (r"^u_core\.u_stream\.(u_scmd_a|u_scmd_b)\.", "cmd_queues"),
    (r"^u_core\.u_stream\.", "stream"),
    (r"^u_core\.u_q[ab]\.", "operand_fifos"),
    (r"^u_core\.u_macq\.", "cmd_queues"),
    (r"^u_core\.u_fe\.", "frontend"),
    (r"^u_regs\.", "regs"),
    (r"^u_core\.", "core_ctl"),
]
_RULES = [(re.compile(p), b) for p, b in _RULES]
_CLOCK = re.compile(r"^(clkbuf|clkload|cts|delaybuf)|_clk|clknet", re.I)


def unescape(name: str) -> str:
    """Netlist path to RTL path: drop Verilog/STA escapes and the Yosys cell suffix."""
    name = name.replace("\\", "")
    return re.sub(r"\$_[A-Za-z0-9_]+$", "", name)


def block_of(name: str):
    """Block of an RTL-named net or cell, or None if the name carries no RTL path."""
    name = unescape(name)
    for pat, block in _RULES:
        if pat.search(name):
            return block
    return None


def attribute(cells: dict, loads: dict) -> dict:
    """Block of every cell.

    cells: name -> kind ("reg", "icg" or "comb"); loads: name -> cells driven by its outputs.
    Registers and clock gates are classified by name. A combinational cell takes the block of the
    nearest register or clock gate in its fanout (breadth-first from all of them at once, against
    the signal direction, so the closest endpoint wins). Clock buffers are their own block, and
    logic that reaches no register (output port buffers) is `io`.
    """
    out = {}
    drivers = {}
    for c, ls in loads.items():
        for l in ls:
            drivers.setdefault(l, []).append(c)
    queue = deque()
    for c, kind in cells.items():
        if kind in ("reg", "icg"):
            out[c] = block_of(c) or "core_ctl"
            queue.append(c)
        elif _CLOCK.search(c):
            out[c] = "clock_tree"
    while queue:
        c = queue.popleft()
        for d in drivers.get(c, ()):
            if d not in out and cells.get(d) == "comb":
                out[d] = out[c]
                queue.append(d)
    for c in cells:
        out.setdefault(c, "io")
    return out
