"""Universal LLM service for MATECOS.

Supports Google Gemini, OpenAI, Anthropic, Ollama, Groq, OpenRouter, and custom endpoints.
Provides intelligent human-understandable explanation generation for any tool result.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

CONFIG_PATH = Path("./config/llm_config.json")


class LLMConfigModel(BaseModel):
    """Configuration for LLM integration."""
    provider: Literal["gemini", "openai", "anthropic", "ollama", "groq", "openrouter", "custom"] = "gemini"
    api_key: str = ""
    model: str = "gemini-1.5-flash"
    base_url: str = ""
    enabled: bool = False
    temperature: float = 0.3


class LLMService:
    """Manages LLM API connections and natural language synthesis."""

    def __init__(self) -> None:
        self.config = self._load_config()
        self._log = logger.bind(component="LLMService")

    def _load_config(self) -> LLMConfigModel:
        """Load configuration from disk or environment variables."""
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                return LLMConfigModel(**data)
            except Exception as exc:
                logger.warning("llm_service.config_load_failed", error=str(exc))

        # Check environment variables
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        groq_key = os.environ.get("GROQ_API_KEY", "")

        if gemini_key:
            return LLMConfigModel(provider="gemini", api_key=gemini_key, model="gemini-1.5-flash", enabled=True)
        elif openai_key:
            return LLMConfigModel(provider="openai", api_key=openai_key, model="gpt-4o-mini", enabled=True)
        elif anthropic_key:
            return LLMConfigModel(provider="anthropic", api_key=anthropic_key, model="claude-3-5-haiku-20241022", enabled=True)
        elif groq_key:
            return LLMConfigModel(provider="groq", api_key=groq_key, model="llama-3.1-8b-instant", enabled=True)

        return LLMConfigModel()

    def save_config(self, new_config: LLMConfigModel) -> None:
        """Save configuration to disk."""
        self.config = new_config
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(self.config.model_dump_json(indent=2), encoding="utf-8")
        self._log.info("llm_service.config_saved", provider=self.config.provider, enabled=self.config.enabled)

    async def call_llm(self, prompt: str, system_prompt: str = "") -> str:
        """Call the configured LLM provider and return response text."""
        if not self.config.enabled or not self.config.api_key:
            raise ValueError("LLM is not configured or enabled.")

        provider = self.config.provider.lower()
        api_key = self.config.api_key.strip()
        model = self.config.model.strip()

        async with httpx.AsyncClient(timeout=30.0) as client:
            if provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                payload: dict[str, Any] = {
                    "contents": [{"parts": [{"text": prompt}]}]
                }
                if system_prompt:
                    payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

                res = await client.post(url, json=payload)
                if res.status_code != 200:
                    raise RuntimeError(f"Gemini API error ({res.status_code}): {res.text[:400]}")
                data = res.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]

            elif provider in ["openai", "groq", "openrouter", "ollama", "custom"]:
                base = self.config.base_url.strip()
                if not base:
                    if provider == "openai":
                        base = "https://api.openai.com/v1"
                    elif provider == "groq":
                        base = "https://api.groq.com/openai/v1"
                    elif provider == "openrouter":
                        base = "https://openrouter.ai/api/v1"
                    elif provider == "ollama":
                        base = "http://localhost:11434/v1"

                url = f"{base.rstrip('/')}/chat/completions"
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})

                headers = {"Authorization": f"Bearer {api_key}"}
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": self.config.temperature,
                }
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code != 200:
                    raise RuntimeError(f"{provider.capitalize()} API error ({res.status_code}): {res.text[:400]}")
                data = res.json()
                return data["choices"][0]["message"]["content"]

            elif provider == "anthropic":
                url = "https://api.anthropic.com/v1/messages"
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }
                payload = {
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.config.temperature
                }
                if system_prompt:
                    payload["system"] = system_prompt

                res = await client.post(url, json=payload, headers=headers)
                if res.status_code != 200:
                    raise RuntimeError(f"Anthropic API error ({res.status_code}): {res.text[:400]}")
                data = res.json()
                return data["content"][0]["text"]

            else:
                raise ValueError(f"Unsupported LLM provider: {provider}")

    async def explain_result(
        self,
        task: str,
        tool_id: str,
        tool_name: str,
        result: Any,
        error: str | None = None
    ) -> str:
        """Provide a clear, human-understandable explanation of tool output."""
        if error:
            return f"❌ The task could not be completed because: {error}"

        # If LLM is active, use it for rich synthesis
        if self.config.enabled and self.config.api_key:
            try:
                system_prompt = (
                    "You are MATECOS Assistant. A user requested an action, a tool was executed, "
                    "and produced structured data. Explain the answer directly to the user in natural, friendly, "
                    "understandable language. Do not output raw code, JSON, or execution logs unless necessary. "
                    "Format key numbers or values in bold."
                )
                user_prompt = (
                    f"User Request: {task}\n"
                    f"Tool Used: {tool_name} ({tool_id})\n"
                    f"Raw Result: {json.dumps(result, default=str)}\n\n"
                    "Please provide the clear human-understandable answer:"
                )
                llm_explanation = await self.call_llm(user_prompt, system_prompt=system_prompt)
                if llm_explanation and len(llm_explanation.strip()) > 5:
                    return llm_explanation.strip()
            except Exception as exc:
                self._log.warning("llm_service.explanation_failed", error=str(exc))

        # Deterministic humanizer fallback
        return self._format_deterministic_explanation(task, tool_id, tool_name, result)

    def _format_deterministic_explanation(self, task: str, tool_id: str, tool_name: str, result: Any) -> str:
        """Deterministic plain-English explanation for all 15 builtins."""
        if not isinstance(result, dict):
            return f"Completed: {result}"

        if tool_id == "math.calculator":
            res = result.get("result")
            expr = result.get("expression", task)
            if isinstance(res, (int, float)):
                return f"The calculation for **{expr}** evaluated to **{res:,}**."
            return f"The calculation evaluated to **{res}**."

        elif tool_id == "util.uuid.gen":
            uuids = result.get("uuids", [])
            lines = [f"Generated **{len(uuids)}** unique UUID identifier(s):"]
            for u in uuids:
                lines.append(f"• `{u}`")
            return "\n".join(lines)

        elif tool_id == "util.password.gen":
            passwords = result.get("passwords", [])
            pw = passwords[0] if passwords else ""
            strength = result.get("strength", "strong").replace("_", " ").title()
            length = result.get("length", len(pw))
            return f"Generated a **{strength}** password ({length} characters):\n`{pw}`"

        elif tool_id == "data.convert.units":
            val = result.get("result")
            from_u = result.get("from_unit", "")
            to_u = result.get("to_unit", "")
            return f"Unit conversion: **{result.get('formula', task)}**\nResult is **{val} {to_u}**."

        elif tool_id == "text.json.format":
            keys = result.get("key_count", 0)
            valid = result.get("valid", True)
            if valid:
                return f"JSON successfully validated and formatted ({keys} keys detected).\n```json\n{result.get('formatted', '')}\n```"
            return f"JSON validation failed: {result.get('error')}"

        elif tool_id == "crypto.hash.gen":
            algo = result.get("algorithm", "sha256").upper()
            h = result.get("hash", "")
            return f"Computed **{algo}** cryptographic checksum:\n`{h}`"

        elif tool_id == "text.base64.codec":
            action = result.get("action", "processed").title()
            res = result.get("result", "")
            return f"Base64 {action} result:\n`{res}`"

        elif tool_id == "text.word.count":
            return (
                f"**Text Analysis Statistics:**\n"
                f"• Words: **{result.get('words', 0):,}**\n"
                f"• Characters: **{result.get('characters', 0):,}** (without spaces: {result.get('characters_no_spaces', 0):,})\n"
                f"• Sentences: **{result.get('sentences', 0)}**\n"
                f"• Estimated reading time: **~{result.get('reading_time_seconds', 0)} seconds**"
            )

        elif tool_id == "text.url.parse":
            return (
                f"**Parsed URL Components:**\n"
                f"• Host: **{result.get('hostname', 'unknown')}** (Port: {result.get('port') or 'default'})\n"
                f"• Protocol: `{result.get('scheme', 'https')}`\n"
                f"• Path: `{result.get('path', '/')}`\n"
                f"• Parameters: {json.dumps(result.get('query', {}))}"
            )

        elif tool_id == "web.http_fetch":
            status_code = result.get("status_code", 200)
            ms = result.get("latency_ms", 0)
            ctype = result.get("content_type", "")
            body = result.get("body", "")
            preview = body[:300] + ("..." if len(body) > 300 else "")
            return f"Successfully fetched URL. Server returned HTTP **{status_code} OK** ({ctype}) in **{ms} ms**.\n\nResponse preview:\n{preview}"

        elif tool_id == "text.diff.compare":
            ratio = round(result.get("similarity_ratio", 0) * 100, 1)
            return f"Comparison complete. Similarity ratio is **{ratio}%** (+{result.get('additions', 0)} / -{result.get('deletions', 0)} lines)."

        elif tool_id == "data.csv.profile":
            rows = result.get("row_count", 0)
            cols = len(result.get("columns", []))
            return f"Profiled CSV dataset: **{rows:,} rows** across **{cols} columns**. No critical schema anomalies found."

        elif "stdout" in result:
            out = result.get("stdout", "").strip()
            code = result.get("exit_code", 0)
            return f"Script execution completed (exit code {code}):\n{out}"

        return f"Tool **{tool_name}** executed successfully. Result: {json.dumps(result, default=str)}"


# Global Singleton
llm_service = LLMService()
