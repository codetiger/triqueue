# Workloads

`gemm.csv` is the single list of GEMM shapes that every benchmark in the repo uses: the cycle model (`model/bench.py`), the AMX comparison (`bench/reference/amx/compare.py`), and later the RTL testbenches and the harnesses for other designs. Add a shape here instead of hard-coding it in a script.

| Column | Meaning |
| --- | --- |
| `group` | Category: `square`, `irregular`, `k_sweep`, `edge`, `gemv`, `tiny`, `amx_fit`, or the network a layer comes from (see below) |
| `m, n, k` | C is m×n, inner dimension k |
| `count` | How many times the shape occurs in its network (1 for synthetic shapes). Network-level figures weight each row by `count`. |
| `tags` | `;`-separated list of the suites that run this row. `model` = `model/bench.py` size sweep, `amx` = AMX comparison table, `dse` = tile-size sweep (`model/dse.py`), `layers` = real int8 network layers (`model/dse.py` §6), `amx_hand` / `amx_sgemm` = shapes measured by `bench/reference/amx/amx_gemm.c` / `sgemm_sweep.c`. |
| `why` | What the shape stresses |

Python: `from workloads import shapes; shapes("model")`, with `bench/workloads` on `sys.path`.

The AMX programs read this file too (`bench/reference/amx/workloads.h`): `amx_gemm.c` measures the rows tagged `amx_hand` (the hand kernel needs multiples of 32; the `amx_fit` group holds the extra shapes its timing fit in `ablation.py` uses), `sgemm_sweep.c` the rows tagged `amx_sgemm`. `compare.py` shows `–` for an `amx` shape they don't measure.

**To do:** add layer shapes from real int8 models (MLPerf Tiny, a small transformer), with the source model and layer named in `why`.

## Real network layers (tag `layers`)

int8 inference, batch 1. Convolutions are lowered with im2col: **M = output pixels, N = output channels, K = kernel height × kernel width × input channels**. Fully connected layers at batch 1 are GEMVs (M = 1). Depthwise convolutions are not GEMMs (each channel has its own 3×3 filter) and are left out; on this core they would run as many K = 9, N = 1 matrix-vector products, or on the host.

| Group | Network | Input | Source |
| --- | --- | --- | --- |
| `kws_dscnn` | DS-CNN (small), keyword spotting | 49×10 MFCC | MLPerf Tiny v1.0 reference model (`mlcommons/tiny`, `benchmark/training/keyword_spotting`); Zhang et al., "Hello Edge", arXiv:1711.07128 |
| `vww_mbv1` | MobileNetV1, width 0.25, visual wake words | 96×96×3 | MLPerf Tiny (`benchmark/training/visual_wake_words`); Howard et al., arXiv:1704.04861 |
| `ic_resnet8` | ResNet-8, CIFAR-10 image classification | 32×32×3 | MLPerf Tiny (`benchmark/training/image_classification`) |
| `ad_fcae` | Fully connected autoencoder, anomaly detection (ToyADMOS) | 640 (5 frames × 128 mel bins) | MLPerf Tiny (`benchmark/training/anomaly_detection`) |
| `bert_tiny` | BERT-tiny: 2 layers, hidden 128, 2 heads, FFN 512, sequence 128 | 128 tokens | Turc et al., "Well-Read Students Learn Better", arXiv:1908.08962 |

BERT-tiny assumes int8 activations everywhere, including the softmax output in P·V. Q, K and V are one fused projection (N = 3 × 128).
