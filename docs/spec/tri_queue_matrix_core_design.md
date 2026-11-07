# Design: Tri-Queue Matrix Core

**Revised Tri-Stack SIMD + Variable-Arity Matrix Compute Core**

Nov 7, 2026 · Harishankar Narayanan · Status: draft, rev 4 (int8, tile size parameterized, checked by cycle model)

Derived from `docs/history/tri_stack_simd_matrix_architecture_specification.md` (the original spec) and `docs/history/matrix_engine_comparison.md` (the comparison). The performance figures come from the cycle-level model in `model/`, with full results in `model/results.md`. That model runs the real instruction bytes and checks every result bit-exact against numpy. These are model results, not RTL results.

**Rev 2 changes:** operands are now int8 or uint8, with int32 accumulation. The core has 64 multipliers instead of 16 and the same 128-bit port. Variable arity returns as a *k-depth reduction*, which keeps edge tiles efficient.

**Rev 4 changes:** the tile size is now a parameter T (T×T tile, T² multipliers, port width W = 2T bytes). The design point is unchanged at T = 8, and §7.3 covers T = 16 and 32. Base addresses and `C_LD` must be multiples of W (§6.1). Host and memory interfaces are settled in `docs/decisions/` 0004–0008. The figures throughout this document are for T = 8. Two execution rules make the hardware exact (§5.2, §6): the MAC unit takes at most one `ZERO_C`/`SWAP_C` per cycle, and loop bodies contain only 1-byte instructions. **RTL v0** (`rtl/`) implements this spec and matches the cycle model cycle for cycle (`verif/`).

**Rev 3 changes:** the numbers are replaced with cycle-model results, and three errors are fixed: the `AUTO` shape rule (§4.2), per-quadrant operand selection (§4.1) and the kernel size (§6.2). The drain-hiding and FIFO-latency conditions are tightened, and §7.1 records what the model found.

---

## 1. Summary

The Tri-Queue is an int8 matrix-multiply coprocessor with **64 multipliers** and one 128-bit SRAM read port. On a 64×64×64 GEMM the cycle model gives **99.2% of peak**: 4,130 cycles against an ideal of 4,096. It executes **2,317 instructions** from a 10-byte kernel plus 40 bytes of configuration, and holds about **10 Kbit** of operand state. It has no register file, crossbar or rename logic.

The design rests on one idea:

> **A matrix multiply is a sum of outer products.**
> C = Σₖ (column k of A) ⊗ (row k of B)

The core keeps an 8×8 tile of C in place. Every cycle it takes 8 values from a column of A and 8 values from a row of B, multiplies all 64 pairs at once, and adds each product into its place in the tile. The rest of the design exists to keep that loop fed every cycle.

### Why int8 is the default

The balance law says a core with **P** multipliers and a port that supplies **W** elements per cycle needs an output tile at least **R = 2P / W** on a side to stay busy.

| | int32 (rev 1) | **int8 (rev 2)** |
| --- | --- | --- |
| Elements per 128-bit beat (W) | 4 | **16** |
| Multipliers (P) | 16 | **64** |
| Balanced tile R = 2P/W | 8×8 | **8×8 (unchanged)** |
| Cycles per k step | 4 beats per op | **1 beat per k** |
| Peak MACs per cycle | 16 | **64 (4×)** |
| Multiplier partial-product bits | 16 × 528 = 8,448 | **64 × 81 = 5,184 (≈ 0.6×)** |

The port, the tile, the accumulator storage and the utilization model all stay the same. The core does 4× the work per cycle, and its multiplier array is about 40% smaller (comparing partial-product bits, before synthesis). int8 is also the standard precision for ML inference.

### What it keeps from the original Tri-Stack

- Implicit operands and 1-byte instructions (opcode `[7:4]`, argument `[3:0]`)
- Role-separated storage: one store for A, one for B, one for C
- An outer-product MAC into a hardwired, carry-save accumulator tile
- Variable arity, now as a variable-depth reduction into each accumulator (§4)

### What it changes, and why

| Change | Original | Tri-Queue | Effect |
| --- | --- | --- | --- |
| Operand type | int32 | **int8 / uint8 → int32** | 4× MACs per port beat, smaller multipliers |
| Operand order | SA and SB are LIFO stacks | QA and QB are FIFOs | The next k step can be prefetched behind the current one |
| Loads | `VPUSH_A/B` in the instruction stream | Decoupled stream engine | Loads overlap compute |
| Accumulator | 4×4, one bank | 8×8, two banks | Reuse rises from 2 to 4 MACs per load, and draining hides under compute |
| Outer product | 16 MACs per beat | 64 MACs per beat (a full 8×8 per k) | 16 bytes per cycle, exactly what a 128-bit port supplies |
| Write-back | Shares the read port | Separate write port | Required at int8: int32 results are 4× wider than the inputs |
| Tile loop | Host-driven | `LOOP_T` hardware tile walker | A whole GEMM is one small program |
| Scope | Compared against CPUs | Coprocessor beside a host core | Branches and scalar math stay on the host |

---

## 2. Top-level datapath

```
                    ┌──────────────────────────────────────────┐
                    │                 SRAM                     │
                    │  A panels (int8)  B panels (int8)  C (int32)
                    └─────┬───────────┬──────────────▲─────────┘
            128-bit read  │           │              │ 128-bit write
            (16 B/cycle)  │           │              │ (separate port)
                    ┌─────▼───────────▼─────┐        │
   CFG regs ───────▶│     STREAM ENGINE     │        │
   (bases, K, M, N) │  A/B panel walkers    │        │
                    └─────┬───────────┬─────┘        │
                 ┌────────▼───┐   ┌───▼────────┐     │
                 │  QA  FIFO  │   │  QB  FIFO  │     │
                 │ 8 × 16 B   │   │ 8 × 16 B   │     │
                 │ A columns  │   │  B rows    │     │
                 └────────┬───┘   └───┬────────┘     │
                   8 B/beat │           │ 8 B/beat     │
                 ┌────────▼───────────▼────────┐     │
                 │   8×8 MULTIPLIER ARRAY      │     │
                 │   64 × (9×9 → 18-bit)       │     │
                 │   one full 8×8 outer        │     │
                 │   product per beat          │     │
                 └─────────────┬───────────────┘     │
                               │ 64 products
                 ┌─────────────▼───────────────┐     │
                 │  64 ACCUMULATE CELLS        │     │
                 │  (variable arity 1/2/4)     │     │
                 │  ┌─────────┐ ┌─────────┐    │     │
                 │  │ Bank X  │ │ Bank Y  │    │     │
                 │  │ 8×8 CSA │ │ 8×8 CSA │    │     │
                 │  │(compute)│ │ (drain) │────┼──▶ DRAIN UNIT ──┘
                 │  └─────────┘ └─────────┘    │    (4 × 32-bit CPA,
                 └─────────────────────────────┘     1 vector/cycle)
```

### 2.1 Storage

| Block | Shape | Bits | Semantics | Role |
| --- | --- | --- | --- | --- |
| **QA** | 8 vectors × 16 B | 1 Kbit | FIFO. Stream engine pushes, `VOUTER_MAC` pops | A column slices: one vector = 8 rows × 2 k steps |
| **QB** | 8 vectors × 16 B | 1 Kbit | FIFO. Stream engine pushes, `VOUTER_MAC` pops | B row slices: one vector = 8 columns × 2 k steps |
| **SC** | 2 banks × 8×8 × (sum + carry) 32 b | 8 Kbit | One bank active (MAC target), the other idle (draining) | The int32 output tile, held in place for all K steps |
| **CFG** | 8 × 32 b | 0.25 Kbit | Written by `CFG` | Bases, C stride, K, M, N, mode |
| **MAC command queue** | 4 × 52 b | 208 b | In-order queue of `ZERO_C` / `VOUTER_MAC` / `SWAP_C`. A `SWAP_C` entry carries the finished tile's C address and size, computed by the front end. | Lets the decoder run ahead of execution (§5.2) |
| **Total** | | **≈ 10.5 Kbit** | | |

This is the architectural state (1 Kbit = 1024 bits). RTL v0 adds pipeline registers (the 64 × 18-bit product register and a 2 × 32-byte beat-1 operand register), the two stream command queues and the 64-byte program buffer: about 14k flip-flops in total at T = 8, from generic Yosys synthesis.

Each FIFO holds 16 k steps of operands. In the cycle model, depth 8 fully hides SRAM latency up to 8 cycles and still reaches 94% at 16 cycles. Depth 16 hides up to 16 cycles (`model/results.md` §5). Choose the depth from the target SRAM's latency.

### 2.2 Where the reuse happens

Reuse comes only from the size of the output tile, not from keeping operands in the queues:

- Each A element meets **8** B elements, one per column of the tile.
- Each B element meets **8** A elements, one per row.
- 64 MACs per 16 elements loaded gives **4 MACs per element** (0.25 bytes read per MAC).

So QA and QB can be plain destructive FIFOs. Nothing has to stay in them, because the accumulator stays put.

---

## 3. How one instruction works: `VOUTER_MAC`

In the default 8×8 mode, one instruction pops **one vector from QA and one from QB** and runs **two beats**, one per k step:

```
QA vector (16 B):  [ A[0..7][k]   |  A[0..7][k+1] ]
QB vector (16 B):  [ B[k][0..7]   |  B[k+1][0..7] ]

beat 0:  SC[r][c] += A[r][k]   × B[k][c]      for all 64 (r, c)
beat 1:  SC[r][c] += A[r][k+1] × B[k+1][c]    for all 64 (r, c)
```

```
                 B[k][0]  B[k][1]  ...  B[k][7]
               ┌────────┬────────┬─────┬────────┐
     A[0][k]   │  ×+    │  ×+    │ ... │  ×+    │   each cell:
     A[1][k]   │  ×+    │  ×+    │ ... │  ×+    │   SC[r][c] += A[r][k]·B[k][c]
       ...     │  ...   │  ...   │     │  ...   │
     A[7][k]   │  ×+    │  ×+    │ ... │  ×+    │   64 MACs, 1 cycle
               └────────┴────────┴─────┴────────┘
```

- **2 beats × 64 MACs = 128 MACs** per instruction.
- **2 vectors popped per 2 cycles.** The 128-bit port delivers exactly 2 vectors in 2 cycles, so compute and bandwidth are balanced with no slack in either direction.
- K/2 consecutive `VOUTER_MAC`s complete the tile.

### 3.1 MAC pipeline

```
 issue → MUL: 9×9 signed multiply → 18-bit product → ACC: compress into SC (sum, carry)
```

- **9×9 signed multipliers** cover int8 and uint8 alike. The 9th bit is the operand's sign or zero extension, chosen by `MODE`.
- Products are only 18 bits wide, so a full carry-propagate add inside the multiplier is cheap. Each accumulate cell then compresses its products together with the stored (sum, carry). In 8×8 mode that is a 3:2 compression, completed in one cycle. Because the accumulate loop is one cycle, the core can update the same 64 accumulators every cycle with no forwarding and no stall.
- **Overflow:** |int8 × int8| ≤ 2¹⁴ and uint8 × uint8 < 2¹⁶. An int32 accumulator therefore cannot overflow for any K ≤ 32,768 in any signedness mode.
- A carry-propagate add on the accumulator happens only once per output element, in the drain unit.

---

## 4. Variable arity: k-depth reduction

The original spec's idea was variable-arity adder trees (`ADD N`). In the Tri-Queue that idea solves a specific problem: **edge tiles**.

**The problem.** A tile with only 4 valid rows uses just 32 of the 64 multipliers per beat, so it runs at 50% utilization. A 4×4 corner tile runs at 25%.

**The fix.** Use the idle multipliers on *additional k steps*, and let each accumulator add 2 or 4 products per cycle instead of 1. The tile shape *r × c* and the reduction depth *d* always satisfy:

> **r × c × d = 64 multipliers**

| Shape (r × c × d) | Used for | Products per accumulator | Bytes per beat (A + B) | Port cycles per beat | Utilization | Without depth arity |
| --- | --- | --- | --- | --- | --- | --- |
| 8 × 8 × 1 | Interior tiles | 1 | 8 + 8 = 16 | 1 | **100%** | 100% |
| 8 × 4 × 2 | Right-edge tiles (≤ 4 columns left) | 2 | 16 + 8 = 24 | 1.5 | **67%** | 50% |
| 4 × 8 × 2 | Bottom-edge tiles (≤ 4 rows left) | 2 | 8 + 16 = 24 | 1.5 | **67%** | 50% |
| 4 × 4 × 4 | Corner tiles | 4 | 16 + 16 = 32 | 2 | **50%** | 25% |

### 4.1 Hardware: arity fixed by quadrant

The 8×8 multiplier array splits into four 4×4 quadrants. In a reduced mode, a multiplier at (r+4, c) works on the same output element as (r, c), one k step later. Its product therefore goes to accumulator cell (r, c) instead of cell (r+4, c).

```
 multiplier array            accumulate-cell arity (max products summed per cycle)
 ┌──────────┬──────────┐     ┌──────────┬──────────┐
 │ TL       │ TR       │     │  4       │  2       │   8×8×1 : every cell adds 1 product
 │ rows 0-3 │ rows 0-3 │     │ 6:2 comp │ 4:2 comp │   4×8×2 : rows 0-3 add products from r and r+4
 ├──────────┼──────────┤     ├──────────┼──────────┤   8×4×2 : cols 0-3 add products from c and c+4
 │ BL       │ BR       │     │  2       │  1       │   4×4×4 : TL cells add all four quadrants
 │ rows 4-7 │ rows 4-7 │     │ 4:2 comp │ 3:2 comp │
 └──────────┴──────────┘     └──────────┴──────────┘
```

- **Routing is fixed.** Each product wire goes to its own cell and to at most one reduced-mode cell. Gating selects which one is active, so no crossbar is needed.
- **Each quadrant has its own operand inputs:** 4 bytes of A and 4 bytes of B, chosen by a small mux. Quadrant (qi, qj) uses k offset 0 in 8×8×1 mode, qi in 4×8×2, qj in 8×4×2, and 2·qi + qj in 4×4×4. A mux per row is not enough, because in 4×4×4 mode the left and right halves of a row need different k steps. The simulator models this routing and asserts the arity limits above.
- **Only the TL quadrant needs a deeper compressor** (6:2). It is the only place where the critical path grows.

### 4.2 Choosing the shape

`VOUTER_MAC AUTO` (the default in the kernel) picks the shape from the tile walker's position: r = 4 if 4 or fewer rows remain, otherwise 8, and likewise for c. A tile with 5–7 rows left therefore still uses 8 rows. The stream engine reads the same position, so it fetches exactly the bytes each beat needs.

### 4.3 Lane-level tails

When the number of remaining rows or columns isn't a multiple of 4, the host packer pads that panel with zeros up to 4. The drain unit masks the writes to the padding lanes. No instruction support is needed.

### 4.4 Dropped from the original

| Original op | Why it is dropped |
| --- | --- |
| `VMUL_RED N` (stand-alone dot-product tree) | Replaced by k-depth arity inside the accumulate cells. Matrix-vector work is still capped at 16 MACs per cycle by the port in any mode (§9). |
| `ADD_RED N`, `MUL_RED N` | Not on the GEMM path. Scalar reductions belong to the host. |
| `VPUSH_A/B N`, `DROP_A N` | Replaced by the stream engine. |
| `SET_ADDR`, `S_ADDR` stack | Replaced by `CFG` registers. This also fixes the spec's missing `LOOP_K` count slot. |

---

## 5. Keeping everything busy

### 5.1 The three-way overlap

| Activity | Hardware | Per 8×8 tile (K = 64) |
| --- | --- | --- |
| Load A and B panels | Stream engine + read port | 2 × 8 × 64 B = 1,024 B = **64 cycles** |
| Compute | Multiplier array + active SC bank | 32 ops × 2 beats = **64 cycles** |
| Drain previous tile | Drain unit + idle SC bank + write port | 64 × int32 = 16 vectors = **16 cycles** |

```
cycle →    0           64          128         192         256
read port  [ A0 B0   ][ A1 B1    ][ A2 B2    ][ A3 B3    ]
MAC array    [ tile 0  ][ tile 1   ][ tile 2   ][ tile 3   ]
SC bank X    [ compute 0 ]drain     [ compute 2 ]drain
SC bank Y               [ compute 1 ]drain     [ compute 3 ]
write port              [d0]        [d1]       [d2]
```

- Loading and compute take the same time per tile, so neither waits on the other.
- The drain (16 writes, plus about 3 cycles of MAC pipeline and CPA latency) hides under the next tile's compute (Kp cycles). In the model it is **fully hidden once Kp ≥ 24**. At Kp = 16 it overruns by about 1 cycle per tile (91% utilization), and at Kp = 8 utilization drops to 46%.
- **The separate write port is required at int8.** Results are int32 while inputs are int8, so the drain carries a quarter of the read traffic. On a shared port each tile would take 64 + 16 = 80 cycles, which is only 80% utilization. Requantizing the output to int8 cuts the drain to 4 vectors (94% on a shared port; see §9).

### 5.2 Instruction issue

At int8 a `VOUTER_MAC` lasts only 2 cycles, so the four bookkeeping instructions per tile (`STREAM_A`, `STREAM_B`, `LOOP_K`, `SWAP_C`) could open a bubble between tiles. Two mechanisms prevent it:

- **MAC command queue (depth 4).** The decoder issues one instruction per cycle, and each `VOUTER_MAC` executes for 2 cycles, so the decoder runs ahead and fills the queue. At the end of a tile the queue holds about 8 cycles of work, which covers the bookkeeping instructions. `SWAP_C` goes through the same queue, so the bank swap lands exactly between the last op of one tile and the first op of the next. In the cycle model, utilization at 64³ is 94.8% with depth 1, 97.7% with depth 2, and 99.2% with depth 3 or more. Depth 4 leaves margin. Each cycle the MAC unit consumes at most one `ZERO_C` or `SWAP_C` from the head of the queue, then at most one `VOUTER_MAC`. A `SWAP_C` and the next tile's first `VOUTER_MAC` therefore issue in the same cycle, with no bubble.
- **Stream prefetch.** `STREAM_A/B` go into the stream engine's own command queue (depth 2 per stream). The kernel issues them for tile *t+1* **before** computing tile *t*, so the engine moves straight from one panel to the next. Commands past the last tile are ignored.

### 5.3 Memory layout (host packing)

- **A** is packed into **column panels**: 8 rows × Kp, k-major, so 8 contiguous bytes per k and one 16-byte vector covers 2 k steps. The last panel is 4 rows wide when 4 or fewer rows remain, and rows beyond M are zero-padded.
- **B** is packed into **row panels**: Kp × 8, k-major, padded the same way.
- **Kp** is K rounded up to a multiple of 8, with zeros in the padding. This lets every shape consume whole vectors.
- Panels are contiguous, so their addresses come from K alone. A panel *ti* is at `A_BASE + ti·8·Kp`, and B panel *tj* is at `B_BASE + tj·8·Kp`.

The analytic model doesn't charge for packing. Real workloads need either a packing pass or a strided stream mode (§9).

---

## 6. Instruction set

Encoding: `opcode [7:4] | arg [3:0]`. `CFG` is the only multi-byte instruction.

| Op | Mnemonic | Bytes | Effect |
| --- | --- | --- | --- |
| `0x0` | `NOP` | 1 | Does nothing |
| `0x1` | `CFG r, imm32` | 5 | Writes config register r (0–7) |
| `0x2` | `STREAM_A` | 1 | Queues the next A panel for the stream engine. Non-blocking. |
| `0x3` | `STREAM_B` | 1 | Queues the next B panel. Non-blocking. |
| `0x4` | `ZERO_C` | 1 | Clears both SC banks (used once at start) |
| `0x5` | `VOUTER_MAC s` | 1 | 2-beat outer-product MAC of shape `s` into the active bank. Stalls only if a queue is empty. |
| `0x6` | `SWAP_C` | 1 | Swaps the active and idle banks, then drains and clears the finished bank in the background. Stalls only if the idle bank is still draining. |
| `0x7` | `LOOP_K n` | 1 | Repeats the next n instructions Kp / 2d times, with zero overhead. d comes from the current tile's `AUTO` shape. Loop bodies may contain only 1-byte instructions (no `CFG`), so the body is n bytes long. Loops nest 2 deep, with at most one `LOOP_T`. |
| `0x8` | `LOOP_T n` | 1 | Repeats the next n instructions once per output tile. The tile walker advances. |
| `0x9` | `FENCE` | 1 | Waits until all streams and drains finish (synchronizes with the host) |

**`VOUTER_MAC` argument:** `arg[3] = AUTO`. Otherwise `arg[1] = (r == 8)` and `arg[0] = (c == 8)`, which gives d = 64 / (r·c). `arg[2]` is reserved.

**What each shape pops per instruction (2 beats):**

| Shape | QA vectors | QB vectors | k steps covered |
| --- | --- | --- | --- |
| 8×8×1 | 1 | 1 | 2 |
| 8×4×2 | 2 | 1 | 4 |
| 4×8×2 | 1 | 2 | 4 |
| 4×4×4 | 2 | 2 | 8 |

### 6.1 Config registers

| r | Name | Meaning |
| --- | --- | --- |
| 0 | `A_BASE` | Address of the first packed A panel |
| 1 | `B_BASE` | Address of the first packed B panel |
| 2 | `C_BASE` | Address of C[0][0] (int32) |
| 3 | `C_LD` | Row stride of C, in bytes |
| 4 | `K` | Inner dimension. Kp and the loop counts are derived from it. |
| 5 | `M` | Rows of A and C: sets the tile count, `AUTO` shapes and edge masks |
| 6 | `N` | Columns of B and C, used the same way |
| 7 | `MODE` | bit 0: A signed, bit 1: B signed. Other bits are reserved (output requantization, §10.1). |

Tile walk: row-major over the ⌈M/T⌉ × ⌈N/T⌉ tiles. Tile (ti, tj) writes to `C_BASE + T·ti·C_LD + 4T·tj` (T = 8: `C_BASE + 8·ti·C_LD + 32·tj`).

**Alignment:** `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` must be multiples of the port width W (16 bytes at T = 8). The host pads each C row to whole words, so every read and every drain write is one aligned word, and partial words use byte enables. The model asserts this rule ([decision 0005](../decisions/0005-memory-interface.md)).

### 6.2 Complete GEMM kernel

```asm
; host: CFG 0..7 (bases, C stride, K, M, N, MODE)  40 bytes, once per GEMM
        ZERO_C              ; both banks clear
        STREAM_A            ; prime: tile 0 A panel
        STREAM_B            ; prime: tile 0 B panel
        LOOP_T 5            ; for each 8x8 output tile:
          STREAM_A          ;   prefetch NEXT tile's A panel
          STREAM_B          ;   prefetch NEXT tile's B panel
          LOOP_K 1          ;   Kp/2d times:
            VOUTER_MAC AUTO ;     128 MACs / 2 beats, paced by queues
          SWAP_C            ;   finished tile drains in background
        FENCE
```

The kernel is **10 bytes** (plus 40 bytes of `CFG`) and runs any M×N×K int8 GEMM. For 64³ it executes 8 + 4 + 64 tiles × 36 + 1 = **2,317 instructions**, a count the simulator matches exactly. Because of the loop buffer, those 10 bytes are fetched only once.

---

## 7. Performance (64×64×64, cycle model)

Ideal = 262,144 MACs ÷ 64 = **4,096 cycles**. The simulated fixed overhead is **34 cycles**: 16 at the start (8 `CFG` writes, the kernel prologue and the first SRAM latency) and 18 at the end (the last drain). The int32 and original-spec columns are analytic figures from rev 1 and the comparison doc.

| Metric | **Tri-Queue int8** (separate write port) | Tri-Queue int8 (shared port) | Tri-Queue int32 (rev 1) | Original Tri-Stack |
| --- | --- | --- | --- | --- |
| Multipliers | 64 (9×9) | 64 (9×9) | 16 (32×32 low) | 16 (32×32) |
| Peak MACs per cycle | 64 | 64 | 16 | 16 |
| Cycles | **4,130** | 5,131 | 16,432 | ≈ 50,700 |
| Utilization | **99.2%** | 79.8% | 99.7% | 32% |
| SRAM reads | 64 KB (65,536 elements) | 64 KB | 256 KB (65,536 elements) | 512 KB (131,072 elements) |
| MACs per element loaded | 4.0 | 4.0 | 4.0 | 2.0 |
| Dynamic instructions | 2,317 | 2,317 | ≈ 4.4k | ≈ 21.8k |
| Program bytes fetched | 10 + 40 CFG | 10 + 40 CFG | 10 + 40 CFG | ≈ 87k |
| Operand state | ≈ 10.5 Kbit | ≈ 10.5 Kbit | ≈ 10.2 Kbit | ≈ 3 Kbit |

On the same port and with about the same state, the int8 core does the 64³ GEMM about **4× faster** than the int32 revision and about **12× faster** than the original spec.

### 7.1 What the cycle model shows

Every run below also matched numpy bit-for-bit. Defaults: SRAM latency 2, FIFO depth 8, separate write port.

| Workload | Cycles | Utilization | What limits it |
| --- | --- | --- | --- |
| 16³ | 101 | 63.4% | The 34-cycle fixed overhead |
| 32³ | 546 | 93.8% | The fixed overhead |
| 64³ | 4,130 | 99.2% | Nothing significant |
| 128³ / 256³ | 32,802 / 262,178 | 99.9% / ≈ 100% | Nothing significant |
| 60³ | 3,859 | 87.5% | Edge tiles are port-bound at 67% or 50%, and K = 60 is padded to 64 |
| 65³ | 5,524 | 77.7% | The single extra row and column use 4-wide tiles with 1 useful lane, and K = 65 is padded to 72 |
| 100³ | 16,919 | 92.4% | Edge tiles and K padding |
| 1×512×512 (GEMV) | 24,593 | 16.7% | 4×8×2 shape with 1 of 4 rows useful (see §10) |

Confirmed: the 99% steady state, exactly 4 MACs per element loaded, the instruction count, 80% on a shared port, edge-shape utilization of 66.4% (predicted 67%) and 48.2% (predicted 50%), and the per-quadrant arity limits.

Not predicted by the analytic model:

- **Small GEMMs pay a fixed 34 cycles,** which is 37% of a 16³ multiply. Back-to-back GEMMs without a `FENCE` in between would hide it (§10).
- **K padding to a multiple of 8 wastes up to 7/Kp** of the compute, for example 11% at K = 65.
- **Odd M or N costs more than the edge-shape table suggests,** because a 1-row remainder still occupies a 4-wide tile.

The cross-design table in the comparison doc assumed int32 at 16 MACs. It needs to be redone at int8 with 64 MACs: for example, Gemmini with an 8×8 int8 mesh, an int8 register-tile engine, and RVV with int8 dot products.

### 7.2 Real-hardware reference: Apple AMX (M2 Pro)

These measurements were taken on this machine (`bench/reference/amx/`). AMX is undocumented, so the kernels use the encodings from corsix/amx. The full data is in `bench/reference/amx/results.md` and `bench/reference/amx/ablation.md`.

**What the measurements show about AMX itself**

- **Peak:** one P-cluster unit does one 16×16 fp32 outer product per cycle, which is **0.815 TMAC/s**. Each op takes about 4 cycles, so full rate needs all 4 Z accumulator tiles in flight.
- **A hand-written AMX GEMM** (32×32 tile in Z, pre-packed operands) costs **4.29 cycles per k step** (ideal 4), **68 cycles per output tile** to store Z, which can't overlap compute, and **142 cycles per call**.
- Both engines read **0.25 bytes per MAC**. AMX's 32×32 fp32 tile does 16 MACs per element loaded, and the Tri-Queue's 8×8 int8 tile does 4.

**What can be compared, and what can't.** The two engines differ in precision (fp32 against int8), size (256 against 64 MACs per cycle) and kind of evidence (silicon against a model with ideal SRAM). So **no throughput, energy or area comparison is valid**. Comparing utilization curves directly would also be biased by tile size. A 32³ multiply is a single tile for AMX but 16 tiles for the Tri-Queue. Instead, the double-bank claim is tested in two ways that don't depend on precision:

1. **Ablation inside one model.** The simulator runs with one SC bank and with two.
2. **One first-order model for both engines:** cycles = F + tiles × (K·c_k + D_exposed), where D_exposed is the drain cost per tile that compute can't overlap.
   - For the Tri-Queue, the parameters come from the design, not a fit, and the model is within 4.7% of the simulator.
   - For AMX, the parameters are fitted on K sweeps at 1, 4 and 16 tiles. The model then predicts held-out square sizes within 4% from 128³ up, and within 13–17% for 32³ and 64³, which are sub-microsecond calls.

**Result (64×64×K utilization):**

| K | AMX (measured, 1 accumulator) | Tri-Queue, 1 bank (sim) | Tri-Queue, 2 banks (sim) |
| --- | --- | --- | --- |
| 8 | 16.8% | 30.5% | 46.0% |
| 16 | 39.9% | 46.7% | 91.3% |
| 32 | 62.2% | 63.7% | 98.4% |
| 128 | 81.5% | 87.5% | 99.6% |
| 1024 | 90.9% | 98.2% | 99.9% |

- Measured in its own k steps, AMX's exposed drain is **about 16 k steps** and a single-bank Tri-Queue's is **19**. Both fall to about half throughput at K ≈ 16–19.
- The second bank removes that loss: 46.7% → 91.3% at K = 16, and 77.8% → 99.2% at 64³.
- This is the supported claim. **Double-banking the accumulator removes the per-tile drain stall that a production outer-product engine measurably pays.**

**Why AMX doesn't double-bank.** The measured fp32 accumulate latency of about 4 ops means 4 independent accumulators must be in flight, and Z holds exactly 4 fp32 tiles. Splitting Z into two halves that take turns would cap compute at the measured 2-chain rate, about 50%. This limit comes from floating point. The Tri-Queue's 1-cycle carry-save int accumulate is what leaves room for a second bank, so **the advantage does not carry over to an fp32 Tri-Queue**.

**Still open:**

- An integer-to-integer measurement. AMX `mac16` (int16) did not scale with independent chains in the peak benchmark, and that is unexplained.
- Area and energy, which need RTL and synthesis.

### 7.3 Scaling the tile (cycle model)

The model takes T as a parameter (`model/dse.md`, [decision 0008](../decisions/0008-tile-size.md)). Every shape, the quadrant arity limits and the kernel stay the same: shapes become T or T/2 rows and columns with reduction depth 1, 2 or 4. All results are bit-exact.

| | T = 8 | T = 16 | T = 32 |
| --- | --- | --- | --- |
| MACs/cycle, read port | 64, 128 b | 256, 256 b | 1024, 512 b |
| State (bits per MAC) | 10.5 Kbit (167) | 36.5 Kbit (146) | 136.5 Kbit (136) |
| Bytes read per MAC | 0.25 | 0.125 | 0.0625 |
| Drain hidden when | Kp ≥ 24 | Kp ≥ 40 | Kp ≥ 72 |
| 64³ / 256³ utilization | 99.2% / 100% | 95.3% / 99.9% | 75.1% / 99.5% |
| Geometric mean over the `dse` workload shapes | 56.0% | 35.4% | 20.1% |

- **The port must be exactly 2T bytes.** At T = 16, a 128-bit port gives 49.8% and a 512-bit port gives the same 99.6% as 256 bits.
- **FIFO depth doesn't depend on T.** In W-byte vectors, depth 8 hides 8 cycles of latency at every size.
- **Drain hiding needs Kp ≥ 2T + 8,** because each tile drains 2T words (T rows × 4T bytes ÷ W).
- **Large tiles lose on small and narrow problems.** The half-width shapes recover only up to 50% of the array, so a 1-row remainder or a GEMV wastes more as T grows.

---

## 8. Hardware budget

| Block | Contents |
| --- | --- |
| Multiplier array | 64 × 9×9 signed multipliers, 18-bit output, 1 pipeline stage |
| Accumulate cells | 64 cells, shared by both banks: 16 × 6:2 (TL), 32 × 4:2 (TR, BL), 16 × 3:2 (BR) |
| Accumulator storage | 2 banks × 64 × (32-bit sum + 32-bit carry) flops |
| Operand routing | Per-quadrant mux selecting 4 A bytes and 4 B bytes from up to 4 k offsets (k-depth modes) |
| Drain unit | 4 × 32-bit CPA, edge-mask byte enables, C address generator |
| Stream engine | 2 increment-only panel walkers, A/B arbiter on one read port, 2-deep command queues |
| Pipeline registers | Product register (64 × 18 b) and beat-1 operand register (2 × 32 B), so a beat's multiply and accumulate take one stage each |
| Control | 4-bit opcode decoder, 4-deep MAC command queue, 2-level loop counters, 64-byte program buffer (holds `CFG` + kernel, so the loop body is never refetched), tile walker with running address offsets (no multipliers), AUTO shape logic |
| Not needed | Register file, operand crossbar, rename or scoreboard, branch unit |

---

## 9. Positioning

At a high level, the Tri-Queue combines the **output-stationary outer-product dataflow of Apple's AMX** with **Gemmini's decoupled load/compute/store control**. It leaves out Gemmini's scratchpad, and the host CPU doesn't issue each operation. The AMX details come from the community reverse-engineering (corsix/amx), not from Apple.

- **Strengths:** it runs autonomously from a 10-byte kernel, so a small in-order host is enough. It has about 10 Kbit of state, against Gemmini's roughly 320 KB of SRAM. It reaches near-peak utilization without a scoreboard or register file, and edge tiles are cheap.
- **Weaknesses:** there is no reuse between tiles (A and B panels are re-read from SRAM for every tile), operands must be pre-packed, there are no built-in convolution or activation operations, and there is no compiler. It also doesn't scale to TPU size: the balance law makes the tile grow with the multiplier count, and broadcasting operands across a large tile becomes a wiring problem.
- **Target niche:** a matrix engine for microcontroller-class or edge SoCs, sitting beside a small core with fast on-chip memory. This is the space of Arm's Ethos-U55 class of parts, not of datacenter TPUs.

---

## 10. Open decisions

Items 1, 2 and 4 are now settled in decision records: 0007 (requantization deferred), 0006 (carry-save for v0, plain adder measured in Phase 3) and 0004 (register port plus adapters). The memory interface is in 0005.

1. **Output requantization.** Adding scale, shift and clamp to int8 in the drain unit cuts the drain to 4 vectors per tile. That would allow a shared read/write port at about 94% utilization (unverified: decision 0007 shows the bank stays busy just as long unless the CPA lanes also grow), and it matches what int8 inference layers need anyway. `MODE` has reserved bits for it, and the scale and shift values would need either a 9th `CFG` register or a packed field.
2. **Accumulator form.** Carry-save keeps the 1-cycle accumulate loop at a high Fmax but doubles SC to 8 Kbit. A plain 32-bit adder per cell halves SC (total state ≈ 6.5 Kbit) if the target clock allows a 32-bit add per cycle.
3. **Other types.** int4 (2× the MACs per beat, so the tile would have to grow by the balance law), and fp16 or bf16 (these can't use carry-save accumulation and would need a separate accumulator design).
4. **Host interface.** A RoCC-style custom-instruction attachment (as in Gemmini) or a memory-mapped command FIFO.
5. **Packing.** Either a separate packing pass, or a strided stream mode that reads unpacked row-major data, at the cost of more address logic and a risk of SRAM bank conflicts.
6. **Matrix-vector work (M = 1).** Every element of the matrix is used once, so the port caps it at 16 MACs per cycle (25% of peak) in any mode. The current design reaches 16.7%. A dedicated mode could reach the cap by keeping the vector in QA instead of re-streaming it for each tile.
7. **Small-GEMM overhead.** The fixed 34-cycle start and end cost could be hidden by letting the host queue the next GEMM's `CFG` and streams before the previous one's `FENCE`. That would need a shadow `CFG` set.

## 11. Next steps

The project-level plan is in `docs/roadmap.md`. The items below are the design-specific steps.

1. ~~Build a Python simulator of this exact ISA that matches numpy bit-for-bit.~~ Done: `model/`, 229 tests.
2. ~~Add a cycle-level model, and check the 64³ figure, the drain condition and the queue depths.~~ Done: `model/results.md`.
3. ~~Measure the AMX utilization curve on the M2 Pro as a real-hardware reference, and test the double-bank claim without depending on precision.~~ Done: `bench/reference/amx/`, §7.2. Open: the integer (`mac16`) path.
4. ~~Generalize the model to any tile size, export golden vectors and traces for RTL, and sweep T = 8/16/32.~~ Done: `model/dse.md`, §7.3, `docs/spec/trace_format.md`.
5. ~~Write RTL and simulate it in Verilator against the Python model.~~ Done: `rtl/`, `verif/`. 643 cases in 7 configurations, cycle-exact. Next: synthesize with Yosys + OpenROAD, and compare against Gemmini configured with an 8×8 int8 mesh in the same flow (roadmap Phases 3 and 5).
6. Build the visual explainer (outer-product walkthrough, beat schedule, k-depth arity, overlap timeline). It can replay traces from the simulator.
