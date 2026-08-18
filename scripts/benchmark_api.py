#!/usr/bin/env python3
"""Run reproducible streaming completion benchmarks against a local vLLM API."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


def json_request(
    base_url: str, path: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_text(base_url: str, path: str, timeout: float) -> str:
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_int_list(value: str) -> list[int]:
    result = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def metric_snapshot(base_url: str, timeout: float) -> dict[str, float]:
    try:
        text = get_text(base_url, "/metrics", timeout)
    except Exception:  # noqa: BLE001 - metrics absence is recorded, not fatal
        return {}
    wanted = {
        "vllm:spec_decode_num_drafts_total": "drafts",
        "vllm:spec_decode_num_draft_tokens_total": "draft_tokens",
        "vllm:spec_decode_num_accepted_tokens_total": "accepted_tokens",
    }
    result = {value: 0.0 for value in wanted.values()}
    found: set[str] = set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            series, raw_value = line.rsplit(None, 1)
            name = series.split("{", 1)[0]
            key = wanted.get(name)
            if key:
                result[key] += float(raw_value)
                found.add(key)
        except (ValueError, IndexError):
            continue
    return result if found else {}


class PromptFactory:
    def __init__(self, base_url: str, model: str, timeout: float, output_dir: Path):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.output_dir = output_dir
        self.cache: dict[int, tuple[str, int]] = {}

    def tokenize(self, prompt: str) -> list[int]:
        data = json_request(
            self.base_url,
            "/tokenize",
            {"model": self.model, "prompt": prompt, "add_special_tokens": False},
            self.timeout,
        )
        tokens = data.get("tokens")
        if not isinstance(tokens, list):
            raise RuntimeError(f"/tokenize returned no tokens: {data}")
        return [int(token) for token in tokens]

    def detokenize(self, tokens: list[int]) -> str:
        data = json_request(
            self.base_url,
            "/detokenize",
            {"model": self.model, "tokens": tokens},
            self.timeout,
        )
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            raise RuntimeError(f"/detokenize returned no prompt: {data}")
        return prompt

    def exact_prompt(self, target_tokens: int) -> tuple[str, int]:
        if target_tokens in self.cache:
            return self.cache[target_tokens]
        seed = (
            "Offline benchmark for DeepSeek V4 on eight A100 GPUs. "
            "Measure prefill and decode without assuming a performance result. "
        )
        repeats = max(1, target_tokens // 12)
        while True:
            tokens = self.tokenize(seed * repeats)
            if len(tokens) >= target_tokens:
                break
            repeats *= 2
        prompt = self.detokenize(tokens[:target_tokens])
        actual = len(self.tokenize(prompt))
        if actual != target_tokens:
            raise RuntimeError(
                f"server tokenizer round trip mismatch: target={target_tokens}, actual={actual}"
            )
        path = self.output_dir / f"generated-prompt-{target_tokens}-tokens.txt"
        if not path.exists():
            path.write_text(prompt, encoding="utf-8")
        self.cache[target_tokens] = (prompt, actual)
        return prompt, actual


def stream_completion(
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    barrier: threading.Barrier | None,
    request_index: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": 0,
        "max_tokens": max_tokens,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
        "cache_salt": f"offline-bench-{uuid.uuid4().hex}",
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if barrier is not None:
        barrier.wait(timeout=timeout)
    started_wall = time.time()
    started = time.perf_counter()
    first_token_at: float | None = None
    last_token_at: float | None = None
    chunks = 0
    characters = 0
    usage: dict[str, Any] = {}
    done = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                event = json.loads(data)
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                choices = event.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                text = choices[0].get("text")
                if text:
                    now = time.perf_counter()
                    if first_token_at is None:
                        first_token_at = now
                    last_token_at = now
                    chunks += 1
                    characters += len(str(text))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        ended = time.perf_counter()
        return {
            "request_index": request_index,
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "start_wall_s": started_wall,
            "end_wall_s": time.time(),
            "e2e_latency_s": ended - started,
        }
    ended = time.perf_counter()
    completion_tokens = int(usage.get("completion_tokens") or chunks)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    ttft = None if first_token_at is None else first_token_at - started
    decode_span = (
        None
        if first_token_at is None or last_token_at is None
        else max(0.0, last_token_at - first_token_at)
    )
    decode_tps = None
    mean_itl = None
    if decode_span is not None and decode_span > 0 and completion_tokens > 1:
        decode_tps = (completion_tokens - 1) / decode_span
        mean_itl = decode_span / (completion_tokens - 1)
    return {
        "request_index": request_index,
        "success": done and first_token_at is not None,
        "error": None if done else "stream ended without [DONE] or output token",
        "start_wall_s": started_wall,
        "end_wall_s": time.time(),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "requested_output_tokens": max_tokens,
        "stream_chunks": chunks,
        "characters": characters,
        "ttft_s": ttft,
        "prefill_latency_proxy_s": ttft,
        "prompt_tokens_per_s_proxy": (
            None if not ttft or not prompt_tokens else prompt_tokens / ttft
        ),
        "decode_tokens_per_s": decode_tps,
        "mean_inter_token_latency_s": mean_itl,
        "e2e_latency_s": ended - started,
    }


def summarize_requests(requests: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [item for item in requests if item.get("success")]

    def numbers(field: str) -> list[float]:
        return [
            float(item[field])
            for item in successful
            if isinstance(item.get(field), (int, float))
        ]

    starts = numbers("start_wall_s")
    ends = numbers("end_wall_s")
    output_tokens = sum(int(item.get("completion_tokens", 0)) for item in successful)
    wall_span = max(ends) - min(starts) if starts and ends else 0
    return {
        "requests": len(requests),
        "successful_requests": len(successful),
        "error_requests": len(requests) - len(successful),
        "aggregate_output_tokens": output_tokens,
        "aggregate_decode_throughput_tokens_per_s": (
            output_tokens / wall_span if wall_span > 0 else None
        ),
        "ttft_p50_s": percentile(numbers("ttft_s"), 0.50),
        "ttft_p95_s": percentile(numbers("ttft_s"), 0.95),
        "prefill_latency_proxy_p50_s": percentile(
            numbers("prefill_latency_proxy_s"), 0.50
        ),
        "prefill_latency_proxy_p95_s": percentile(
            numbers("prefill_latency_proxy_s"), 0.95
        ),
        "prompt_tokens_per_s_proxy_p50": percentile(
            numbers("prompt_tokens_per_s_proxy"), 0.50
        ),
        "prompt_tokens_per_s_proxy_p95": percentile(
            numbers("prompt_tokens_per_s_proxy"), 0.95
        ),
        "e2e_p50_s": percentile(numbers("e2e_latency_s"), 0.50),
        "e2e_p95_s": percentile(numbers("e2e_latency_s"), 0.95),
        "decode_tps_mean": (
            statistics.fmean(numbers("decode_tokens_per_s"))
            if numbers("decode_tokens_per_s")
            else None
        ),
        "decode_tps_p50": percentile(numbers("decode_tokens_per_s"), 0.50),
        "decode_tps_p95": percentile(numbers("decode_tokens_per_s"), 0.95),
        "itl_p50_s": percentile(numbers("mean_inter_token_latency_s"), 0.50),
        "itl_p95_s": percentile(numbers("mean_inter_token_latency_s"), 0.95),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8005")
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("target-only", "dspark"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt-lengths", type=parse_int_list, default=[1024, 11000])
    parser.add_argument("--output-lengths", type=parse_int_list, default=[128, 512])
    parser.add_argument("--concurrency", type=parse_int_list, default=[1, 2])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--baseline-prompt-file", type=Path)
    parser.add_argument("--no-warmup", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    json_path = args.output_dir / f"benchmark-{args.mode}-{timestamp}.json"
    csv_path = args.output_dir / f"benchmark-{args.mode}-{timestamp}.csv"
    prompt_factory = PromptFactory(args.base_url, args.model, args.timeout, args.output_dir)

    baseline_prompt: str | None = None
    baseline_note = "No original MiniMax prompt was supplied; generated exact-token prompts are used."
    if args.baseline_prompt_file:
        baseline_prompt = args.baseline_prompt_file.read_text(encoding="utf-8")
        baseline_note = f"Used caller-supplied BASELINE_PROMPT_FILE={args.baseline_prompt_file}"

    warmup: dict[str, Any] | None = None
    if not args.no_warmup:
        prompt, _ = prompt_factory.exact_prompt(128)
        warmup_started = time.perf_counter()
        warmup = stream_completion(
            args.base_url, args.model, prompt, 32, args.timeout, None, 0
        )
        warmup["wall_time_s"] = time.perf_counter() - warmup_started
        if not warmup.get("success"):
            raise RuntimeError(f"warmup failed: {warmup}")

    metrics_before = metric_snapshot(args.base_url, 30)
    groups: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for prompt_target in args.prompt_lengths:
        if baseline_prompt is not None and 10000 <= prompt_target <= 11000:
            prompt = baseline_prompt
            actual_target = len(prompt_factory.tokenize(prompt))
            prompt_source = "BASELINE_PROMPT_FILE"
        else:
            prompt, actual_target = prompt_factory.exact_prompt(prompt_target)
            prompt_source = "generated via /tokenize + /detokenize"
        for output_tokens in args.output_lengths:
            for concurrency in args.concurrency:
                requests: list[dict[str, Any]] = []
                for repeat in range(args.repeats):
                    barrier = threading.Barrier(concurrency) if concurrency > 1 else None
                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        futures = [
                            executor.submit(
                                stream_completion,
                                args.base_url,
                                args.model,
                                prompt,
                                output_tokens,
                                args.timeout,
                                barrier,
                                index,
                            )
                            for index in range(concurrency)
                        ]
                        batch = [future.result() for future in futures]
                    for item in batch:
                        item.update(
                            {
                                "repeat": repeat,
                                "prompt_target_tokens": prompt_target,
                                "prompt_factory_tokens": actual_target,
                                "prompt_source": prompt_source,
                                "concurrency": concurrency,
                            }
                        )
                    requests.extend(batch)
                group = {
                    "prompt_target_tokens": prompt_target,
                    "prompt_factory_tokens": actual_target,
                    "prompt_source": prompt_source,
                    "output_tokens": output_tokens,
                    "concurrency": concurrency,
                    "summary": summarize_requests(requests),
                    "requests": requests,
                }
                groups.append(group)
                all_rows.extend(requests)
                summary = group["summary"]
                print(
                    f"prompt={prompt_target} output={output_tokens} concurrency={concurrency} "
                    f"ok={summary['successful_requests']}/{summary['requests']} "
                    f"ttft_p50={summary['ttft_p50_s']} decode_mean={summary['decode_tps_mean']}"
                )

    metrics_after = metric_snapshot(args.base_url, 30)
    spec_metrics: dict[str, Any] = {"available": False}
    if metrics_before and metrics_after:
        delta = {
            key: max(0.0, metrics_after.get(key, 0.0) - metrics_before.get(key, 0.0))
            for key in ("drafts", "draft_tokens", "accepted_tokens")
        }
        spec_metrics = {
            "available": True,
            "delta": delta,
            "acceptance_rate": (
                delta["accepted_tokens"] / delta["draft_tokens"]
                if delta["draft_tokens"]
                else None
            ),
            "mean_acceptance_length_including_bonus": (
                1 + delta["accepted_tokens"] / delta["drafts"]
                if delta["drafts"]
                else None
            ),
            "draft_latency_s": None,
            "draft_latency_note": (
                "This commit exports draft/accepted counters but no direct draft-latency metric."
            ),
        }

    result = {
        "schema_version": 1,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": args.mode,
        "model": args.model,
        "base_url": args.base_url,
        "matrix": {
            "prompt_lengths": args.prompt_lengths,
            "output_lengths": args.output_lengths,
            "concurrency": args.concurrency,
            "repeats": args.repeats,
        },
        "warmup": warmup,
        "baseline_prompt_note": baseline_note,
        "definitions": {
            "ttft": "client send to first non-empty streamed text chunk",
            "prefill_latency_proxy": "TTFT; includes scheduling and transport overhead",
            "itl": "decode span divided by completion_tokens - 1",
            "aggregate_throughput": "sum completion tokens divided by concurrent group wall span",
        },
        "speculative_metrics": spec_metrics,
        "groups": groups,
    }
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    fieldnames = sorted({key for row in all_rows for key in row})
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"BENCHMARK_JSON={json_path}")
    print(f"BENCHMARK_CSV={csv_path}")
    return 1 if any(group["summary"]["error_requests"] for group in groups) else 0


if __name__ == "__main__":
    raise SystemExit(main())
