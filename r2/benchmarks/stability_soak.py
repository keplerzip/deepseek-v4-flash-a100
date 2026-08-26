#!/usr/bin/env python3
"""Twenty-four hour C16 cache-preservation and decode soak."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from long_context_matrix import (
    JsonHttpClient,
    UNIQUE_UNIT,
    aggregate_decode_tps,
    calibrate_prompt,
    counter_delta,
    one_streaming_request,
    prime_prefix,
)


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://host.docker.internal:8005/v1")
    parser.add_argument("--api-key", default=os.getenv("DSV4_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-v4-flash[1M]")
    parser.add_argument("--context", type=int, default=600_000)
    parser.add_argument("--output-tokens", type=int, default=1_000)
    parser.add_argument("--cache-hit", type=float, default=0.90)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration-hours", type=float, default=24)
    parser.add_argument("--max-waves", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=28_800)
    args = parser.parse_args()
    if args.context + args.output_tokens > 1_048_576:
        raise RuntimeError("soak input+output exceeds engine max_model_len")
    if not 1 <= args.concurrency <= 16:
        raise RuntimeError("soak concurrency must be in C1..C16")
    if args.context <= 0 or args.output_tokens <= 0:
        raise RuntimeError("soak context and output-tokens must be positive")
    if not 0 < args.cache_hit < 1:
        raise RuntimeError("soak cache-hit must be between 0 and 1")
    if args.timeout <= 0:
        raise RuntimeError("soak timeout must be positive")
    if args.duration_hours <= 0:
        raise RuntimeError("soak duration-hours must be positive")
    if args.max_waves is not None and args.max_waves <= 0:
        raise RuntimeError("soak max-waves must be positive when provided")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    events_path = args.output.with_suffix(".jsonl")
    client = JsonHttpClient(args.base_url, args.api_key, args.timeout)
    shared, suffix_reps, calibrated = calibrate_prompt(
        client, args.model, args.context, args.cache_hit
    )
    started_wall = datetime.now(UTC).isoformat()
    started = time.monotonic()
    deadline = started + args.duration_hours * 3600
    wave = 0
    failures = 0
    min_hit = 1.0
    total_completion_tokens = 0
    total_decode_tokens = 0
    total_decode_seconds = 0.0

    with events_path.open("w") as event_file:
        while wave == 0 or time.monotonic() < deadline:
            if args.max_waves is not None and wave >= args.max_waves:
                break
            wave += 1
            salt = f"dsv4-r2-soak-{started_wall}-{wave}"
            before = client.metrics()
            prime_prefix(client, args.model, shared, salt)
            barrier = threading.Barrier(args.concurrency)
            wave_started = time.perf_counter()
            results = []
            with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                futures = []
                for index in range(args.concurrency):
                    marker = f"\n// 稳定性波次 {wave}，请求 {index}，私有分支。\n"
                    content = shared + marker + UNIQUE_UNIT * suffix_reps
                    futures.append(
                        executor.submit(
                            one_streaming_request,
                            client,
                            args.model,
                            content,
                            args.output_tokens,
                            salt,
                            barrier,
                        )
                    )
                results.extend(future.result() for future in as_completed(futures))
            wave_seconds = time.perf_counter() - wave_started
            after = client.metrics()
            success = [item for item in results if item.get("ok")]
            failed = [item for item in results if not item.get("ok")]
            hits = [float(item["cache_hit_rate"]) for item in success]
            wave_min_hit = min(hits) if hits else 0.0
            min_hit = min(min_hit, wave_min_hit)
            wave_ok = len(success) == args.concurrency and wave_min_hit >= args.cache_hit - 0.01
            if not wave_ok:
                failures += 1
            completion = sum(int(item["completion_tokens"]) for item in success)
            total_completion_tokens += completion
            decode_tokens = sum(
                max(0, int(item["completion_tokens"]) - 1) for item in success
            )
            decode_tps = aggregate_decode_tps(success)
            if decode_tps is not None and decode_tokens:
                total_decode_tokens += decode_tokens
                total_decode_seconds += decode_tokens / decode_tps
            accepted = counter_delta(before, after, "vllm:spec_decode_num_accepted_tokens")
            drafted = counter_delta(before, after, "vllm:spec_decode_num_draft_tokens")
            event = {
                "wave": wave,
                "finished_at": datetime.now(UTC).isoformat(),
                "status": "pass" if wave_ok else "fail",
                "requests_success": len(success),
                "requests_failed": len(failed),
                "min_cache_hit_rate": wave_min_hit,
                "wave_seconds": wave_seconds,
                "completion_tokens": completion,
                "aggregate_decode_tokens_per_second": decode_tps,
                "dspark_acceptance_rate": accepted / drafted if drafted else None,
                "errors": [item.get("error") for item in failed],
            }
            event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
            event_file.flush()
            summary = {
                "schema_version": 1,
                "started_at": started_wall,
                "updated_at": event["finished_at"],
                "duration_target_hours": args.duration_hours,
                "elapsed_hours": (time.monotonic() - started) / 3600,
                "context_target": args.context,
                "calibrated_prompt_tokens": calibrated,
                "output_tokens": args.output_tokens,
                "cache_hit_target": args.cache_hit,
                "concurrency": args.concurrency,
                "waves": wave,
                "failed_waves": failures,
                "minimum_cache_hit_rate": min_hit,
                "completion_tokens": total_completion_tokens,
                "aggregate_decode_tokens_per_second": (
                    total_decode_tokens / total_decode_seconds
                    if total_decode_seconds
                    else None
                ),
                "status": "pass_so_far" if failures == 0 else "failed",
            }
            atomic_json(args.output, summary)
            print(
                f"SOAK_WAVE wave={wave} status={event['status']} "
                f"hit_min={wave_min_hit:.4f} seconds={wave_seconds:.1f}",
                flush=True,
            )
            if failures:
                return 1

    summary["status"] = "pass" if failures == 0 else "failed"
    summary["finished_at"] = datetime.now(UTC).isoformat()
    atomic_json(args.output, summary)
    print(f"SOAK=PASS waves={wave} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
