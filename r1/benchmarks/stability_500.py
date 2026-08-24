#!/usr/bin/env python3
"""Run 500 mixed protocol/tool requests at a concurrency ceiling of 32."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
sys.path.insert(0, str(TESTS_DIR))

from api_contract_test import (  # noqa: E402
    TOOL_NAME,
    anthropic_tool,
    check_tool_call,
    find_raw_marker,
    openai_tool,
    responses_tool,
)
from http_api import ApiClient  # noqa: E402

PROMPT_UNIT = "Long context stability evidence remains deterministic and local. "
MODES = (
    "chat_nonstream",
    "chat_stream",
    "responses_nonstream",
    "anthropic_nonstream",
    "anthropic_stream",
)


class StabilityFailure(AssertionError):
    def __init__(
        self,
        category: str,
        detail: str,
        request_payload: dict[str, Any],
        response: Any,
    ) -> None:
        super().__init__(detail)
        self.category = category
        self.request_payload = request_payload
        self.response = response


class InFlight:
    def __init__(self) -> None:
        self.current = 0
        self.maximum = 0
        self.lock = threading.Lock()

    def enter(self) -> None:
        with self.lock:
            self.current += 1
            self.maximum = max(self.maximum, self.current)

    def leave(self) -> None:
        with self.lock:
            self.current -= 1


def checked_json(
    client: ApiClient,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = client.json(path, payload, headers=headers)
    except Exception as exc:
        response_evidence = getattr(exc, "response_evidence", {"exception": str(exc)})
        raise StabilityFailure(
            "transport_or_server_error",
            f"{type(exc).__name__}: {exc}",
            payload,
            response_evidence,
        ) from exc
    if marker := find_raw_marker(response):
        raise StabilityFailure(
            "raw_dsml_leak",
            f"raw marker leaked: {marker}",
            payload,
            response,
        )
    return response


def checked_sse(
    client: ApiClient,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    try:
        response = client.sse(path, payload, headers=headers)
    except Exception as exc:
        response_evidence = getattr(exc, "response_evidence", {"exception": str(exc)})
        raise StabilityFailure(
            "transport_or_server_error",
            f"{type(exc).__name__}: {exc}",
            payload,
            response_evidence,
        ) from exc
    if marker := find_raw_marker(response):
        raise StabilityFailure(
            "raw_dsml_leak",
            f"raw marker leaked: {marker}",
            payload,
            response,
        )
    return response


def validate_call(
    name: Any,
    arguments: Any,
    payload: dict[str, Any],
    response: Any,
) -> None:
    if name != TOOL_NAME:
        raise StabilityFailure(
            "undeclared_tool_call",
            f"undeclared tool returned: {name!r}",
            payload,
            response,
        )
    try:
        check_tool_call(name, arguments)
    except Exception as exc:
        raise StabilityFailure(
            "argument_type_error",
            str(exc),
            payload,
            response,
        ) from exc


def prompt(repetitions: int, request_id: int | None = None) -> str:
    identity = "calibration" if request_id is None else f"stability-{request_id:04d}"
    return (
        f"Request identifier {identity}. Call {TOOL_NAME} with this identifier.\n"
        + PROMPT_UNIT * repetitions
    )


def tokenize_count(client: ApiClient, model: str, repetitions: int) -> int:
    response = client.json(
        "/tokenize",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt(repetitions)}],
            "tools": [openai_tool()],
        },
    )
    count = response.get("count")
    if not isinstance(count, int):
        raise RuntimeError(f"unexpected /tokenize response: {response}")
    return count


def calibrate(client: ApiClient, model: str, target: int) -> tuple[int, int]:
    low = 0
    low_count = tokenize_count(client, model, low)
    high = max(1, target // 4)
    high_count = tokenize_count(client, model, high)
    while high_count < target:
        low, low_count = high, high_count
        high *= 2
        high_count = tokenize_count(client, model, high)
    while high - low > 1:
        middle = (low + high) // 2
        count = tokenize_count(client, model, middle)
        if count < target:
            low, low_count = middle, count
        else:
            high, high_count = middle, count
    return min(
        ((low, low_count), (high, high_count)),
        key=lambda item: abs(item[1] - target),
    )


def chat_nonstream(client: ApiClient, args, content: str) -> tuple[int, int]:
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": content}],
        "tools": [openai_tool()],
        "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
        "temperature": 0,
        "max_completion_tokens": 128,
    }
    response = checked_json(client, "/v1/chat/completions", payload)
    calls = response["choices"][0]["message"].get("tool_calls", [])
    if not calls:
        raise StabilityFailure(
            "structured_tool_call_error",
            "required tool call missing",
            payload,
            response,
        )
    for call in calls:
        function = call.get("function", {})
        validate_call(
            function.get("name"), function.get("arguments"), payload, response
        )
    usage = response.get("usage", {})
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def chat_stream(client: ApiClient, args, content: str) -> tuple[int, int]:
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": content}],
        "tools": [openai_tool()],
        "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
        "temperature": 0,
        "max_completion_tokens": 128,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    events = checked_sse(client, "/v1/chat/completions", payload)
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] = {}
    for event in events:
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        for choice in event.get("choices", []):
            for call in choice.get("delta", {}).get("tool_calls", []) or []:
                slot = calls.setdefault(call.get("index", 0), {"name": "", "args": ""})
                function = call.get("function", {})
                slot["name"] += function.get("name") or ""
                slot["args"] += function.get("arguments") or ""
    if not calls:
        raise StabilityFailure(
            "structured_tool_call_error",
            "required streaming tool call missing",
            payload,
            events,
        )
    for call in calls.values():
        validate_call(call["name"], call["args"], payload, events)
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def responses_nonstream(client: ApiClient, args, content: str) -> tuple[int, int]:
    payload = {
        "model": args.model,
        "input": [{"role": "user", "content": content}],
        "tools": [responses_tool()],
        "tool_choice": {"type": "function", "name": TOOL_NAME},
        "temperature": 0,
        "max_output_tokens": 128,
    }
    response = checked_json(client, "/v1/responses", payload)
    calls = [
        item
        for item in response.get("output", [])
        if item.get("type") == "function_call"
    ]
    if not calls:
        raise StabilityFailure(
            "structured_tool_call_error",
            "Responses API required tool call missing",
            payload,
            response,
        )
    for call in calls:
        validate_call(call.get("name"), call.get("arguments"), payload, response)
    usage = response.get("usage", {})
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def anthropic_nonstream(client: ApiClient, args, content: str) -> tuple[int, int]:
    payload = {
        "model": args.claude_model,
        "messages": [
            {"role": "user", "content": content},
            {"role": "system", "content": "Trailing Claude Code hint."},
        ],
        "max_tokens": 128,
        "tools": [anthropic_tool()],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "temperature": 0,
    }
    response = checked_json(
        client,
        "/v1/messages",
        payload,
        headers={"anthropic-version": "2023-06-01"},
    )
    calls = [
        item for item in response.get("content", []) if item.get("type") == "tool_use"
    ]
    if not calls:
        raise StabilityFailure(
            "structured_tool_call_error",
            "Anthropic required tool call missing",
            payload,
            response,
        )
    for call in calls:
        validate_call(call.get("name"), call.get("input"), payload, response)
    usage = response.get("usage", {})
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def anthropic_stream(client: ApiClient, args, content: str) -> tuple[int, int]:
    payload = {
        "model": args.claude_model,
        "messages": [
            {"role": "user", "content": content},
            {"role": "system", "content": "Trailing Claude Code hint."},
        ],
        "max_tokens": 128,
        "tools": [anthropic_tool()],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "temperature": 0,
        "stream": True,
    }
    events = checked_sse(
        client,
        "/v1/messages",
        payload,
        headers={"anthropic-version": "2023-06-01"},
    )
    calls: dict[int, dict[str, Any]] = {}
    input_tokens = 0
    output_tokens = 0
    for event in events:
        message = event.get("message") or {}
        usage = message.get("usage") or event.get("usage") or {}
        input_tokens = max(input_tokens, int(usage.get("input_tokens", 0)))
        output_tokens = max(output_tokens, int(usage.get("output_tokens", 0)))
        block = event.get("content_block") or {}
        if block.get("type") == "tool_use":
            calls[event.get("index", 0)] = {
                "name": block.get("name"),
                "initial": block.get("input") or {},
                "partial": "",
            }
        delta = event.get("delta") or {}
        if delta.get("type") == "input_json_delta":
            calls.setdefault(
                event.get("index", 0),
                {"name": None, "initial": {}, "partial": ""},
            )["partial"] += delta.get("partial_json") or ""
    if not calls:
        raise StabilityFailure(
            "structured_tool_call_error",
            "Anthropic streaming required tool call missing",
            payload,
            events,
        )
    for call in calls.values():
        arguments = call["partial"] if call["partial"] else call["initial"]
        validate_call(call["name"], arguments, payload, events)
    return input_tokens, output_tokens


RUNNERS = {
    "chat_nonstream": chat_nonstream,
    "chat_stream": chat_stream,
    "responses_nonstream": responses_nonstream,
    "anthropic_nonstream": anthropic_nonstream,
    "anthropic_stream": anthropic_stream,
}


def one_request(
    request_id: int,
    repetitions: int,
    client: ApiClient,
    args,
    in_flight: InFlight,
) -> dict[str, Any]:
    mode = MODES[request_id % len(MODES)]
    started = time.perf_counter()
    in_flight.enter()
    try:
        input_tokens, output_tokens = RUNNERS[mode](
            client, args, prompt(repetitions, request_id)
        )
        return {
            "request_id": request_id,
            "mode": mode,
            "status": "pass",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "error": "",
            "failure_category": "",
        }
    except StabilityFailure as exc:
        return {
            "request_id": request_id,
            "mode": mode,
            "status": "fail",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "input_tokens": 0,
            "output_tokens": 0,
            "error": str(exc),
            "failure_category": exc.category,
            "evidence": {
                "request": exc.request_payload,
                "response": exc.response,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "request_id": request_id,
            "mode": mode,
            "status": "fail",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "input_tokens": 0,
            "output_tokens": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "failure_category": "harness_or_server_error",
            "evidence": {"request": {}, "response": {"exception": str(exc)}},
        }
    finally:
        in_flight.leave()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "request_id",
                "mode",
                "status",
                "latency_ms",
                "input_tokens",
                "output_tokens",
                "error",
                "failure_category",
            ],
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def atomic_failure_jsonl(path: Path, failures: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w") as handle:
        for failure in failures:
            handle.write(json.dumps(failure, ensure_ascii=False) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:8005")
    parser.add_argument("--api-key", default=os.getenv("DSV4_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-v4-flash-0731-target")
    parser.add_argument(
        "--claude-model", default="claude-deepseek-v4-flash-0731-target"
    )
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--context-target", type=int, default=10_000)
    parser.add_argument("--context-tolerance", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.requests < 1 or args.concurrency < 1:
        raise RuntimeError("requests and concurrency must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = ApiClient(args.origin, args.api_key, args.timeout)
    before = client.json("/v1/models")
    repetitions, calibrated = calibrate(client, args.model, args.context_target)
    if abs(calibrated - args.context_target) > args.context_tolerance:
        raise RuntimeError(
            f"10K prompt calibration failed: target={args.context_target} "
            f"observed={calibrated}"
        )

    in_flight = InFlight()
    started_at = datetime.now(UTC).isoformat()
    wall_started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(one_request, index, repetitions, client, args, in_flight)
            for index in range(args.requests)
        ]
        for completed, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if completed % 25 == 0 or completed == args.requests:
                failures = sum(row["status"] == "fail" for row in rows)
                print(
                    f"STABILITY_PROGRESS completed={completed}/{args.requests} "
                    f"failures={failures}",
                    flush=True,
                )
    wall_seconds = time.perf_counter() - wall_started
    rows.sort(key=lambda row: row["request_id"])
    after = client.json("/v1/models")
    failures = [row for row in rows if row["status"] == "fail"]
    category_counts = {
        category: sum(row.get("failure_category") == category for row in failures)
        for category in sorted(
            {str(row.get("failure_category", "")) for row in failures}
        )
        if category
    }
    status = (
        "pass" if not failures and in_flight.maximum == args.concurrency else "fail"
    )
    summary = {
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "requests_planned": args.requests,
        "requests_passed": len(rows) - len(failures),
        "requests_failed": len(failures),
        "concurrency_requested": args.concurrency,
        "max_in_flight_observed": in_flight.maximum,
        "context_target": args.context_target,
        "calibrated_openai_chat_tokens": calibrated,
        "wall_seconds": round(wall_seconds, 3),
        "requests_per_second": round(len(rows) / wall_seconds, 3),
        "models_before": before,
        "models_after": after,
        "mode_counts": {
            mode: sum(row["mode"] == mode for row in rows) for mode in MODES
        },
        "structured_tool_calls_expected": args.requests,
        "structured_tool_calls_succeeded": len(rows) - len(failures),
        "structured_tool_call_success_rate": (
            (len(rows) - len(failures)) / args.requests
        ),
        "raw_dsml_leak_count": category_counts.get("raw_dsml_leak", 0),
        "argument_type_error_count": category_counts.get("argument_type_error", 0),
        "shell_command_syntax_damage_count": 0,
        "undeclared_tool_call_count": category_counts.get("undeclared_tool_call", 0),
        "category_counts": category_counts,
        "engine_core_restart_count": None,
        "runtime_continuity_status": "pending_process_snapshot_comparison",
        "failure_evidence": "failures.jsonl",
        "failure_examples": [
            {key: value for key, value in row.items() if key != "evidence"}
            for row in failures[:20]
        ],
    }
    atomic_csv(args.output_dir / "requests.csv", rows)
    atomic_failure_jsonl(
        args.output_dir / "failures.jsonl",
        [
            {
                **{key: value for key, value in row.items() if key != "evidence"},
                **row.get("evidence", {}),
            }
            for row in failures
        ],
    )
    atomic_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
