"""Implementation/code-generation agent."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import BaseAgent, LLMAgentError
from .planner import PlannedFile, TaskPlan

logger = logging.getLogger(__name__)


class GeneratedFile(BaseModel):
    """A complete repository file emitted by the coder."""

    filepath: str = Field(min_length=1)
    action: Literal["create", "update"] = "update"
    content: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "update"
        v_lower = v.strip().lower()
        if v_lower in {"create", "new", "add", "created", "added"}:
            return "create"
        return "update"

    @model_validator(mode="before")
    @classmethod
    def _normalize_generated_file(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Resolve filepath
            if "filepath" not in data or not data["filepath"]:
                for alt in ("path", "file", "filename", "name", "file_path", "target_file"):
                    if alt in data and data[alt]:
                        data["filepath"] = str(data[alt])
                        break

            # Resolve content
            found_content = ""
            for alt in ("content", "code", "file_content", "body", "source", "text", "file"):
                if alt in data and isinstance(data[alt], str) and data[alt].strip():
                    found_content = data[alt]
                    break

            # If still empty, check any other string value in data that might contain code
            if not found_content.strip():
                for key, val in data.items():
                    if key in ("filepath", "path", "file", "filename", "name", "action") or not isinstance(val, str):
                        continue
                    if "\n" in val or len(val) > 20:
                        found_content = val
                        break

            # If content itself was wrapped in markdown code fence, extract inside fence
            if "```" in found_content:
                fence_match = re.search(r"```(?:\w+)?\s*\n([\s\S]*?)```", found_content)
                if fence_match:
                    found_content = fence_match.group(1)

            data["content"] = found_content
        return data


class CommitMessage(BaseModel):
    """Single-line commit summary for the generated files."""

    commit_message: str = Field(min_length=3, max_length=150, default="feat: implement requested code changes")

    @model_validator(mode="before")
    @classmethod
    def _normalize_commit(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"commit_message": data}
        if isinstance(data, dict):
            if "commit_message" not in data:
                for alt in ("message", "commit", "summary", "title"):
                    if alt in data and data[alt]:
                        data["commit_message"] = str(data[alt])
                        break
                if "commit_message" not in data:
                    data["commit_message"] = "feat: implement requested code changes"
        return data


class CoderOutput(BaseModel):
    """Structured code changes and the single commit message."""

    files: list[GeneratedFile] = Field(min_length=1)
    commit_message: str = Field(min_length=3, max_length=150, default="feat: implement requested code changes")


class CoderAgent:
    """Generate complete code files from a plan and optional review feedback."""

    def __init__(self, base: BaseAgent) -> None:
        self.base = base

    async def implement(
        self,
        plan: TaskPlan,
        *,
        review_feedback: str | None = None,
        repository_context: str = "",
        memory_context: str = "",
    ) -> CoderOutput:
        """Implement the plan one file at a time so Groq TPM limits are not exceeded."""
        feedback = review_feedback or "No prior review feedback; implement the plan from scratch."
        generated: list[GeneratedFile] = []
        for i, planned in enumerate(plan.files):
            if i > 0:
                await asyncio.sleep(2.0)
            file_impl = await self._implement_file(
                plan,
                planned,
                feedback=feedback,
                repository_context=repository_context,
                memory_context=memory_context,
                sibling_paths=[item.filepath for item in generated],
            )
            generated.append(file_impl)

        commit = await self.base.generate_json(
            json.dumps(
                {
                    "summary": plan.summary,
                    "files": [item.filepath for item in generated],
                },
                separators=(",", ":"),
            ),
            response_model=CommitMessage,
            max_tokens=256,
            system_instruction=(
                "Write one concise conventional-commit style message for these files. "
                "No body, no secrets."
            ),
        )
        return CoderOutput(files=generated, commit_message=commit.commit_message)

    async def _implement_file(
        self,
        plan: TaskPlan,
        planned: PlannedFile,
        *,
        feedback: str,
        repository_context: str,
        memory_context: str,
        sibling_paths: list[str],
    ) -> GeneratedFile:
        prompt_dict = {
            "summary": plan.summary,
            "stack": plan.technology_stack,
            "acceptance": plan.acceptance_criteria[:6],
            "target_file": planned.model_dump(),
            "other_planned_paths": [item.filepath for item in plan.files],
            "already_generated_paths": sibling_paths,
            "repository_excerpt": repository_context[:3500],
            "review_feedback": feedback[:1500],
            "memory": memory_context[:500],
        }

        system_instruction = (
            "You are the Coder agent. Produce the complete code content for exactly the "
            f"target_file '{planned.filepath}'.\n"
            "MANDATORY REQUIREMENTS:\n"
            "1. The 'content' field must contain the FULL, WORKING source code for this file. "
            "Never leave 'content' empty, and never output placeholder comments like '// TODO' or '...rest of code...'.\n"
            "2. 'filepath' must exactly match the target_file path.\n"
            "3. 'action' must match the target_file action ('create' or 'update').\n"
            "4. Return valid JSON only with keys: 'filepath', 'action', and 'content'."
        )

        last_result: GeneratedFile | None = None
        for attempt in range(3):
            if attempt > 0:
                prompt_dict["correction_needed"] = (
                    f"CRITICAL ERROR: Previous generation for '{planned.filepath}' returned empty content! "
                    "You MUST populate the 'content' field with the complete, functional code for this file."
                )
                await asyncio.sleep(1.5)

            prompt = json.dumps(prompt_dict, separators=(",", ":"))
            result = await self.base.generate_json(
                prompt,
                response_model=GeneratedFile,
                max_tokens=3500,
                system_instruction=system_instruction,
            )

            # Ensure path and action match
            content = (result.content or "").strip()
            if content:
                return GeneratedFile(
                    filepath=planned.filepath,
                    action=planned.action,
                    content=result.content,
                )

            logger.warning(
                "Coder returned empty content for '%s' on attempt %d/3. Retrying...",
                planned.filepath,
                attempt + 1,
            )
            last_result = result

        if last_result is not None:
            return GeneratedFile(
                filepath=planned.filepath,
                action=planned.action,
                content=last_result.content,
            )

        raise LLMAgentError(f"Coder agent produced an empty file for '{planned.filepath}' after 3 attempts.")

