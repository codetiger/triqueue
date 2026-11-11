#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Build a Chipyard Rocket SoC with the Tri-Queue attached (TriQueue.scala, TriQueueRocketConfig),
# simulate it in Verilator and run tq_gemm_test.c on the Rocket core: C = A x B on the Tri-Queue,
# checked against the CPU. Chipyard comes from bench/chipyard/setup.sh (Docker volume VOLUME);
# the first run also builds the bare-metal runtime (libgloss-htif).
#
#   bench/soc/chipyard/run.sh          # -> build/soc/<config>/ (simulator log, program, output)
set -euo pipefail

CHIPYARD_TAG=${CHIPYARD_TAG:-1.15.0}
CONFIG=${CONFIG:-TriQueueRocketConfig}
IMAGE=${IMAGE:-ubuntu:24.04}
VOLUME=${VOLUME:-triqueue-chipyard}
NAME=${NAME:-triqueue-chipyard-soc}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=$ROOT/build/soc/$CONFIG
mkdir -p "$OUT"

docker run --rm --name "$NAME" --platform linux/arm64 -v "$VOLUME:/work" -v "$ROOT:/tq:ro" -v "$OUT:/out" \
  -e TAG="$CHIPYARD_TAG" -e CONFIG="$CONFIG" -e MINIFORGE="${MINIFORGE:-}" "$IMAGE" bash -euo pipefail -c '
  source /tq/bench/chipyard/setup.sh
  if ! ls $RISCV/riscv64-unknown-elf/lib/libgloss_htif.a > /dev/null 2>&1; then
    ./scripts/build-toolchain-extra.sh riscv-tools -p $RISCV > /out/toolchain-extra.log 2>&1 \
      || { tail -30 /out/toolchain-extra.log; exit 1; }
  fi
  mkdir -p generators/chipyard/src/main/scala/triqueue
  cp /tq/bench/soc/chipyard/TriQueue.scala generators/chipyard/src/main/scala/triqueue/TriQueue.scala
  # tests/atexit-workaround.c: GCC 16 nano newlib dereferences a NULL _on_exit_args at exit
  riscv64-unknown-elf-gcc -O2 -mcmodel=medany -specs=htif_nano.specs -std=c99 -Wall -Werror \
      -Wl,--wrap=atexit -I/tq/sw /tq/bench/soc/chipyard/tq_gemm_test.c /tq/sw/tq.c \
      tests/atexit-workaround.c -o /out/tq_gemm_test.riscv
  make -C sims/verilator CONFIG=$CONFIG CONFIG_PACKAGE=chipyard.triqueue -j"$(nproc)" > /out/build.log 2>&1 || { tail -40 /out/build.log; exit 1; }
  make -C sims/verilator CONFIG=$CONFIG CONFIG_PACKAGE=chipyard.triqueue run-binary BINARY=/out/tq_gemm_test.riscv LOADMEM=1 \
      > /out/run.log 2>&1 || true
  grep -h "^tq:\|Assertion failed" /out/run.log | sort -u
  grep -q "^tq: PASS" /out/run.log
'
