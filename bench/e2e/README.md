# End-to-end networks: DS-CNN and ResNet-8

Real int8 networks run layer by layer on the Tri-Queue, every tensor bit-exact with the TFLite
interpreter's reference kernels. Both are MLPerf Tiny reference models (MLCommons, Apache-2.0)
with per-channel int8 quantization:

- **DS-CNN keyword spotting** (`kws_ref_model.tflite`): a 10×4 stride-2 convolution, four
  depthwise-separable blocks (3×3 depthwise + 1×1 pointwise, 64 channels), an average pool, a
  64→12 fully connected layer and softmax.
- **ResNet-8 image classification** (`pretrainedResnet_quant.tflite`, CIFAR-10): a 3×3
  convolution and three residual stages (two 3×3 convolutions each, the second and third at stride
  2 with a 1×1 stride-2 shortcut; 16, 32 and 64 channels), each ending in an add with a fused
  ReLU, then an average pool, a 64→10 fully connected layer and softmax.

| File | What |
| --- | --- |
| `export.py` | Runs a `.tflite` in the TFLite interpreter (reference kernels, no XNNPACK) and writes `<net>_golden.npz`: graph, op options, weights, quantization, and every tensor for 3 random int8 inputs. Needs `ai-edge-litert` and `tflite`, which are not in `requirements.txt` (setup in the script's docstring); fetches the model and checks its SHA-256. |
| `dscnn_golden.npz`, `resnet8_golden.npz` | The exported references (checked in, so nothing else needs TFLite). |
| `tflite_ref.py` | Lowers `CONV_2D` (im2col, padding with the input zero point) and `FULLY_CONNECTED` to a GEMM + the core's epilogue parameters (bias with the input zero point folded in, per-channel `M0`/shift from the scales, activation clamp); host kernels for depthwise, add, average pool and reshape. Transcribed from TFLite's reference integer kernels, with each kernel's rounding form (below). |
| `network.py` | Runs both networks: core layers on the cycle model (`run_gemm` with `MODE[9]`, the driver's walk choice), host layers in numpy, each op consuming this run's tensors; checks every tensor against TFLite and writes `results.md` (T = 8 and T = 4, ~3 min). |
| `test_e2e.py` | `make e2e-test`: the same checks as pytest (~20 s). |

The core layers also run in RTL simulation: `t8_dscnn`, `t4_dscnn`, `t8_resnet8` and `t4_resnet8`
in `verif/test_rtl.py` feed each layer's TFLite input through im2col into the core and compare the
core's C with TFLite's output tensor, cycle-exact with the model. The C driver's `tq_layer_*` API
builds the same memory image and program as `run_gemm` (`sw/test_tq.py`).

**Rounding.** TFLite's reference int8 kernels do not all round the same way. In the LiteRT build
used for the export (ai-edge-litert 2.1.6), `CONV_2D` uses the default
`MultiplyByQuantizedMultiplier` (rounding doubling high multiply, then rounding right shift) and
`FULLY_CONNECTED` the `TFLITE_SINGLE_ROUNDING` form (one 64-bit product, round half up). Over
2,000 random inputs per network, every output where the two forms differ follows that split
(`tflite_ref.ROUNDING`). The core has both (`MODE[10]` selects the single rounding), so each layer
runs with its kernel's form and every tensor equals TFLite's. With double rounding throughout,
about 0.3% (DS-CNN) and 1.4% (ResNet-8) of the classifier outputs would be one LSB off.
TFLite's optimized kernels round differently again (the optimized `CONV_2D` rounds once, and
XNNPACK matches neither form exactly).

What is not covered: softmax runs on the host (its input is checked), the host work is counted
rather than simulated, and the inputs are random rather than audio features or images
(bit-exactness and cycle counts do not depend on the data).
