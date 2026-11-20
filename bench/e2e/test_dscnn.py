# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""DS-CNN end to end on the cycle model, bit-exact against the TFLite interpreter (bench/e2e)."""
import numpy as np
import pytest

import dscnn
import tflite_ref as tr


@pytest.fixture(scope="module")
def net():
    return tr.load()


def test_host_reference_matches_tflite(net):
    """The lowering and host kernels alone (numpy GEMM + requant) give every TFLite tensor."""
    for seed in net.seeds:
        x = net.golden(seed, net.inputs[0])
        for op in net.ops[:-1]:
            if op["kind"] in dscnn.ENGINE:
                g = tr.lower(net, op, x)
                y = tr.gemm_reference(g).reshape(g.out_shape)
            elif op["kind"] == "DEPTHWISE_CONV_2D":
                y = tr.depthwise(net, op, x)
            elif op["kind"] == "AVERAGE_POOL_2D":
                y = tr.average_pool(net, op, x)
            else:
                y = x.reshape(net.tensors[op["outputs"][0]]["shape"])
            assert np.array_equal(y, net.golden(seed, op["outputs"][0])), op["kind"]
            x = y


@pytest.mark.parametrize("tile,seed", [(8, 1), (8, 3), (4, 2)])
def test_network_on_the_core_is_bit_exact(net, tile, seed):
    rows, bad = dscnn.run(net, seed, tile)
    assert not bad
    assert [r["where"] for r in rows].count("core") == 6


def test_input_zero_point_padding(net):
    """The 10x4 SAME convolution pads im2col with the input zero point (83), not 0."""
    op = net.ops[0]
    g = tr.lower(net, op, net.golden(1, op["inputs"][0]))
    assert g.a.shape == (125, 40) and g.im2col_bytes == 125 * 40
    assert net.tensors[op["inputs"][0]]["zero_point"] == [83] and (g.a == 83).any()
