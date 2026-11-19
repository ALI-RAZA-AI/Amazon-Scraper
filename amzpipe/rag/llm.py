"""Minimal LLM client (no SDKs needed): OpenAI-compatible APIs, Ollama, Anthropic."""
from __future__ import annotations

import json
import os
import re

import requests

DEFAULT_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class LLMClient:
    def __init__(self, cfg: dict):
        self.provider = (cfg.get("provider") or "none").lower()
        self.model = cfg.get("model")
        self.temperature = cfg.get("temperature", 0.0)
        self.base_url = cfg.get("base_url")
        key_env = cfg.get("api_key_env") or DEFAULT_KEY_ENV.get(self.provider, "")
        if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
            # never echo the value: it is usually the key itself, pasted into the wrong setting
            raise RuntimeError("rag.llm.api_key_env must be the NAME of an environment variable "
                               "(e.g. ANTHROPIC_API_KEY), not the API key itself")
        self.api_key = os.environ.get(key_env, "")
        if self.provider == "ollama" and not self.base_url:
            self.base_url = "http://localhost:11434/v1"
        if self.provider == "openai" and not self.base_url:
            self.base_url = "https://api.openai.com/v1"
        if self.provider in ("openai", "anthropic") and not self.api_key:
            raise RuntimeError(f"LLM provider '{self.provider}' needs an API key in env var '{key_env}'")

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def complete_json(self, system: str, user: str) -> dict:
        if self.provider in ("openai", "ollama"):
            r = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key or 'ollama'}"},
                json={"model": self.model, "temperature": self.temperature,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=120)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        elif self.provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={"model": self.model, "max_tokens": 1024, "temperature": self.temperature,
                      "system": system, "messages": [{"role": "user", "content": user}]},
                timeout=120)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json()["content"])
        else:
            raise RuntimeError("LLM disabled")
        return parse_json(text)


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
