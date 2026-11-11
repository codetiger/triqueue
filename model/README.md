# Tri-Queue cycle model

Cycle-level Python model of the int8 Tri-Queue core in `docs/specification.md`.

```
python3 -m pytest -q     # functional + structural proofs (bit-exact vs numpy), ~3 s
python3 bench.py         # cycle benchmarks at T = 8, writes results.md (~70 s)
python3 dse.py           # tile-size sweep T = 8/16/32, writes dse.md (~35 s)
python3 -m tri_queue.vectors --m 16 --n 16 --k 16 -o ../build/vectors/16x16x16   # golden vectors for RTL
```

Shapes for `bench.py` and `dse.py` come from `bench/workloads/gemm.csv`. The vector and trace formats are in `docs/golden-vectors.md`.

## What it models

- **The real program.** The front end decodes the 10-byte kernel and 40 bytes of `CFG`, including zero-overhead `LOOP_T` and `LOOP_K` and the `AUTO` tile shapes.
- **Host packing.** Panels are packed exactly as in spec §5.3, and the stream engine reads them by address.
- **Size parameters.** Tile T (T×T tile, T² multipliers; the spec's design point is 8) and port width W (default 2T), both set in `Params`. The ISA and kernel don't change with T.
- **The multiplier array.** T² 9×9 multipliers with per-quadrant operand selection for the four k-depth shapes. It asserts that no accumulate cell ever sums more products than its quadrant's hardware arity (TL 4, TR 2, BL 2, BR 1).
- **The accumulator.** 32-bit carry-save (sum, carry) using real 3:2 compressor reduction, with the CPA at drain. The RTL's default plain 32-bit adder (spec §10.2) gives the same values mod 2³² and the same cycles, so the model checks both forms. C is written to memory with C_LD strides and masked lanes.
- **Timing.** One read port, an optional separate write port, fixed SRAM latency, QA/QB FIFO depth, the MAC command queue, the stream command queues, double-banked SC with drain and clear, `SWAP_C` stalls and `FENCE`.

## Assumptions

- The front end decodes 1 instruction per cycle.
- `ZERO_C` and `SWAP_C` toggle bank pointers and take no MAC cycle. Per cycle the MAC unit consumes at most one of them, then at most one `VOUTER_MAC`.
- Loop bodies contain only 1-byte instructions: a `CFG` reached inside a loop stops the core with `ERR_CFG_LOOP`, as in the RTL.
- `VOUTER_MAC` waits until all of its operand bytes are in the FIFOs, then pops them when it starts.
- SRAM reads are pipelined, one W-byte read per cycle, with a fixed latency. Ports always grant unless `gnt_stall` > 0, which withholds each port's `gnt` at random with that probability (seeded by `gnt_seed`, drawn as in `docs/golden-vectors.md`). An ungranted request is held unchanged until granted (OBI request stability). Bank conflicts are not modeled.
- Errors stop the core at the end of the cycle that finds them, with the RTL's STATUS code (`Stats.error`, `isa.ERR_*`); the program is NOP-padded to `prog_bytes` (PROG_BYTES). `tri_queue/programs.py` builds the illegal and edge-case programs the model tests and the RTL regression share.
- A panel buffer (`panel_bytes` > 0, off by default, spec §10.9): an A panel that fits is kept as it is read and replayed for the tile row's later tiles, one W-byte vector per cycle (`panel_latency` cycles to QA) without the memory port. A replay waits until no A read is in flight, and an A read waits until no replayed vector is, so QA receives its vectors in order. The RTL implements this with flip-flops; the model does not model the area.
- Int8 output writes C with `C_LD` still a multiple of W, one T-byte tile row per W-byte word at a T-aligned address; odd tile columns use the high half of the word. `read_c` extracts either form.
- Output requantization (implemented in the RTL; spec §6.1 and §10.1): `out_bytes` = 1 writes C as int8, `clip(acc >> out_shift, −128, 127)`, and `drain_lanes` sets the values the drain reads per cycle (0: T lanes for int8 output, W/4 for int32 output, the RTL's two modes). The shift is arithmetic. This is shift-only requantization; there is no per-tensor scale, bias or activation.
- A chaining output layout (`chain_c` with `out_bytes` = 1, off by default; model study, not in the RTL, spec §10.5): C is written in the next layer's A-panel order, column-major with `w = panel_width(rows)` bytes per column, so the result is already packed as the next layer's A. A tile is contiguous in that layout, and padding rows and columns are zero in the bank, so partial tiles are written whole. `C_BASE` must still be W-aligned but `C_LD` is unused, and the last word of a column block can be a short write (byte enables). `chain_snapshot` models a wide bank read that forms the first word one cycle after the bank is stable; the default drain reads one row per cycle and needs T−1 cycles. `read_c` extracts either layout.
- A write is complete when granted (SRAM), unless `wr_ack`: then it completes `wr_resp_latency` cycles after its grant (an AXI B response), `FENCE` waits until no write is outstanding, and no new write is requested while `wr_out_max` are outstanding.
- `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` are multiples of W (asserted). `run_gemm` pads C rows to whole words.
- The MAC pipeline (`mac_pipe`) and the drain CPA latency only affect when a drain may begin.
