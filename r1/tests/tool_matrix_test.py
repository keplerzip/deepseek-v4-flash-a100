#!/usr/bin/env python3
"""Exercise the complete target-only Codex tool-call contract on a live API."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_contract_test import find_raw_marker
from http_api import ApiClient

TOOL_NAME = "exercise_codex_types"
CONCURRENCIES = (1, 4, 8)
STREAM_MODES = (False, True)
TOOL_CHOICES = ("auto", "required", "none")
THINKING_MODES = (False, True)
EXPECTED_COMMAND = (
    "python3 - <<'E0F'\n"
    "import re\n"
    "text = 'abc123'\n"
    "print(re.sub(r'\\d+', 'N', text))\n"
    "E0F\n"
)
SCHEMA_TYPES = ("string", "boolean", "integer", "number", "array", "object")


class MatrixFailure(AssertionError):
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


def expected_arguments(request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "enabled": True,
        "count": 7,
        "ratio": 2.5,
        "items": ["alpha", "beta"],
        "metadata": {"owner": "codex", "safe": True},
        "command": EXPECTED_COMMAND,
    }


def tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Round-trip typed Codex arguments without executing them.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "count": {"type": "integer"},
                    "ratio": {"type": "number"},
                    "items": {"type": "array", "items": {"type": "string"}},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                            "safe": {"type": "boolean"},
                        },
                        "required": ["owner", "safe"],
                        "additionalProperties": False,
                    },
                    "command": {"type": "string"},
                },
                "required": [
                    "request_id",
                    "enabled",
                    "count",
                    "ratio",
                    "items",
                    "metadata",
                    "command",
                ],
                "additionalProperties": False,
            },
        },
    }


def build_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": (
                f"stream-{int(stream)}_choice-{choice}_"
                f"thinking-{int(thinking)}_c{concurrency}"
            ),
            "stream": stream,
            "tool_choice": choice,
            "thinking": thinking,
            "concurrency": concurrency,
        }
        for stream, choice, thinking, concurrency in itertools.product(
            STREAM_MODES,
            TOOL_CHOICES,
            THINKING_MODES,
            CONCURRENCIES,
        )
    ]


def request_payload(
    args: argparse.Namespace,
    case: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    expected = expected_arguments(request_id)
    if case["tool_choice"] == "none":
        prompt = (
            "Do not call any tool. Reply with the plain text SAFE. "
            "Never emit tool syntax or DSML markup."
        )
    else:
        serialized = json.dumps(expected, ensure_ascii=False, indent=2)
        prompt = (
            f"Call {TOOL_NAME} exactly once. Use an argument object byte-for-byte "
            "equivalent after JSON decoding to the following JSON. Preserve every "
            "newline in command, including the newline after E0F.\n"
            f"```json\n{serialized}\n```"
        )
    return {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [tool_definition()],
        "tool_choice": case["tool_choice"],
        "chat_template_kwargs": {"enable_thinking": case["thinking"]},
        "temperature": 0,
        "max_completion_tokens": 512,
        "stream": case["stream"],
        "stream_options": {"include_usage": True} if case["stream"] else None,
        "cache_salt": f"target-tool-matrix-{request_id}-{time.time_ns()}",
    }


def stream_calls(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    calls: dict[int, dict[str, str]] = {}
    for event in events:
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index", 0))
                slot = calls.setdefault(index, {"name": "", "arguments": ""})
                function = call.get("function") or {}
                slot["name"] += function.get("name") or ""
                slot["arguments"] += function.get("arguments") or ""
    return [calls[index] for index in sorted(calls)]


def nonstream_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message") or {}
    result = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        result.append(
            {"name": function.get("name"), "arguments": function.get("arguments")}
        )
    return result


def validate_arguments(
    raw_arguments: Any,
    expected: dict[str, Any],
    payload: dict[str, Any],
    response: Any,
) -> None:
    try:
        value = (
            json.loads(raw_arguments)
            if isinstance(raw_arguments, str)
            else raw_arguments
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise MatrixFailure(
            "argument_type_error",
            f"tool arguments are not valid JSON: {exc}",
            payload,
            response,
        ) from exc
    expected_types = {
        "request_id": str,
        "enabled": bool,
        "count": int,
        "ratio": float,
        "items": list,
        "metadata": dict,
        "command": str,
    }
    if not isinstance(value, dict):
        raise MatrixFailure(
            "argument_type_error", "tool arguments are not an object", payload, response
        )
    for name, expected_type in expected_types.items():
        if type(value.get(name)) is not expected_type:  # noqa: E721
            raise MatrixFailure(
                "argument_type_error",
                f"{name} has type {type(value.get(name)).__name__}, "
                f"expected {expected_type.__name__}",
                payload,
                response,
            )
    command = value["command"]
    if command != EXPECTED_COMMAND:
        raise MatrixFailure(
            "shell_command_syntax_damage",
            "multiline heredoc changed during the tool-call round trip",
            payload,
            response,
        )
    # Parse only.  The model-produced command is never executed.
    syntax = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if syntax.returncode != 0:
        raise MatrixFailure(
            "shell_command_syntax_damage",
            f"bash -n rejected heredoc: {syntax.stderr.strip()}",
            payload,
            response,
        )
    if value != expected:
        raise MatrixFailure(
            "argument_value_error",
            f"decoded arguments changed: expected={expected!r}, observed={value!r}",
            payload,
            response,
        )


def validate_response(
    case: dict[str, Any],
    request_id: str,
    payload: dict[str, Any],
    response: Any,
) -> None:
    if marker := find_raw_marker(response):
        raise MatrixFailure(
            "raw_dsml_leak",
            f"unsafe parser marker leaked: {marker}",
            payload,
            response,
        )
    calls = stream_calls(response) if case["stream"] else nonstream_calls(response)
    if case["tool_choice"] == "none":
        if calls:
            raise MatrixFailure(
                "tool_choice_none_violation",
                "tool_choice=none returned a structured tool call",
                payload,
                response,
            )
        return
    if len(calls) != 1:
        raise MatrixFailure(
            "structured_tool_call_error",
            f"expected exactly one tool call, observed {len(calls)}",
            payload,
            response,
        )
    call = calls[0]
    if call.get("name") != TOOL_NAME:
        raise MatrixFailure(
            "undeclared_tool_call",
            f"unexpected tool name: {call.get('name')!r}",
            payload,
            response,
        )
    validate_arguments(
        call.get("arguments"), expected_arguments(request_id), payload, response
    )


def run_one(
    args: argparse.Namespace,
    client: ApiClient,
    case: dict[str, Any],
    index: int,
    barrier: threading.Barrier,
    in_flight: InFlight,
) -> dict[str, Any]:
    request_id = f"{case['case_id']}-{index}"
    payload = request_payload(args, case, request_id)
    started = time.perf_counter()
    barrier.wait()
    in_flight.enter()
    try:
        response = (
            client.sse("/v1/chat/completions", payload)
            if case["stream"]
            else client.json("/v1/chat/completions", payload)
        )
        validate_response(case, request_id, payload, response)
        return {
            "request_id": request_id,
            "status": "pass",
            "category": "",
            "detail": "",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except MatrixFailure as exc:
        return {
            "request_id": request_id,
            "status": "fail",
            "category": exc.category,
            "detail": str(exc),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "evidence": {
                "request": exc.request_payload,
                "response": exc.response,
            },
        }
    except Exception as exc:  # noqa: BLE001
        response_evidence = getattr(exc, "response_evidence", {"exception": str(exc)})
        return {
            "request_id": request_id,
            "status": "fail",
            "category": "transport_or_server_error",
            "detail": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "evidence": {"request": payload, "response": response_evidence},
        }
    finally:
        in_flight.leave()


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
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
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def plan() -> dict[str, Any]:
    cases = build_cases()
    return {
        "method": "target",
        "cases": len(cases),
        "requests": sum(int(case["concurrency"]) for case in cases),
        "streams": list(STREAM_MODES),
        "tool_choices": list(TOOL_CHOICES),
        "thinking": list(THINKING_MODES),
        "concurrencies": list(CONCURRENCIES),
        "parameter_types": list(SCHEMA_TYPES),
        "multiline_heredoc": True,
    }


def main() -> int:
    args = parse_args()
    if args.plan_only:
        print(json.dumps(plan(), sort_keys=True))
        return 0
    if args.output_dir is None:
        raise RuntimeError("--output-dir is required unless --plan-only is used")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = ApiClient(args.origin, args.api_key, args.timeout)
    cases = build_cases()
    case_results: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for number, case in enumerate(cases, start=1):
        concurrency = int(case["concurrency"])
        barrier = threading.Barrier(concurrency)
        in_flight = InFlight()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(run_one, args, client, case, index, barrier, in_flight)
                for index in range(concurrency)
            ]
            rows = [future.result() for future in as_completed(futures)]
        rows.sort(key=lambda row: row["request_id"])
        for row in rows:
            row.update({key: value for key, value in case.items()})
        all_rows.extend(rows)
        failures = sum(row["status"] == "fail" for row in rows)
        observed_ok = in_flight.maximum == concurrency
        case_results.append(
            {
                **case,
                "requests": len(rows),
                "passed": len(rows) - failures,
                "failed": failures,
                "max_in_flight_observed": in_flight.maximum,
                "status": "pass" if failures == 0 and observed_ok else "fail",
            }
        )
        print(
            f"TOOL_MATRIX_CASE {number}/{len(cases)} id={case['case_id']} "
            f"passed={len(rows) - failures} failed={failures} "
            f"max_in_flight={in_flight.maximum}",
            flush=True,
        )

    failures = [row for row in all_rows if row["status"] == "fail"]
    category_counts = {
        category: sum(row["category"] == category for row in failures)
        for category in sorted({row["category"] for row in failures})
    }
    structured_expected = sum(row["tool_choice"] != "none" for row in all_rows)
    structured_success = sum(
        row["tool_choice"] != "none" and row["status"] == "pass" for row in all_rows
    )
    summary = {
        "status": "pass"
        if not failures and all(case["status"] == "pass" for case in case_results)
        else "fail",
        "generated_at": datetime.now(UTC).isoformat(),
        "plan": plan(),
        "requests_passed": len(all_rows) - len(failures),
        "requests_failed": len(failures),
        "structured_tool_calls_expected": structured_expected,
        "structured_tool_calls_succeeded": structured_success,
        "structured_tool_call_success_rate": (
            structured_success / structured_expected if structured_expected else 0.0
        ),
        "raw_dsml_leak_count": category_counts.get("raw_dsml_leak", 0),
        "argument_type_error_count": category_counts.get("argument_type_error", 0),
        "shell_command_syntax_damage_count": category_counts.get(
            "shell_command_syntax_damage", 0
        ),
        "undeclared_tool_call_count": category_counts.get("undeclared_tool_call", 0),
        "category_counts": category_counts,
        "cases": case_results,
        "failure_evidence": "failures.jsonl",
    }
    public_rows = [
        {key: value for key, value in row.items() if key != "evidence"}
        for row in all_rows
    ]
    atomic_json(args.output_dir / "summary.json", summary)
    atomic_json(args.output_dir / "requests.json", public_rows)
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
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
