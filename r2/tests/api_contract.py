#!/usr/bin/env python3
"""Dependency-free online API, alias, Claude and prefix-cache acceptance."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

EXPECTED_MODELS = {
    "deepseek-v4-flash": 262_144,
    "deepseek-v4-flash[1M]": 1_048_576,
    "deepseek-v4-flash-claude": 262_144,
    "deepseek-v4-flash-claude[1M]": 1_048_576,
}
CODE_UNIT = """
// 缓存验收：稳定的中文注释与代码前缀。
func reconcile(ctx context.Context, revision int64) error {
    if revision < 0 { return errors.New("非法版本") }
    return nil
}
"""


class Client:
    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        base = base_url.rstrip("/")
        self.origin = base[:-3] if base.endswith("/v1") else base
        self.api_key = api_key
        self.timeout = timeout

    def json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["x-api-key"] = self.api_key
        if extra_headers:
            headers.update(extra_headers)
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.origin + path,
            data=body,
            headers=headers,
            method="GET" if body is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read(8192).decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"expected JSON object from {path}")
        return value

    def sse(
        self,
        path: str,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["x-api-key"] = self.api_key
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            self.origin + path,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode(errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read(8192).decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(f"stream transport failed for {path}: {exc}") from exc

        events: list[dict[str, Any]] = []
        normalized = raw.replace("\r\n", "\n")
        for block in normalized.split("\n\n"):
            data_lines = [
                line.removeprefix("data:").lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            data = "\n".join(data_lines)
            if data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid SSE JSON from {path}: {data[:512]}") from exc
            if not isinstance(event, dict):
                raise RuntimeError(f"expected SSE object from {path}: {event!r}")
            events.append(event)
        if not events:
            raise RuntimeError(f"empty SSE stream from {path}: {raw[:512]}")
        return events


def openai_chat(client: Client, model: str) -> dict[str, Any]:
    response = client.json(
        "/v1/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": "用一句中文回复：服务正常。"}],
            "temperature": 0,
            "max_completion_tokens": 16,
        },
    )
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"OpenAI chat returned no choices for {model}")
    return response


def anthropic_chat(client: Client, model: str) -> dict[str, Any]:
    response = client.json(
        "/v1/messages",
        {
            "model": model,
            "messages": [{"role": "user", "content": "用一句中文回复：Claude 接口正常。"}],
            "max_tokens": 16,
            "temperature": 0,
        },
        {"anthropic-version": "2023-06-01"},
    )
    if response.get("type") != "message" or not response.get("content"):
        raise RuntimeError(f"Anthropic messages returned an invalid response for {model}")
    return response


def codex_responses_stream(client: Client, model: str) -> int:
    events = client.sse(
        "/v1/responses",
        {
            "model": model,
            "input": "只输出：服务正常。",
            "tools": [
                {
                    "type": "function",
                    "name": "record_status",
                    "description": "记录服务状态；本次请求不调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                }
            ],
            "tool_choice": "none",
            "temperature": 0,
            "max_output_tokens": 64,
            "stream": True,
        },
    )
    event_types = [event.get("type") for event in events]
    failed = [
        event_type
        for event_type in event_types
        if event_type in {"error", "response.failed"}
    ]
    if failed:
        raise RuntimeError(f"Responses stream failed for {model}: {failed}")
    if "response.completed" not in event_types:
        raise RuntimeError(
            f"Responses stream ended without response.completed for {model}: "
            f"{event_types}"
        )
    return len(events)


def cache_request(client: Client, content: str, cache_salt: str) -> dict[str, Any]:
    return client.json(
        "/v1/chat/completions",
        {
            "model": "deepseek-v4-flash[1M]",
            "messages": [
                {"role": "system", "content": "保持接口与中文注释。"},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_completion_tokens": 8,
            "cache_salt": cache_salt,
        },
    )


def cached_usage(response: dict[str, Any]) -> tuple[int, int]:
    usage = response.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    prompt = usage.get("prompt_tokens")
    cached = details.get("cached_tokens")
    if not isinstance(prompt, int) or not isinstance(cached, int):
        raise RuntimeError(f"response omitted prompt cache usage: {usage}")
    return prompt, cached


def verify_short_alias_rejects_oversized_prompt(client: Client) -> int:
    """Exercise the 256K alias gate without scheduling a long GPU prefill."""
    model = "deepseek-v4-flash"
    target = EXPECTED_MODELS[model] + 1_024
    repetitions = 4_096
    prompt_tokens = 0
    content = ""
    for _ in range(5):
        content = CODE_UNIT * repetitions
        tokenized = client.json(
            "/tokenize",
            {
                "model": model,
                "messages": [{"role": "user", "content": content}],
            },
        )
        prompt_tokens = tokenized.get("count")
        if not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
            raise RuntimeError(f"/tokenize returned an invalid count: {tokenized}")
        if prompt_tokens >= target:
            break
        repetitions = max(
            repetitions + 1,
            (repetitions * target + prompt_tokens - 1) // prompt_tokens + 64,
        )
    if prompt_tokens < target or prompt_tokens >= 1_048_576:
        raise RuntimeError(
            f"could not calibrate a safe 256K rejection probe: {prompt_tokens} tokens"
        )

    try:
        client.json(
            "/v1/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "max_completion_tokens": 1,
            },
        )
    except RuntimeError as exc:
        detail = str(exc).lower()
        if not detail.startswith("http 400") or not any(
            marker in detail for marker in ("context", "length", "token")
        ):
            raise RuntimeError(
                f"256K alias failed for an unexpected reason: {exc}"
            ) from exc
        return prompt_tokens
    raise RuntimeError("256K alias accepted a prompt above its advertised context limit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://host.docker.internal:8005/v1")
    parser.add_argument("--api-key", default=os.getenv("DSV4_API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    client = Client(args.base_url, args.api_key, args.timeout)

    model_data = client.json("/v1/models").get("data")
    if not isinstance(model_data, list):
        raise RuntimeError("/v1/models returned no data list")
    observed = {
        item.get("id"): item.get("max_model_len")
        for item in model_data
        if isinstance(item, dict) and item.get("id") in EXPECTED_MODELS
    }
    if observed != EXPECTED_MODELS:
        raise RuntimeError(f"four-alias contract mismatch: {observed}")

    openai_chat(client, "deepseek-v4-flash")
    openai_chat(client, "deepseek-v4-flash[1M]")
    responses_event_counts = {
        model: codex_responses_stream(client, model)
        for model in ("deepseek-v4-flash", "deepseek-v4-flash[1M]")
    }
    anthropic_chat(client, "deepseek-v4-flash-claude")
    anthropic_chat(client, "deepseek-v4-flash-claude[1M]")
    rejected_prompt_tokens = verify_short_alias_rejects_oversized_prompt(client)

    code = CODE_UNIT * 240
    cache_salt = f"dsv4-r2-api-contract-{uuid.uuid4().hex}"
    first = cache_request(client, code, cache_salt)
    second = cache_request(client, code, cache_salt)
    first_prompt, first_cached = cached_usage(first)
    second_prompt, second_cached = cached_usage(second)
    if first_prompt != second_prompt:
        raise RuntimeError("identical cache probes reported different prompt lengths")
    second_rate = second_cached / second_prompt
    if second_rate < 0.90:
        raise RuntimeError(
            f"warm prefix-cache hit rate is below 90%: {second_cached}/{second_prompt}"
        )

    print(
        json.dumps(
            {
                "status": "pass",
                "models": observed,
                "openai_aliases_tested": 2,
                "responses_stream_aliases_tested": responses_event_counts,
                "anthropic_aliases_tested": 2,
                "short_alias_limit_probe": {
                    "prompt_tokens": rejected_prompt_tokens,
                    "expected_http_status": 400,
                },
                "cache_probe": {
                    "prompt_tokens": second_prompt,
                    "first_cached_tokens": first_cached,
                    "second_cached_tokens": second_cached,
                    "second_hit_rate": second_rate,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
