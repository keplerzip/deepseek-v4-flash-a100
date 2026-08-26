#!/usr/bin/env python3
"""Match target and DSpark cells and produce a decision-ready comparison."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path


def load(path: Path) -> dict[tuple[int, int, float], dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        (
            int(row["context_target"]),
            int(row["output_target"]),
            float(row["cache_hit_target"]),
        ): row
        for row in rows
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("dspark", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = load(args.target)
    dspark = load(args.dspark)
    keys = sorted(set(target) & set(dspark))
    comparisons = []
    for key in keys:
        left, right = target[key], dspark[key]
        if left["status"] != "complete" or right["status"] != "complete":
            continue
        target_decode = float(left["decode_tps_aggregate"])
        dspark_decode = float(right["decode_tps_aggregate"])
        target_ttft = float(left["ttft_ms_p95"])
        dspark_ttft = float(right["ttft_ms_p95"])
        target_hit = float(left["cache_hit_actual_p50"])
        dspark_hit = float(right["cache_hit_actual_p50"])
        comparisons.append(
            {
                "context": key[0],
                "output": key[1],
                "cache_hit_target": key[2],
                "decode_speedup": dspark_decode / target_decode,
                "ttft_ratio": dspark_ttft / target_ttft,
                "cache_hit_delta_pp": (dspark_hit - target_hit) * 100,
                "dspark_acceptance_rate": (
                    float(right["dspark_acceptance_rate"])
                    if right["dspark_acceptance_rate"]
                    else None
                ),
            }
        )
    full_match = len(keys) == len(target) == len(dspark) == 60
    complete_match = len(comparisons) == 60
    cache_preserved = complete_match and min(
        row["cache_hit_delta_pp"] for row in comparisons
    ) >= -1.0
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "target_csv": str(args.target),
        "dspark_csv": str(args.dspark),
        "matched_cells": len(keys),
        "complete_compared_cells": len(comparisons),
        "full_matrix_match": full_match,
        "cache_preservation_gate": cache_preserved,
        "median_decode_speedup": (
            statistics.median(row["decode_speedup"] for row in comparisons)
            if comparisons
            else None
        ),
        "median_ttft_ratio": (
            statistics.median(row["ttft_ratio"] for row in comparisons)
            if comparisons
            else None
        ),
        "worst_cache_hit_delta_pp": (
            min(row["cache_hit_delta_pp"] for row in comparisons)
            if comparisons
            else None
        ),
        "median_dspark_acceptance_rate": (
            statistics.median(
                row["dspark_acceptance_rate"]
                for row in comparisons
                if row["dspark_acceptance_rate"] is not None
            )
            if any(row["dspark_acceptance_rate"] is not None for row in comparisons)
            else None
        ),
        "decision_status": "awaiting_user_confirmation_and_24h_soak",
        "per_cell": comparisons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output)
    print(
        f"SCHEME_COMPARISON=PASS cells={len(comparisons)} "
        f"cache_gate={cache_preserved} output={args.output}"
    )
    return 0 if complete_match and cache_preserved else 1


if __name__ == "__main__":
    raise SystemExit(main())
