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
  reset release to `clk_i` in the SoC.
- `tq_core` is idle when `STATUS.BUSY = 0`. If you want to gate its clock, split `tq_top` into
  `tq_regs` (always clocked) and `tq_core` (gated when idle); gating the whole `tq_top` also blocks
  register access. This split is not provided in v1.
- `CLK_GATE = 1` uses `rtl/common/tq_icg.sv`. It is a simulation/generic-synthesis model with no
  test enable. For scan, replace it with the library ICG cell and connect the test enable to the
  SoC scan-mode signal. `tq_cg` and the formal pattern check must be updated together.
- The operand SRAM is outside `tq_top`. Its clock and power are your responsibility.

## 3. Register port

OBI-style: `reg_req_i`/`reg_gnt_o`, `reg_addr_i[11:0]`, `reg_we_i`, `reg_be_i[3:0]`,
`reg_wdata_i[31:0]`, `reg_rvalid_o`, `reg_rdata_o[31:0]`, `irq_o`. `reg_gnt_o` is always 1 and read
data returns one cycle after the request. The map is in [`docs/specification.md`](specification.md)
§6.4 and the header of `rtl/core/tq_regs.sv`.

Software sequence:

1. Read `ID` (`0x000`); expect `0x5451_0001`.
2. Read `HW_CONFIG` (`0x004`) to get T, FIFO depth, MAC queue depth, shared port and program-buffer
   size. Do not hard-code these.
3. Write the program to `PROG` (`0x100`). Writes while `BUSY` are ignored.
4. Write `CTRL.START` (`0x008`).
5. Poll `STATUS.DONE` or wait for `irq_o` (`IRQ_EN` bit 0).
6. Read `STATUS.ERROR` and `STATUS[11:8]`; read `STATUS.BUS_ERR` separately.
7. Read C only after `DONE` and no error/bus error. With `WR_ACK = 1`, `DONE` also means all writes
   have their responses.

`START` while `BUSY` is ignored. `ABORT` clears `BUSY` but does not set `DONE`; poll `BUSY` after an
abort. After an error or abort, wait for `BUSY = 0` and for outstanding writes to complete before
reusing C.

### Interrupts

`irq_o = |(IRQ_STATUS & IRQ_EN)`. `IRQ_STATUS` is write-1-to-clear. Bits: 0 done, 1 error, 2 bus
error. A typical handler reads `STATUS`, clears `IRQ_STATUS`, and wakes the waiting thread.

### Capability discovery

`HW_CONFIG` does not report every build option. int8 output, `PERF`, `MAC_PIPE`, `ACC_FORM`,
`PANEL_BYTES` and `WR_ACK` are build-time choices. If your software must run on several builds,
either fix the profile in the SoC integration or add a capability register in a future revision.

## 4. Memory ports

Both ports are W = 2T bytes wide (16 bytes at T = 8). They are OBI-style: a request is accepted when
`req && gnt`, an ungranted request stays asserted unchanged, and responses return in order.

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

### Cache coherency

The core is **not** coherent with a host data cache. Before `START`, flush the packed A/B/workspace
from the host cache to the memory the core sees (or use uncached/TCM memory). After `DONE`, invalidate
the C region before the host reads it. On Chipyard, the tested path uses the AXI periphery/front bus;
check the cacheability attributes of your address map.

### Latency and FIFO sizing

The operand FIFOs hide SRAM read latency, not cache misses. A FIFO depth of 8 hides up to 8 cycles at
every T; depth 16 hides 16. Choose `FIFO_DEPTH` from the memory's read latency plus the adapter's
latency. The 64³ GEMM on the Chipyard Rocket SoC passes bit-exact but runs at 10.6% because the 8-word
FIFOs do not hide the TileLink/L2/DRAM latency; that is a memory-system result, not a core limit.

## 5. AXI4 master (`tq_top_axi`)

The adapter is an AXI4 master with a W-byte data bus:

- Reads: one aligned `BURST_BEATS`-beat INCR burst per line when `BURST_BEATS > 1`, or one single-beat
  INCR per port word when `BURST_BEATS = 1` (default). `BURST_BEATS` must be a power of two and
  `BURST_BEATS × W` must not cross a 4 KiB boundary. The RTL does not assert these parameter rules
  yet; check them in your build (and prefer a value that divides 4 KiB).
- Writes: one single-beat INCR per drain write, with `WSTRB = wr_be_o`.
- `RREADY` and `BREADY` are always 1; the core never back-pressures a response.
- `ARREADY`, `AWREADY`/`WREADY` act as the core's `gnt`.

**Signal subset.** The adapter does not expose `AxID`, `AxPROT`, `AxCACHE`, `AxQOS`, `AxREGION` or
`AxUSER`, and ignores `RID`/`BID`. At the SoC boundary:

- tie `AxID`/`AWID` to 0 and ignore `RID`/`BID` if your interconnect requires them;
- choose `AxPROT`/`AxCACHE` for the address map (for a non-coherent accelerator, typically a
  bufferable, non-cacheable data access);
- tie `AxQOS`/`AxREGION`/`AxUSER` to 0 unless your fabric uses them.

If your interconnect cannot accept a master without these signals, add them as constants in a wrapper;
that is a small, local change and does not affect the core.

**Errors and ordering.** An `RRESP`/`BRESP` error on a core transaction sets `STATUS.BUS_ERR` and the
run continues. C must then be treated as invalid. The read line buffer is invalidated when the core
requests a write to the same line, but it is not coherent with concurrent host writes; do not write
A/B/C from another master during a run.

## 6. Parameter profiles

These are starting points, not measured v1 results. The reference PPA is the `t8_cpa` profile with
`FIFO_DEPTH = 4`, `MACQ_DEPTH = 3`, `CLK_GATE = 1`, `ACC_FORM = ACC_CPA`, `PANEL_BYTES = 0`.

| Profile | Target | Suggested parameters | Notes |
| --- | --- | --- | --- |
| MCU / tightly coupled SRAM | smallest area, low latency | `T=8`, `FIFO_DEPTH=4`, `MACQ_DEPTH=3`, `SHARED_PORT=1` if the SRAM has one port, `PANEL_BYTES=0`, `PERF=0` if counters are not needed | Reference PPA is close to this; shared port costs drain cycles. |
| SoC / AXI, latency-tolerant | hide bus latency | `T=8`, `FIFO_DEPTH=16`, `MACQ_DEPTH=4`, `BURST_BEATS=4` or `8`, `PANEL_BYTES=0`, `MAX_OUT=16` | No measured PPA yet. Measure the memory system before quoting a utilization. |
| Throughput / area | fewer bytes per MAC | `T=16` with a 256-bit port, `MAC_PIPE=2`, `ACC_FORM=ACC_CPA` | T=16 closes at 858 MHz on ASAP7, not 1 GHz; mobile/narrow shapes lose utilization. |
| Panel-buffer experiment | GEMV / A-reuse | add `PANEL_BYTES=1024` or `4096` | Experimental: flops today, no PPA; verify your SRAM macro plan first. |

For every profile, regenerate the model results and record the PPA with `flow/ppa.py`; do not quote a
profile's energy or area from a different configuration.

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
- The panel buffer is experimental and uses flip-flops.
- The AXI master is a subset of AXI4 (see §5).
- Cached-DRAM performance is not a v1 use case; the Chipyard result is 10.6%.
- T=16 closes at 858 MHz on ASAP7; T=32 has no PPA record.
- CV-X-IF has not been validated on a real CVA6/CV32E40X.
- APB and RoCC adapters are not included.
- There is no compiler; the host packs operands and writes the program.
