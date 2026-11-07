# Workloads

`gemm.csv` is the single list of GEMM shapes that every benchmark in the repo uses: the cycle model (`model/bench.py`), the AMX comparison (`bench/reference/amx/compare.py`), and later the RTL testbenches and the harnesses for other designs. Add a shape here instead of hard-coding it in a script.

| Column | Meaning |
| --- | --- |
| `group` | Category: `square`, `irregular`, `k_sweep`, `edge`, `gemv`, `tiny` (later: layer shapes from real models) |
| `m, n, k` | C is m×n, inner dimension k |
| `tags` | `;`-separated list of the suites that run this row. `model` = `model/bench.py` size sweep, `amx` = AMX comparison table, `dse` = tile-size sweep (`model/dse.py`). |
| `why` | What the shape stresses |

Python: `from workloads import shapes; shapes("model")`, with `bench/workloads` on `sys.path`.

**Not yet in sync:** `bench/reference/amx/amx_gemm.c` and `sgemm_sweep.c` still have their own size lists. `compare.py` shows `–` for any shape they don't measure.

**To do (Phase 0 follow-up):** add layer shapes from real int8 models (MLPerf Tiny, a small transformer), with the source model and layer named in `why`.
