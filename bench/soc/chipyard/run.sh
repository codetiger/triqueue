#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Build a Chipyard Rocket SoC with the Tri-Queue attached (TriQueue.scala, TriQueueRocketConfig),
# simulate it in Verilator and run the bare-metal programs TESTS on the Rocket core:
#   tq_gemm_test   C = A x B (int32) on the Tri-Queue, checked against the CPU
#   tq_layer_test  real DS-CNN / ResNet-8 int8 layers through the epilogue, checked against
#                  TFLite's tensors (tq_layers.h, written here by export_layers.py; needs numpy)
# Chipyard comes from bench/chipyard/setup.sh (Docker volume VOLUME); the first run also builds
# the bare-metal runtime (libgloss-htif).
#
#   bench/soc/chipyard/run.sh                       # -> build/soc/<config>/ (logs run-<test>.log)
#   TESTS=tq_layer_test bench/soc/chipyard/run.sh   # one program
set -euo pipefail

CHIPYARD_TAG=${CHIPYARD_TAG:-1.15.0}
CONFIG=${CONFIG:-TriQueueRocketConfig}
IMAGE=${IMAGE:-ubuntu:24.04}
VOLUME=${VOLUME:-triqueue-chipyard}
NAME=${NAME:-triqueue-chipyard-soc}
TESTS=${TESTS:-"tq_gemm_test tq_layer_test"}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=$ROOT/build/soc/$CONFIG
mkdir -p "$OUT"
python3 "$HERE/export_layers.py" "$OUT/tq_layers.h"

docker run --rm --name "$NAME" --platform linux/arm64 -v "$VOLUME:/work" -v "$ROOT:/tq:ro" -v "$OUT:/out" \
  -e TAG="$CHIPYARD_TAG" -e CONFIG="$CONFIG" -e TESTS="$TESTS" -e MINIFORGE="${MINIFORGE:-}" "$IMAGE" bash -euo pipefail -c '
  source /tq/bench/chipyard/setup.sh
  if ! ls $RISCV/riscv64-unknown-elf/lib/libgloss_htif.a > /dev/null 2>&1; then
    ./scripts/build-toolchain-extra.sh riscv-tools -p $RISCV > /out/toolchain-extra.log 2>&1 \
      || { tail -30 /out/toolchain-extra.log; exit 1; }
  fi
  mkdir -p generators/chipyard/src/main/scala/triqueue
  cp /tq/bench/soc/chipyard/TriQueue.scala generators/chipyard/src/main/scala/triqueue/TriQueue.scala
  # tests/atexit-workaround.c: GCC 16 nano newlib dereferences a NULL _on_exit_args at exit
  for t in $TESTS; do
    riscv64-unknown-elf-gcc -O2 -mcmodel=medany -specs=htif_nano.specs -std=c99 -Wall -Werror \
        -Wl,--wrap=atexit -I/tq/sw -I/out /tq/bench/soc/chipyard/$t.c /tq/sw/tq.c \
        tests/atexit-workaround.c -o /out/$t.riscv
  done
  # The RTL is read at elaboration, which make does not track: regenerate when it changes.
  gen=sims/verilator/generated-src/chipyard.harness.TestHarness.$CONFIG
  sum=$(cd /tq && cat rtl/files.f $(cat rtl/files.f) bench/soc/chipyard/vsrc/tq_chipyard.sv bench/soc/chipyard/TriQueue.scala | sha256sum | cut -c1-16)
  [ "$(cat $gen.tq-sources 2>/dev/null)" = "$sum" ] || rm -rf $gen $gen.tq-sources
  make -C sims/verilator CONFIG=$CONFIG CONFIG_PACKAGE=chipyard.triqueue -j"$(nproc)" > /out/build.log 2>&1 || { tail -40 /out/build.log; exit 1; }
  echo $sum > $gen.tq-sources
  fail=0
  for t in $TESTS; do
    make -C sims/verilator CONFIG=$CONFIG CONFIG_PACKAGE=chipyard.triqueue run-binary BINARY=/out/$t.riscv LOADMEM=1 \
        > /out/run-$t.log 2>&1 || true
    echo "== $t"
    grep -h "^tq:\|Assertion failed" /out/run-$t.log | uniq
    grep -q "^tq: PASS" /out/run-$t.log || fail=1
  done
  exit $fail
'
