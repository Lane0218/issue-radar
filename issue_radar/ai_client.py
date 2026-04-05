from __future__ import annotations

import json
import os
import re
from typing import Any

import requests


class AIClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = int(os.environ.get("AI_TIMEOUT_SECONDS", "120"))
        self.max_tokens = int(os.environ.get("AI_MAX_TOKENS", "500"))
        self.reasoning_effort = _normalize_reasoning_effort(os.environ.get("AI_REASONING_EFFORT"))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    @classmethod
    def from_env(cls) -> "AIClient":
        base_url = os.environ.get("AI_BASE_URL")
        api_key = os.environ.get("AI_API_KEY")
        model = os.environ.get("AI_MODEL")
        if not base_url or not api_key or not model:
            raise RuntimeError("AI_BASE_URL, AI_API_KEY and AI_MODEL are required.")
        return cls(base_url=base_url, api_key=api_key, model=model)

    def analyze_issue(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if _should_send_reasoning_effort(self.model):
            payload["reasoning_effort"] = self.reasoning_effort
        if _should_disable_thinking(self.model):
            payload["enable_thinking"] = False
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        return _extract_json(content)


def _extract_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Model response does not contain JSON: {content[:400]}")
    return json.loads(match.group(0))


def _should_disable_thinking(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized == "qwen3.5-flash" or normalized.startswith("qwen3.5-flash-")


def _should_send_reasoning_effort(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith("gpt-5")


def _normalize_reasoning_effort(value: str | None) -> str:
    normalized = str(value or "medium").strip().lower().replace(" ", "_")
    if normalized not in {"low", "medium", "high"}:
        return "medium"
    return normalized
