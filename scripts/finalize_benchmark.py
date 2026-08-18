#!/usr/bin/env python3
"""Attach GPU peaks and runtime log diagnostics to a benchmark JSON file."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def numeric(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def gpu_summary(path: Path) -> dict[str, Any]:
    by_gpu: dict[str, dict[str, float]] = {}
    memory_series: dict[str, list[float]] = {}
    errors: list[str] = []
    if not path.is_file():
        return {"available": False, "reason": f"missing {path}"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("collector_error"):
                errors.append(row["collector_error"])
            index = row.get("index", "").strip()
            if not index:
                continue
            item = by_gpu.setdefault(index, {})
            for source, target in (
                ("memory.used", "peak_memory_mib"),
                ("utilization.gpu", "peak_gpu_utilization_percent"),
                ("utilization.memory", "peak_memory_utilization_percent"),
                ("power.draw", "peak_power_w"),
                ("temperature.gpu", "peak_temperature_c"),
            ):
                value = numeric(row.get(source, ""))
                if value is not None:
                    item[target] = max(item.get(target, value), value)
                    if source == "memory.used":
                        memory_series.setdefault(index, []).append(value)
    for index, values in memory_series.items():
        if values:
            by_gpu[index]["first_memory_mib"] = values[0]
            by_gpu[index]["last_memory_mib"] = values[-1]
            by_gpu[index]["memory_growth_mib"] = values[-1] - values[0]
    return {"available": bool(by_gpu), "per_gpu": by_gpu, "collector_errors": errors[:20]}


def startup_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"available": False, "reason": f"missing {path}"}
    result: dict[str, Any] = {"available": True, "source": str(path)}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.endswith("_s"):
            parsed = numeric(value)
            result[key] = parsed if parsed is not None else value
        else:
            result[key] = value
    return result


def log_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False, "reason": f"missing {path}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    patterns = {
        "nccl_errors": r"(?im)^.*NCCL.*(?:error|warn|timeout|hang).*$",
        "cuda_errors": r"(?im)^.*(?:CUDA error|illegal memory access|invalid device function|no kernel image).*$",
        "oom_errors": r"(?im)^.*(?:out of memory|CUDA OOM).*$",
        "graph_errors": r"(?im)^.*(?:CUDA Graph|cudagraph).*(?:error|hang|fail).*$",
        "dspark_lines": r"(?im)^.*(?:DSpark|Mean acceptance|Draft acceptance).*$",
        "kv_capacity_lines": r"(?im)^.*(?:KV cache.*tokens|maximum concurrency|max concurrency).*$",
        "model_loading_lines": r"(?im)^.*(?:Model loading took|Loading weights took).*$",
        "jit_warmup_lines": r"(?im)^.*(?:warmup|JIT|torch\.compile|CUDA Graph|cudagraph).*$",
    }
    result: dict[str, Any] = {"available": True}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, text)
        result[name] = {"count": len(matches), "examples": matches[-10:]}
    model_loading_seconds = [
        float(value)
        for value in re.findall(
            r"(?i)(?:Model loading took|Loading weights took)[^\n]*?\b([0-9]+(?:\.[0-9]+)?) seconds",
            text,
        )
    ]
    result["model_loading_seconds_samples"] = model_loading_seconds
    result["model_loading_seconds_max"] = (
        max(model_loading_seconds) if model_loading_seconds else None
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-json", type=Path, required=True)
    parser.add_argument("--gpu-csv", type=Path, required=True)
    parser.add_argument("--runtime-log", type=Path, required=True)
    parser.add_argument("--startup-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(args.benchmark_json.read_text(encoding="utf-8"))
    data["startup_metrics"] = startup_summary(args.startup_file)
    data["gpu_metrics"] = gpu_summary(args.gpu_csv)
    data["runtime_log_diagnostics"] = log_summary(args.runtime_log)
    temporary = args.benchmark_json.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.benchmark_json)
    print(f"FINALIZED={args.benchmark_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
