#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Build Chipyard Rocket SoCs with the Tri-Queue attached (TriQueue.scala: TriQueueRocketConfig,
# TriQueueFbusConfig, TriQueueOneIdConfig), simulate them in Verilator and run the bare-metal
# programs TESTS on the Rocket core:
#   tq_gemm_test   C = A x B (int32) on the Tri-Queue, checked against numpy's product
#                  (tq_gemm_ref.h, written here by export_gemm.py)
#   tq_layer_test  real DS-CNN / ResNet-8 int8 layers through the epilogue, checked against
#                  TFLite's tensors (tq_layers.h, written here by export_layers.py)
# Both headers need numpy on the host. Chipyard comes from bench/chipyard/setup.sh (Docker volume
# VOLUME); the first run also builds the bare-metal runtime (libgloss-htif). The simulators are
# built one after another (they share the Chipyard checkout), then every program on every
# configuration runs in parallel (each Verilator simulation is single-threaded).
#
#   bench/soc/chipyard/run.sh                       # TriQueueRocketConfig -> build/soc/<config>/
#   CONFIGS="TriQueueRocketConfig TriQueueFbusConfig TriQueueOneIdConfig" bench/soc/chipyard/run.sh
#   TESTS=tq_layer_test bench/soc/chipyard/run.sh   # one program
# Logs: build/soc/<config>/build.log and run-<program>.log.
set -euo pipefail

CHIPYARD_TAG=${CHIPYARD_TAG:-1.15.0}
CONFIGS=${CONFIGS:-${CONFIG:-TriQueueRocketConfig}}
IMAGE=${IMAGE:-ubuntu:24.04}
VOLUME=${VOLUME:-triqueue-chipyard}
NAME=${NAME:-triqueue-chipyard-soc}
TESTS=${TESTS:-"tq_gemm_test tq_layer_test"}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
SOC=$ROOT/build/soc
mkdir -p "$SOC/include" "$SOC/bin"
for c in $CONFIGS; do mkdir -p "$SOC/$c"; done
python3 "$HERE/export_layers.py" "$SOC/include/tq_layers.h"
python3 "$HERE/export_gemm.py" "$SOC/include/tq_gemm_ref.h"

docker run --rm --name "$NAME" --platform linux/arm64 -v "$VOLUME:/work" -v "$ROOT:/tq:ro" -v "$SOC:/soc" \
  -e TAG="$CHIPYARD_TAG" -e CONFIGS="$CONFIGS" -e TESTS="$TESTS" -e MINIFORGE="${MINIFORGE:-}" "$IMAGE" bash -euo pipefail -c '
  source /tq/bench/chipyard/setup.sh
  if ! ls $RISCV/riscv64-unknown-elf/lib/libgloss_htif.a > /dev/null 2>&1; then
    ./scripts/build-toolchain-extra.sh riscv-tools -p $RISCV > /soc/toolchain-extra.log 2>&1 \
      || { tail -30 /soc/toolchain-extra.log; exit 1; }
  fi
  mkdir -p generators/chipyard/src/main/scala/triqueue
  cp /tq/bench/soc/chipyard/TriQueue.scala generators/chipyard/src/main/scala/triqueue/TriQueue.scala
  # tests/atexit-workaround.c: GCC 16 nano newlib dereferences a NULL _on_exit_args at exit
  for t in $TESTS; do
    riscv64-unknown-elf-gcc -O2 -mcmodel=medany -specs=htif_nano.specs -std=c99 -Wall -Werror \
        -Wl,--wrap=atexit -I/tq/sw -I/soc/include /tq/bench/soc/chipyard/$t.c /tq/sw/tq.c \
        tests/atexit-workaround.c -o /soc/bin/$t.riscv
  done
  # The RTL is read at elaboration, which make does not track: regenerate when it changes.
  sum=$(cd /tq && cat rtl/files.f $(cat rtl/files.f) bench/soc/chipyard/vsrc/tq_chipyard.sv bench/soc/chipyard/TriQueue.scala | sha256sum | cut -c1-16)
  for c in $CONFIGS; do
    gen=sims/verilator/generated-src/chipyard.harness.TestHarness.$c
    [ "$(cat $gen.tq-sources 2>/dev/null)" = "$sum" ] || rm -rf $gen $gen.tq-sources
    echo "== build $c"
    make -C sims/verilator CONFIG=$c CONFIG_PACKAGE=chipyard.triqueue -j"$(nproc)" > /soc/$c/build.log 2>&1 \
      || { tail -40 /soc/$c/build.log; exit 1; }
    echo $sum > $gen.tq-sources
  done
  for c in $CONFIGS; do
    for t in $TESTS; do
      make -C sims/verilator CONFIG=$c CONFIG_PACKAGE=chipyard.triqueue run-binary BINARY=/soc/bin/$t.riscv \
          LOADMEM=1 > /soc/$c/run-$t.log 2>&1 &
    done
  done
  wait || true
  fail=0
  for c in $CONFIGS; do
    for t in $TESTS; do
      echo "== $c $t"
      grep -h "^tq:\|^tqbus:\|Assertion failed" /soc/$c/run-$t.log | uniq
      grep -q "^tq: PASS" /soc/$c/run-$t.log || fail=1
    done
  done
  exit $fail
'
