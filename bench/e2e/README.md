# End-to-end network: DS-CNN keyword spotting

A real int8 network run layer by layer on the Tri-Queue, every tensor bit-exact with the TFLite
interpreter. The network is MLPerf Tiny's keyword-spotting DS-CNN (`kws_ref_model.tflite`,
MLCommons, Apache-2.0): a 10×4 stride-2 convolution, four depthwise-separable blocks (3×3
depthwise + 1×1 pointwise, 64 channels), an average pool, a 64→12 fully connected layer and
softmax, with per-channel int8 quantization.

| File | What |
| --- | --- |
| `export_dscnn.py` | Runs the `.tflite` in the TFLite interpreter (reference kernels, no XNNPACK) and writes `dscnn_golden.npz`: graph, op options, weights, quantization, and every tensor for 3 random int8 inputs. Needs `ai-edge-litert` and `tflite`, which are not in `requirements.txt` (setup in the script's docstring); fetches the model and checks its SHA-256. |
| `dscnn_golden.npz` | The exported reference (checked in, so nothing else needs TFLite). |
| `tflite_ref.py` | Lowers `CONV_2D` (im2col, padding with the input zero point) and `FULLY_CONNECTED` to a GEMM + the core's epilogue parameters (bias with the input zero point folded in, per-channel `M0`/shift from the scales, activation clamp); host kernels for depthwise, average pool and reshape. Transcribed from TFLite's reference integer kernels. |
| `dscnn.py` | Runs the network: core layers on the cycle model (`run_gemm` with `MODE[9]`, the driver's walk choice), host layers in numpy, each consuming this run's previous output; checks every tensor against TFLite and writes `results.md` (T = 8 and T = 4). |
| `test_dscnn.py` | `make e2e-test`: the same checks as pytest. |

The core layers also run in RTL simulation: `t8_dscnn` and `t4_dscnn` in `verif/test_rtl.py` feed
each layer's TFLite input through im2col into the core and compare the core's C with TFLite's
output tensor, cycle-exact with the model. The C driver's `tq_layer_*` API builds the same memory
image and program as `run_gemm` (`sw/test_tq.py`).

What is not covered: softmax runs on the host (its input is checked), the host work is counted
rather than simulated, and the inputs are random rather than audio features (bit-exactness and
cycle counts do not depend on the data).
