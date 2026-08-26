#!/usr/bin/env python3
"""Resumable long-context, prefix-cache and decode benchmark for R2.

The measured request is streamed so TTFT and decode time are observed at the
client boundary. A dedicated priming request shares only the requested prefix;
every measured request diverges immediately afterwards. Final API usage is the
authoritative source for actual cached tokens.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONTEXTS = (200_000, 400_000, 600_000, 800_000, 1_000_000)
DEFAULT_OUTPUTS = (10_000, 20_000, 30_000)
DEFAULT_HIT_RATES = (0.80, 0.85, 0.90, 0.95)

SYSTEM_MESSAGE = (
    "你是一名资深代码维护工程师。请延续给定代码，保持已有接口、类型和中文注释；"
    "不得删除错误处理，也不得输出工具调用。"
)
SHARED_UNIT = """
// 共享仓库代码：该段用于模拟大型工程中稳定、可复用的历史前缀。
type AuditRecord struct {
    RequestID string `json:"request_id"`
    Revision  int64  `json:"revision"`
    Payload   []byte `json:"payload"`
}

// ValidateRecord 检查输入边界；中文注释刻意贴近日常代码审查场景。
func ValidateRecord(r AuditRecord) error {
    if r.RequestID == "" { return errors.New("请求标识不能为空") }
    if r.Revision < 0 { return errors.New("版本号不能为负数") }
    return nil
}
"""
UNIQUE_UNIT = """
// 本次修改分支：只影响当前请求，不应被其他并发请求误判为共享缓存。
func applyPatch(ctx context.Context, item AuditRecord) (AuditRecord, error) {
    if err := ValidateRecord(item); err != nil { return AuditRecord{}, err }
    item.Revision++
    return item, nil
}
"""
RAW_MARKERS = ("<｜DSML｜", "<|DSML|", "<invoke", "<parameter", "</invoke>")
METRIC_NAMES = (
    "vllm:prefix_cache_queries",
    "vllm:prefix_cache_hits",
    "vllm:spec_decode_num_accepted_tokens",
    "vllm:spec_decode_num_draft_tokens",
)

FIELDS = (
    "run_id",
    "status",
    "scheme",
    "dspark_k",
    "cache_profile",
    "model",
    "context_target",
    "output_target",
    "cache_hit_target",
    "concurrency",
    "requests_success",
    "requests_failed",
    "prompt_tokens_p50",
    "completion_tokens_p50",
    "cached_tokens_p50",
    "cache_hit_actual_p50",
    "cache_hit_actual_min",
    "cache_hit_delta_pp",
    "ttft_ms_p50",
    "ttft_ms_p95",
    "itl_ms_p50",
    "itl_ms_p95",
    "decode_tps_per_request_p50",
    "decode_tps_per_request_p95",
    "decode_tps_aggregate",
    "effective_uncached_prefill_tps",
    "e2e_ms_p50",
    "e2e_ms_p95",
    "server_prefix_hit_rate",
    "dspark_acceptance_rate",
    "wave_seconds",
    "output_digest_count",
    "error_summary",
    "started_at",
    "finished_at",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def rounded(value: float | None, digits: int = 3) -> str:
    return "" if value is None else str(round(value, digits))


def parse_ints(raw: str) -> tuple[int, ...]:
    result = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not result or any(value <= 0 for value in result):
        raise ValueError("integer matrix axes must contain positive values")
    return result


def parse_rates(raw: str) -> tuple[float, ...]:
    result = tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    if not result or any(not 0 < value < 1 for value in result):
        raise ValueError("cache hit rates must be between 0 and 1")
    return result


def parse_metric_totals(text: str) -> dict[str, float]:
    """Parse the selected Prometheus counters from raw exposition text.

    ``prometheus_client.Counter`` exposes ``<name>_total`` even though vLLM
    registers and documents the logical metric without that suffix.
    """
    totals = {name: 0.0 for name in METRIC_NAMES}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        head, separator, raw_value = line.rpartition(" ")
        if not separator:
            continue
        exposed_name = head.split("{", 1)[0]
        metric_name = (
            exposed_name.removesuffix("_total")
            if exposed_name.endswith("_total")
            else exposed_name
        )
        if metric_name not in totals:
            continue
        try:
            totals[metric_name] += float(raw_value)
        except ValueError:
            continue
    return totals


class JsonHttpClient:
    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        base = base_url.rstrip("/")
        self.origin = base[:-3] if base.endswith("/v1") else base
        self.api_key = api_key
        self.timeout = timeout

    def open(self, path: str, payload: dict[str, Any] | None = None):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.origin + path,
            data=body,
            headers=headers,
            method="GET" if body is None else "POST",
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(8192).decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc

    def json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.open(path, payload) as response:
            value = json.loads(response.read())
        if not isinstance(value, dict):
            raise RuntimeError(f"expected JSON object from {path}")
        return value

    def metrics(self) -> dict[str, float]:
        with self.open("/metrics") as response:
            text = response.read().decode(errors="replace")
        return parse_metric_totals(text)


def messages(content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": content},
    ]


def token_count(client: JsonHttpClient, model: str, content: str) -> int:
    response = client.json("/tokenize", {"model": model, "messages": messages(content)})
    count = response.get("count")
    if not isinstance(count, int):
        raise RuntimeError(f"/tokenize omitted integer count: {response}")
    return count


def calibrate_repetitions(
    client: JsonHttpClient,
    model: str,
    target: int,
    fixed: str,
    unit: str,
) -> tuple[int, int]:
    """Find a near-target token count with few large tokenizer requests."""
    base = token_count(client, model, fixed)
    one = token_count(client, model, fixed + unit)
    per_unit = max(1, one - base)
    guess = max(0, round((target - base) / per_unit))
    best = (guess, token_count(client, model, fixed + unit * guess))
    for _ in range(6):
        error = target - best[1]
        if abs(error) <= max(32, per_unit):
            break
        candidate = max(0, best[0] + round(error / per_unit))
        if candidate == best[0]:
            candidate += 1 if error > 0 else -1
            candidate = max(0, candidate)
        measured = token_count(client, model, fixed + unit * candidate)
        if abs(measured - target) < abs(best[1] - target):
            best = (candidate, measured)
        else:
            per_unit = max(1, round(abs(measured - best[1]) / max(1, abs(candidate - best[0]))))
            if abs(candidate - best[0]) <= 1:
                break
    for candidate in range(max(0, best[0] - 2), best[0] + 3):
        measured = token_count(client, model, fixed + unit * candidate)
        if abs(measured - target) < abs(best[1] - target):
            best = (candidate, measured)
    return best


def calibrate_prompt(
    client: JsonHttpClient,
    model: str,
    context_target: int,
    hit_target: float,
) -> tuple[str, int, int]:
    prefix_target = round(context_target * hit_target)
    prefix_reps, _ = calibrate_repetitions(
        client, model, prefix_target, "", SHARED_UNIT
    )
    shared = SHARED_UNIT * prefix_reps
    marker = "\n// 请求唯一编号：CALIBRATION；从这里开始是私有修改。\n"
    suffix_reps, measured = calibrate_repetitions(
        client, model, context_target, shared + marker, UNIQUE_UNIT
    )
    return shared, suffix_reps, measured


def prime_prefix(
    client: JsonHttpClient,
    model: str,
    shared: str,
    cache_salt: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages(shared + "\n// 缓存预热结束，只回复 OK。\n"),
        "temperature": 0,
        "max_completion_tokens": 1,
        "cache_salt": cache_salt,
    }
    return client.json("/v1/chat/completions", payload)


def meaningful_delta(event: dict[str, Any]) -> str:
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    delta = choices[0].get("delta") or {}
    if not isinstance(delta, dict):
        return ""
    parts: list[str] = []
    for key in ("reasoning", "reasoning_content", "content"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if delta.get("tool_calls"):
        parts.append(json.dumps(delta["tool_calls"], sort_keys=True))
    return "".join(parts)


def one_streaming_request(
    client: JsonHttpClient,
    model: str,
    content: str,
    output_target: int,
    cache_salt: str,
    barrier: threading.Barrier,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages(content),
        "temperature": 0,
        "max_completion_tokens": output_target,
        "min_tokens": output_target,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
        "cache_salt": cache_salt,
    }
    barrier.wait()
    started = time.perf_counter()
    first_token_at: float | None = None
    usage: dict[str, Any] = {}
    digest = hashlib.sha256()
    meaningful_events = 0
    try:
        with client.open("/v1/chat/completions", payload) as response:
            for raw_line in response:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                if any(marker in data for marker in RAW_MARKERS):
                    raise RuntimeError("raw DeepSeek parser marker leaked")
                event = json.loads(data)
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                delta = meaningful_delta(event)
                if delta:
                    first_token_at = first_token_at or time.perf_counter()
                    meaningful_events += 1
                    digest.update(delta.encode(errors="replace"))
        finished = time.perf_counter()
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details") or {}
        cached_tokens = details.get("cached_tokens", 0)
        if not all(isinstance(value, int) for value in (prompt_tokens, completion_tokens, cached_tokens)):
            raise RuntimeError(f"stream omitted integer token usage: {usage}")
        if first_token_at is None or meaningful_events == 0:
            raise RuntimeError("stream returned no meaningful output delta")
        if completion_tokens != output_target:
            raise RuntimeError(
                f"completion length mismatch: expected={output_target} observed={completion_tokens}"
            )
        ttft_seconds = first_token_at - started
        decode_seconds = max(finished - first_token_at, 1e-9)
        return {
            "ok": True,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit_rate": cached_tokens / prompt_tokens,
            "ttft_ms": ttft_seconds * 1000,
            "itl_ms": decode_seconds * 1000 / max(1, completion_tokens - 1),
            "decode_tps": max(0, completion_tokens - 1) / decode_seconds,
            "effective_uncached_prefill_tps": max(0, prompt_tokens - cached_tokens)
            / max(ttft_seconds, 1e-9),
            "latency_ms": (finished - started) * 1000,
            "output_digest": digest.hexdigest(),
            "first_token_at_perf": first_token_at,
            "finished_at_perf": finished,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": (time.perf_counter() - started) * 1000,
        }


def counter_delta(before: dict[str, float], after: dict[str, float], name: str) -> float:
    return max(0.0, after.get(name, 0.0) - before.get(name, 0.0))


def aggregate_decode_tps(results: list[dict[str, Any]]) -> float | None:
    """Measure concurrent decode after the first observed streamed token.

    The window begins at the earliest successful first token and ends at the
    latest successful completion. This avoids folding the entire long-context
    prefill/queue interval into a value labelled as decode throughput.
    """
    if not results:
        return None
    starts = [float(item["first_token_at_perf"]) for item in results]
    finishes = [float(item["finished_at_perf"]) for item in results]
    decoded = sum(max(0, int(item["completion_tokens"]) - 1) for item in results)
    return decoded / max(max(finishes) - min(starts), 1e-9)


def pending_row(args: argparse.Namespace, run_id: str, context: int, output: int, hit: float):
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "run_id": run_id,
            "status": "pending",
            "scheme": args.scheme,
            "dspark_k": args.dspark_k if args.scheme == "dspark" else 0,
            "cache_profile": args.cache_profile,
            "model": args.model,
            "context_target": context,
            "output_target": output,
            "cache_hit_target": hit,
            "concurrency": args.concurrency,
            "requests_success": 0,
            "requests_failed": 0,
        }
    )
    return row


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def run_cell(
    args: argparse.Namespace,
    client: JsonHttpClient,
    row: dict[str, Any],
    shared: str,
    suffix_reps: int,
    calibrated_count: int,
) -> dict[str, Any]:
    context_target = int(row["context_target"])
    output_target = int(row["output_target"])
    hit_target = float(row["cache_hit_target"])
    started_at = utc_now()
    # Include this attempt's timestamp so rerunning a failed cell cannot reuse
    # that cell's prior measured branches and report an inflated cache hit.
    salt_source = (
        f"{row['run_id']}:{context_target}:{output_target}:{hit_target}:{started_at}"
    )
    cache_salt = "dsv4-r2-" + hashlib.sha256(salt_source.encode()).hexdigest()

    before = client.metrics()
    prime_prefix(client, args.model, shared, cache_salt)
    barrier = threading.Barrier(args.concurrency)
    wave_started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for index in range(args.concurrency):
            unique_marker = (
                f"\n// 请求唯一编号：{index:04d}-{row['run_id']}；从这里开始是私有修改。\n"
            )
            content = shared + unique_marker + UNIQUE_UNIT * suffix_reps
            futures.append(
                executor.submit(
                    one_streaming_request,
                    client,
                    args.model,
                    content,
                    output_target,
                    cache_salt,
                    barrier,
                )
            )
        for future in as_completed(futures):
            results.append(future.result())
    wave_seconds = time.perf_counter() - wave_started
    after = client.metrics()

    successes = [item for item in results if item.get("ok")]
    failures = [item for item in results if not item.get("ok")]
    prompt_tokens = [float(item["prompt_tokens"]) for item in successes]
    completion_tokens = [float(item["completion_tokens"]) for item in successes]
    cached_tokens = [float(item["cached_tokens"]) for item in successes]
    hit_rates = [float(item["cache_hit_rate"]) for item in successes]
    ttfts = [float(item["ttft_ms"]) for item in successes]
    itls = [float(item["itl_ms"]) for item in successes]
    decode_tps = [float(item["decode_tps"]) for item in successes]
    prefill_tps = [float(item["effective_uncached_prefill_tps"]) for item in successes]
    latencies = [float(item["latency_ms"]) for item in successes]

    actual_hit = percentile(hit_rates, 0.50)
    min_hit = min(hit_rates) if hit_rates else None
    errors = [str(item.get("error", "unknown error")) for item in failures]
    if successes and any(abs(value - context_target) > args.token_tolerance for value in prompt_tokens):
        errors.append(
            f"prompt token target missed: calibrated={calibrated_count} tolerance={args.token_tolerance}"
        )
    if actual_hit is not None and actual_hit < hit_target - args.cache_tolerance:
        errors.append(
            f"cache hit gate failed: target={hit_target:.4f} actual={actual_hit:.4f}"
        )
    if min_hit is not None and min_hit < hit_target - args.cache_tolerance:
        errors.append(
            f"cache hit minimum gate failed: target={hit_target:.4f} min={min_hit:.4f}"
        )
    if len(successes) != args.concurrency:
        errors.append(f"request success gate failed: {len(successes)}/{args.concurrency}")

    prefix_queries = counter_delta(before, after, "vllm:prefix_cache_queries")
    prefix_hits = counter_delta(before, after, "vllm:prefix_cache_hits")
    accepted = counter_delta(before, after, "vllm:spec_decode_num_accepted_tokens")
    drafted = counter_delta(before, after, "vllm:spec_decode_num_draft_tokens")
    status = "complete" if not errors else "failed"
    row.update(
        {
            "status": status,
            "requests_success": len(successes),
            "requests_failed": len(failures),
            "prompt_tokens_p50": rounded(percentile(prompt_tokens, 0.50), 0),
            "completion_tokens_p50": rounded(percentile(completion_tokens, 0.50), 0),
            "cached_tokens_p50": rounded(percentile(cached_tokens, 0.50), 0),
            "cache_hit_actual_p50": rounded(actual_hit, 6),
            "cache_hit_actual_min": rounded(min_hit, 6),
            "cache_hit_delta_pp": rounded(
                None if actual_hit is None else (actual_hit - hit_target) * 100, 3
            ),
            "ttft_ms_p50": rounded(percentile(ttfts, 0.50)),
            "ttft_ms_p95": rounded(percentile(ttfts, 0.95)),
            "itl_ms_p50": rounded(percentile(itls, 0.50), 6),
            "itl_ms_p95": rounded(percentile(itls, 0.95), 6),
            "decode_tps_per_request_p50": rounded(percentile(decode_tps, 0.50)),
            "decode_tps_per_request_p95": rounded(percentile(decode_tps, 0.95)),
            "decode_tps_aggregate": rounded(aggregate_decode_tps(successes)),
            "effective_uncached_prefill_tps": rounded(percentile(prefill_tps, 0.50)),
            "e2e_ms_p50": rounded(percentile(latencies, 0.50)),
            "e2e_ms_p95": rounded(percentile(latencies, 0.95)),
            "server_prefix_hit_rate": rounded(
                prefix_hits / prefix_queries if prefix_queries else None, 6
            ),
            "dspark_acceptance_rate": rounded(accepted / drafted if drafted else None, 6),
            "wave_seconds": rounded(wave_seconds),
            "output_digest_count": len(
                {item["output_digest"] for item in successes if item.get("output_digest")}
            ),
            "error_summary": " | ".join(sorted(set(errors)))[:4000],
            "started_at": started_at,
            "finished_at": utc_now(),
        }
    )
    return row


def write_summary(path: Path, rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    complete = [row for row in rows if row["status"] == "complete"]
    failed = [row for row in rows if row["status"] == "failed"]
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source_csv": str(path),
        "scheme": args.scheme,
        "dspark_k": args.dspark_k if args.scheme == "dspark" else 0,
        "cache_profile": args.cache_profile,
        "model": args.model,
        "matrix": {
            "contexts": list(args.contexts),
            "outputs": list(args.outputs),
            "cache_hit_targets": list(args.hit_rates),
            "concurrency": args.concurrency,
            "cells": len(rows),
            "complete": len(complete),
            "failed": len(failed),
            "pending": len(rows) - len(complete) - len(failed),
        },
        "primary_kpis": {
            "ttft_ms_p95": "client start to first meaningful streamed delta",
            "effective_uncached_prefill_tps": "(prompt_tokens-cached_tokens)/TTFT; includes queue and first decode overhead",
            "decode_tps_aggregate": "successful tokens after each first token divided by earliest-first-token to latest-finish window",
        },
        "drivers": {
            "cache_hit_actual_p50": "cached_tokens/prompt_tokens from final API usage",
            "dspark_acceptance_rate": "server accepted speculative tokens / drafted tokens",
        },
        "guardrails": {
            "request_success": "16/16 required",
            "cache_regression": f"actual hit may not trail target by more than {args.cache_tolerance * 100:.1f} percentage point",
            "output_length": "completion_tokens must equal requested 10K/20K/30K",
            "parser_safety": "no raw DSML/tool parser marker may leak",
        },
    }
    target = path.with_suffix(".summary.json")
    temporary = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://host.docker.internal:8005/v1")
    parser.add_argument("--api-key", default=os.getenv("DSV4_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-v4-flash[1M]")
    parser.add_argument("--scheme", choices=("target", "dspark"), required=True)
    parser.add_argument("--dspark-k", type=int, choices=(1, 3, 5, 7), default=7)
    parser.add_argument("--cache-profile", choices=("legacy", "zero", "32768"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contexts", default=",".join(map(str, DEFAULT_CONTEXTS)))
    parser.add_argument("--outputs", default=",".join(map(str, DEFAULT_OUTPUTS)))
    parser.add_argument("--hit-rates", default=",".join(map(str, DEFAULT_HIT_RATES)))
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--request-timeout", type=float, default=28_800)
    parser.add_argument("--token-tolerance", type=int, default=512)
    parser.add_argument("--cache-tolerance", type=float, default=0.01)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--rerun-failed", action="store_true")
    parser.add_argument("--max-cells", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.contexts = parse_ints(args.contexts)
    args.outputs = parse_ints(args.outputs)
    args.hit_rates = parse_rates(args.hit_rates)
    if not 1 <= args.concurrency <= 16:
        raise RuntimeError("benchmark concurrency must be in C1..C16")
    if args.request_timeout <= 0:
        raise RuntimeError("request-timeout must be positive")
    if args.token_tolerance < 0:
        raise RuntimeError("token-tolerance must not be negative")
    if not 0 <= args.cache_tolerance < 1:
        raise RuntimeError("cache-tolerance must be in [0, 1)")
    if args.max_cells is not None and args.max_cells <= 0:
        raise RuntimeError("max-cells must be positive when provided")
    if max(args.contexts) + max(args.outputs) > 1_048_576:
        raise RuntimeError("largest input+output exceeds the physical 1M engine limit")
    if args.output.exists() and args.overwrite:
        rows: list[dict[str, Any]] = []
    elif args.output.exists():
        rows = read_rows(args.output)
    else:
        rows = []
    run_id = f"dsv4-r2-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    existing = {
        (int(row["context_target"]), int(row["output_target"]), float(row["cache_hit_target"])): row
        for row in rows
    }
    rows = [
        existing.get((context, output, hit), pending_row(args, run_id, context, output, hit))
        for context in args.contexts
        for output in args.outputs
        for hit in args.hit_rates
    ]
    atomic_csv(args.output, rows)
    write_summary(args.output, rows, args)
    if args.plan_only:
        print(
            f"MATRIX_PLAN=PASS cells={len(rows)} contexts={len(args.contexts)} "
            f"outputs={len(args.outputs)} hit_rates={len(args.hit_rates)} "
            f"concurrency={args.concurrency} requests={len(rows) * args.concurrency}"
        )
        return 0

    client = JsonHttpClient(args.base_url, args.api_key, args.request_timeout)
    models = client.json("/v1/models").get("data", [])
    available = {item.get("id") for item in models if isinstance(item, dict)}
    if args.model not in available:
        raise RuntimeError(f"model alias is not advertised: {args.model}; available={available}")

    calibration: dict[tuple[int, float], tuple[str, int, int]] = {}
    attempted = 0
    for index, row in enumerate(rows):
        if row["status"] == "complete":
            continue
        if row["status"] == "failed" and not args.rerun_failed:
            continue
        if args.max_cells is not None and attempted >= args.max_cells:
            break
        context = int(row["context_target"])
        hit = float(row["cache_hit_target"])
        key = (context, hit)
        if key not in calibration:
            calibration[key] = calibrate_prompt(client, args.model, context, hit)
        shared, suffix_reps, observed = calibration[key]
        print(
            f"CELL_START index={index + 1}/{len(rows)} context={context} "
            f"output={row['output_target']} hit={hit:.2%} calibrated={observed}",
            flush=True,
        )
        rows[index] = run_cell(args, client, row, shared, suffix_reps, observed)
        atomic_csv(args.output, rows)
        write_summary(args.output, rows, args)
        attempted += 1
        print(
            f"CELL_END status={rows[index]['status']} "
            f"hit={rows[index]['cache_hit_actual_p50']} "
            f"ttft_p95_ms={rows[index]['ttft_ms_p95']} "
            f"decode_tps={rows[index]['decode_tps_aggregate']}",
            flush=True,
        )

    complete = sum(row["status"] == "complete" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    pending = len(rows) - complete - failed
    print(
        f"MATRIX_RUN cells={len(rows)} complete={complete} failed={failed} "
        f"pending={pending} output={args.output}"
    )
    if args.max_cells is not None:
        return 0 if failed == 0 else 1
    return 0 if failed == 0 and pending == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
