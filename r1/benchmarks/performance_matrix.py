#!/usr/bin/env python3
"""Run a profile-locked long-context concurrency matrix."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONCURRENCIES = tuple(range(1, 17))
CONTEXTS = tuple(range(10_000, 200_001, 10_000))
PROMPT_UNIT = "The quick brown fox audits one deterministic inference token. "
RAW_MARKERS = (
    "<｜DSML｜",
    "<| DSML|",
    "<|DSML|",
    "<invoke",
    "<parameter",
    "</invoke>",
    "</parameter>",
)
FIELDS = (
    "run_id",
    "status",
    "context_target",
    "concurrency",
    "repetitions",
    "requests_planned",
    "requests_success",
    "requests_failed",
    "prompt_tokens_min",
    "prompt_tokens_p50",
    "prompt_tokens_max",
    "completion_tokens_total",
    "ttft_ms_p50",
    "ttft_ms_p95",
    "ttft_ms_p99",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_p99",
    "input_tokens_per_second",
    "output_tokens_per_second",
    "total_tokens_per_second",
    "wave_wall_seconds",
    "error_summary",
    "started_at",
    "finished_at",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_origin(value: str) -> str:
    base = value.rstrip("/")
    return base.removesuffix("/v1")


class JsonHttpClient:
    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        self.origin = normalize_origin(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def request(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        stream: bool = False,
    ) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if stream:
            headers["Accept"] = "text/event-stream"
        request = urllib.request.Request(
            self.origin + path,
            data=data,
            headers=headers,
            method="GET" if payload is None else "POST",
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc

    def json(self, path: str, payload: dict[str, Any] | None = None) -> dict:
        with self.request(path, payload) as response:
            value = json.loads(response.read())
        if not isinstance(value, dict):
            raise RuntimeError(f"expected a JSON object from {path}")
        return value


def messages(repetitions: int) -> list[dict[str, str]]:
    content = (
        "This is a deterministic long-context throughput measurement. "
        "Reply with exactly the word OK.\n" + PROMPT_UNIT * repetitions
    )
    return [{"role": "user", "content": content}]


def token_count(client: JsonHttpClient, model: str, repetitions: int) -> int:
    response = client.json(
        "/tokenize",
        {"model": model, "messages": messages(repetitions)},
    )
    count = response.get("count")
    if not isinstance(count, int):
        raise RuntimeError(f"/tokenize omitted integer count: {response}")
    return count


def calibrate_prompt(
    client: JsonHttpClient,
    model: str,
    target: int,
) -> tuple[int, int]:
    low = 0
    low_count = token_count(client, model, low)
    high = max(1, target // 4)
    high_count = token_count(client, model, high)
    while high_count < target:
        low, low_count = high, high_count
        high *= 2
        high_count = token_count(client, model, high)
    while high - low > 1:
        middle = (low + high) // 2
        middle_count = token_count(client, model, middle)
        if middle_count < target:
            low, low_count = middle, middle_count
        else:
            high, high_count = middle, middle_count
    candidates = ((low, low_count), (high, high_count))
    return min(candidates, key=lambda item: abs(item[1] - target))


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def rounded(value: float | None, digits: int = 3) -> str:
    return "" if value is None else str(round(value, digits))


def meaningful_delta(event: dict[str, Any]) -> bool:
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    delta = choices[0].get("delta", {})
    if not isinstance(delta, dict):
        return False
    return any(delta.get(key) for key in ("content", "reasoning_content", "tool_calls"))


def one_streaming_request(
    client: JsonHttpClient,
    model: str,
    prompt_messages: list[dict[str, str]],
    max_output_tokens: int,
    barrier: threading.Barrier,
) -> dict[str, Any]:
    barrier.wait()
    started = time.perf_counter()
    first_event_at: float | None = None
    first_token_at: float | None = None
    usage: dict[str, Any] = {}
    events = 0
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": 0,
        "max_completion_tokens": max_output_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "cache_salt": secrets.token_urlsafe(32),
    }
    try:
        with client.request(
            "/v1/chat/completions", payload, stream=True
        ) as response:
            for raw_line in response:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                if marker := next((item for item in RAW_MARKERS if item in data), None):
                    raise RuntimeError(f"raw parser marker leaked: {marker}")
                event = json.loads(data)
                decoded_event = json.dumps(event, ensure_ascii=False)
                if marker := next(
                    (item for item in RAW_MARKERS if item in decoded_event), None
                ):
                    raise RuntimeError(f"raw parser marker leaked: {marker}")
                events += 1
                now = time.perf_counter()
                first_event_at = first_event_at or now
                if first_token_at is None and meaningful_delta(event):
                    first_token_at = now
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    if delta.get("tool_calls"):
                        raise RuntimeError(
                            "structured tool call returned without declared tools"
                        )
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
        finished = time.perf_counter()
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
            raise RuntimeError(f"stream omitted final usage: {usage}")
        ttft_at = first_token_at or first_event_at
        if ttft_at is None:
            raise RuntimeError("stream returned no SSE events")
        return {
            "ok": True,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "ttft_ms": (ttft_at - started) * 1000,
            "latency_ms": (finished - started) * 1000,
            "events": events,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": (time.perf_counter() - started) * 1000,
        }


def pending_row(run_id: str, context: int, concurrency: int, repetitions: int):
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "run_id": run_id,
            "status": "pending",
            "context_target": context,
            "concurrency": concurrency,
            "repetitions": repetitions,
            "requests_planned": concurrency * repetitions,
            "requests_success": 0,
            "requests_failed": 0,
        }
    )
    return row


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def run_cell(
    client: JsonHttpClient,
    model: str,
    row: dict[str, Any],
    prompt_repetitions: int,
    calibrated_count: int,
    max_output_tokens: int,
    tolerance: int,
) -> dict[str, Any]:
    concurrency = int(row["concurrency"])
    repetitions = int(row["repetitions"])
    started_at = utc_now()
    results: list[dict[str, Any]] = []
    wall_seconds = 0.0
    prompt_messages = messages(prompt_repetitions)
    for _ in range(repetitions):
        barrier = threading.Barrier(concurrency)
        wave_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    one_streaming_request,
                    client,
                    model,
                    prompt_messages,
                    max_output_tokens,
                    barrier,
                )
                for _ in range(concurrency)
            ]
            results.extend(future.result() for future in as_completed(futures))
        wall_seconds += time.perf_counter() - wave_started

    successes = [result for result in results if result["ok"]]
    failures = [result for result in results if not result["ok"]]
    prompt_tokens = [result["prompt_tokens"] for result in successes]
    completion_tokens = [result["completion_tokens"] for result in successes]
    ttfts = [result["ttft_ms"] for result in successes]
    latencies = [result["latency_ms"] for result in successes]
    context_ok = all(
        abs(value - int(row["context_target"])) <= tolerance for value in prompt_tokens
    )
    status = "complete" if not failures and successes and context_ok else "failed"
    errors = [result["error"] for result in failures]
    if not context_ok:
        errors.append(
            f"calibrated prompt count {calibrated_count} exceeded tolerance {tolerance}"
        )
    input_total = sum(prompt_tokens)
    output_total = sum(completion_tokens)
    row.update(
        {
            "status": status,
            "requests_success": len(successes),
            "requests_failed": len(failures),
            "prompt_tokens_min": min(prompt_tokens) if prompt_tokens else "",
            "prompt_tokens_p50": rounded(percentile(prompt_tokens, 0.50), 0),
            "prompt_tokens_max": max(prompt_tokens) if prompt_tokens else "",
            "completion_tokens_total": output_total,
            "ttft_ms_p50": rounded(percentile(ttfts, 0.50)),
            "ttft_ms_p95": rounded(percentile(ttfts, 0.95)),
            "ttft_ms_p99": rounded(percentile(ttfts, 0.99)),
            "latency_ms_p50": rounded(percentile(latencies, 0.50)),
            "latency_ms_p95": rounded(percentile(latencies, 0.95)),
            "latency_ms_p99": rounded(percentile(latencies, 0.99)),
            "input_tokens_per_second": rounded(input_total / wall_seconds),
            "output_tokens_per_second": rounded(output_total / wall_seconds),
            "total_tokens_per_second": rounded(
                (input_total + output_total) / wall_seconds
            ),
            "wave_wall_seconds": rounded(wall_seconds),
            "error_summary": " | ".join(sorted(set(errors)))[:2000],
            "started_at": started_at,
            "finished_at": utc_now(),
        }
    )
    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8005/v1")
    parser.add_argument("--api-key", default=os.getenv("DSV4_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-v4-flash-0731-target")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=32)
    parser.add_argument("--request-timeout", type=float, default=1800)
    parser.add_argument("--token-tolerance", type=int, default=32)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=max(CONCURRENCIES),
        help="inclusive C1..N range selected by the deployment scheme",
    )
    parser.add_argument("--run-prefix", default="dsv4-r1")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--rerun-failed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repetitions < 1:
        raise RuntimeError("--repetitions must be positive")
    configured_max = getattr(args, "max_concurrency", None)
    if configured_max is None:
        concurrencies = CONCURRENCIES
    else:
        if configured_max < 1:
            raise RuntimeError("--max-concurrency must be positive")
        concurrencies = tuple(range(1, configured_max + 1))
    if args.output.exists() and args.overwrite:
        rows: list[dict[str, Any]] = []
    elif args.output.exists():
        rows = read_rows(args.output)
    else:
        rows = []

    run_prefix = getattr(args, "run_prefix", "dsv4-r1")
    run_id = f"{run_prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    existing = {
        (int(row["context_target"]), int(row["concurrency"])): row for row in rows
    }
    rows = [
        existing.get(
            (context, concurrency),
            pending_row(run_id, context, concurrency, args.repetitions),
        )
        for context in CONTEXTS
        for concurrency in concurrencies
    ]
    atomic_csv(args.output, rows)
    if args.plan_only:
        print(
            f"MATRIX_PLAN=PASS cells={len(rows)} contexts={len(CONTEXTS)} "
            f"concurrencies={len(concurrencies)} output={args.output}"
        )
        return 0

    client = JsonHttpClient(args.base_url, args.api_key, args.request_timeout)
    models = client.json("/v1/models")
    if not models.get("data"):
        raise RuntimeError("/v1/models returned no models")

    calibration: dict[int, tuple[int, int]] = {}
    for index, row in enumerate(rows):
        if row["status"] == "complete":
            continue
        if row["status"] == "failed" and not args.rerun_failed:
            continue
        context = int(row["context_target"])
        if context not in calibration:
            calibration[context] = calibrate_prompt(client, args.model, context)
            prompt_repetitions, observed = calibration[context]
            if abs(observed - context) > args.token_tolerance:
                raise RuntimeError(
                    f"could not calibrate {context} tokens within tolerance: {observed}"
                )
        prompt_repetitions, observed = calibration[context]
        print(
            f"CELL_START index={index + 1}/{len(rows)} context={context} "
            f"concurrency={row['concurrency']} calibrated={observed}",
            flush=True,
        )
        rows[index] = run_cell(
            client,
            args.model,
            row,
            prompt_repetitions,
            observed,
            args.max_output_tokens,
            args.token_tolerance,
        )
        atomic_csv(args.output, rows)
        print(
            f"CELL_END status={rows[index]['status']} "
            f"success={rows[index]['requests_success']} "
            f"failed={rows[index]['requests_failed']}",
            flush=True,
        )

    complete = sum(row["status"] == "complete" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    pending = len(rows) - complete - failed
    print(
        f"MATRIX_RUN cells={len(rows)} complete={complete} failed={failed} "
        f"pending={pending} output={args.output}"
    )
    return 0 if failed == 0 and pending == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
