"""
Pluggable LLM Backend — Route AI requests to different providers per component.

Supports Claude CLI (default), OpenAI API, Ollama, and Gemini.
Each AI component (scoring, cover letters, resume tailoring, etc.) can be
independently configured to use a different backend.

Configuration in profile.yaml:
    ai:
      default_backend: claude_cli
      backends:
        claude_cli:
          timeout: 120
        openai:
          api_key: ${OPENAI_API_KEY}
          model: gpt-4o
          timeout: 60
        gemini:
          api_key: ${GEMINI_API_KEY}
          model: gemini-2.0-flash
          timeout: 60
        ollama:
          base_url: http://localhost:11434
          model: llama3
          timeout: 120
      components:
        scoring: claude_cli
        cover_letter: claude_cli
        interview: gemini
"""

import os
import subprocess
import json
import re
from abc import ABC, abstractmethod
from typing import Optional


class LLMBackend(ABC):
    """Abstract base for all LLM backends."""

    @abstractmethod
    def ask(self, prompt: str, timeout: int = 120) -> str:
        """Send a prompt and return text response."""
        ...

    def ask_chat(self, messages: list[dict], system: str = "", timeout: int = 120) -> str:
        """
        Send a multi-turn conversation and return the assistant's response.

        Args:
            messages: List of {"role": "user"|"assistant", "content": str}
            system: System prompt (prepended or passed natively)
            timeout: Request timeout in seconds

        Default implementation: flatten messages to a single string and call ask().
        Backends with native multi-turn support should override this.
        """
        parts = []
        if system:
            parts.append(f"System: {system}\n")
        for msg in messages:
            role_label = "Assistant" if msg["role"] == "assistant" else "User"
            parts.append(f"{role_label}: {msg['content']}")
        parts.append("Assistant:")
        return self.ask("\n\n".join(parts), timeout=timeout)

    def ask_vision(self, prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg", timeout: int = 30) -> str:
        """
        Send a prompt with an image for multimodal vision analysis.

        Args:
            prompt: Text prompt describing what to analyze
            image_bytes: Raw image bytes (JPEG, PNG, etc.)
            mime_type: MIME type of the image
            timeout: Request timeout in seconds

        Default implementation: ignore image, call ask() with text only.
        Vision-capable backends should override this.
        """
        return self.ask(prompt, timeout=timeout)

    def ask_vision_json(self, prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg", timeout: int = 30) -> dict:
        """Send a prompt with an image and parse JSON from the response."""
        full_prompt = prompt + (
            "\n\nIMPORTANT: Respond ONLY with valid JSON. "
            "No markdown fencing, no explanation, no preamble. Just the JSON object."
        )
        raw = self.ask_vision(full_prompt, image_bytes, mime_type, timeout=timeout)
        cleaned = raw.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM didn't return valid JSON: {e}\nRaw: {raw[:500]}")

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


class OpenAIBackend(LLMBackend):
    """OpenAI API backend (GPT-4o, etc.) using the openai SDK."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.api_key = self._resolve_env(self.config.get("api_key", ""))
        self.model = self.config.get("model", "gpt-4o")
        self.base_url = self.config.get("base_url", None)
        self.default_timeout = self.config.get("timeout", 60)
        self._client = None

    def _resolve_env(self, val: str) -> str:
        """Resolve ${ENV_VAR} references."""
        if val and val.startswith("${") and val.endswith("}"):
            env_name = val[2:-1]
            return os.environ.get(env_name, "")
        return val

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("openai package required: pip install openai")
            kwargs = {"api_key": self.api_key, "timeout": self.default_timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def ask(self, prompt: str, timeout: int = None) -> str:
        return self.ask_chat(
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout or self.default_timeout,
        )

    def ask_chat(self, messages: list[dict], system: str = "", timeout: int = None) -> str:
        """Native multi-turn chat via OpenAI SDK."""
        client = self._get_client()
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for msg in messages:
            api_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })
        response = client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            temperature=0.3,
            timeout=timeout or self.default_timeout,
        )
        return response.choices[0].message.content

    def ask_vision(self, prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg", timeout: int = 30) -> str:
        """Native OpenAI vision — sends base64 image via GPT-4o."""
        client = self._get_client()
        import base64 as b64
        img_b64 = b64.b64encode(image_bytes).decode()
        response = client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_b64}"}},
                ],
            }],
            temperature=0.2,
            timeout=timeout or self.default_timeout,
        )
        return response.choices[0].message.content


class GeminiBackend(LLMBackend):
    """Google Gemini API backend using google-genai SDK."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.api_key = self._resolve_env(self.config.get("api_key", ""))
        self.model = self.config.get("model", "gemini-2.5-flash")
        self.default_timeout = self.config.get("timeout", 60)
        self._client = None

    def _resolve_env(self, val: str) -> str:
        if val and val.startswith("${") and val.endswith("}"):
            env_name = val[2:-1]
            return os.environ.get(env_name, "")
        return val

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise RuntimeError("google-genai package required: pip install google-genai")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def ask(self, prompt: str, timeout: int = None) -> str:
        return self.ask_chat(
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout or self.default_timeout,
        )

    def ask_chat(self, messages: list[dict], system: str = "", timeout: int = None) -> str:
        """Native multi-turn chat via Gemini SDK."""
        client = self._get_client()
        try:
            from google.genai import types
        except ImportError:
            raise RuntimeError("google-genai package required: pip install google-genai")

        # Build Gemini-native history (all messages except the last user message)
        config = types.GenerateContentConfig(
            temperature=0.3,
        )
        if system:
            config.system_instruction = system

        # Convert messages to Gemini format
        gemini_history = []
        for msg in messages[:-1]:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_history.append(types.Content(
                role=role,
                parts=[types.Part(text=msg["content"])],
            ))

        # Create chat with history and send the last message
        chat = client.chats.create(
            model=self.model,
            config=config,
            history=gemini_history,
        )
        last_msg = messages[-1]["content"] if messages else ""
        response = chat.send_message(last_msg)
        return response.text

    def ask_vision(self, prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg", timeout: int = 30) -> str:
        """Native Gemini multimodal vision — sends image bytes directly to the model."""
        client = self._get_client()
        try:
            from google.genai import types
        except ImportError:
            raise RuntimeError("google-genai package required: pip install google-genai")

        response = client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                types.Part(text=prompt),
            ],
            config=types.GenerateContentConfig(temperature=0.2),
        )
        return response.text


class OllamaBackend(LLMBackend):
    """Local Ollama backend."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.base_url = self.config.get("base_url", "http://localhost:11434")
        self.model = self.config.get("model", "llama3")
        self.default_timeout = self.config.get("timeout", 120)

    def ask(self, prompt: str, timeout: int = None) -> str:
        timeout = timeout or self.default_timeout
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx required for Ollama backend: pip install httpx")

        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self.base_url}/api/generate",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")


# Backend registry
_BACKENDS = {
    "claude_cli": ClaudeCLIBackend,
    "openai": OpenAIBackend,
    "gemini": GeminiBackend,
    "ollama": OllamaBackend,
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


def check_provider_availability() -> dict:
    """Check which LLM providers are available (have API keys / CLI installed)."""
    providers = {}

    # Claude CLI
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        providers["claude_cli"] = {
            "available": result.returncode == 0,
            "label": "Claude (CLI)",
            "supports_vision": False,
            "supports_live": False,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        providers["claude_cli"] = {"available": False, "label": "Claude (CLI)", "supports_vision": False, "supports_live": False}

    # OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    providers["openai"] = {
        "available": bool(openai_key),
        "label": "OpenAI (GPT-4o)",
        "supports_vision": True,
        "supports_live": True,
        "supports_video": False,
    }

    # Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    providers["gemini"] = {
        "available": bool(gemini_key),
        "label": "Google Gemini",
        "supports_vision": True,
        "supports_live": True,
        "supports_video": True,
    }

    # Ollama
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        providers["ollama"] = {
            "available": resp.status_code == 200,
            "label": "Ollama (Local)",
            "supports_vision": False,
            "supports_live": False,
        }
    except Exception:
        providers["ollama"] = {"available": False, "label": "Ollama (Local)", "supports_vision": False, "supports_live": False}

    return providers


def clear_backend_cache():
    """Clear the backend instance cache (useful after profile changes)."""
    _backend_cache.clear()
