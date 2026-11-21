# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""DS-CNN and ResNet-8 end to end on the cycle model, bit-exact against the TFLite interpreter
(bench/e2e)."""
import numpy as np
import pytest

import network
import tflite_ref as tr

NETS = {name: tr.load(name) for name in tr.NETS}


@pytest.mark.parametrize("name", tr.NETS)
def test_host_reference_matches_tflite(name):
    """The lowering and host kernels alone (numpy GEMM + requant) give every TFLite tensor, each
    op fed TFLite's own inputs."""
    net = NETS[name]
    for seed in net.seeds:
        for op in net.ops[:-1]:
            x = [net.golden(seed, i) for i in network.activations(net, op)]
            gold = net.golden(seed, op["outputs"][0])
            if op["kind"] in network.ENGINE:
                g = tr.lower(net, op, x[0])
                want, _ = tr.core_expected(op, g, gold)
                assert np.array_equal(tr.gemm_reference(g), want), op["kind"]
            else:
                assert np.array_equal(network.host_op(net, op, x), gold), op["kind"]


@pytest.mark.parametrize("name,tile,seed,core_ops", [("dscnn", 8, 1, 6), ("dscnn", 8, 3, 6), ("dscnn", 4, 2, 6),
                                                     ("resnet8", 8, 2, 10)])
def test_network_on_the_core_is_bit_exact(name, tile, seed, core_ops):
    rows, bad = network.run(NETS[name], seed, tile)
    assert not bad
    assert [r["where"] for r in rows].count("core") == core_ops


def test_fully_connected_rounding_difference():
    """ResNet-8, input 2: one classifier output is a tie after the core's first rounding (-106.499
    real), where TFLite's single-rounding FULLY_CONNECTED gives -82 and the core -83."""
    net = NETS["resnet8"]
    op = net.ops[14]
    g = tr.lower(net, op, net.golden(2, op["inputs"][0]))
    gold = net.golden(2, op["outputs"][0])
    want, diff = tr.core_expected(op, g, gold)
    assert diff == 1 and want[0, 9] == -83 and gold[0, 9] == -82
    assert np.array_equal(gold, tr.gemm_reference(g, "single"))


def test_input_zero_point_padding():
    """The 10x4 SAME convolution pads im2col with the input zero point (83), not 0."""
    net = NETS["dscnn"]
    op = net.ops[0]
    g = tr.lower(net, op, net.golden(1, op["inputs"][0]))
    assert g.a.shape == (125, 40) and g.im2col_bytes == 125 * 40
    assert net.tensors[op["inputs"][0]]["zero_point"] == [83] and (g.a == 83).any()


def test_residual_add_rescales_both_inputs():
    """ResNet-8's first residual add: inputs with different scales and zero points (-128 and 4),
    fused ReLU (act_min = zp_out = -128)."""
    net = NETS["resnet8"]
    op = net.ops[3]
    assert op["kind"] == "ADD" and op["options"]["activation"] == "relu"
    (s1,), (z1,) = net.q(op["inputs"][0])
    (s2,), (z2,) = net.q(op["inputs"][1])
    assert s1 != s2 and (z1, z2) == (-128, 4)
    y = tr.add(net, op, *(net.golden(1, i) for i in op["inputs"]))
    assert np.array_equal(y, net.golden(1, op["outputs"][0])) and y.min() == -128
