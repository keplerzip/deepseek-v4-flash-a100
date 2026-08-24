#!/usr/bin/env python3
"""Exercise OpenAI, Responses, and Anthropic compatibility on a live server."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from http_api import ApiClient

# Any of these strings in an assistant response is unsafe.  Keep the exact
# spellings required by the release contract and a few observed malformed
# variants.  Callers inspect the raw response before accepting a tool call.
RAW_MARKERS = (
    "<｜DSML｜",
    "<| DSML|",
    "<|DSML|",
    "<invoke",
    "<parameter",
    "</invoke>",
    "</parameter>",
    "<function=",
    "toolcalls>",
)
TOOL_NAME = "lookup_status"
TOOL_SCHEMA = {
    "type": "object",
    "properties": {"request_id": {"type": "string"}},
    "required": ["request_id"],
    "additionalProperties": False,
}


def find_raw_marker(value: Any) -> str | None:
    text = json.dumps(value, ensure_ascii=False)
    return next((marker for marker in RAW_MARKERS if marker in text), None)


def assert_no_raw(value: Any) -> None:
    if marker := find_raw_marker(value):
        raise AssertionError(f"raw parser marker leaked: {marker}")


class StrictApiClient(ApiClient):
    def json(self, *args, **kwargs) -> dict[str, Any]:
        value = super().json(*args, **kwargs)
        assert_no_raw(value)
        return value

    def sse(self, *args, **kwargs) -> list[dict[str, Any]]:
        value = super().sse(*args, **kwargs)
        assert_no_raw(value)
        return value


def openai_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Return the status for one request identifier.",
            "parameters": TOOL_SCHEMA,
            "strict": True,
        },
    }


def responses_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": TOOL_NAME,
        "description": "Return the status for one request identifier.",
        "parameters": TOOL_SCHEMA,
        "strict": True,
    }


def anthropic_tool() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Return the status for one request identifier.",
        "input_schema": TOOL_SCHEMA,
        "strict": True,
    }


def check_tool_call(name: Any, raw_arguments: Any) -> None:
    if name != TOOL_NAME:
        raise AssertionError(f"undeclared tool returned: {name!r}")
    arguments = (
        json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    )
    if not isinstance(arguments, dict) or not isinstance(
        arguments.get("request_id"), str
    ):
        raise AssertionError(f"invalid tool arguments: {raw_arguments!r}")


def run_tests(args: argparse.Namespace) -> list[dict[str, Any]]:
    client = StrictApiClient(args.origin, args.api_key, args.timeout)
    results: list[dict[str, Any]] = []

    def run(name: str, function: Callable[[], None]) -> None:
        started = datetime.now(UTC)
        client.clear_evidence()
        try:
            function()
            status = "pass"
            detail = ""
            category = ""
            evidence = None
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            detail = f"{type(exc).__name__}: {exc}"
            category = (
                "raw_dsml_leak"
                if find_raw_marker(client.last_response) is not None
                else "api_contract_error"
            )
            evidence = {
                "request": client.last_request,
                "response": client.last_response,
            }
        result = {
            "test": name,
            "status": status,
            "detail": detail,
            "failure_category": category,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        }
        if evidence is not None:
            result["evidence"] = evidence
        results.append(result)
        print(f"{name}: {status.upper()} {detail}", flush=True)

    def models() -> None:
        response = client.json("/v1/models")
        names = {item.get("id") for item in response.get("data", [])}
        expected = {args.model, args.claude_model}
        if not expected.issubset(names):
            raise AssertionError(f"missing model aliases: {expected - names}")

    def tokenizer() -> None:
        info = client.json("/tokenizer_info")
        if not info.get("tokenizer_class"):
            raise AssertionError("/tokenizer_info omitted tokenizer_class")
        response = client.json(
            "/tokenize",
            {"model": args.model, "messages": [{"role": "user", "content": "x"}]},
        )
        if response.get("max_model_len") != 262_144:
            raise AssertionError(f"unexpected max_model_len: {response}")
        if not isinstance(response.get("count"), int):
            raise AssertionError(f"unexpected tokenize response: {response}")

    def chat_basic() -> None:
        response = client.json(
            "/v1/chat/completions",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "temperature": 0,
                "max_completion_tokens": 32,
            },
        )
        assert_no_raw(response)
        if not response.get("choices"):
            raise AssertionError("chat response has no choices")

    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["ok"]}},
        "required": ["status"],
        "additionalProperties": False,
    }

    def chat_json_object() -> None:
        response = client.json(
            "/v1/chat/completions",
            {
                "model": args.model,
                "messages": [
                    {"role": "user", "content": 'Return JSON: {"status":"ok"}'}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_completion_tokens": 64,
            },
        )
        content = response["choices"][0]["message"]["content"]
        if not isinstance(json.loads(content), dict):
            raise AssertionError(f"not a JSON object: {content}")

    def chat_json_schema() -> None:
        response = client.json(
            "/v1/chat/completions",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Status is ok."}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "status_result",
                        "strict": True,
                        "schema": schema,
                    },
                },
                "temperature": 0,
                "max_completion_tokens": 64,
            },
        )
        if json.loads(response["choices"][0]["message"]["content"]) != {"status": "ok"}:
            raise AssertionError(f"schema result mismatch: {response}")

    def guided_json() -> None:
        response = client.json(
            "/v1/chat/completions",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Status is ok."}],
                "structured_outputs": {"json": schema},
                "temperature": 0,
                "max_completion_tokens": 64,
            },
        )
        value = json.loads(response["choices"][0]["message"]["content"])
        if value != {"status": "ok"}:
            raise AssertionError(f"guided JSON mismatch: {value}")

    def tool_nonstream() -> None:
        response = client.json(
            "/v1/chat/completions",
            {
                "model": args.model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call lookup_status for request_id contract-1.",
                    }
                ],
                "tools": [openai_tool()],
                "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
                "temperature": 0,
                "max_completion_tokens": 128,
            },
        )
        assert_no_raw(response)
        calls = response["choices"][0]["message"].get("tool_calls", [])
        if not calls:
            raise AssertionError(f"required tool call missing: {response}")
        for call in calls:
            function = call.get("function", {})
            check_tool_call(function.get("name"), function.get("arguments"))

    def tool_stream() -> None:
        events = client.sse(
            "/v1/chat/completions",
            {
                "model": args.model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call lookup_status for request_id contract-2.",
                    }
                ],
                "tools": [openai_tool()],
                "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
                "temperature": 0,
                "max_completion_tokens": 128,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        assert_no_raw(events)
        calls: dict[int, dict[str, str]] = {}
        for event in events:
            for choice in event.get("choices", []):
                for call in choice.get("delta", {}).get("tool_calls", []) or []:
                    slot = calls.setdefault(
                        call.get("index", 0), {"name": "", "args": ""}
                    )
                    function = call.get("function", {})
                    slot["name"] += function.get("name") or ""
                    slot["args"] += function.get("arguments") or ""
        if not calls:
            raise AssertionError("stream returned no tool calls")
        for call in calls.values():
            check_tool_call(call["name"], call["args"])

    def responses_nonstream() -> None:
        response = client.json(
            "/v1/responses",
            {
                "model": args.model,
                "input": "Reply with OK.",
                "max_output_tokens": 64,
                "temperature": 0,
            },
        )
        assert_no_raw(response)
        if not isinstance(response.get("output"), list):
            raise AssertionError(f"Responses API omitted output: {response}")

    def responses_stream() -> None:
        events = client.sse(
            "/v1/responses",
            {
                "model": args.model,
                "input": "Reply with OK.",
                "max_output_tokens": 64,
                "temperature": 0,
                "stream": True,
            },
        )
        assert_no_raw(events)
        event_types = {event.get("type") for event in events}
        if "response.completed" not in event_types:
            raise AssertionError(f"Responses stream did not complete: {event_types}")

    anthropic_headers = {"anthropic-version": "2023-06-01"}

    def anthropic_count_tokens() -> None:
        response = client.json(
            "/v1/messages/count_tokens",
            {
                "model": args.claude_model,
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "system", "content": "Trailing Claude Code hint."},
                ],
            },
            headers=anthropic_headers,
        )
        if not isinstance(response.get("input_tokens"), int):
            raise AssertionError(f"count_tokens failed: {response}")

    def anthropic_nonstream() -> None:
        response = client.json(
            "/v1/messages",
            {
                "model": args.claude_model,
                "max_tokens": 128,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call lookup_status for request_id claude-1.",
                    },
                    {"role": "system", "content": "Trailing Claude Code hint."},
                ],
                "tools": [anthropic_tool()],
                "tool_choice": {"type": "tool", "name": TOOL_NAME},
                "temperature": 0,
            },
            headers=anthropic_headers,
        )
        assert_no_raw(response)
        calls = [
            item
            for item in response.get("content", [])
            if item.get("type") == "tool_use"
        ]
        if not calls:
            raise AssertionError(f"Anthropic tool call missing: {response}")
        for call in calls:
            check_tool_call(call.get("name"), call.get("input"))

    def anthropic_stream() -> None:
        events = client.sse(
            "/v1/messages",
            {
                "model": args.claude_model,
                "max_tokens": 64,
                "messages": [
                    {"role": "user", "content": "Reply with OK."},
                    {"role": "system", "content": "Trailing Claude Code hint."},
                ],
                "temperature": 0,
                "stream": True,
            },
            headers=anthropic_headers,
        )
        assert_no_raw(events)
        event_types = {event.get("type") for event in events}
        if not {"message_start", "message_stop"}.issubset(event_types):
            raise AssertionError(f"Anthropic stream incomplete: {event_types}")

    run("models_and_aliases", models)
    run("tokenizer_endpoints", tokenizer)
    run("chat_basic", chat_basic)
    if args.smoke:
        run("anthropic_trailing_system", anthropic_count_tokens)
        return results
    run("chat_json_object", chat_json_object)
    run("chat_json_schema", chat_json_schema)
    run("guided_json", guided_json)
    run("tool_nonstream", tool_nonstream)
    run("tool_stream", tool_stream)
    run("responses_nonstream", responses_nonstream)
    run("responses_stream", responses_stream)
    run("anthropic_count_tokens", anthropic_count_tokens)
    run("anthropic_nonstream_tool", anthropic_nonstream)
    run("anthropic_stream", anthropic_stream)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:8005")
    parser.add_argument("--api-key", default=os.getenv("DSV4_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-v4-flash-0731-target")
    parser.add_argument(
        "--claude-model", default="claude-deepseek-v4-flash-0731-target"
    )
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = run_tests(args)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "pass"
        if all(item["status"] == "pass" for item in results)
        else "fail",
        "tests": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        if payload["status"] != "pass":
            os.chmod(temporary, 0o600)
        temporary.replace(args.output)
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
