"""Small dependency-free JSON and SSE client used by target acceptance tests."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, message: str, response_evidence: Any) -> None:
        super().__init__(message)
        self.response_evidence = response_evidence


class ApiClient:
    def __init__(self, origin: str, api_key: str, timeout: float) -> None:
        self.origin = origin.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.last_request: dict[str, Any] | None = None
        self.last_response: Any = None

    def clear_evidence(self) -> None:
        self.last_request = None
        self.last_response = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            headers.update(extra)
        return headers

    def open(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ):
        # Deliberately omit headers: Authorization must never enter evidence.
        self.last_request = {"path": path, "payload": payload}
        self.last_response = None
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.origin + path,
            data=data,
            headers=self._headers(headers),
            method="GET" if payload is None else "POST",
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            self.last_response = {"http_status": exc.code, "body": detail}
            raise ApiError(
                f"HTTP {exc.code} for {path}: {detail}", self.last_response
            ) from exc

    def json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self.open(path, payload, headers=headers) as response:
            raw_body = response.read()
        try:
            value = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.last_response = {
                "invalid_json_body": raw_body.decode(errors="replace")
            }
            raise ApiError(
                f"invalid JSON response from {path}: {exc}", self.last_response
            ) from exc
        self.last_response = value
        if not isinstance(value, dict):
            raise ApiError(f"expected JSON object from {path}", value)
        return value

    def sse(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        request_headers = {"Accept": "text/event-stream"}
        if headers:
            request_headers.update(headers)
        events: list[dict[str, Any]] = []
        with self.open(path, payload, headers=request_headers) as response:
            for raw_line in response:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    value = json.loads(data)
                except json.JSONDecodeError as exc:
                    self.last_response = {
                        "parsed_events": events,
                        "invalid_sse_line": line,
                    }
                    raise ApiError(
                        f"invalid SSE JSON from {path}: {exc}", self.last_response
                    ) from exc
                if isinstance(value, dict):
                    events.append(value)
        self.last_response = events
        return events
