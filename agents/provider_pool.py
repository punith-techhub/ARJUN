"""Multi-provider and multi-key LLM pool manager with failover and rotation."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_GEMINI_MODELS = (
    "gemini-2.0-flash",
    "gemini-1.5-flash-8b",
    "gemini-2.5-flash",
)
DEFAULT_GROQ_MODELS = (
    "groq/compound",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "groq/compound-mini",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
)
DEFAULT_OPENROUTER_MODELS = (
    "inclusionai/ling-3.0-flash-vl:free",
    "nex-agi/nex-n2.5-mini:free",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
)


@dataclass(frozen=True, slots=True)
class LLMTarget:
    """A configured target endpoint, model, and authentication key."""

    provider: str
    model: str
    base_url: str | None
    api_key: str
    is_audio_capable: bool = False
    audio_model: str = "whisper-1"

    @property
    def identifier(self) -> str:
        """Unique fingerprint for cooldown tracking."""
        key_tail = self.api_key[-6:] if len(self.api_key) >= 6 else self.api_key
        return f"{self.provider}:{self.model}:{key_tail}"


class LLMProviderPool:
    """Manages a pool of LLM keys and models with automatic rotation and fallback."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clients: dict[tuple[str, str | None], AsyncOpenAI] = {}
        self._cooldowns: dict[str, float] = {}
        self._key_rotation_index: dict[str, int] = {}
        self._targets: list[LLMTarget] = []
        self._lock = asyncio.Lock()
        self._closed = False
        self._build_targets()

    def _build_targets(self) -> None:
        """Construct the list of eligible targets based on settings."""
        targets: list[LLMTarget] = []
        custom_models = list(self.settings.llm_models) if self.settings.llm_models else [self.settings.llm_model]

        # 1. Google Gemini Keys
        if self.settings.gemini_api_keys:
            gemini_models = [m for m in custom_models if "gemini" in m.lower()]
            if not gemini_models:
                gemini_models = list(DEFAULT_GEMINI_MODELS)
            for model in gemini_models:
                for key in self.settings.gemini_api_keys:
                    targets.append(
                        LLMTarget(
                            provider="gemini",
                            model=model,
                            base_url=GEMINI_OPENAI_BASE_URL,
                            api_key=key,
                            is_audio_capable=False,
                        )
                    )

        # 2. Groq Keys
        if self.settings.groq_api_keys:
            groq_models = [
                m for m in custom_models 
                if any(x in m.lower() for x in ("gpt-oss", "qwen", "compound", "llama", "mixtral", "gemma"))
            ]
            if not groq_models:
                groq_models = list(DEFAULT_GROQ_MODELS)
            else:
                for def_m in DEFAULT_GROQ_MODELS:
                    if def_m not in groq_models:
                        groq_models.append(def_m)
            for model in groq_models:
                for key in self.settings.groq_api_keys:
                    targets.append(
                        LLMTarget(
                            provider="groq",
                            model=model,
                            base_url=GROQ_OPENAI_BASE_URL,
                            api_key=key,
                            is_audio_capable=True,
                            audio_model="whisper-large-v3",
                        )
                    )

        # 3. OpenRouter Keys
        if self.settings.openrouter_api_keys:
            openrouter_models = [m for m in custom_models if "/" in m or ":free" in m]
            if not openrouter_models:
                openrouter_models = list(DEFAULT_OPENROUTER_MODELS)
            for model in openrouter_models:
                for key in self.settings.openrouter_api_keys:
                    targets.append(
                        LLMTarget(
                            provider="openrouter",
                            model=model,
                            base_url=OPENROUTER_BASE_URL,
                            api_key=key,
                            is_audio_capable=False,
                        )
                    )

        # 4. Generic Keys (LLM_API_KEYS / LLM_API_KEY)
        handled_keys = set(self.settings.gemini_api_keys) | set(self.settings.groq_api_keys) | set(self.settings.openrouter_api_keys)
        generic_keys = [
            k for k in self.settings.llm_api_keys
            if k not in handled_keys and k not in {"not_needed_for_omniroute", "replace_with_api_key", ""}
        ]
        if (
            self.settings.llm_api_key
            and self.settings.llm_api_key not in handled_keys
            and self.settings.llm_api_key not in generic_keys
            and self.settings.llm_api_key not in {"not_needed_for_omniroute", "replace_with_api_key", ""}
        ):
            generic_keys.insert(0, self.settings.llm_api_key)

        for key in generic_keys:
            base_url = self.settings.llm_base_url
            if base_url and any(x in base_url for x in ("20128", "omniroute")):
                base_url = None

            provider = "custom"
            is_audio = False
            audio_model = "whisper-1"

            if key.startswith("gsk_"):
                provider = "groq"
                base_url = GROQ_OPENAI_BASE_URL
                is_audio = True
                audio_model = "whisper-large-v3"
            elif key.startswith("AIza"):
                provider = "gemini"
                base_url = GEMINI_OPENAI_BASE_URL
            elif key.startswith("sk-or-"):
                provider = "openrouter"
                base_url = OPENROUTER_BASE_URL

            # Determine models for this generic key
            models = list(custom_models)
            if provider == "groq":
                models = [m for m in models if any(x in m.lower() for x in ("llama", "mixtral", "gemma"))]
                if not models:
                    models = list(DEFAULT_GROQ_MODELS)
            elif provider == "gemini":
                models = [m for m in models if "gemini" in m.lower()]
                if not models:
                    models = list(DEFAULT_GEMINI_MODELS)
            elif provider == "openrouter":
                models = [m for m in models if "/" in m or ":free" in m]
                if not models:
                    models = list(DEFAULT_OPENROUTER_MODELS)

            for model in models:
                targets.append(
                    LLMTarget(
                        provider=provider,
                        model=model,
                        base_url=base_url,
                        api_key=key,
                        is_audio_capable=is_audio or (base_url is None),
                        audio_model=audio_model,
                    )
                )

        self._targets = targets
        logger.info(
            "Initialized LLMProviderPool with %d targets across providers: %s",
            len(targets),
            list({t.provider for t in targets}),
        )

    def get_client(self, target: LLMTarget) -> AsyncOpenAI:
        """Retrieve or construct a cached AsyncOpenAI client for a target."""
        cache_key = (target.api_key, target.base_url)
        if cache_key not in self._clients:
            self._clients[cache_key] = AsyncOpenAI(
                api_key=target.api_key,
                base_url=target.base_url,
            )
        return self._clients[cache_key]

    async def get_candidate_targets(self) -> list[LLMTarget]:
        """Return candidate targets, prioritizing available targets without active cooldown."""
        async with self._lock:
            now = time.monotonic()
            # Clean up expired cooldowns
            expired = [k for k, v in self._cooldowns.items() if v <= now]
            for k in expired:
                del self._cooldowns[k]

            available: list[LLMTarget] = []
            cooling: list[LLMTarget] = []

            for target in self._targets:
                if target.identifier in self._cooldowns:
                    cooling.append(target)
                else:
                    available.append(target)

            # If all are in cooldown, return the ones that will recover soonest
            if not available and cooling:
                cooling.sort(key=lambda t: self._cooldowns.get(t.identifier, float("inf")))
                logger.warning(
                    "All %d LLM targets are in cooldown! Using soonest recoverable target: %s",
                    len(self._targets),
                    cooling[0].identifier,
                )
                return cooling

            # Rotate keys per provider to distribute load
            result: list[LLMTarget] = []
            providers = sorted(list({t.provider for t in available}))
            for prov in providers:
                prov_targets = [t for t in available if t.provider == prov]
                idx = self._key_rotation_index.get(prov, 0) % max(1, len(prov_targets))
                # Shift by idx
                rotated = prov_targets[idx:] + prov_targets[:idx]
                result.extend(rotated)
                self._key_rotation_index[prov] = idx + 1

            return result or self._targets

    def record_cooldown(self, target: LLMTarget, duration: float, reason: str = "rate_limit") -> None:
        """Mark a target in cooldown to prevent repeated failures."""
        now = time.monotonic()
        self._cooldowns[target.identifier] = now + duration
        logger.warning(
            "LLM target %s entered %.1fs cooldown due to %s. Total in cooldown: %d/%d",
            target.identifier,
            duration,
            reason,
            len(self._cooldowns),
            len(self._targets),
        )

    def parse_retry_delay(self, error: BaseException, default_delay: float = 30.0) -> float:
        """Extract retry-after seconds from error text or header if present."""
        text = str(error)
        match = re.search(
            r"(?:try\s+again\s+in|retry\s+after|wait\s+for|in\s+)\D{0,10}(\d+(?:\.\d+)?)\s*s",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return float(match.group(1)) + 1.0
        return default_delay

    async def get_audio_target(self) -> tuple[AsyncOpenAI, str]:
        """Get an audio-capable client and transcription model."""
        audio_targets = [t for t in self._targets if t.is_audio_capable]
        if not audio_targets:
            # Fallback to any target
            audio_targets = self._targets

        if not audio_targets:
            raise RuntimeError("No LLM targets configured for audio transcription")

        target = audio_targets[0]
        client = self.get_client(target)
        return client, target.audio_model

    async def close(self) -> None:
        """Close all cached AsyncOpenAI clients."""
        if self._closed:
            return
        self._closed = True
        for client in self._clients.values():
            try:
                await client.close()
            except Exception as err:
                logger.debug("Error closing OpenAI client: %s", err)
        self._clients.clear()
