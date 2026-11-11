<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Integrating the Tri-Queue core into an SoC

This is the integration contract for v1. The architecture and register map are in
[`docs/specification.md`](specification.md) §6; the verification scope is in
[`docs/verification.md`](verification.md). If something here conflicts with the RTL, the RTL wins and
this page is the bug.

## 1. What you are integrating

`tq_top` contains the register port and the matrix core. It has two native memory ports and one
32-bit register port. It does not include a bus adapter, an operand SRAM, a clock/reset controller,
or a DFT wrapper.

| Top | Use |
| --- | --- |
| `tq_top` | Native memory ports, register port exposed. Pair with your own bus bridge and tightly coupled SRAM. |
| `tq_top_axil` | `tq_top` + AXI4-Lite register slave; memory ports stay native (SRAM). |
| `tq_top_axi` | `tq_top` + AXI4-Lite registers + AXI4 memory master. Use for a bus-attached SoC. |
| `tq_top_xif` | `tq_top` + CV-X-IF register access; memory ports stay native. Reference adapter. |

The core is a coprocessor: the host writes a 50-byte program, starts it, and waits for `DONE` or the
interrupt. There is no register file, no compiler, and no operating-system interface.

## 2. Clock, reset and power

- One clock domain. `clk_i` is the core clock; `rst_ni` is asynchronous and active low. Synchronize
  reset release to `clk_i` in the SoC. In `tq_top_axi`, `tq_axi_mem` must be reset in the same
  domain as the AXI interconnect; resetting `tq_axi_mem` alone while an AXI transaction is in flight
  violates the AXI handshake rules.
- `tq_core` is idle and has no transactions in flight when `STATUS.PENDING = 0` (`STATUS[12] = 0`,
  which requires `BUSY = 0`, all read tags drained, all `WR_ACK` write responses returned, and
  `ext_pending_i = 0`). If you want to gate the core clock externally, gate only when
  `STATUS.PENDING = 0`, and split `tq_top` into `tq_regs` (always clocked) and `tq_core` (gated when
  `PENDING = 0`); gating the whole `tq_top` also blocks register access. This split is not provided in v1.
- `CLK_GATE = 1` uses `rtl/common/tq_icg.sv`. It is a simulation/generic-synthesis model with no
  test enable. For scan, replace it with the library ICG cell and connect the test enable to the
  SoC scan-mode signal. `tq_cg` and the formal pattern check must be updated together.
- The operand SRAM is outside `tq_top`. Its clock and power are your responsibility.

## 3. Register port

OBI-style: `reg_req_i`/`reg_gnt_o`, `reg_addr_i[11:0]`, `reg_we_i`, `reg_be_i[3:0]`,
`reg_wdata_i[31:0]`, `reg_rvalid_o`, `reg_rdata_o[31:0]`, `irq_o`. `reg_gnt_o` is always 1 and read
data returns one cycle after the request. The map is in [`docs/specification.md`](specification.md)
§6.4 and the header of `rtl/core/tq_regs.sv`.

Address decoding uses word index `reg_addr_i[11:2]` (`reg_addr_i[1:0]` is ignored). Reads of
write-only (`CTRL`) or unmapped offsets return `0` without side effects; writes to read-only or
unmapped offsets are ignored (`reg_be_i` is honored on `PROG` writes and require `reg_be_i[0]` on
control/IRQ writes). In `tq_axil_regs`, `BRESP`/`RRESP` is `OKAY` (`2'b00`) by default; setting
`CHECK_PROT = 1` rejects unprivileged (`AxPROT[0] == 0`) or non-secure (`AxPROT[1] == 1`) accesses
with `SLVERR` (`2'b10`) without forwarding `reg_req_o`.

Software sequence:

1. Read `ID` (`0x000`); expect `0x5451_0001`.
2. Read `HW_CONFIG` (`0x004`) to get T, FIFO depth, MAC queue depth, shared port, program-buffer
   size, panel-buffer size (`PANEL_BYTES / 64` in `[26:21]`) and `MAC_PIPE` (`[27]`). Do not
   hard-code these.
3. Write the program to `PROG` (`0x100`). Writes while `BUSY` are ignored.
4. Write `CTRL.START` (`0x008`). `START` clears `STATUS` (and pulses `flush_o` to invalidate any
   adapter read line buffer), but does **not** clear `IRQ_STATUS`; clear stale `IRQ_STATUS` bits
   (`0x014`, W1C) before unmasking `IRQ_EN` if a previous run left them set.
5. Poll `STATUS.DONE` or wait for `irq_o` (`IRQ_EN` bit 0).
6. Read `STATUS.ERROR` and `STATUS[11:8]`; read `STATUS.BUS_ERR` separately.
7. Read C only after `DONE`, `STATUS.PENDING = 0` (`STATUS[12] = 0`), and no error/bus error. With
   `WR_ACK = 1`, `DONE` also means all core writes have their responses.

`START` while `BUSY` or `PENDING` is ignored by the C driver (`TQ_ERR_BUSY`). `ABORT` clears `BUSY`
and pulses `flush_o`, but does not set `DONE`. After an error or `ABORT`, poll `STATUS.PENDING = 0`
(`STATUS[12] = 0`) so all in-flight read tags, `WR_ACK` write responses, and adapter transactions
have drained before starting a new run or reusing A/B/C memory. Performance counters (`PERF_*`,
`0x020`–`0x030`) are 32-bit wrapping counters cleared at `START`.

### Interrupts

`irq_o = |(IRQ_STATUS & IRQ_EN)`. `IRQ_STATUS` is write-1-to-clear (hardware set wins over a
same-cycle W1C on the same bit). Bits: 0 done, 1 error, 2 bus error (when `STATUS.ERROR` is set at
the end of a run, both bit 1 and bit 0 latch). A typical handler reads `STATUS`, clears
`IRQ_STATUS`, and wakes the waiting thread.

### Capability discovery

`HW_CONFIG` reports `T`, `FIFO_DEPTH`, `MACQ_DEPTH`, `SHARED_PORT`, `PROG_BYTES`, `PANEL_BYTES / 64`
and `MAC_PIPE`. Int8 output (`MODE[2]`, with shift `MODE[7:3]`) is always present and is selected per
run in the program's `MODE` register. `PERF`, `ACC_FORM` and `WR_ACK` remain build-time synthesis
choices not reported in `HW_CONFIG`. Record the exact git tag/commit and synthesis parameter set in
your SoC ROM or device tree so system software can identify the build profile.

## 4. Memory ports

Both ports are W = 2T bytes wide (16 bytes at T = 8). They are OBI-style: a request is accepted when
`req && gnt`, an ungranted request stays asserted unchanged (except when `CTRL.ABORT` or a fatal
program error `STATUS.ERROR = 1` terminates the run, which drops an ungranted request on the next
clock edge), and responses return in order.

| Port | Required behavior |
| --- | --- |
| Read | Return `rd_rvalid_i`/`rd_rdata_i` in order, any number of cycles later (including the request cycle). The core never back-pressures `rvalid`; it only requests when the destination FIFO has room. |
| Write | Accept `wr_req_o`/`wr_addr_o`/`wr_be_o`/`wr_wdata_o` when `gnt`. With `WR_ACK = 1`, return one `wr_done_i` per accepted write, in order. Byte enables must be honored. |

`SHARED_PORT = 1` makes the core never request both ports in the same cycle, so a single-port SRAM
can serve both. The cost is drain writes taking read cycles; 64³ drops from 99.2% to 79.8% in the
model (88.7% with int8 output).

### Address and layout rules

- `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` must be multiples of W. The core stops with `ERR_ALIGN`
  otherwise.
- C rows are padded to whole W-byte words. The host-side helpers are `tq_c_ld(n, W)` for int32 and
  `tq_c_ld_int8(n, W)` for int8.
- int32 C tile `(ti, tj)` starts at `C_BASE + T*ti*C_LD + 4*T*tj`. int8 starts at
  `C_BASE + T*ti*C_LD + T*tj`.
- In int8 mode a write is one W-byte word carrying one T-byte tile row in the low or high half. Odd
  tile columns use the high half. The SRAM or bus bridge must honor `wr_be_o` starting at byte 0 or
  byte T; it cannot assume strobes always start at the low byte.
- A, B and C must not overlap. The core assumes the host does not write them while `BUSY`.

### Cache coherency and memory ordering

The core is **not** coherent with a host data cache. Before `START`, flush the packed A/B/workspace
from the host cache to the memory the core sees (or use uncached/TCM memory). After `DONE`, invalidate
the C region before the host reads it. The C driver (`sw/tq.h`, `sw/tq.c`) provides two mechanisms:

- **Memory barriers (`TQ_WMB()` / `TQ_RMB()`):** `tq_run()` issues `TQ_WMB()` after loading `PROG`
  and before writing `CTRL.START`, and `TQ_RMB()` after observing `STATUS.DONE`. On RISC-V these
  default to `fence ow, ow` and `fence ir, ir`; override them at compile time if your platform needs
  specific bus fences.
- **Cache maintenance hooks (`tq_set_cache_ops(clean_fn, inv_fn)`):** register platform callbacks once
  at init; `tq_gemm_prepare()` then cleans the packed A/B buffers before `tq_run()`, and
  `tq_gemm_finish()` invalidates the packed C buffer after `tq_run()` completes.

On Chipyard, the tested path uses the AXI periphery/front bus; check the cacheability attributes of
your address map.

### Latency and FIFO sizing

The operand FIFOs hide SRAM read latency, not cache misses. A FIFO of depth `FIFO_DEPTH` hides up to
`L = FIFO_DEPTH - 1` cycles of SRAM read latency (`FIFO_DEPTH >= L + 1`, where model `mem_latency =
L + 1` includes the 1-cycle operand FIFO write); depth 4 hides up to `L = 3`, depth 8 hides up to
`L = 7`, and depth 16 hides up to `L = 15`. Choose `FIFO_DEPTH` from the memory's read latency plus
the adapter's latency. The 64³ GEMM on the Chipyard Rocket SoC passes bit-exact but runs at 10.6%
because the 8-word FIFOs do not hide the TileLink/L2/DRAM latency; that is a memory-system result,
not a core limit. The committed run predates the driver's GEMM API and the v1 RTL, so treat the
cycle count as indicative until it is re-run (`docs/benchmarks.md` §6).

## 5. AXI4 master (`tq_top_axi`)

The adapter is an AXI4 master with a W-byte data bus:

- Reads: one aligned `BURST_BEATS`-beat INCR burst per line when `BURST_BEATS > 1`, or one single-beat
  INCR per port word when `BURST_BEATS = 1` (default). Bursts are aligned to their own size, so
  `BURST_BEATS × W ≤ 4 KiB` keeps a burst inside the AXI 4 KiB boundary. `tq_axi_mem` checks that
  `BURST_BEATS` is a power of two, that `BURST_BEATS × W ≤ 4 KiB`, `ID_W ≥ 1` and `MAX_OUT ≥ 1` at
  elaboration, and refuses to build otherwise.
- **Burst over-read (`BURST_BEATS > 1`):** because bursts are aligned to `BURST_BEATS × W` bytes and
  always fetch a full line, the last burst of an A or B panel can read up to
  `(BURST_BEATS - 1) × W` bytes past the end of the packed operand buffer (or before its start if a
  panel is not line-aligned). Ensure at least `(BURST_BEATS - 1) × W` bytes before and after the
  operand buffers are mapped to normal readable memory (never a read-sensitive MMIO peripheral or an
  unmapped decode hole that returns `DECERR`/`SLVERR`).
- Writes: one single-beat INCR per drain write, with `WSTRB = wr_be_o`.
- `RREADY` and `BREADY` are always 1; the core never back-pressures a response.
- `ARREADY`, `AWREADY`/`WREADY` act as the core's `gnt`.

**Signals.** The adapter drives `ARID`/`AWID` = 0, `ARPROT`/`AWPROT`, `ARCACHE`/`AWCACHE`, `ARQOS`/`AWQOS`
and `ARREGION`/`AWREGION` (region = 0), and accepts `RID`/`BID` (ignored). `AxUSER` is not exposed; tie
it off in a wrapper if your fabric requires it. Set `ID_W`, `AXPROT`, `AXCACHE` and `AXQOS` for your
address map.

**Errors, ordering and reset.** An `RRESP`/`BRESP` error on a live core transaction sets
`STATUS.BUS_ERR` and the run continues (orphan responses from an aborted run are drained without
setting `BUS_ERR`). C must then be treated as invalid. The read line buffer (`BURST_BEATS > 1`) is
automatically invalidated on `START` and `ABORT` (via `tq_top.flush_o`) and whenever the core issues
a write to the same line, so back-to-back runs reusing the same buffer addresses never hit stale
data; however, it is not coherent with concurrent writes from another master during a run. Reset
`tq_axi_mem` in the same reset domain as the AXI interconnect.

## 6. Parameter profiles and adapter parameters

These are starting points, not measured v1 results. The reference PPA is the `t8_cpa` profile with
`FIFO_DEPTH = 4`, `MACQ_DEPTH = 3`, `CLK_GATE = 1`, `ACC_FORM = ACC_CPA`, `PANEL_BYTES = 0`.

| Profile | Target | Suggested parameters | Notes |
| --- | --- | --- | --- |
| MCU / tightly coupled SRAM | smallest area, low latency | `T=8`, `FIFO_DEPTH=4`, `MACQ_DEPTH=3`, `SHARED_PORT=1` if the SRAM has one port, `PANEL_BYTES=0`, `PERF=0` if counters are not needed | Reference PPA is close to this; shared port costs drain cycles. |
| SoC / AXI, latency-tolerant | hide bus latency | `T=8`, `FIFO_DEPTH=16`, `MACQ_DEPTH=4`, `BURST_BEATS=4` or `8`, `PANEL_BYTES=0`, `MAX_OUT=16` | No measured PPA yet. Measure the memory system before quoting a utilization. |
| Throughput / area | fewer bytes per MAC | `T=16` with a 256-bit port, `MAC_PIPE=2`, `ACC_FORM=ACC_CPA` | T=16 closes at 762 MHz on ASAP7 (741 MHz run), not 1 GHz; mobile/narrow shapes lose utilization. |
| Panel-buffer experiment | GEMV / A-reuse | add `PANEL_BYTES=1024` or `4096` | Experimental: flops today, no PPA; verify your SRAM macro plan first. |

For every profile, regenerate the model results and record the PPA with `flow/ppa.py`; do not quote a
profile's energy or area from a different configuration.

**Adapter top parameters** (in addition to the core parameters `T`, `FIFO_DEPTH`, `MACQ_DEPTH`,
`SHARED_PORT`, `CLK_GATE`, `PERF`, `MAC_PIPE`, `ACC_FORM`, `PROG_BYTES`, `PANEL_BYTES`, `WR_ACK`,
`WR_OUT_MAX`):

| Top | Adapter-specific parameters | Notes |
| --- | --- | --- |
| `tq_top_axil` | `CHECK_PROT = 0` | Set `CHECK_PROT = 1` to reject unprivileged/non-secure AXI4-Lite accesses with `SLVERR`. |
| `tq_top_axi` | `BURST_BEATS = 1`, `MAX_OUT = 8`, `ID_W = 4`, `AXPROT = 3'b000`, `AXCACHE = 4'b0011`, `AXQOS = 4'b0000`, `CHECK_PROT = 0` | Forces `WR_ACK = 1` and `WR_OUT_MAX = MAX_OUT` on `tq_top`. |
| `tq_top_xif` | `X_ID_W = 4`, `CUSTOM_OPC = 7'b0001011` (`custom-0`) | Implements the OpenHW `core-v-xif` issue/commit/result subset (`core-v-xif` commit `8a46f9a`, pre-v1.0 draft). For CV32E40X or CVA6, map the flat ports (`x_issue_*`, `x_commit_*`, `x_result_*`) to `cv32e40x_if_xif` in a thin SV interface shim and tie `x_compressed_ready_o = 0`, `x_mem_valid_o = 0`. |

## 7. Verification and bring-up

- Run `make rtl-test` for the shipped parameters; add your configuration to `verif/test_rtl.py`.
- Run the C driver's `tq_probe` and a small GEMM before a full workload.
- Check `STATUS` for `ERROR`, `BUS_ERR` and the error code after every run.
- Compare `PERF_READS`, `PERF_WRITES`, `PERF_BEATS` and `PERF_CYCLES` against the cycle model; they
  are the cheapest bring-up signal.
- If you add a bus adapter or change the ICG, add a formal or simulation test for it; the existing
  proofs cover the shipped adapters, not your wrapper.

## 8. Known limitations

- int8 output is shift-only: no per-tensor/per-channel scale, bias or activation.
- The panel buffer (`PANEL_BYTES > 0`) is experimental and uses flip-flops (`PANEL_BYTES ≤ 2048` by
  default unless `ALLOW_LARGE_FLOP_PANEL = 1`).
- `rtl/common/tq_icg.sv` is a simulation/generic-synthesis model with no DFT scan-enable (`SE`/`TE`)
  pin; replace it with the foundry's technology ICG cell and wire `TE` for scan insertion (§2).
- `AxUSER` is not exposed on `tq_top_axi`; tie it off in a wrapper if required.
- Cached-DRAM performance is not a v1 use case; the Chipyard result is 10.6% (measured on pre-v1 RTL
  `65815f3`).
- T=16 closes at 762 MHz on ASAP7 (741 MHz run); T=32 has no PPA record.
- CV-X-IF (`tq_xif_regs`, `core-v-xif` `8a46f9a` subset) has not been validated on a real
  CVA6/CV32E40X pipeline.
- APB and RoCC adapters are not included.
- There is no compiler; the host packs operands and writes the program.
