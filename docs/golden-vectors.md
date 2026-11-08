# Golden vectors and trace format

Format version 1. Produced by `model/tri_queue/vectors.py`. This is the contract between the Python model (the golden reference) and the RTL testbenches.

```
cd model
python3 -m tri_queue.vectors --m 16 --n 16 --k 16 -o ../build/vectors/16x16x16
python3 -m tri_queue.vectors --m 20 --n 9 --k 33 --tile 16 --a u8 -o ../build/vectors/edge_t16
```

## Files

| File | Contents |
| --- | --- |
| `meta.json` | Format version, every `Params` field, the memory layout (`m, n, k, a_base, b_base, c_base, c_ld, mode`), image and program sizes, and the expected counters (`cycles`, `instructions`, `beats`, `read_vectors`, `read_bytes`, `write_vectors`, `write_bytes`, `mac_ops`, `utilization`) |
| `prog.hex` | The program (8 `CFG` + the GEMM kernel), one byte per line, 2 hex digits |
| `mem_init.hex` | SRAM image before the run, one W-byte word per line, for `$readmemh` |
| `mem_final.hex` | SRAM image after the run, same format. The C region holds the expected result. |
| `trace.txt` | Event trace, described below |

**Word images:** line *i* holds bytes `[i·W, (i+1)·W)`. The byte at the lowest address is the least significant, so a `logic [8*W-1:0] mem [N]` loaded with `$readmemh` has byte *j* of a word at bits `[8j+7 : 8j]`.

**Alignment (ISA rule):** `A_BASE`, `B_BASE`, `C_BASE` and `C_LD` are multiples of W. The host pads each C row to a whole number of words. Every drain write is then one aligned W-byte word with byte enables on its low `n` bytes.

## Trace lines

```
<cycle> <unit> <event> key=value ...
```

Cycle numbers are decimal and start at 0, with lines in cycle order. Inside one cycle, lines follow the model's evaluation order (memory responses → MAC unit → port → front end), but that order is **not** part of the contract. Addresses are decimal byte addresses. `data` is hex in memory order (lowest address first).

| Unit | Event | Keys | Meaning |
| --- | --- | --- | --- |
| `fe` | `issue` | `pc op arg [imm]` | Front end retires an instruction (`imm` for `CFG` only) |
| `fe` | `halt` | | `FENCE` complete: all streams, MACs and drains are finished |
| `fe` | `error` | `code` | The core stops at the end of this cycle with STATUS error `code` (spec §6.4) |
| `se` | `panel` | `s tile addr bytes` | Stream engine starts an A or B panel |
| `port` | `rd` | `s addr n` | Read issued for stream `s`, `n` bytes |
| `port` | `wr` | `addr n data` | Drain write, `n` bytes |
| `mac` | `zero` | | `ZERO_C` executed |
| `mac` | `start` | `shape bank` | `VOUTER_MAC` pops its operands, shape `r x c x d` |
| `mac` | `beat` | `bank beat` | One beat (`beat` = 0 or 1) accumulates into `bank` |
| `mac` | `swap` | `tile active` | `SWAP_C`: banks swapped, new active bank |
| `mac` | `drain_start` | `bank tile writes` | Drain of a finished tile begins (`writes` = number of `wr` it will issue) |
| `mac` | `drain_done` | `bank tile` | Last write of that drain issued, and the bank is cleared |

## How the RTL is checked against the model

In order of strictness:

1. **Results:** `mem_final.hex` must match exactly. Bit-exact, always required.
2. **Transactions:** the sequence of `port rd` (s, addr, n) and of `port wr` (addr, n, data) must match exactly, compared as two separate ordered lists. Required.
3. **Counters:** `instructions`, `beats`, `read_vectors`, `write_vectors` and `mac_ops` must match exactly. Required.
4. **Cycles:** the total, and the cycle of each `mac start`, `port rd` and `port wr`, must match when the testbench SRAM answers L cycles after the request and grants as described under "Back-pressure", with the model run at `mem_latency = L + 1`. The extra cycle is the operand FIFO write. A difference is either an RTL bug or a model assumption the RTL doesn't hold. Either way it gets explained and recorded, never ignored.

The RTL regression (`verif/`) applies checks 1–4 to every case, including those with back-pressure.

## Back-pressure

`Params.gnt_stall` (probability p, default 0) and `Params.gnt_seed` set the grants of both ports. With p = 0 every request is granted. Otherwise the model and the testbench use the same generator, `tri_queue.core.GrantSource`:

- `rng = numpy.random.default_rng(gnt_seed)`.
- On every model cycle, starting at 0, draw `rd_gnt = rng.random() >= p`, then `wr_gnt = rng.random() >= p`. Both are drawn whether or not the port requests.
- Outside the busy cycles the testbench grants without drawing.

A request that isn't granted is held unchanged until it is: a held read keeps its stream (A/B arbitration and panel loads wait), and on a shared port a held read or write keeps the port. The testbench checks that every ungranted request reappears unchanged the next cycle.

The trace exposes no internal state (FIFO contents, sum/carry values), so an RTL that keeps the accumulator in a different internal form (spec §10.2) checks against the same vectors.
