"""LLM access: a swappable chat backend plus a wrapper that enforces structured output."""

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from .errors import RetryableError
from .http import request_json

T = TypeVar("T", bound=BaseModel)


class ChatBackend(Protocol):
    async def chat(self, system: str, user: str, schema: dict[str, Any] | None = None) -> str:
        """Return the model's reply; when `schema` is given the reply must be JSON matching it."""
        ...


class OllamaBackend:
    def __init__(self, http: httpx.AsyncClient, base_url: str, model: str) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model

    async def chat(self, system: str, user: str, schema: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0},
        }
        if schema is not None:
            payload["format"] = schema
        data = await request_json(self._http, "POST", self._url, json=payload)
        content = data.get("message", {}).get("content")
        if not content:
            raise RetryableError("Ollama returned an empty response.")
        return content


class LLM:
    """Agents talk to this class, never to a concrete backend (dependency inversion)."""

    def __init__(self, backend: ChatBackend, max_attempts: int = 2) -> None:
        self._backend = backend
        self._max_attempts = max_attempts

    async def text(self, system: str, user: str) -> str:
        return (await self._backend.chat(system, user)).strip()

    async def structured(
        self,
        system: str,
        user: str,
        model: type[T],
        check: Callable[[T], None] | None = None,
    ) -> T:
        """Parse the reply into `model`. Invalid output is fed back to the model for one self-repair."""
        schema = model.model_json_schema()
        prompt = user
        last_error = ""
        for _ in range(self._max_attempts):
            raw = await self._backend.chat(system, prompt, schema)
            try:
                parsed = model.model_validate_json(raw)
                if check is not None:
                    check(parsed)
                return parsed
            except ValueError as exc:  # pydantic.ValidationError is a ValueError
                last_error = str(exc)
                prompt = (
                    f"{user}\n\nYour previous answer was rejected.\nReason: {last_error}\n"
                    f"Previous answer: {raw}\nReturn corrected JSON only."
                )
        raise RetryableError(f"Model output stayed invalid after {self._max_attempts} attempts: {last_error}")
