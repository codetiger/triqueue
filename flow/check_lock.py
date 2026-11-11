#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Verify that floorplan utilization, PPA records, and 3D UI layouts match `flow/LAYOUT_LOCK.json`.

    python3 flow/check_lock.py
    python3 flow/check_lock.py --check-git-base origin/main

Enforces two invariants:
  1. Every locked silicon file (`flow/io_constraints.tcl`, `flow/constraint.sdc`,
     `flow/records/*/*.json`, and `ui/data/chip_*.js`) matches its recorded SHA-256 digest
     byte-for-byte, `flow/ppa.py` platform utilization/clock/thread settings and overrides match
     the lock manifest and records, and every record's `pj_per_mac` / `tops_per_w` / `utilization`
     is consistent with its `energy_j`, `useful_macs`, and `cycles`.
     (Derived files `flow/results.md` and `ui/data/traces_data.js` are verified by regeneration.)
  2. When `--check-git-base <ref>` is supplied (e.g. in CI), `<ref>:flow/LAYOUT_LOCK.json` must be
     readable, and any change to the locked silicon artifacts or utilization settings relative to
     `<ref>` MUST be accompanied by a strictly higher semantic `release_version` in
     `flow/LAYOUT_LOCK.json`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "flow" / "LAYOUT_LOCK.json"
SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_semver(ver: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(ver.strip())
    if not m:
        raise ValueError(f"Invalid release_version '{ver}'; expected 'vMAJOR.MINOR.PATCH'")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_chip_js(path: Path) -> dict:
    txt = path.read_text()
    idx = txt.find("{")
    if idx < 0:
        raise ValueError(f"Malformed JS bundle: {path}")
    return json.loads(txt[idx:].rstrip(";\n "))


def verify_lock(check_git_base: str | None = None) -> list[str]:
    errors: list[str] = []
    if not LOCK_PATH.exists():
        return [f"Missing lockfile {LOCK_PATH.relative_to(ROOT)}"]

    lock = json.loads(LOCK_PATH.read_text())
    try:
        cur_ver = parse_semver(lock.get("release_version", ""))
    except ValueError as exc:
        return [str(exc)]

    sys.path.insert(0, str(ROOT / "flow"))
    import ppa  # noqa: E402

    if ppa.ORFS_COMMIT != lock.get("orfs_commit"):
        errors.append(
            f"flow/ppa.py ORFS_COMMIT is '{ppa.ORFS_COMMIT}', locked as '{lock.get('orfs_commit')}'"
        )

    # Check platform floorplan utilization, clock, and thread settings in flow/ppa.py
    for plat, pmeta in lock.get("platforms", {}).items():
        if plat not in ppa.PLATFORMS:
            errors.append(f"Locked platform '{plat}' missing from flow/ppa.py PLATFORMS")
            continue
        actual_p = ppa.PLATFORMS[plat]
        unit_ps = 1000.0 if actual_p["unit"] == "ns" else 1.0
        cfg_name = pmeta.get("config", "t8_cpa")
        expected_util = ppa.reference_util(plat, cfg_name)
        expected_period_ps = ppa.reference_period(plat, cfg_name) * unit_ps
        if expected_util != pmeta["core_utilization_pct"]:
            errors.append(
                f"flow/ppa.py reference_util('{plat}', '{cfg_name}') is {expected_util}%, "
                f"locked as {pmeta['core_utilization_pct']}% in {lock['release_version']}"
            )
        if expected_period_ps != pmeta["clock_period_ps"]:
            errors.append(
                f"flow/ppa.py reference_period('{plat}', '{cfg_name}') is {expected_period_ps} ps, "
                f"locked as {pmeta['clock_period_ps']} ps in {lock['release_version']}"
            )
        if actual_p["jobs"] != pmeta["jobs"]:
            errors.append(
                f"flow/ppa.py PLATFORMS['{plat}']['jobs'] is {actual_p['jobs']}, "
                f"locked as {pmeta['jobs']}"
            )

    # Check exact set of committed PPA record files
    locked_files: dict[str, str] = lock.get("files", {})
    actual_records = {
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in (ROOT / "flow" / "records").glob("*/*.json")
    }
    locked_records = {k for k in locked_files if k.startswith("flow/records/")}
    for extra in sorted(actual_records - locked_records):
        errors.append(f"Unlocked PPA record present on disk: {extra}")

    # Check SHA-256 of every locked silicon file
    for rel_str, expected_sha in sorted(locked_files.items()):
        fpath = ROOT / rel_str
        if not fpath.exists():
            errors.append(f"Locked file missing: {rel_str}")
            continue
        actual_sha = sha256_file(fpath)
        if actual_sha != expected_sha:
            errors.append(
                f"SHA-256 mismatch on locked file {rel_str}: "
                f"expected {expected_sha[:12]}..., got {actual_sha[:12]}... "
                f"(bump release version via flow/release_layout.py to modify)"
            )

    # Verify internal arithmetic consistency and overrides of every PPA record
    for rel_str in sorted(locked_records):
        fpath = ROOT / rel_str
        if not fpath.exists():
            continue
        rec = json.loads(fpath.read_text())
        plat = rec.get("platform", "")
        cfg_name = rec.get("config", "")
        t = rec.get("params", {}).get("T", 0)
        if plat in ppa.PLATFORMS:
            unit_ps = 1000.0 if ppa.PLATFORMS[plat]["unit"] == "ns" else 1.0
            exp_period_ps = ppa.reference_period(plat, cfg_name) * unit_ps
            exp_util = ppa.reference_util(plat, cfg_name)
            if rec.get("clock_period_ps") != exp_period_ps:
                errors.append(
                    f"{rel_str}: clock_period_ps is {rec.get('clock_period_ps')}, "
                    f"expected {exp_period_ps} from flow/ppa.py"
                )
            if rec.get("core_utilization_pct") != exp_util:
                errors.append(
                    f"{rel_str}: core_utilization_pct is {rec.get('core_utilization_pct')}, "
                    f"expected {exp_util} from flow/ppa.py"
                )
        for wl, p in rec.get("power", {}).items():
            shape = p.get("shape", [0, 0, 0])
            macs = p.get("useful_macs", 0)
            cycles = p.get("cycles", 0)
            energy_j = p.get("energy_j", 0.0)
            if len(shape) != 3 or shape[0] * shape[1] * shape[2] != macs or macs <= 0 or cycles <= 0 or energy_j <= 0:
                errors.append(f"{rel_str} [{wl}]: invalid shape/useful_macs/cycles/energy_j")
                continue
            exp_util_w = macs / (t * t * cycles)
            exp_pj = energy_j / macs * 1e12
            exp_tw = 2.0 * macs / energy_j / 1e12
            if not math.isclose(p.get("utilization", 0.0), exp_util_w, rel_tol=1e-9):
                errors.append(f"{rel_str} [{wl}]: utilization {p.get('utilization')} != {exp_util_w}")
            if not math.isclose(p.get("pj_per_mac", 0.0), exp_pj, rel_tol=1e-9):
                errors.append(f"{rel_str} [{wl}]: pj_per_mac {p.get('pj_per_mac')} != {exp_pj}")
            if not math.isclose(p.get("tops_per_w", 0.0), exp_tw, rel_tol=1e-9):
                errors.append(f"{rel_str} [{wl}]: tops_per_w {p.get('tops_per_w')} != {exp_tw}")

    # Check internal consistency of 3D silicon layout bundles against PPA records & lock
    for plat in ("asap7", "sky130hd"):
        if plat not in lock.get("platforms", {}):
            continue
        pmeta = lock["platforms"][plat]
        chip_path = ROOT / "ui" / "data" / f"chip_{plat}.js"
        if not chip_path.exists():
            continue
        chip = parse_chip_js(chip_path)
        if chip.get("release_version") != lock["release_version"]:
            errors.append(
                f"chip_{plat}.js release_version '{chip.get('release_version')}' != "
                f"LOCK '{lock['release_version']}'"
            )
        if chip.get("def_sha256") != pmeta.get("def_sha256"):
            errors.append(f"chip_{plat}.js def_sha256 does not match LOCK platforms.{plat}.def_sha256")
        if chip.get("cell_units") != "dbu":
            errors.append(f"chip_{plat}.js must use exact 1:1 integer DBU coordinates (cell_units='dbu')")
        rec = chip.get("record", {})
        if rec.get("core_utilization_pct") != pmeta["core_utilization_pct"]:
            errors.append(
                f"chip_{plat}.js record core_utilization_pct ({rec.get('core_utilization_pct')}%) != "
                f"LOCK ({pmeta['core_utilization_pct']}%)"
            )
        if rec.get("drc_errors", 1) != 0:
            errors.append(f"chip_{plat}.js has non-zero DRC errors: {rec.get('drc_errors')}")
        verif = chip.get("verification", {})
        fill_pct = verif.get("core_row_fill_pct", 0.0)
        if fill_pct < 99.0:
            errors.append(
                f"chip_{plat}.js core_row_fill_pct is {fill_pct}% (< 99.0%); layout is not 100% row-filled"
            )

    # Optional Git base comparison: if any locked artifact or lockfile changed vs base, version MUST increase
    if check_git_base and not errors:
        res = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{check_git_base}:flow/LAYOUT_LOCK.json"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0 or not res.stdout.strip():
            errors.append(
                f"Cannot read {check_git_base}:flow/LAYOUT_LOCK.json "
                f"(git show exited {res.returncode}: {res.stderr.strip() or 'empty output'})"
            )
        else:
            base_lock = json.loads(res.stdout)
            base_ver = parse_semver(base_lock["release_version"])
            if lock != base_lock and cur_ver <= base_ver:
                errors.append(
                    f"Locked layout/PPA artifacts changed relative to {check_git_base}, "
                    f"but release_version '{lock['release_version']}' was not incremented above "
                    f"'{base_lock['release_version']}'"
                )

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check-git-base", help="git ref (e.g. origin/main) to enforce semver bump on changes")
    args = ap.parse_args()

    errors = verify_lock(check_git_base=args.check_git_base)
    if errors:
        print("[lock-check] FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    lock = json.loads(LOCK_PATH.read_text())
    plats = ", ".join(
        f"{p}={m['core_utilization_pct']}% ({m['placement_density']*100:.1f}% post-route)"
        for p, m in lock.get("platforms", {}).items()
    )
    print(f"[lock-check] OK — release {lock['release_version']} locked ({plats}, {len(lock['files'])} files verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
