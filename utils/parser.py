"""Defensive extraction helpers for model responses."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_markdown_fence(value: str) -> str:
    """Remove a surrounding or embedded Markdown code fence, if present."""
    val = value.strip()
    match = re.search(r"```(?:json|JSON)?\s*([\s\S]*?)\s*```", val)
    if match:
        return match.group(1).strip()
    return val


def parse_json_response(value: str) -> Any:
    """Parse strict JSON or recover the first valid object/array from model prose."""
    cleaned = strip_markdown_fence(value)
    
    def try_load(s: str) -> Any:
        try:
            return json.loads(s)
        except Exception:
            # Clean trailing commas before } or ]
            s_clean = re.sub(r",\s*([\]}])", r"\1", s)
            return json.loads(s_clean)

    try:
        return try_load(cleaned)
    except Exception as original_error:
        decoder = json.JSONDecoder()
        candidates: list[Any] = []
        for target in (cleaned, value):
            # Prioritize JSON objects ({}) over arrays ([]) since all schema models are objects
            for preferred_char in ("{", "["):
                for index, character in enumerate(target):
                    if character != preferred_char:
                        continue
                    try:
                        parsed, _ = decoder.raw_decode(target[index:])
                        if isinstance(parsed, dict):
                            return parsed
                        candidates.append(parsed)
                    except json.JSONDecodeError:
                        continue
        if candidates:
            return candidates[0]
        raise ValueError(f"No valid JSON object or array found in model response: {value[:200]}") from original_error
