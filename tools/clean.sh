#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Clean recreatable build outputs, formal work directories, simulator artifacts,
# and temporary caches while preserving tracked files and private/ planning notes.
#
#   ./tools/clean.sh              # remove all build, formal, dist, and cache files
#   ./tools/clean.sh --dry-run    # preview paths and disk usage without deleting
#   ./tools/clean.sh --keep-orfs  # keep build/orfs_* physical-design runs
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

DRY_RUN=0
KEEP_ORFS=0

usage() {
  cat <<'EOF'
Usage: ./tools/clean.sh [OPTIONS]

Remove recreatable build outputs, formal verification directories, release
tarballs, compiled driver/benchmark binaries, and temporary caches.
Never touches tracked source/data files or private/.

Options:
  -n, --dry-run    Show what would be removed and its size without deleting
      --keep-orfs  Preserve build/orfs_native and build/orfs_docker directories
  -h, --help       Show this help message
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    --keep-orfs)
      KEEP_ORFS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

TARGETS=()

add_target() {
  local p="${1%/}"
  if [ -e "$p" ] || [ -L "$p" ]; then
    # Safety check: never delete any path tracked by git
    if GIT_CONFIG_GLOBAL=/dev/null git ls-files --error-unmatch "$p" >/dev/null 2>&1; then
      return
    fi
    TARGETS+=("$p")
  fi
}

# 1. Top-level build/ and dist/
if [ "$KEEP_ORFS" -eq 1 ]; then
  if [ -d build ]; then
    for entry in build/*; do
      [ -e "$entry" ] || continue
      case "$entry" in
        build/orfs_native|build/orfs_docker) ;;
        *) add_target "$entry" ;;
      esac
    done
  fi
else
  add_target build
fi
add_target dist

# 2. SymbiYosys formal work directories (verif/formal/)
for dir in \
  verif/formal/tq_fifo/ \
  verif/formal/tq_fifo_*/ \
  verif/formal/tq_core/ \
  verif/formal/tq_core_*/ \
  verif/formal/tq_axil_regs/ \
  verif/formal/tq_axil_regs_*/ \
  verif/formal/tq_xif_regs/ \
  verif/formal/tq_xif_regs_*/ \
  verif/formal/tq_axi_mem/ \
  verif/formal/tq_axi_mem_*/ \
  verif/formal/tq_cg/ \
  verif/formal/tq_cg_*/ \
  verif/formal/tq_mac_array/ \
  verif/formal/tq_mac_array_*/
do
  [ -d "$dir" ] && add_target "$dir"
done

# 3. C driver and AMX benchmark build outputs
for f in \
  sw/*.o \
  sw/*.a \
  bench/reference/amx/amx_peak \
  bench/reference/amx/amx_gemm \
  bench/reference/amx/sgemm_sweep
do
  add_target "$f"
done

# 4. Stray simulator outputs at root (outside build/ and private/)
for f in obj_dir sim_build results.xml .coverage htmlcov; do
  add_target "$f"
done

# 5. Python bytecode caches, pytest caches, egg-info, flow runs, and temp files
while IFS= read -r -d '' p; do
  add_target "${p#./}"
done < <(
  find . \
    \( -path ./build -o -path ./dist -o -path ./.git -o -path ./private -o -path ./.venv -o -path ./venv -o -path './verif/formal/tq_*' \) -prune \
    -o \( \
      -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name "*.egg-info" -o -name ".mypy_cache" -o -name ".ruff_cache" -o \( -path "./flow/*" -name "runs" \) \) -print0 -prune \
    \) \
    -o \( \
      -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" -o -name "*.vcd" -o -name "*.fst" -o -name "*.saif" -o -name "*.log" \) -print0 \
    \)
)

if [ "${#TARGETS[@]}" -eq 0 ]; then
  echo "[clean] Already clean — no temporary or build files found."
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[clean] Dry run — would remove ${#TARGETS[@]} path(s):"
  du -shc "${TARGETS[@]}" 2>/dev/null || true
  exit 0
fi

echo "[clean] Removing ${#TARGETS[@]} path(s)..."
du -shc "${TARGETS[@]}" 2>/dev/null | tail -n 1 | awk '{print "[clean] Reclaiming approximately " $1 "..."}'
rm -rf "${TARGETS[@]}"
echo "[clean] Done."
