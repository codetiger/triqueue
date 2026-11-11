#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Publish and lock a versioned Silicon Layout & PPA Release in `flow/LAYOUT_LOCK.json`.

    python3 flow/release_layout.py --version v1.0.0 --init          # initial lock creation
    python3 flow/release_layout.py --version v1.0.1 --records-only  # update lock from committed records/layouts
    python3 flow/release_layout.py --version v1.1.0                 # full native ORFS re-extraction

Enforces:
  1. `release_version` must strictly advance above the existing `flow/LAYOUT_LOCK.json` version
     (unless `--init` when creating the lock for the first time).
  2. `rtl/` must have no uncommitted changes (unless `--force`).
  3. Extracts 100%-exact 1:1 DBU layouts (`chip_asap7.js`, `chip_sky130hd.js`) from `6_final.def`
     (or re-stamps existing locked layouts with `--records-only`), rewrites deterministic
     `flow/results.md` and `ui/data/traces_data.js`, writes SHA-256 digests of silicon artifacts
     to `flow/LAYOUT_LOCK.json`, and self-verifies via `flow/check_lock.py`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flow"))
sys.path.insert(0, str(ROOT / "ui" / "pipeline"))

import check_lock  # noqa: E402
import ppa  # noqa: E402
import results as flow_results  # noqa: E402


def git_out(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True, help="new semantic version tag (e.g. v1.0.1)")
    ap.add_argument("--config", default="t8_cpa", help="reference config rendered in 3D UI (default: t8_cpa)")
    ap.add_argument("--init", action="store_true", help="allow creating or re-initializing the initial version")
    ap.add_argument("--records-only", action="store_true", help="update lock from committed flow/records/ and ui/data/chip_*.js")
    ap.add_argument("--force", action="store_true", help="allow dirty rtl/ working tree")
    args = ap.parse_args()

    new_ver = check_lock.parse_semver(args.version)
    lock_path = check_lock.LOCK_PATH
    if lock_path.exists() and not args.init:
        old_lock = json.loads(lock_path.read_text())
        old_ver = check_lock.parse_semver(old_lock["release_version"])
        if new_ver <= old_ver:
            sys.exit(
                f"[release-layout] Refusing: new version {args.version} must be strictly greater "
                f"than locked version {old_lock['release_version']}"
            )

    if git_out("status", "--porcelain", "--", "rtl") and not args.force:
        sys.exit("[release-layout] rtl/ has uncommitted changes; commit them first (or pass --force)")

    import extract_layout  # noqa: E402

    platforms_meta = {}
    if args.records_only:
        flow_results.write_results()
        for plat in ("asap7", "sky130hd"):
            chip_path = ROOT / "ui" / "data" / f"chip_{plat}.js"
            data = check_lock.parse_chip_js(chip_path)
            data["release_version"] = args.version
            dst = extract_layout.write_chip_js(plat, data)
            v = data["verification"]
            rec = json.loads((ROOT / "flow" / "records" / plat / f"{args.config}.json").read_text())
            print(
                f"[release-layout] (records-only) {plat}/{args.config} -> {dst.relative_to(ROOT)}: "
                f"util={rec['core_utilization_pct']}%, post-route={rec['placement_density']*100:.1f}%, "
                f"{v['active_stdcells_exported']:,} stdcells + {v['physical_fill_spans_exported']:,} fill spans "
                f"({v['core_row_fill_pct']:.2f}% row fill)"
            )
            platforms_meta[plat] = {
                "config": args.config,
                "core_utilization_pct": ppa.PLATFORMS[plat]["util"],
                "clock_period_ps": rec["clock_period_ps"],
                "jobs": ppa.PLATFORMS[plat]["jobs"],
                "placement_density": rec["placement_density"],
                "cell_area_um2": rec["cell_area_um2"],
                "core_area_um2": rec["core_area_um2"],
                "fmax_hz": rec["fmax_hz"],
                "drc_errors": rec["drc_errors"],
                "rtl_commit": rec["rtl_commit"],
                "def_sha256": data["def_sha256"],
                "core_row_fill_pct": v["core_row_fill_pct"],
            }
    else:
        # 1. Collect PPA records for reference config and regenerate flow/results.md
        for plat in ("asap7", "sky130hd"):
            flow_results.collect(plat, args.config, force=args.force)
        flow_results.write_results()

        # 2. Extract 100%-exact 1:1 silicon layouts from build/orfs_native/ for asap7 and sky130hd
        for plat in ("asap7", "sky130hd"):
            data = extract_layout.extract_exact_layout(plat, args.config, release_version=args.version)
            dst = extract_layout.write_chip_js(plat, data)
            v = data["verification"]
            rec = data["record"]
            print(
                f"[release-layout] {plat}/{args.config} -> {dst.relative_to(ROOT)}: "
                f"util={rec['core_utilization_pct']}%, post-route={rec['placement_density']*100:.1f}%, "
                f"{v['active_stdcells_exported']:,} stdcells + {v['physical_fill_spans_exported']:,} fill spans "
                f"({v['core_row_fill_pct']:.2f}% row fill)"
            )
            platforms_meta[plat] = {
                "config": args.config,
                "core_utilization_pct": ppa.PLATFORMS[plat]["util"],
                "clock_period_ps": rec["clock_period_ps"],
                "jobs": ppa.PLATFORMS[plat]["jobs"],
                "placement_density": rec["placement_density"],
                "cell_area_um2": rec["cell_area_um2"],
                "core_area_um2": rec["core_area_um2"],
                "fmax_hz": rec["fmax_hz"],
                "drc_errors": rec["drc_errors"],
                "rtl_commit": rec["rtl_commit"],
                "def_sha256": data["def_sha256"],
                "core_row_fill_pct": v["core_row_fill_pct"],
            }

    # 3. Regenerate deterministic UI scenario traces
    rc = subprocess.run([sys.executable, str(ROOT / "ui" / "pipeline" / "build_ui_data.py")]).returncode
    if rc != 0:
        sys.exit(f"[release-layout] build_ui_data.py failed with exit {rc}")

    # 4. Compute SHA-256 hashes of all locked silicon files
    tracked_paths = [
        Path("flow/constraint.sdc"),
        Path("flow/io_constraints.tcl"),
        *sorted(p.relative_to(ROOT) for p in (ROOT / "flow" / "records").glob("*/*.json")),
        Path("ui/data/chip_asap7.js"),
        Path("ui/data/chip_sky130hd.js"),
    ]
    files_sha = {
        str(rel).replace("\\", "/"): check_lock.sha256_file(ROOT / rel)
        for rel in tracked_paths
    }

    rtl_commits = sorted({m["rtl_commit"] for m in platforms_meta.values()})
    lock_doc = {
        "release_version": args.version,
        "orfs_commit": ppa.ORFS_COMMIT,
        "rtl_commit": rtl_commits[0] if len(rtl_commits) == 1 else git_out("log", "-1", "--format=%h", "--", "rtl"),
        "platforms": platforms_meta,
        "files": files_sha,
    }
    lock_path.write_text(json.dumps(lock_doc, indent=2) + "\n")
    print(f"[release-layout] Wrote {lock_path.relative_to(ROOT)} ({args.version})")

    # 5. Self-verify lock
    errs = check_lock.verify_lock()
    if errs:
        for e in errs:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 1
    print(f"[release-layout] Verified {args.version} lock successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
