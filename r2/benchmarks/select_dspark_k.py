#!/usr/bin/env python3
"""Summarize the k=1/3/5/7 DSpark screen without silently fixing a winner."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path


def summarize(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "available": False, "eligible": False}
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    complete = [row for row in rows if row["status"] == "complete"]

    def values(key: str) -> list[float]:
        return [float(row[key]) for row in complete if row.get(key, "")]

    hit_deltas = [
        float(row["cache_hit_actual_p50"]) - float(row["cache_hit_target"])
        for row in complete
        if row.get("cache_hit_actual_p50", "")
    ]
    all_complete = bool(rows) and len(complete) == len(rows)
    eligible = all_complete and bool(hit_deltas) and min(hit_deltas) >= -0.01
    decode = values("decode_tps_aggregate")
    ttft = values("ttft_ms_p95")
    acceptance = values("dspark_acceptance_rate")
    return {
        "path": str(path),
        "available": True,
        "cells": len(rows),
        "complete": len(complete),
        "failed": sum(row["status"] == "failed" for row in rows),
        "pending": sum(row["status"] == "pending" for row in rows),
        "worst_cache_delta_pp": min(hit_deltas) * 100 if hit_deltas else None,
        "median_decode_tps_aggregate": statistics.median(decode) if decode else None,
        "median_ttft_p95_ms": statistics.median(ttft) if ttft else None,
        "median_acceptance_rate": statistics.median(acceptance) if acceptance else None,
        "eligible": eligible,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for k in (1, 3, 5, 7):
        parser.add_argument(f"--k{k}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summaries = {str(k): summarize(getattr(args, f"k{k}")) for k in (1, 3, 5, 7)}
    eligible = [k for k, summary in summaries.items() if summary["eligible"]]
    leader = None
    if eligible:
        leader = max(
            eligible,
            key=lambda k: summaries[k]["median_decode_tps_aggregate"],
        )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "profiles": summaries,
        "provisional_decode_leader": int(leader) if leader is not None else None,
        "configured_production_candidate": 7,
        "decision_status": "awaiting_user_confirmation",
        "selection_note": (
            "Leader ranks median aggregate decode only. Final selection must also compare "
            "TTFT, acceptance, correctness, cache preservation and 24-hour stability."
        ),
        "gate": "all screen cells complete and worst cache shortfall <= 1 percentage point",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output)
    status = "PASS" if leader is not None else "FAIL"
    print(f"DSPARK_K_SCREEN={status} leader={leader} output={args.output}")
    return 0 if leader is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
