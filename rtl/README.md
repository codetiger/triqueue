# Tri-Queue RTL

Synthesizable SystemVerilog for the Tri-Queue matrix core (spec: `docs/specification.md`). RTL v0: int8/uint8 operands, int32 results, T×T tile.

`rtl/files.f` lists the sources in compile order. The top level is `tq_top`.

## Parameters (`tq_top`)

| Parameter | Default | Meaning |
| --- | --- | --- |
| `T` | 8 | Tile size: T×T outputs, T² 9×9 multipliers. Power of two, ≥ 4. Memory ports are W = 2T bytes wide. |
| `FIFO_DEPTH` | 8 | QA/QB depth in W-byte words. Hides up to this many cycles of SRAM latency (`model/dse.md` §5). |
| `MACQ_DEPTH` | 4 | MAC command queue depth (spec §5.2) |
| `SCMD_DEPTH` | 2 | Stream command queue depth |
| `SHARED_PORT` | 0 | 1: drain writes share the read port (single-port SRAM); the core never requests both in one cycle |
| `PROG_BYTES` | 64 | Program buffer size (8 `CFG` + 10-byte kernel = 50 bytes) |

## Interfaces

**Register port** (spec §6.4): 32-bit, OBI-style. `reg_gnt_o` is always 1, and read data arrives one cycle after the request (`reg_rvalid_o`). The register map is in the header of `core/tq_regs.sv`. A run is: write the program to `PROG` (0x100), write `CTRL.START`, wait for `STATUS.DONE` or `irq_o`.

**Memory ports** (spec §6.3): OBI-style, W bytes wide.

| Port | Signals | Rules |
| --- | --- | --- |
| Read | `rd_req_o`, `rd_addr_o` → `rd_gnt_i`; `rd_rvalid_i`, `rd_rdata_i` | A request is accepted in a cycle where `req && gnt`. Responses come back in order, any number of cycles later (including the same cycle). `rvalid` has no backpressure: the core only requests when it has room for the response. |
| Write | `wr_req_o`, `wr_addr_o`, `wr_be_o`, `wr_wdata_o` → `wr_gnt_i` | Accepted when `req && gnt`. Byte enables are contiguous from the low byte. |

Requests never depend combinationally on `gnt`. All addresses are W-aligned: the program must keep `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` multiples of W, otherwise the core stops with `ERR_ALIGN`.

**Timing against the model:** with an SRAM that always grants and answers L cycles after the request, the core matches the cycle model run with `mem_latency = L + 1`, cycle for cycle (`verif/`).

## Blocks

| File | Block |
| --- | --- |
| `core/tq_pkg.sv` | Opcodes, config register indices, error codes, internal command formats |
| `common/tq_fifo.sv` | Ring-buffer FIFO, 1 push / up to 2 pops per cycle. Formally proved (`verif/formal`). |
| `core/tq_frontend.sv` | Decoder, 2-level zero-overhead loop stack, tile walker, stream command counters. No multipliers: tile addresses are running offsets. |
| `core/tq_stream.sv` | A/B panel walkers, round-robin read arbitration with credits, response tags, shared-port arbitration |
| `core/tq_mac_array.sv` | Per-quadrant operand select, T² 9×9 multipliers, product register, carry-save accumulator cells (2 banks), drain read port with T/2 CPA lanes |
| `core/tq_drain.sv` | Drain sequencer: one CPA stage, staging register, aligned writes with byte enables, bank clear |
| `core/tq_core.sv` | MAC command control (at most one `ZERO_C`/`SWAP_C` plus one `VOUTER_MAC` per cycle), halt detection, errors, performance counters |
| `core/tq_regs.sv` | Register port and program buffer |
| `core/tq_top.sv` | Registers + core |

## Coding rules

The RTL must pass `verilator --lint-only -Wall` and Yosys `read_verilog -sv` with no warnings (`make rtl-lint`). That rules out several constructs in Yosys:

- Refer to package items with their full name (`tq_pkg::OP_CFG`). Don't use `import`.
- No `return`: assign to the function name instead.
- No `$bits()` of a type. Use the width constants in `tq_pkg`.
- No `break` or `continue`.
