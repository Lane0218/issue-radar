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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = self.session.post(f"{self.base_url}/chat/completions", json=payload, timeout=90)
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
