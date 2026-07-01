"""LLM client: OpenAI-compatible chat completion via httpx."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .config import ApiConfig


class LLMError(Exception):
    """Raised on HTTP/network/transport failures (retryable)."""


class LLMResponseError(Exception):
    """Raised when the API returns an error payload or empty content (retryable)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class LLMResult:
    text: str
    raw: dict


def chat_complete(
    client: httpx.Client,
    api: ApiConfig,
    prompt: str,
    retry_hint: str | None = None,
) -> LLMResult:
    """Call the chat-completions endpoint and return the assistant text.

    retry_hint, if given, is appended as an extra user message to help the model
    self-correct after a previous invalid output.
    """
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "请处理这条 issue，只输出 JSON 对象。"},
    ]
    if retry_hint:
        messages.append(
            {"role": "user", "content": retry_hint}
        )

    body: dict = {
        "model": api.model,
        "messages": messages,
        "temperature": api.temperature,
    }
    if api.json_mode:
        body["response_format"] = {"type": "json_object"}

    url = api.url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api.key}",
        "Content-Type": "application/json",
    }

    try:
        resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"network error: {exc}") from exc

    if resp.status_code >= 400:
        # 4xx for unsupported response_format is retryable without json_mode;
        # surface as LLMResponseError so pipeline can decide to retry/fallback.
        snippet = resp.text[:500]
        raise LLMResponseError(
            f"http {resp.status_code}: {snippet}", status_code=resp.status_code
        )

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"non-json response: {exc}") from exc

    # OpenAI-compatible shape: choices[0].message.content
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMResponseError(f"unexpected response shape: {json.dumps(data)[:500]}")

    if not text or not text.strip():
        raise LLMResponseError("empty content in response")

    return LLMResult(text=text, raw=data)
