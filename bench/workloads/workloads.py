# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Shared GEMM workload list. Every benchmark (model, RTL, other designs) reads gemm.csv.

    from workloads import shapes
    for m, n, k in shapes("model"): ...
"""
import csv
from pathlib import Path

CSV = Path(__file__).with_name("gemm.csv")


def rows(tag: str | None = None) -> list[dict]:
    """All rows in file order, optionally only those carrying `tag`."""
    with open(CSV, newline="") as f:
        out = []
        for r in csv.DictReader(f):
            r["m"], r["n"], r["k"] = int(r["m"]), int(r["n"]), int(r["k"])
            r["tags"] = set(filter(None, r["tags"].split(";")))
            if tag is None or tag in r["tags"]:
                out.append(r)
        return out


def shapes(tag: str | None = None) -> list[tuple[int, int, int]]:
    return [(r["m"], r["n"], r["k"]) for r in rows(tag)]
