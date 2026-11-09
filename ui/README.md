<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Tri-Queue Interactive RTL-to-Silicon Showcase (`ui/`)

A single-page interactive hardware visualization pipeline connecting:
1. **Logical RTL Architecture (`tq_top`)** — Interactive block schematic with live bus handshakes, 102 integrated clock gates (`tq_cg`), SystemVerilog source inspector, and SymbiYosys formal verification badges.
2. **Cycle Dataflow & Tri-Queue Layout** — Cycle-accurate visualizer showing the 10-byte GEMM kernel (`LOOP_T`, `LOOP_K`), `QA` / `QB` operand FIFOs, `MAC_CMD_Q`, 8×8 outer-product beats (`64 × 9×9 → 18b`), variable-arity edge-tile quadrant folding (`8×8×1`, `8×4×2`, `4×8×2`, `4×4×4`), ping-pong `Bank X` / `Bank Y` accumulators, and the draggable 3-way overlap timeline.
3. **3D Silicon Die & Layer Stackup (`OpenROAD 6_final.def`)** — Hardware-accelerated WebGL 2.0 instanced 3D renderer of the routed **ASAP7 (7nm FinFET)** and **Sky130HD (130nm Planar)** layouts with 5 color lenses (`Modules`, `Metals`, `Cells`, `Activity`, `Clock`), Z-axis layer explosion, orientation compass, and module fly-to bookmarks.

## Local Development

Regenerate the UI data bundles from `model/triqueue_mod.py`, `build/orfs_native/.../6_final.def`, and `flow/records/`:

```bash
make ui-data      # or: python3 ui/pipeline/build_ui_data.py
```

Open `ui/index.html` directly in any browser, or serve locally:

```bash
make ui-serve     # serves http://localhost:8080
```

## GitHub Pages Deployment (`.github/workflows/pages.yml`)

The UI is automatically built and deployed to GitHub Pages via [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) whenever `ui/**`, `model/**`, or `flow/records/**` are pushed to `main` (or triggered manually via **Actions → pages → Run workflow**).

### One-Time Repository Setup
In your GitHub repository:
1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, select **GitHub Actions**.
