"""Adapter LLM provider-agnostic berbasis httpx.

Provider:
- ``openai`` / ``openai_compatible``: ``POST {base_url}/chat/completions`` (Authorization: Bearer),
  jawaban di ``choices[0].message.content``.
- ``anthropic``: ``POST {base_url}/v1/messages`` (x-api-key + anthropic-version), ``system`` top-level,
  jawaban di ``content[].text``.
- ``none``: tidak ada panggilan.

Kunci API tidak pernah masuk log/exception: semua error diredaksi dan URL tidak memuat kunci.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from config.settings import Settings
from core import redaction
from core.redaction import redact, redact_exception

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    """Panggilan LLM gagal (jaringan, HTTP, atau respons tidak sesuai skema)."""


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    user: str
    max_tokens: int = 400
    temperature: float | None = 0.2


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


class LLMClient(ABC):
    provider: str = "none"

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def aclose(self) -> None:  # pragma: no cover - default tanpa sumber daya
        return None


class Transport(Protocol):
    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> httpx.Response: ...

    async def aclose(self) -> None: ...


class NullLLMClient(LLMClient):
    provider = "none"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMError("LLM_PROVIDER=none: tidak ada klien LLM")


class _HttpLLMClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        transport: Transport | None = None,
    ) -> None:
        if not model:
            raise ValueError("LLM_MODEL kosong")
        # Server dapat memantulkan kunci di body error; pastikan selalu teredaksi.
        redaction.registry.register(api_key)
        self._api_key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport: Transport = transport or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def _post(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = await asyncio.wait_for(
                self._transport.post(url, headers=headers, json=payload), timeout=self._timeout
            )
        except TimeoutError as exc:
            raise LLMError(f"timeout {self._timeout}s memanggil {self.provider}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"kesalahan jaringan {self.provider}: {redact_exception(exc)}") from exc
        if response.status_code >= 400:
            body = redact(response.text[:300])
            raise LLMError(f"{self.provider} HTTP {response.status_code}: {body}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(f"{self.provider}: respons bukan JSON") from exc
        if not isinstance(data, dict):
            raise LLMError(f"{self.provider}: respons JSON bukan objek")
        return data


class OpenAICompatibleClient(_HttpLLMClient):
    provider = "openai"

    def __init__(self, *, provider_name: str = "openai", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.provider = provider_name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        data = await self._post(f"{self._base}/chat/completions", headers, payload)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.provider}: skema respons tidak dikenal") from exc
        if isinstance(content, list):  # beberapa server kompatibel memakai content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str):
            raise LLMError(f"{self.provider}: content bukan string")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        return LLMResponse(
            text=content,
            provider=self.provider,
            model=self._model,
            finish_reason=finish,
            usage=usage,
        )


class AnthropicClient(_HttpLLMClient):
    provider = "anthropic"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        data = await self._post(f"{self._base}/v1/messages", headers, payload)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError("anthropic: skema respons tidak dikenal (content)")
        text = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self._model,
            finish_reason=data.get("stop_reason"),
            usage=usage,
        )


def build_llm_client(settings: Settings, *, transport: Transport | None = None) -> LLMClient:
    provider = settings.llm_provider
    if provider == "none":
        return NullLLMClient()
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    common = {
        "model": settings.llm_model,
        "timeout_seconds": settings.llm_timeout_seconds,
        "transport": transport,
    }
    if provider == "anthropic":
        return AnthropicClient(
            api_key=key, base_url=settings.llm_base_url or DEFAULT_ANTHROPIC_BASE, **common
        )
    if provider == "openai":
        return OpenAICompatibleClient(
            api_key=key, base_url=settings.llm_base_url or DEFAULT_OPENAI_BASE, **common
        )
    if provider == "openai_compatible":
        return OpenAICompatibleClient(
            provider_name="openai_compatible",
            api_key=key or "none",
            base_url=settings.llm_base_url,
            **common,
        )
    raise ValueError(f"LLM_PROVIDER tidak dikenal: {provider}")
