#!/usr/bin/env python3
"""Apply the R2 cache preservation gate and make a provisional recommendation."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def summarize(path: Path) -> dict:
    rows = load(path)
    complete = [row for row in rows if row["status"] == "complete"]
    hit_deltas = [
        (float(row["cache_hit_actual_p50"]) - float(row["cache_hit_target"]))
        for row in complete
        if row["cache_hit_actual_p50"]
    ]
    hits = [
        float(row["cache_hit_actual_p50"])
        for row in complete
        if row["cache_hit_actual_p50"]
    ]
    ttfts = [
        float(row["ttft_ms_p95"])
        for row in complete
        if row["ttft_ms_p95"]
    ]
    all_complete = len(complete) == len(rows) and bool(rows)
    return {
        "path": str(path),
        "cells": len(rows),
        "complete": len(complete),
        "failed": sum(row["status"] == "failed" for row in rows),
        "pending": sum(row["status"] == "pending" for row in rows),
        "all_complete": all_complete,
        "worst_target_delta_pp": min(hit_deltas) * 100 if hit_deltas else None,
        "median_actual_hit_rate": statistics.median(hits) if hits else None,
        "median_ttft_p95_ms": statistics.median(ttfts) if ttfts else None,
        "target_gate_pass": all_complete and bool(hit_deltas) and min(hit_deltas) >= -0.01,
    }


def complete_hit_map(path: Path) -> dict[tuple[int, int, float], float]:
    rows = load(path)
    return {
        (
            int(row["context_target"]),
            int(row["output_target"]),
            float(row["cache_hit_target"]),
        ): float(row["cache_hit_actual_p50"])
        for row in rows
        if row["status"] == "complete" and row["cache_hit_actual_p50"]
    }


def worst_paired_delta_pp(
    baseline: dict[tuple[int, int, float], float],
    candidate: dict[tuple[int, int, float], float],
) -> float | None:
    if not baseline or set(candidate) != set(baseline):
        return None
    return min((candidate[key] - baseline[key]) * 100 for key in baseline)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--zero", type=Path, required=True)
    parser.add_argument("--retention-32768", dest="retention_32768", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summaries = {
        "legacy": summarize(args.legacy),
        "zero": summarize(args.zero),
        "32768": summarize(args.retention_32768),
    }
    hit_maps = {
        "legacy": complete_hit_map(args.legacy),
        "zero": complete_hit_map(args.zero),
        "32768": complete_hit_map(args.retention_32768),
    }
    legacy_hits = hit_maps["legacy"]
    eligible: list[str] = []
    for name, summary in summaries.items():
        worst_legacy_delta_pp = worst_paired_delta_pp(legacy_hits, hit_maps[name])
        preserves_legacy = (
            summaries["legacy"]["all_complete"]
            and summary["all_complete"]
            and worst_legacy_delta_pp is not None
            and worst_legacy_delta_pp >= -1.0
        )
        summary["legacy_paired_cells"] = len(hit_maps[name])
        summary["worst_legacy_delta_pp"] = worst_legacy_delta_pp
        summary["preserves_legacy_within_1pp"] = preserves_legacy
        summary["eligible"] = summary["target_gate_pass"] and preserves_legacy
        if summary["eligible"]:
            eligible.append(name)

    recommendation = None
    reason = "no profile passed correctness and cache-preservation gates"
    if eligible:
        fastest = min(eligible, key=lambda name: summaries[name]["median_ttft_p95_ms"])
        fastest_ttft = summaries[fastest]["median_ttft_p95_ms"]
        zero_ttft = summaries["zero"]["median_ttft_p95_ms"]
        if "zero" in eligible and zero_ttft <= fastest_ttft * 1.02:
            recommendation = "zero"
            reason = "current upstream policy is within 2% of the fastest eligible TTFT"
        else:
            recommendation = fastest
            reason = "lowest median P95 TTFT among profiles preserving every legacy cache-hit cell"

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "profiles": summaries,
        "recommended_profile": recommendation,
        "reason": reason,
        "decision_status": (
            "provisional_requires_24h_stability" if recommendation else "rejected"
        ),
        "gates": {
            "all_cells_complete": True,
            "target_hit_shortfall_max_percentage_points": 1.0,
            "per_cell_legacy_hit_regression_max_percentage_points": 1.0,
            "upstream_zero_near_fastest_tolerance_percent": 2.0,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output)
    print(
        f"CACHE_PROFILE_DECISION={'PASS' if recommendation else 'FAIL'} "
        f"recommended={recommendation} output={args.output}"
    )
    return 0 if recommendation else 1


if __name__ == "__main__":
    raise SystemExit(main())
