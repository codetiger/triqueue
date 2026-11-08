#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Chipyard inside an arm64 Ubuntu container (sourced by bench/designs/gemmini/generate.sh and
# bench/soc/chipyard/run.sh; the repository is mounted at /tq, the Docker volume at /work).
# Leaves Chipyard TAG in /work/chipyard-$TAG with its environment sourced ($CY set).
#
# - Chipyard and conda live in the volume: its file system is case-sensitive, which some conda
#   packages (kernel-headers) need, unlike a macOS bind mount.
# - Lean conda environment; no RISC-V toolchain collateral (run.sh builds it when needed),
#   FireSim or FireMarshal.
# - CIRCT publishes no Linux arm64 build of firtool, and Chipyard would build LLVM from source.
#   Instead firtool $FIRTOOL (Chipyard's pinned version, conda-reqs/circt.json) comes from Maven
#   Central's llvm-firtool jar (linux-x64) and runs under Docker Desktop's Rosetta x86 emulation,
#   with the amd64 C runtime installed by Ubuntu multiarch.
set -euo pipefail
: "${TAG:?}"
MINIFORGE=${MINIFORGE:-26.9.4-2}
export DEBIAN_FRONTEND=noninteractive

dpkg --add-architecture amd64
sed -i 's/^Types: deb$/Types: deb\nArchitectures: arm64/' /etc/apt/sources.list.d/ubuntu.sources
printf 'Types: deb\nURIs: http://archive.ubuntu.com/ubuntu/\nSuites: noble noble-updates noble-security\nComponents: main universe\nArchitectures: amd64\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' \
  > /etc/apt/sources.list.d/amd64.sources
apt-get update -qq
apt-get install -y -qq git curl ca-certificates make build-essential python3 file rsync bc bzip2 unzip \
    libc6:amd64 libstdc++6:amd64 zlib1g:amd64 > /dev/null
git config --global --add safe.directory '*'

CY=/work/chipyard-$TAG
if [ ! -d $CY/.git ]; then
  git clone -q --branch $TAG https://github.com/ucb-bar/chipyard.git $CY
fi
[ "$(git -C $CY describe --tags --exact-match)" = "$TAG" ]
cd $CY

if [ ! -x /work/conda/bin/conda ]; then
  if curl -fsSL --retry 3 --retry-delay 20 --retry-all-errors -o /tmp/mf.sh \
      https://github.com/conda-forge/miniforge/releases/download/$MINIFORGE/Miniforge3-$MINIFORGE-Linux-aarch64.sh; then
    bash /tmp/mf.sh -b -p /work/conda > /dev/null
  else
    # GitHub release downloads unavailable: bootstrap conda from conda-forge with micromamba
    curl -fsSL --retry 5 --retry-all-errors https://micro.mamba.pm/api/micromamba/linux-aarch64/latest \
      | tar -xj -C /tmp bin/micromamba
    /tmp/bin/micromamba create -y -q -p /work/conda -c conda-forge python=3.12 conda > /dev/null
  fi
fi
set +u                       # conda's and Chipyard's activation scripts read unset variables
source /work/conda/etc/profile.d/conda.sh

if [ ! -f /work/.setup-$TAG ]; then
  if [ -f $CY/env.sh ] && [ -d $CY/.conda-env ]; then
    echo "[setup] resuming: conda environment and submodules are in place"
  else
    # steps 1 conda env, 2 submodules, 5 precompile
    ./build-setup.sh --use-lean-conda --skip-toolchain --skip-ctags --skip-firesim --skip-marshal \
        --skip-circt --skip-clean
  fi
  touch /work/.setup-$TAG
fi
source $CY/env.sh
set -u

FIRTOOL=$(python3 -c 'import json; print(json.load(open("conda-reqs/circt.json"))["version"].split("-")[1])')
if [ "$(firtool --version 2>/dev/null | grep -oE 'CIRCT firtool-[0-9.]+' | cut -d- -f2)" != "$FIRTOOL" ]; then
  curl -fsSL --retry 5 --retry-all-errors -o /tmp/firtool.jar \
    https://repo1.maven.org/maven2/org/chipsalliance/llvm-firtool/$FIRTOOL/llvm-firtool-$FIRTOOL.jar
  unzip -o -q -j /tmp/firtool.jar "org.chipsalliance/llvm-firtool/linux-x64/bin/*" -d $RISCV/bin
  chmod +x $RISCV/bin/firtool $RISCV/bin/om-linker
fi
echo "[setup] Chipyard $TAG, $(firtool --version | grep -oE 'CIRCT firtool-[0-9.]+')"
