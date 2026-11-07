# Tri-Queue simulator

Cycle-level Python model of the int8 Tri-Queue core in `docs/spec/tri_queue_matrix_core_design.md`.

```
python3 -m pytest -q     # functional + structural proofs (bit-exact vs numpy), ~3 s
python3 bench.py         # cycle benchmarks at T = 8, writes results.md (~70 s)
python3 dse.py           # tile-size sweep T = 8/16/32, writes dse.md (~35 s)
python3 -m tri_queue.vectors --m 16 --n 16 --k 16 -o ../build/vectors/16x16x16   # golden vectors for RTL
```

Shapes for `bench.py` and `dse.py` come from `bench/workloads/gemm.csv`. The vector and trace formats are in `docs/spec/trace_format.md`.

## What it models

- **The real program.** The front end decodes the 10-byte kernel and 40 bytes of `CFG`, including zero-overhead `LOOP_T` and `LOOP_K` and the `AUTO` tile shapes.
- **Host packing.** Panels are packed exactly as in doc §5.3, and the stream engine reads them by address.
- **Size parameters.** Tile T (T×T tile, T² multipliers; the spec's design point is 8) and port width W (default 2T), both set in `Params`. The ISA and kernel don't change with T.
- **The multiplier array.** T² 9×9 multipliers with per-quadrant operand selection for the four k-depth shapes. It asserts that no accumulate cell ever sums more products than its quadrant's hardware arity (TL 4, TR 2, BL 2, BR 1).
- **The accumulator.** 32-bit carry-save (sum, carry) using real 3:2 compressor reduction. The CPA happens at drain, and C is written to memory with C_LD strides and masked lanes.
- **Timing.** One read port, an optional separate write port, fixed SRAM latency, QA/QB FIFO depth, the MAC command queue, the stream command queues, double-banked SC with drain and clear, `SWAP_C` stalls and `FENCE`.

## Assumptions

- The front end decodes 1 instruction per cycle.
- `ZERO_C` and `SWAP_C` toggle bank pointers and take no MAC cycle.
- `VOUTER_MAC` waits until all of its operand bytes are in the FIFOs, then pops them when it starts.
- SRAM reads are pipelined, one W-byte read per cycle, with a fixed latency and no `gnt` stalls. Bank conflicts are not modeled.
- `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` are multiples of W (asserted). `run_gemm` pads C rows to whole words.
- The MAC pipeline (`mac_pipe`) and the drain CPA latency only affect when a drain may begin.
