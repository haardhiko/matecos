"""
llm_interface.py
================
Provider-neutral LLM interface. No vendor lock-in in orchestration logic.

All adapters implement the ``LLMProvider`` Protocol so orchestration code
only ever depends on the protocol, never on a specific vendor SDK.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
import structlog
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class Message(BaseModel):
    """A single message in a conversation with an LLM."""

    model_config = {"populate_by_name": True}

    role: Literal["system", "user", "assistant", "tool"] = Field(
        description="Role of the message sender."
    )
    content: str = Field(description="Text content of the message.")
    tool_call_id: str | None = Field(
        default=None,
        description="ID of the tool call this message responds to (role='tool' only).",
    )
    name: str | None = Field(
        default=None,
        description="Name of the tool when role='tool', or function name when role='assistant'.",
    )


class LLMConfig(BaseModel):
    """Configuration for a single LLM inference request."""

    model_config = {"populate_by_name": True}

    model: str = Field(
        description="Model identifier, e.g. 'gpt-4o' or 'claude-3-5-sonnet-20241022'."
    )
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Low value = more deterministic tool decisions.",
    )
    max_tokens: int = Field(
        default=2048,
        ge=1,
        le=128000,
        description="Maximum tokens in the completion.",
    )
    response_format: Literal["text", "json"] = Field(
        default="json",
        description="Prefer structured JSON output when set to 'json'.",
    )
    timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=600,
        description="Request timeout in wall-clock seconds.",
    )
    seed: int | None = Field(
        default=None,
        description="Optional random seed for reproducible sampling.",
    )


class LLMUsage(BaseModel):
    """Token and cost accounting for one LLM call."""

    model_config = {"populate_by_name": True}

    prompt_tokens: int = Field(ge=0, description="Tokens consumed by the prompt.")
    completion_tokens: int = Field(ge=0, description="Tokens in the generated completion.")
    total_tokens: int = Field(ge=0, description="Sum of prompt and completion tokens.")
    cost_usd: float = Field(ge=0.0, description="Estimated monetary cost in USD.")


class LLMResponse(BaseModel):
    """Structured result of one LLM inference call."""

    model_config = {"populate_by_name": True}

    content: str = Field(description="Raw text content returned by the model.")
    usage: LLMUsage = Field(description="Token and cost accounting.")
    model: str = Field(description="Model identifier as reported by the provider.")
    finish_reason: str = Field(description="Stop reason, e.g. 'stop', 'length', 'tool_calls'.")
    raw_json: dict[str, Any] | None = Field(
        default=None,
        description="Parsed JSON object when response_format='json' and the model returns valid JSON.",
    )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-neutral contract all LLM adapters must satisfy."""

    async def complete(self, messages: list[Message], config: LLMConfig) -> LLMResponse:
        """Return a single completion for the given messages."""
        ...

    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[str]:
        """Yield completion token chunks as they arrive."""
        ...

    async def health_check(self) -> bool:
        """Return True if the provider endpoint is reachable and functional."""
        ...


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter
# ---------------------------------------------------------------------------


class OpenAIAdapter:
    """OpenAI-compatible adapter.

    Works with OpenAI, Azure OpenAI, Groq, Together AI, or any provider that
    exposes the ``/v1/chat/completions`` endpoint.
    """

    _OPENAI_BASE = "https://api.openai.com"
    _PROMPT_COST_PER_1K = 0.005
    _COMPLETION_COST_PER_1K = 0.015

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or self._OPENAI_BASE).rstrip("/")
        self._log = logger.bind(adapter="openai", model=model)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, messages: list[Message], config: LLMConfig) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model or self._model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        if config.seed is not None:
            payload["seed"] = config.seed
        return payload

    def _parse_usage(self, raw: dict[str, Any]) -> LLMUsage:
        usage = raw.get("usage", {})
        prompt_tokens: int = usage.get("prompt_tokens", 0)
        completion_tokens: int = usage.get("completion_tokens", 0)
        total_tokens: int = usage.get("total_tokens", prompt_tokens + completion_tokens)
        cost = (
            prompt_tokens / 1000 * self._PROMPT_COST_PER_1K
            + completion_tokens / 1000 * self._COMPLETION_COST_PER_1K
        )
        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=round(cost, 6),
        )

    @retry(
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def complete(self, messages: list[Message], config: LLMConfig) -> LLMResponse:
        payload = self._build_payload(messages, config)
        url = f"{self._base_url}/v1/chat/completions"

        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            self._log.debug("openai_request", url=url, model=payload["model"])
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code == 429:
                self._log.warning("openai_rate_limited", status=429)
                resp.raise_for_status()
            resp.raise_for_status()
            raw: dict[str, Any] = resp.json()

        choice = raw["choices"][0]
        content: str = choice["message"]["content"] or ""
        finish_reason: str = choice.get("finish_reason", "stop")

        raw_json: dict[str, Any] | None = None
        if config.response_format == "json" and content:
            try:
                raw_json = json.loads(content)
            except json.JSONDecodeError:
                self._log.warning("openai_json_parse_failed", finish_reason=finish_reason)

        usage = self._parse_usage(raw)
        self._log.info(
            "openai_complete",
            model=raw.get("model", config.model),
            total_tokens=usage.total_tokens,
            cost_usd=usage.cost_usd,
        )
        return LLMResponse(
            content=content,
            usage=usage,
            model=raw.get("model", config.model),
            finish_reason=finish_reason,
            raw_json=raw_json,
        )

    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[str]:
        payload = self._build_payload(messages, config)
        payload["stream"] = True
        url = f"{self._base_url}/v1/chat/completions"

        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk: dict[str, Any] = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        token: str = delta.get("content") or ""
                        if token:
                            yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def health_check(self) -> bool:
        try:
            cfg = LLMConfig(model=self._model, max_tokens=1)
            await self.complete([Message(role="user", content="ping")], cfg)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log.warning("openai_health_check_failed", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------


class AnthropicAdapter:
    """Anthropic Claude adapter using the Messages API."""

    _BASE_URL = "https://api.anthropic.com"
    _API_VERSION = "2023-06-01"
    _PROMPT_COST_PER_1K = 0.003
    _COMPLETION_COST_PER_1K = 0.015

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._log = logger.bind(adapter="anthropic", model=model)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._API_VERSION,
            "Content-Type": "application/json",
        }

    def _convert_messages(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        system: str | None = None
        converted: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system = msg.content
            elif msg.role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id or "",
                                "content": msg.content,
                            }
                        ],
                    }
                )
            else:
                converted.append({"role": msg.role, "content": msg.content})
        return system, converted

    @retry(
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def complete(self, messages: list[Message], config: LLMConfig) -> LLMResponse:
        system, conv_messages = self._convert_messages(messages)
        payload: dict[str, Any] = {
            "model": config.model or self._model,
            "messages": conv_messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
        if system:
            payload["system"] = system

        url = f"{self._BASE_URL}/v1/messages"
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            self._log.debug("anthropic_request", model=payload["model"])
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code == 429:
                resp.raise_for_status()
            resp.raise_for_status()
            raw: dict[str, Any] = resp.json()

        content_blocks: list[dict[str, Any]] = raw.get("content", [])
        content = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        finish_reason = raw.get("stop_reason", "end_turn")
        usage_raw = raw.get("usage", {})
        prompt_tokens: int = usage_raw.get("input_tokens", 0)
        completion_tokens: int = usage_raw.get("output_tokens", 0)
        cost = (
            prompt_tokens / 1000 * self._PROMPT_COST_PER_1K
            + completion_tokens / 1000 * self._COMPLETION_COST_PER_1K
        )
        usage = LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=round(cost, 6),
        )

        raw_json: dict[str, Any] | None = None
        if config.response_format == "json" and content:
            try:
                raw_json = json.loads(content)
            except json.JSONDecodeError:
                self._log.warning("anthropic_json_parse_failed")

        self._log.info(
            "anthropic_complete",
            model=raw.get("model", config.model),
            total_tokens=usage.total_tokens,
            cost_usd=usage.cost_usd,
        )
        return LLMResponse(
            content=content,
            usage=usage,
            model=raw.get("model", config.model),
            finish_reason=finish_reason,
            raw_json=raw_json,
        )

    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[str]:
        system, conv_messages = self._convert_messages(messages)
        payload: dict[str, Any] = {
            "model": config.model or self._model,
            "messages": conv_messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system

        url = f"{self._BASE_URL}/v1/messages"
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    try:
                        event: dict[str, Any] = json.loads(data)
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text_val: str = delta.get("text", "")
                                if text_val:
                                    yield text_val
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def health_check(self) -> bool:
        try:
            cfg = LLMConfig(model=self._model, max_tokens=1)
            await self.complete([Message(role="user", content="ping")], cfg)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log.warning("anthropic_health_check_failed", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Mock adapter (testing)
# ---------------------------------------------------------------------------


class MockLLMAdapter:
    """Deterministic mock LLM adapter for unit tests.

    Returns pre-configured JSON response dicts in round-robin order.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._index = 0
        self._log = logger.bind(adapter="mock")

    def _next_response(self) -> dict[str, Any]:
        if not self._responses:
            raise StopIteration("MockLLMAdapter: no responses configured")
        resp = self._responses[self._index % len(self._responses)]
        self._index += 1
        return resp

    async def complete(self, messages: list[Message], config: LLMConfig) -> LLMResponse:
        raw = dict(self._next_response())
        cost_usd: float = float(raw.pop("_cost_usd", 0.001))
        tokens: int = int(raw.pop("_tokens", 100))
        content = json.dumps(raw)
        raw_json: dict[str, Any] | None = raw if config.response_format == "json" else None
        usage = LLMUsage(
            prompt_tokens=tokens // 2,
            completion_tokens=tokens // 2,
            total_tokens=tokens,
            cost_usd=cost_usd,
        )
        self._log.debug("mock_complete", index=self._index - 1, response_keys=list(raw.keys()))
        return LLMResponse(
            content=content,
            usage=usage,
            model=config.model,
            finish_reason="stop",
            raw_json=raw_json,
        )

    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[str]:
        response = await self.complete(messages, config)
        yield response.content

    async def health_check(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_llm_provider(settings: Any) -> LLMProvider:
    """Create an ``LLMProvider`` instance from application settings.

    Reads ``settings.llm`` sub-settings and constructs the appropriate adapter.
    Defaults to ``MockLLMAdapter`` in test environments.
    """
    llm = getattr(settings, "llm", None)
    testing: bool = getattr(settings, "app_env", "development") == "testing"
    provider: str = (getattr(llm, "llm_provider", "mock") if llm else "mock").lower()

    if testing or provider == "mock":
        mock_responses: list[dict[str, Any]] = [
            {
                "decision_type": "complete",
                "reason_summary": "Task completed (mock response).",
                "tool_id": None,
                "tool_input": None,
                "expected_output": "Mock output",
                "risk_assessment": "low",
            }
        ]
        logger.info("llm_provider_created", adapter="mock")
        return MockLLMAdapter(responses=mock_responses)

    if provider == "openai":
        api_key = getattr(llm, "openai_api_key", "")
        model = getattr(llm, "default_model", "gpt-4o")
        base_url: str | None = None
        logger.info("llm_provider_created", adapter="openai", model=model)
        return OpenAIAdapter(api_key=api_key, model=model, base_url=base_url)

    if provider == "anthropic":
        api_key = getattr(llm, "anthropic_api_key", "")
        model = getattr(llm, "default_model", "claude-3-5-sonnet-20241022")
        logger.info("llm_provider_created", adapter="anthropic", model=model)
        return AnthropicAdapter(api_key=api_key, model=model)

    logger.warning("llm_provider_unknown", provider=provider, fallback="mock")
    return MockLLMAdapter(responses=[])
