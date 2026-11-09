# Tri-Queue RTL

Synthesizable SystemVerilog for the Tri-Queue matrix core (spec: `docs/specification.md`). RTL v0: int8/uint8 operands, int32 or saturating-shift int8 results, T×T tile.

`rtl/files.f` lists the sources in compile order. The top level is `tq_top`.

## Parameters (`tq_top`)

| Parameter | Default | Meaning |
| --- | --- | --- |
| `T` | 8 | Tile size: T×T outputs, T² 9×9 multipliers. Power of two, ≥ 4 (checked at elaboration). Memory ports are W = 2T bytes wide. |
| `FIFO_DEPTH` | 8 | QA/QB depth in W-byte words. Hides up to this many cycles of SRAM latency (`model/dse.md` §5). |
| `MACQ_DEPTH` | 4 | MAC command queue depth (spec §5.2) |
| `SCMD_DEPTH` | 2 | Stream command queue depth |
| `SHARED_PORT` | 0 | 1: drain writes share the read port (single-port SRAM); the core never requests both in one cycle |
| `PROG_BYTES` | 64 | Program buffer size (8 `CFG` + 10-byte kernel = 50 bytes) |
| `WR_ACK` | 0 | 1: a write is complete at its `wr_done_i` pulse, not at its grant (AXI B). FENCE waits for every write response. 0: `wr_done_i` is ignored (tie it to 0 for an SRAM). |
| `PANEL_BYTES` | 0 | A panel buffer (spec §10.9): an A panel of at most this many bytes is kept as it is read and replayed for the tile row's later tiles without the memory port. A multiple of 2T; 0 = none. Flops for now (a macro later); same results, fewer reads, and cycles change only where the port was the limit. |
| `MAC_PIPE` | 2 | 2: product register between multiply and accumulate. 1: none (the 64 × 17-bit register at T = 8 goes); multiply and accumulate in one cycle, a longer critical path. Model `mac_pipe`. |
| `PERF` | 1 | 1: five 32-bit performance counters, clock-gated while idle. 0: counters omitted and tied to 0 for area-constrained MCUs. |
| `WR_OUT_MAX` | 15 | With `WR_ACK`: most writes granted and not yet complete; the core holds back new writes at the limit. |
| `CLK_GATE` | 1 | 1: clock gates (`tq_cg`) on the wide register groups: accumulator banks (one gate per bank and quadrant, open only for a clear or an accumulate that reaches the quadrant), product register, beat-1 operands, every queue entry at least 16 bits wide, drain staging register and each program-buffer byte (102 gates at T = 8). 0: plain enable flops. Same results and cycle timing. |
| `ACC_FORM` | `tq_pkg::ACC_CPA` | Accumulator cell (spec §10.2): `ACC_CPA` one 32-bit value with a carry-propagate adder in the loop (the default: smaller and lower energy after place and route), or `ACC_CSA` carry-save (sum, carry) with CPA lanes at the drain (twice the accumulator state). Same results and cycle timing. |

## Interfaces

**Register port** (spec §6.4): 32-bit, OBI-style. `reg_gnt_o` is always 1, and read data arrives one cycle after the request (`reg_rvalid_o`). The register map is in the header of `core/tq_regs.sv`. A run is: write the program to `PROG` (0x100), write `CTRL.START`, wait for `STATUS.DONE` or `irq_o`.

**Memory ports** (spec §6.3): OBI-style, W bytes wide.

| Port | Signals | Rules |
| --- | --- | --- |
| Read | `rd_req_o`, `rd_addr_o` → `rd_gnt_i`; `rd_rvalid_i`, `rd_rdata_i` | A request is accepted in a cycle where `req && gnt`. Responses come back in order, any number of cycles later (including the same cycle). `rvalid` has no backpressure: the core only requests when it has room for the response. |
| Write | `wr_req_o`, `wr_addr_o`, `wr_be_o`, `wr_wdata_o` → `wr_gnt_i`; `wr_done_i` | Accepted when `req && gnt`. Byte enables are contiguous from the low byte. `wr_done_i`: one pulse per completed write when `WR_ACK = 1`, else tie to 0. `bus_err_i`: a pulse per error response from an interconnect (sets `STATUS.BUS_ERR`), else tie to 0. |

Requests never depend combinationally on `gnt`, and an ungranted request stays asserted with the same address and data until granted (OBI request stability; START or ABORT drops it). All addresses are W-aligned: the program must keep `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` multiples of W, otherwise the core stops with `ERR_ALIGN`.

**Timing against the model:** with an SRAM that always grants and answers L cycles after the request, the core matches the cycle model run with `mem_latency = L + 1`, cycle for cycle (`verif/`).

## Blocks

| File | Block |
| --- | --- |
| `core/tq_pkg.sv` | Opcodes, config register indices, error codes, internal command formats |
| `common/tq_icg.sv` | Clock-gating cell: latch + AND model for simulation and generic synthesis; `OPENROAD_CLKGATE` (the PDK cell) in the ORFS flow. An SoC flow replaces this file with its own library ICG cell (connecting scan/test enable `TE`/`SE` to the SoC scan-mode signal for DFT). |
| `common/tq_cg.sv` | Clock gate for one register group (`tq_icg`, or the clock itself when `EN = 0`) |
| `common/tq_fifo.sv` | Ring-buffer FIFO, 1 push / up to 2 pops per cycle, optional per-entry clock gates. Formally proved (`verif/formal`). |
| `core/tq_frontend.sv` | Decoder, 2-level zero-overhead loop stack, tile walker, stream command counters. No multipliers: tile addresses are running offsets. |
| `core/tq_stream.sv` | A/B panel walkers, round-robin read arbitration with credits, response tags, shared-port arbitration, the optional A panel buffer |
| `core/tq_mac_array.sv` | Per-quadrant operand select, T² 9×9 multipliers, product register, accumulator cells (2 banks; 32-bit adders, or carry-save with T/2 CPA lanes on the drain read port) |
| `core/tq_drain.sv` | Drain sequencer: one CPA stage, staging register, aligned writes with byte enables, bank clear |
| `core/tq_core.sv` | MAC command control (at most one `ZERO_C`/`SWAP_C` plus one `VOUTER_MAC` per cycle), halt detection, errors, performance counters |
| `core/tq_regs.sv` | Register port and program buffer |
| `core/tq_top.sv` | Registers + core |
| `adapters/tq_axil_regs.sv` | AXI4-Lite slave to the register port: one transaction at a time, reads and writes alternate when both wait, B / R after the register port's response. Formally proved (`verif/formal/tq_axil_regs.sby`). |
| `adapters/tq_axi_mem.sv` | AXI4 master for the memory ports: single-beat INCR per port word (`BURST_BEATS = 1`, default) or a 2-stream read line buffer issuing aligned `BURST_BEATS`-beat INCR bursts (`BURST_BEATS > 1`), RREADY / BREADY always 1, `wr_done` from B. Finishes and discards a request the core drops at START / ABORT. An error response to a core transaction pulses `bus_err_o` (`STATUS.BUS_ERR`). Formally proved (`verif/formal/tq_axi_mem.sby`). |
| `adapters/tq_top_axi.sv` | `tq_top` for an AXI SoC: AXI4-Lite registers + AXI4 memory master, `WR_ACK = 1`. Regression configuration `t8_axi`. |
| `adapters/tq_top_axil.sv` | `tq_top` with the AXI4-Lite register port; memory ports native (for tightly coupled SRAM). Regression configuration `t8_axil`. |
| `adapters/tq_xif_regs.sv` | CV-X-IF coprocessor adapter to the register port: `tq.wr rs1, rs2` (register[rs1] = rs2) and `tq.rd rd, rs1` (rd = register[rs1]), R-type, custom-0, funct7 0, funct3 0 / 1 (`OPCODE`, `FUNCT7` parameters). One instruction at a time; the register access happens only after commit, so killed instructions have no effect; one result per committed instruction, after the register port's response. Issue / register / commit / result fields flattened (connect the core's x-interface structs). Formally proved (`verif/formal/tq_xif_regs.sby`). |
| `adapters/tq_top_xif.sv` | `tq_top` with the CV-X-IF register access; memory ports native. Regression configuration `t8_xif` (operand and commit transactions in random order and delay, a killed speculative copy and a foreign instruction before every write). |

## Coding rules

The RTL must pass `verilator --lint-only -Wall` and Yosys `read_verilog -sv` with no warnings (`make rtl-lint`). That rules out several constructs in Yosys:

- Refer to package items with their full name (`tq_pkg::OP_CFG`). Don't use `import`.
- No `return`: assign to the function name instead.
- No `$bits()` of a type. Use the width constants in `tq_pkg`.
- No `break` or `continue`.

Clock-gated registers follow one pattern, so that a gate can only remove clock edges where the register would not change:

```systemverilog
tq_cg #(.EN(GATE)) u_cg (.clk_i, .en_i(we), .gclk_o(gclk));
always_ff @(posedge gclk) if (we || GATE) q <= d;
```

With `GATE = 0` the clock passes through and `we` is an ordinary enable; that is the form the core proofs check (`CLK_GATE = 0`). `verif/formal/tq_cg.sby` proves the template itself (with the `tq_icg` latch model, multiclock) equal to an enable flop, with and without an asynchronous reset, and that the gated clock never rises without the clock; `verif/formal/check_cg_pattern.py` checks that every gated register uses the template. The regression runs both forms (`t8_no_clock_gating`), and fails if a gate is stuck open. Generic synthesis (`make synth-check`) keeps `tq_icg` as its own module and allows latches only there.
