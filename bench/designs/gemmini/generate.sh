#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Generate the Gemmini 8x8 baseline's Verilog with Chipyard, inside a Linux container.
# Chipyard ships conda environments for Linux only (linux-64, linux-aarch64), so on an Apple
# Silicon Mac this runs an arm64 Ubuntu container at native speed.
#
#   bench/designs/gemmini/generate.sh            # setup (first run, ~1 h) + elaboration
#
# Chipyard (CHIPYARD_TAG) lives in the Docker volume VOLUME, set up by bench/chipyard/setup.sh
# (see there: case-sensitive volume, lean conda, firtool from Maven Central under Rosetta). Output: the generated collateral
# (all SystemVerilog modules, Gemmini's top module is `Gemmini`) in build/gemmini/<config>/.
set -euo pipefail

CHIPYARD_TAG=${CHIPYARD_TAG:-1.15.0}
CONFIG=${CONFIG:-Gemmini8x8RocketConfig}
IMAGE=${IMAGE:-ubuntu:24.04}
MINIFORGE=${MINIFORGE:-26.9.4-2}                     # Miniforge release (conda for the container)
VOLUME=${VOLUME:-triqueue-chipyard}
NAME=${NAME:-triqueue-chipyard-gen}                  # container name (docker stop $NAME)
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=$ROOT/build/gemmini/$CONFIG
mkdir -p "$OUT"

docker run --rm --name "$NAME" --platform linux/arm64 -v "$VOLUME:/work" -v "$ROOT:/tq:ro" -v "$OUT:/out" \
  -e TAG="$CHIPYARD_TAG" -e CONFIG="$CONFIG" -e MINIFORGE="$MINIFORGE" "$IMAGE" bash -euo pipefail -c '
  source /tq/bench/chipyard/setup.sh
  cp /tq/bench/designs/gemmini/Configs.scala generators/chipyard/src/main/scala/config/TriqueueGemminiConfigs.scala
  make -C sims/verilator verilog CONFIG=$CONFIG -j"$(nproc)"
  rsync -a --delete sims/verilator/generated-src/chipyard.harness.TestHarness.$CONFIG/gen-collateral/ /out/gen-collateral/
'
echo "[gemmini] $CONFIG: $(ls "$OUT/gen-collateral" | wc -l) files in $OUT/gen-collateral"
