#!/usr/bin/env python3
"""Exercise DeepSeek V4's OpenAI-compatible API and local message encoding."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, dict[str, Any], float]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw), time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"raw": raw}
        return exc.code, data, time.perf_counter() - started


def response_text(data: dict[str, Any]) -> tuple[str, str | None, str | None]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", None, None
    choice = choices[0]
    if not isinstance(choice, dict):
        return "", None, None
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        # This community vLLM commit serializes DeepSeek V4 thinking as
        # `reasoning`; some OpenAI-compatible servers use
        # `reasoning_content`. Accept both spellings during validation.
        reasoning = message.get("reasoning_content")
        reasoning_field = "reasoning_content" if reasoning is not None else None
        if reasoning is None and message.get("reasoning") is not None:
            reasoning = message.get("reasoning")
            reasoning_field = "reasoning"
        return (
            str(content or ""),
            None if reasoning is None else str(reasoning),
            reasoning_field,
        )
    return str(choice.get("text") or ""), None, None


def stream_chat(
    base_url: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first_token = None
    content: list[str] = []
    reasoning: list[str] = []
    done = False
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
            choices = event.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            for fields, target in (
                (("content",), content),
                (("reasoning_content", "reasoning"), reasoning),
            ):
                value = next(
                    (delta.get(field) for field in fields if delta.get(field) is not None),
                    None,
                )
                if value:
                    if first_token is None:
                        first_token = time.perf_counter()
                    target.append(str(value))
    ended = time.perf_counter()
    return {
        "done_marker": done,
        "content": "".join(content),
        "reasoning_content": "".join(reasoning) or None,
        "ttft_s": None if first_token is None else first_token - started,
        "latency_s": ended - started,
    }


def chat_payload(model: str, prompt: str, max_tokens: int = 96) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }


def compact(value: Any, limit: int = 800) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    if isinstance(value, dict):
        return {key: compact(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [compact(item, limit) for item in value[:10]]
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8005")
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("target-only", "dspark"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--long-chars", type=int, default=32768)
    parser.add_argument(
        "--strict-features",
        action="store_true",
        help="make optional reasoning/completions checks affect exit status",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results: list[dict[str, Any]] = []

    def record(
        name: str,
        required: bool,
        operation: Callable[[], dict[str, Any]],
        validator: Callable[[dict[str, Any]], tuple[bool, str]],
    ) -> None:
        started = time.perf_counter()
        try:
            detail = operation()
            passed, message = validator(detail)
            results.append(
                {
                    "name": name,
                    "required": required,
                    "passed": passed,
                    "message": message,
                    "elapsed_s": time.perf_counter() - started,
                    "detail": compact(detail),
                }
            )
        except Exception as exc:  # noqa: BLE001 - preserve every API failure
            results.append(
                {
                    "name": name,
                    "required": required,
                    "passed": False,
                    "message": f"{type(exc).__name__}: {exc}",
                    "elapsed_s": time.perf_counter() - started,
                }
            )

    def post_chat(payload: dict[str, Any]) -> dict[str, Any]:
        status, data, latency = request_json(
            args.base_url,
            "POST",
            "/v1/chat/completions",
            payload,
            args.timeout,
        )
        content, reasoning, reasoning_field = response_text(data)
        return {
            "status": status,
            "content": content,
            "reasoning_content": reasoning,
            "reasoning_field": reasoning_field,
            "usage": data.get("usage"),
            "latency_s": latency,
            "raw": data if status != 200 else None,
        }

    def valid_nonempty(detail: dict[str, Any]) -> tuple[bool, str]:
        text = str(detail.get("content") or detail.get("reasoning_content") or "")
        passed = detail.get("status") == 200 and bool(text.strip()) and "\ufffd" not in text
        return passed, "non-empty UTF-8 output" if passed else "empty, garbled, or HTTP failure"

    record(
        "GET /v1/models",
        True,
        lambda: {
            "status_data_latency": request_json(
                args.base_url, "GET", "/v1/models", None, args.timeout
            )
        },
        lambda detail: (
            detail["status_data_latency"][0] == 200
            and any(
                item.get("id") == args.model
                for item in detail["status_data_latency"][1].get("data", [])
                if isinstance(item, dict)
            ),
            "served model is present",
        ),
    )
    record(
        "Chinese chat and DeepSeek V4 message encoding",
        True,
        lambda: post_chat(chat_payload(args.model, "请用一句中文说明为什么天空通常是蓝色。")),
        valid_nonempty,
    )
    record(
        "simple mathematics",
        True,
        lambda: post_chat(chat_payload(args.model, "只给出答案：6乘以7等于多少？", 32)),
        lambda detail: (
            valid_nonempty(detail)[0] and "42" in str(detail.get("content", "")),
            "expected answer 42",
        ),
    )
    record(
        "Python code generation",
        True,
        lambda: post_chat(
            chat_payload(args.model, "写一个 Python 函数 fibonacci(n)，只返回代码。", 160)
        ),
        lambda detail: (
            valid_nonempty(detail)[0]
            and ("def " in str(detail.get("content", "")) or "lambda" in str(detail.get("content", ""))),
            "Python function syntax found",
        ),
    )
    record(
        "TCL/EDA code generation",
        True,
        lambda: post_chat(
            chat_payload(
                args.model,
                "生成一段 Synopsys 风格 TCL：创建周期 10ns、名为 clk 的时钟，端口也是 clk。只给代码。",
                128,
            )
        ),
        lambda detail: (
            valid_nonempty(detail)[0]
            and "create_clock" in str(detail.get("content", "")),
            "create_clock found",
        ),
    )

    json_request = chat_payload(
        args.model, '只输出一个 JSON 对象，字段为 "status" 和 "value"，值分别为 "ok" 和 42。', 96
    )
    json_request["response_format"] = {"type": "json_object"}

    def validate_json(detail: dict[str, Any]) -> tuple[bool, str]:
        if not valid_nonempty(detail)[0]:
            return False, "empty or HTTP failure"
        try:
            value = json.loads(str(detail["content"]))
        except json.JSONDecodeError:
            return False, "content is not a bare JSON value"
        return isinstance(value, dict), "valid JSON object"

    record("JSON response_format", True, lambda: post_chat(json_request), validate_json)

    for effort in ("low", "high", "max"):
        payload = chat_payload(
            args.model,
            "求 17×19，并简要说明思路。",
            128,
        )
        payload["reasoning_effort"] = effort
        record(
            f"reasoning_effort={effort}",
            args.strict_features,
            lambda payload=payload: post_chat(payload),
            valid_nonempty,
        )

    reasoning_payload = chat_payload(
        args.model,
        "请先思考再回答：一个正方形边长 9，面积是多少？",
        160,
    )
    reasoning_payload["reasoning_effort"] = "high"
    def validate_reasoning_content(detail: dict[str, Any]) -> tuple[bool, str]:
        if not valid_nonempty(detail)[0]:
            return False, "reasoning output is empty or the HTTP request failed"
        field = detail.get("reasoning_field")
        if field == "reasoning_content":
            return True, "exact message.reasoning_content field returned"
        if field == "reasoning":
            return False, "thinking was returned under message.reasoning, not reasoning_content"
        return False, "no reasoning field returned"

    record(
        "reasoning_content exact field",
        args.strict_features,
        lambda: post_chat(reasoning_payload),
        validate_reasoning_content,
    )

    multi_turn = {
        "model": args.model,
        "messages": [
            {"role": "user", "content": "记住数字 731，只回复收到。"},
            {"role": "assistant", "content": "收到。"},
            {"role": "user", "content": "我让你记住的数字是什么？只写数字。"},
        ],
        "temperature": 0,
        "max_tokens": 32,
    }
    record(
        "multi-turn chat",
        True,
        lambda: post_chat(multi_turn),
        lambda detail: (
            valid_nonempty(detail)[0] and "731" in str(detail.get("content", "")),
            "multi-turn value recovered",
        ),
    )

    repeated = "离线部署验证段落：A100、NVLink、KV cache、DSpark。"
    long_prompt = (repeated * (args.long_chars // len(repeated) + 1))[: args.long_chars]
    long_prompt += "\n请只回复：长文本已收到。"
    record(
        "long chat prompt",
        True,
        lambda: post_chat(chat_payload(args.model, long_prompt, 64)),
        valid_nonempty,
    )

    stream_payload = chat_payload(args.model, "从 1 数到 10，用逗号分隔。", 64)
    stream_payload["stream"] = True
    record(
        "streaming chat",
        True,
        lambda: stream_chat(args.base_url, stream_payload, args.timeout),
        lambda detail: (
            detail.get("done_marker") is True
            and bool(detail.get("content") or detail.get("reasoning_content"))
            and detail.get("ttft_s") is not None,
            "SSE stream yielded tokens and [DONE]",
        ),
    )

    completion_payload = {
        "model": args.model,
        "prompt": "Python 中列表推导式的一个例子是：",
        "temperature": 0,
        "max_tokens": 64,
    }
    def completion_operation() -> dict[str, Any]:
        status, data, latency = request_json(
            args.base_url,
            "POST",
            "/v1/completions",
            completion_payload,
            args.timeout,
        )
        content, _, _ = response_text(data)
        return {
            "status": status,
            "content": content,
            "latency_s": latency,
            "raw": data if status != 200 else None,
        }

    record("POST /v1/completions", args.strict_features, completion_operation, valid_nonempty)

    summary = {
        "mode": args.mode,
        "model": args.model,
        "base_url": args.base_url,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strict_features": args.strict_features,
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "required_failed": sum(
            item["required"] and not item["passed"] for item in results
        ),
        "results": results,
        "interpretation": (
            "API checks exercise the built-in DeepSeek V4 chat encoding and output "
            "parsers. They do not establish model accuracy beyond the explicit assertions."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for item in results:
        print(f"{'PASS' if item['passed'] else 'FAIL'} {item['name']}: {item['message']}")
    print(f"SMOKE_RESULT={args.output}")
    return 1 if summary["required_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
