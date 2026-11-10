<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Tri-Queue Interactive RTL-to-Silicon Showcase (`ui/`)

A single-page interactive hardware visualization pipeline connecting:
1. **Logical RTL Architecture (`tq_top`)** — Interactive block schematic with live bus handshakes, the clock-gating cells (`tq_cg`), SystemVerilog source inspector, and SymbiYosys formal verification badges.
2. **Cycle Dataflow & Tri-Queue Layout** — Cycle-accurate visualizer showing the 10-byte GEMM kernel (`LOOP_T`, `LOOP_K`), `QA` / `QB` operand FIFOs, `MAC_CMD_Q`, 8×8 outer-product beats (`64 × 9×9 → 17b`), variable-arity edge-tile quadrant folding (`8×8×1`, `8×4×2`, `4×8×2`, `4×4×4`), ping-pong `Bank X` / `Bank Y` accumulators, and the draggable 3-way overlap timeline.
3. **3D Silicon Die & Layer Stackup (`OpenROAD 6_final.def`)** — Hardware-accelerated WebGL 2.0 instanced 3D renderer of the routed **ASAP7 (7nm FinFET)** and **Sky130HD (130nm Planar)** layouts with 100% 1:1 integer DBU standard-cell & row-fill placement fidelity, 5 color lenses (`Modules`, `Metals`, `Cells`, `Activity`, `Clock`), Z-axis layer explosion, orientation compass, and module fly-to bookmarks.

## Deterministic & Release-Locked Data Pipeline

- **Routine trace & PPA record bundling** (`ui/data/traces_data.js`, `ui/data/ppa_records.js` — never modifies `chip_*.js`):
  ```bash
  make ui-data      # or: python3 ui/pipeline/build_ui_data.py
  ```
- **Silicon 3D layout bundles** (`ui/data/chip_asap7.js`, `ui/data/chip_sky130hd.js`) are extracted 1:1 from `6_final.def` + PDK LEFs + `6_final.v` (`ui/pipeline/extract_layout.py`) **only** when publishing a new versioned layout release via `python3 flow/release_layout.py --version vX.Y.Z`, and are cryptographically locked by SHA-256 in [`flow/LAYOUT_LOCK.json`](../flow/LAYOUT_LOCK.json) (enforced by `make lock-check`, CI, and GitHub Pages).

Open `ui/index.html` directly in any browser, or serve locally:

```bash
make ui-serve     # serves http://localhost:8080
```

## GitHub Pages Deployment (`.github/workflows/pages.yml`)

GitHub Pages verifies `python flow/check_lock.py` and deploys the exact committed `ui/data/*.js` artifacts verbatim without regenerating or mutating layout data on the runner.

### One-Time Repository Setup
In your GitHub repository:
1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, select **GitHub Actions**.
