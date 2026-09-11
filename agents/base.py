"""Shared LLM client wrapper and structured-response primitives."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import Sequence
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from config.settings import Settings
from utils.parser import parse_json_response
from .provider_pool import LLMProviderPool, LLMTarget

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)
# Groq on-demand TPM is often 8000 and counts prompt + reserved max_tokens.
_DEFAULT_MAX_TOKENS = 1024
_HARD_MAX_TOKENS = 4096


class LLMAgentError(RuntimeError):
    """Raised when the LLM cannot produce a usable response."""

    def __init__(self, message: str, *, status_code: int | str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def compact_model_schema(model: type[BaseModel]) -> str:
    """Generate a minimal, token-efficient JSON template for LLM structured output."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})

    def resolve_field(field_schema: dict) -> Any:
        if "$ref" in field_schema:
            ref_name = field_schema["$ref"].split("/")[-1]
            if ref_name in defs:
                return resolve_obj(defs[ref_name])
            return ref_name
        ftype = field_schema.get("type", "string")
        if ftype == "array":
            items = field_schema.get("items", {})
            return [resolve_field(items)]
        if ftype == "object":
            return resolve_obj(field_schema)
        if "enum" in field_schema:
            return " | ".join(f'"{e}"' for e in field_schema["enum"])
        return ftype

    def resolve_obj(obj_schema: dict) -> dict:
        result = {}
        for prop_name, prop_val in obj_schema.get("properties", {}).items():
            result[prop_name] = resolve_field(prop_val)
        return result

    try:
        template = resolve_obj(schema)
        return json.dumps(template, indent=2)
    except Exception:
        return json.dumps(schema, separators=(",", ":"))


class BaseAgent:
    """Base class for agents with retrying text and multimodal generation."""

    def __init__(self, settings: Settings, pool: LLMProviderPool | None = None) -> None:
        self.settings = settings
        self.pool = pool or LLMProviderPool(settings)
        self._closed = False

    @property
    def client(self) -> AsyncOpenAI:
        """Compatibility property returning the primary AsyncOpenAI client."""
        if self.pool._targets:
            return self.pool.get_client(self.pool._targets[0])
        return AsyncOpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url)

    async def close(self) -> None:
        """Close all underlying async clients in the provider pool."""
        if self._closed:
            return
        await self.pool.close()
        self._closed = True

    @staticmethod
    def _is_output_limit_error(error: BaseException) -> bool:
        text = str(error).lower()
        return any(
            marker in text
            for marker in (
                "max completion tokens",
                "json_validate_failed",
                "failed to generate json",
                "ran out of completion tokens",
            )
        )

    @staticmethod
    def _is_tpm_error(error: BaseException) -> bool:
        status = getattr(error, "status_code", None) or getattr(error, "code", None)
        text = str(error).lower()
        if any(marker in text for marker in ("tokens per minute", "tpm:", "request too large")):
            return True
        return status in {413, 429} and "rate_limit_exceeded" in text

    @staticmethod
    def _tpm_budget(error: BaseException, current_budget: int) -> int:
        """Shrink reserved completion tokens so prompt + max_tokens fits the TPM limit."""
        text = str(error)
        m_limit = re.search(r"Limit\s+(\d+)", text, re.IGNORECASE)
        m_used = re.search(r"Used\s+(\d+)", text, re.IGNORECASE)
        m_req = re.search(r"Requested\s+(\d+)", text, re.IGNORECASE)
        
        if m_limit and m_used:
            limit = int(m_limit.group(1))
            used = int(m_used.group(1))
            available = limit - used
            if available > 300:
                return max(256, min(current_budget, available - 64))
            return max(256, current_budget // 2)
            
        if m_limit and m_req:
            limit = int(m_limit.group(1))
            req = int(m_req.group(1))
            overflow = max(0, req - limit)
            return max(256, min(current_budget - overflow - 64, limit - 256))
            
        return max(256, current_budget // 2)

    @staticmethod
    def _is_retryable(error: BaseException) -> bool:
        status = getattr(error, "status_code", None) or getattr(error, "code", None)
        text = str(error).lower()
        if BaseAgent._is_output_limit_error(error) or BaseAgent._is_tpm_error(error):
            return True
        return status in {408, 409, 413, 425, 429, 500, 502, 503, 504} or any(
            marker in text
            for marker in ("429", "resource_exhausted", "rate limit", "temporarily unavailable")
        )

    @staticmethod
    def _status_code(error: BaseException) -> int | str | None:
        return getattr(error, "status_code", None) or getattr(error, "code", None)

    @staticmethod
    def _retry_delay(error: BaseException, attempt: int) -> float:
        text = str(error)
        match = re.search(
            r"(?:try\s+again\s+in|retry\s+after|retrydelay|wait\s+for|in\s+)\D{0,10}(\d+(?:\.\d+)?)\s*s",
            text,
            flags=re.IGNORECASE,
        )
        if match is not None:
            return float(match.group(1)) + random.uniform(1.5, 3.0)
        
        is_rate_limit = BaseAgent._is_tpm_error(error) or "429" in text or "rate limit" in text.lower()
        if is_rate_limit:
            return min(60.0, 15.0 + attempt * 5.0 + random.uniform(0.5, 2.0))
            
        fallback = min(30.0, 2**attempt) + random.uniform(0.0, 0.75)
        return fallback

    @classmethod
    def _safe_failure_detail(cls, error: BaseException) -> tuple[int | str | None, str]:
        status = cls._status_code(error)
        text = str(error).lower()
        if status in {401, 403} or "unauthenticated" in text or "authentication" in text:
            return status, (
                "LLM authentication failed. Check your API key and base URL."
            )
        if status == 404 or "not found" in text:
            return status, (
                "The configured LLM model or API endpoint was not found. Check LLM_MODEL."
            )
        if cls._is_tpm_error(error) or status == 429 or any(
            marker in text for marker in ("quota", "resource_exhausted", "rate limit")
        ):
            return status, (
                "LLM token budget exceeded the provider TPM limit. "
                "Retrying with a smaller reserved completion size."
            )
        if any(marker in text for marker in ("timeout", "timed out", "connection", "dns")):
            return status, "LLM could not be reached. Check the cloud worker network and retry."
        if cls._is_output_limit_error(error):
            return status, (
                "LLM ran out of completion tokens before finishing valid JSON. "
                "Retrying with a larger output budget."
            )
        return status, f"LLM provider error ({status}): {text}"

    async def generate_text(
        self,
        contents: Any,
        *,
        system_instruction: str,
        temperature: float = 0.2,
        attempts: int = 8,
        response_mime_type: str | None = None,
        response_schema: Any = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> str:
        """Generate text with multi-key rotation and multi-model fallback."""
        last_error: BaseException | None = None
        token_budget = max(256, min(max_tokens, _HARD_MAX_TOKENS))
        del response_schema

        kwargs: dict[str, Any] = {}
        if response_mime_type == "application/json":
            kwargs["response_format"] = {"type": "json_object"}

        messages = [
            {"role": "system", "content": system_instruction},
        ]

        if isinstance(contents, str):
            messages.append({"role": "user", "content": contents})
        elif isinstance(contents, Sequence) and not isinstance(contents, str):
            user_content = ""
            for part in contents:
                if isinstance(part, str):
                    user_content += part + "\n"
                elif hasattr(part, "text"):
                    user_content += part.text + "\n"
                elif hasattr(part, "data") and hasattr(part, "mime_type"):
                    logger.warning("Unsupported part data with mime_type: %s", part.mime_type)
            messages.append({"role": "user", "content": user_content.strip()})
        else:
            messages.append({"role": "user", "content": str(contents)})

        for attempt in range(attempts):
            candidates = await self.pool.get_candidate_targets()
            if not candidates:
                raise LLMAgentError("No configured LLM targets or API keys available.")

            for target in candidates:
                client = self.pool.get_client(target)
                target_budget = token_budget
                if target.provider == "gemini":
                    target_budget = max(token_budget, min(max_tokens, 8192))
                elif target.provider == "groq":
                    target_budget = min(token_budget, _HARD_MAX_TOKENS)

                try:
                    response = await client.chat.completions.create(
                        model=target.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=target_budget,
                        **kwargs,
                    )
                    choice = response.choices[0]
                    text = (choice.message.content or "").strip()
                    finish_reason = getattr(choice, "finish_reason", None)
                    if finish_reason == "length":
                        raise LLMAgentError(
                            "LLM ran out of completion tokens before finishing the response",
                            status_code=400,
                        )
                    if not text:
                        raise LLMAgentError("LLM returned an empty response")
                    return text
                except Exception as error:
                    last_error = error
                    status = self._status_code(error)
                    is_tpm = self._is_tpm_error(error)
                    is_out_limit = self._is_output_limit_error(error)

                    if is_tpm or status in {413, 429}:
                        cooldown = self.pool.parse_retry_delay(error, default_delay=45.0)
                        self.pool.record_cooldown(target, cooldown, reason=f"RateLimit/TPM ({status})")
                        if is_tpm:
                            token_budget = max(256, self._tpm_budget(error, token_budget))
                        logger.warning(
                            "Target %s rate-limited; immediately rotating to next key/model in pool.",
                            target.identifier,
                        )
                        continue

                    if status in {401, 403}:
                        self.pool.record_cooldown(target, 3600.0, reason=f"Auth/Quota ({status})")
                        logger.warning(
                            "Target %s auth/quota error (%s); disabling target and rotating.",
                            target.identifier,
                            error,
                        )
                        continue

                    if is_out_limit:
                        token_budget = min(token_budget * 2, _HARD_MAX_TOKENS)
                        logger.warning(
                            "LLM JSON/output truncated on %s; increasing max_tokens to %s and retrying.",
                            target.identifier,
                            token_budget,
                        )
                        continue

                    if not self._is_retryable(error):
                        self.pool.record_cooldown(target, 60.0, reason=f"NonRetryable ({status})")
                        logger.warning("Non-retryable error on %s: %s; trying next target.", target.identifier, error)
                        continue

                    # Transient provider error
                    delay = self.pool.parse_retry_delay(error, default_delay=10.0)
                    self.pool.record_cooldown(target, delay, reason="Transient")
                    logger.warning("Transient error on %s: %s; trying next target.", target.identifier, error)
                    continue

            # If all candidates in this attempt were tried, wait briefly before next attempt
            if attempt < attempts - 1:
                wait_sec = min(15.0, 2.0 + attempt * 2.0)
                logger.warning(
                    "All LLM candidates exhausted on attempt %d/%d; waiting %.1fs before retry.",
                    attempt + 1,
                    attempts,
                    wait_sec,
                )
                await asyncio.sleep(wait_sec)

        status, detail = self._safe_failure_detail(last_error or RuntimeError("unknown provider error"))
        logger.error(
            "LLM request failed after retries: error_type=%s status=%s",
            type(last_error).__name__ if last_error else "unknown",
            status,
        )
        raise LLMAgentError(detail, status_code=status) from last_error

    async def generate_json(
        self,
        contents: Any,
        *,
        system_instruction: str,
        response_model: type[ModelT],
        temperature: float = 0.1,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> ModelT:
        """Generate, extract, and validate a JSON response against a Pydantic model."""
        schema_json = compact_model_schema(response_model)
        correction = ""
        last_error: BaseException | None = None
        token_budget = max(256, min(max_tokens, _HARD_MAX_TOKENS))
        for attempt in range(3):
            instruction = (
                f"{system_instruction}\n\n"
                "CRITICAL OUTPUT INSTRUCTION: Return a single compact JSON object strictly matching this template format.\n"
                "Do NOT wrap in Markdown code blocks, do NOT include explanations, return ONLY raw JSON.\n\n"
                f"JSON Template:\n{schema_json}"
                f"{correction}"
            )
            try:
                raw = await self.generate_text(
                    contents,
                    system_instruction=instruction,
                    temperature=temperature,
                    response_mime_type="application/json",
                    max_tokens=token_budget,
                )
            except LLMAgentError as error:
                last_error = error
                if attempt == 2:
                    raise
                if self._is_tpm_error(error):
                    token_budget = self._tpm_budget(error, token_budget)
                    continue
                if self._is_output_limit_error(error):
                    token_budget = min(token_budget * 2, _HARD_MAX_TOKENS)
                    correction = (
                        "\n\nThe previous response was truncated before it became valid JSON. "
                        "Return a complete, compact JSON object with no extra commentary."
                    )
                    continue
                raise
            try:
                parsed = parse_json_response(raw)
                return response_model.model_validate(parsed)
            except Exception as error:
                last_error = error
                if attempt == 2:
                    break
                correction = (
                    "\n\nThe previous JSON did not validate against the required schema. Correct these validation "
                    f"errors and return the complete JSON object again: {error}."
                )
                logger.warning(
                    "LLM structured response failed validation; retrying model=%s "
                    "schema=%s attempt=%s error=%s raw_excerpt=%s",
                    self.settings.llm_model,
                    response_model.__name__,
                    attempt + 1,
                    error,
                    raw[:200] if "raw" in locals() else "N/A",
                )
        raise LLMAgentError(
            f"LLM returned JSON that does not match {response_model.__name__} "
            f"after 3 correction attempts: {last_error}"
        ) from last_error

    async def generate_audio_text(self, audio_bytes: bytes, prompt: str) -> str:
        """Interpret an in-memory OGG voice note."""
        if not audio_bytes:
            raise LLMAgentError("The voice message was empty")

        import io
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.ogg"

        try:
            client, model = await self.pool.get_audio_target()
            response = await client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                prompt=prompt,
            )
            return response.text
        except Exception as error:
            logger.error("Audio transcription failed: %s", str(error))
            raise LLMAgentError(f"Audio transcription failed: {error}")

