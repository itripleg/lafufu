"""Ollama HTTP client for chat completions."""

import logging
import time

import httpx

log = logging.getLogger(__name__)


class Ollama:
    """Async client for Ollama's /api/chat endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        system_prompt: str = "",
        keep_alive: str = "10m",
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.keep_alive = keep_alive
        self.timeout_s = timeout_s

    async def warmup(self) -> float:
        """Hit the model with a no-op request to load it. Returns seconds taken."""
        t0 = time.monotonic()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt or "You are a helpful assistant."},
                {"role": "user", "content": "warmup"},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": 1},
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
        return time.monotonic() - t0

    async def chat(
        self,
        user_text: str,
        history: list[tuple[str, str]] | None = None,
    ) -> str:
        """Run one chat turn.

        ``history`` is an optional list of (role, content) tuples representing
        prior turns of the current session — used by trigger-mode to feed the
        opening phrase + earlier rounds back into the LLM so multi-round
        sessions can produce context-aware ("personalized") fortunes. Each
        entry's role must be ``"user"`` or ``"assistant"``. Default ``None``
        preserves the single-shot continuous-mode behaviour exactly.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend({"role": role, "content": content} for role, content in history)
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        return ((data.get("message") or {}).get("content") or "").strip()
