"""
Pluggable LLM Backend — Route AI requests to different providers per component.

Currently supports Claude CLI (default). Each AI component (scoring, cover
letters, resume tailoring, etc.) can be independently configured to use a
different backend.

Configuration in profile.yaml:
    ai:
      default_backend: claude_cli
      backends:
        claude_cli:
          timeout: 120
      components:
        scoring: claude_cli
        cover_letter: claude_cli
"""

import os
import subprocess
import json
import re
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """Abstract base for all LLM backends."""

    @abstractmethod
    def ask(self, prompt: str, timeout: int = 120) -> str:
        """Send a prompt and return text response."""
        ...

    def ask_json(self, prompt: str, timeout: int = 120) -> dict:
        """Send a prompt and parse JSON from the response."""
        full_prompt = prompt + (
            "\n\nIMPORTANT: Respond ONLY with valid JSON. "
            "No markdown fencing, no explanation, no preamble. Just the JSON object."
        )
        raw = self.ask(full_prompt, timeout=timeout)
        cleaned = raw.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM didn't return valid JSON: {e}\nRaw: {raw[:500]}")


class ClaudeCLIBackend(LLMBackend):
    """Claude Code CLI backend (default). Uses `claude -p` subprocess."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.default_timeout = self.config.get("timeout", 120)

    def ask(self, prompt: str, timeout: int = None) -> str:
        timeout = timeout or self.default_timeout
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error"
            raise RuntimeError(f"Claude CLI error: {error_msg}")
        try:
            data = json.loads(result.stdout)
            return data.get("result", result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip()


# Backend registry
_BACKENDS = {
    "claude_cli": ClaudeCLIBackend,
}

# Cache instantiated backends
_backend_cache: dict[str, LLMBackend] = {}


def get_backend(component: str, profile: dict) -> LLMBackend:
    """
    Get the configured LLM backend for a specific component.

    Falls back to default_backend if component isn't specifically configured.
    Falls back to claude_cli if nothing is configured at all.
    """
    ai_config = profile.get("ai", {})
    backend_name = ai_config.get("components", {}).get(
        component, ai_config.get("default_backend", "claude_cli")
    )
    backend_config = ai_config.get("backends", {}).get(backend_name, {})

    # Cache key includes name + config hash for reuse
    cache_key = f"{backend_name}:{hash(json.dumps(backend_config, sort_keys=True, default=str))}"
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    backend_class = _BACKENDS.get(backend_name)
    if not backend_class:
        print(f"  Warning: Unknown LLM backend '{backend_name}', falling back to claude_cli")
        backend_class = ClaudeCLIBackend
        backend_config = ai_config.get("backends", {}).get("claude_cli", {})

    instance = backend_class(backend_config)
    _backend_cache[cache_key] = instance
    return instance


def clear_backend_cache():
    """Clear the backend instance cache (useful after profile changes)."""
    _backend_cache.clear()
