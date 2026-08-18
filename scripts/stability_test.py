#!/usr/bin/env python3
"""Run mixed continuous requests and health checks for a configured duration."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmark_api import PromptFactory, metric_snapshot, stream_completion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8005")
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("target-only", "dspark"), required=True)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    factory = PromptFactory(args.base_url, args.model, args.timeout, args.output.parent)
    prompt_lengths = [256, 1024, 8192]
    prompts = {length: factory.exact_prompt(length)[0] for length in prompt_lengths}
    deadline = time.monotonic() + args.minutes * 60
    metrics_before = metric_snapshot(args.base_url, 30)
    requests: list[dict] = []
    health_checks: list[dict] = []
    iteration = 0
    while time.monotonic() < deadline:
        health_started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                args.base_url.rstrip("/") + "/v1/models", timeout=10
            ) as response:
                health_checks.append(
                    {
                        "ok": response.status == 200,
                        "latency_s": time.perf_counter() - health_started,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            health_checks.append(
                {
                    "ok": False,
                    "latency_s": time.perf_counter() - health_started,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        length = prompt_lengths[iteration % len(prompt_lengths)]
        output_tokens = (64, 128, 256)[iteration % 3]
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    stream_completion,
                    args.base_url,
                    args.model,
                    prompts[length],
                    output_tokens,
                    args.timeout,
                    None,
                    index,
                )
                for index in range(args.concurrency)
            ]
            batch = [future.result() for future in futures]
        for item in batch:
            item["prompt_target_tokens"] = length
            item["iteration"] = iteration
        requests.extend(batch)
        iteration += 1

    metrics_after = metric_snapshot(args.base_url, 30)
    spec_delta = {}
    if metrics_before and metrics_after:
        spec_delta = {
            key: max(0.0, metrics_after.get(key, 0.0) - metrics_before.get(key, 0.0))
            for key in ("drafts", "draft_tokens", "accepted_tokens")
        }
    successes = sum(bool(item.get("success")) for item in requests)
    result = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": args.mode,
        "requested_minutes": args.minutes,
        "concurrency": args.concurrency,
        "mixed_prompt_lengths": prompt_lengths,
        "requests": len(requests),
        "successful_requests": successes,
        "error_requests": len(requests) - successes,
        "health_checks": len(health_checks),
        "failed_health_checks": sum(not item["ok"] for item in health_checks),
        "speculative_counter_delta": spec_delta,
        "request_details": requests,
        "health_details": health_checks,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"STABILITY_RESULT={args.output}")
    return 1 if result["error_requests"] or result["failed_health_checks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
