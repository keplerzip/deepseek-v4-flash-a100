#!/usr/bin/env python3
"""Compare matching target-only and DSpark benchmark result groups."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def group_map(data: dict[str, Any]) -> dict[tuple[int, int, int], dict[str, Any]]:
    result = {}
    for group in data.get("groups", []):
        key = (
            int(group["prompt_target_tokens"]),
            int(group["output_tokens"]),
            int(group["concurrency"]),
        )
        result[key] = group.get("summary", {})
    return result


def ratio(numerator: Any, denominator: Any) -> float | None:
    if (
        isinstance(numerator, (int, float))
        and isinstance(denominator, (int, float))
        and denominator
    ):
        return numerator / denominator
    return None


def fmt(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("dspark", type=Path)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = load(args.target)
    dspark = load(args.dspark)
    target_groups = group_map(target)
    dspark_groups = group_map(dspark)
    keys = sorted(set(target_groups) & set(dspark_groups))
    rows: list[dict[str, Any]] = []
    for key in keys:
        target_summary = target_groups[key]
        dspark_summary = dspark_groups[key]
        rows.append(
            {
                "prompt_tokens": key[0],
                "output_tokens": key[1],
                "concurrency": key[2],
                "target_ttft_p50_s": target_summary.get("ttft_p50_s"),
                "dspark_ttft_p50_s": dspark_summary.get("ttft_p50_s"),
                "target_decode_tps_mean": target_summary.get("decode_tps_mean"),
                "dspark_decode_tps_mean": dspark_summary.get("decode_tps_mean"),
                "decode_speedup": ratio(
                    dspark_summary.get("decode_tps_mean"),
                    target_summary.get("decode_tps_mean"),
                ),
                "target_aggregate_tps": target_summary.get(
                    "aggregate_decode_throughput_tokens_per_s"
                ),
                "dspark_aggregate_tps": dspark_summary.get(
                    "aggregate_decode_throughput_tokens_per_s"
                ),
                "aggregate_speedup": ratio(
                    dspark_summary.get("aggregate_decode_throughput_tokens_per_s"),
                    target_summary.get("aggregate_decode_throughput_tokens_per_s"),
                ),
                "target_errors": target_summary.get("error_requests"),
                "dspark_errors": dspark_summary.get("error_requests"),
            }
        )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["no_matching_groups"]
    with args.csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Benchmark summary",
        "",
        f"Target-only source: `{args.target}`  ",
        f"DSpark source: `{args.dspark}`",
        "",
        "| Prompt | Output | C | Target TTFT p50 | DSpark TTFT p50 | Target decode tok/s | DSpark decode tok/s | Decode speedup | Target aggregate tok/s | DSpark aggregate tok/s | Aggregate speedup | Errors T/D |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['prompt_tokens']} | {row['output_tokens']} | {row['concurrency']} | "
            f"{fmt(row['target_ttft_p50_s'])} | {fmt(row['dspark_ttft_p50_s'])} | "
            f"{fmt(row['target_decode_tps_mean'])} | {fmt(row['dspark_decode_tps_mean'])} | "
            f"{fmt(row['decode_speedup'])}x | {fmt(row['target_aggregate_tps'])} | "
            f"{fmt(row['dspark_aggregate_tps'])} | {fmt(row['aggregate_speedup'])}x | "
            f"{row['target_errors']}/{row['dspark_errors']} |"
        )
    spec = dspark.get("speculative_metrics", {})
    lines.extend(
        [
            "",
            "## DSpark counters",
            "",
            f"- Acceptance rate: {fmt(spec.get('acceptance_rate'))}",
            f"- Mean acceptance length including bonus: {fmt(spec.get('mean_acceptance_length_including_bonus'))}",
            f"- Draft latency: {fmt(spec.get('draft_latency_s'))} ({spec.get('draft_latency_note', 'not reported')})",
            "",
            "## Historical local reference",
            "",
            "MiniMax-M2.7: 230B total / 10B activated, approximately 11K input, prefill/TTFT approximately 15 seconds. This remains a reference, not an inferred benchmark row.",
            "",
            "TTFT includes scheduling and client transport. Community values are never presented as local measurements.",
        ]
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"MARKDOWN={args.markdown}\nCSV={args.csv}")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
