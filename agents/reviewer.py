"""Code review and self-correction agent."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import BaseAgent
from .coder import CoderOutput
from .provider_pool import TaskComplexity


class ReviewIssue(BaseModel):
    """Actionable issue found during review."""

    filepath: str = Field(min_length=1)
    severity: Literal["blocker", "high", "medium", "low"] = "medium"
    problem: str = Field(min_length=1)
    correction: str = Field(min_length=1)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "medium"
        v_lower = v.strip().lower()
        if v_lower in {"blocker", "critical", "fatal", "urgent"}:
            return "blocker"
        if v_lower in {"high", "major", "error"}:
            return "high"
        if v_lower in {"low", "minor", "info"}:
            return "low"
        return "medium"

    @model_validator(mode="before")
    @classmethod
    def _normalize_issue(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "filepath" not in data:
                for alt in ("file", "path", "filename", "name"):
                    if alt in data and data[alt]:
                        data["filepath"] = str(data[alt])
                        break
                if "filepath" not in data:
                    data["filepath"] = "general"
            if "problem" not in data:
                for alt in ("issue", "description", "reason", "error"):
                    if alt in data and data[alt]:
                        data["problem"] = str(data[alt])
                        break
                if "problem" not in data:
                    data["problem"] = "Review finding"
            if "correction" not in data:
                for alt in ("fix", "solution", "suggestion", "recommendation"):
                    if alt in data and data[alt]:
                        data["correction"] = str(data[alt])
                        break
                if "correction" not in data:
                    data["correction"] = "Address issue"
        return data


class ReviewResult(BaseModel):
    """Review decision and structured feedback for the coder."""

    approved: bool = True
    summary: str = Field(min_length=1, default="Code review completed")
    issues: list[ReviewIssue] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_review_result(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "approved" not in data:
                data["approved"] = True
            if "summary" not in data:
                for alt in ("feedback", "overview", "notes", "result"):
                    if alt in data and data[alt]:
                        data["summary"] = str(data[alt])
                        break
                if "summary" not in data:
                    data["summary"] = "Code review completed"
            if "issues" not in data:
                data["issues"] = []
        return data


class ReviewerAgent:
    """Inspect generated code for correctness, security, and completeness."""

    def __init__(self, base: BaseAgent) -> None:
        self.base = base

    async def review(
        self,
        request: str,
        output: CoderOutput,
        *,
        memory_context: str = "",
    ) -> ReviewResult:
        """Review all generated files and return a push/no-push decision."""
        payload = {
            "original_request": request,
            "generated_output": output.model_dump(),
            "memory_context": memory_context,
        }
        return await self.base.generate_json(
            json.dumps(payload, separators=(",", ":")),
            response_model=ReviewResult,
            max_tokens=1024,
            complexity=TaskComplexity.STANDARD,
            system_instruction=(
                "You are the Reviewer agent. Review the generated repository changes as a "
                "strict production gate. Check imports, syntax consistency, edge cases, "
                "security issues, leaked credentials, unsafe path handling, incomplete logic, "
                "and whether the original request is actually satisfied. "
                "Verify that frontend files meet modern UI/UX standards (responsive layout, modern styling/Tailwind, complete scripts). "
                "APPROVAL RULES: Set approved=true when the code is functionally complete and "
                "safe to commit. Only set approved=false when there is at least one issue with "
                "severity='blocker' or severity='high' (e.g. syntax errors, missing required "
                "files, broken imports, leaked secrets, security vulnerabilities, completely unstyled/broken UI, or the request "
                "is clearly not implemented). Medium and low severity issues (minor inefficiencies, "
                "style preferences, optional optimizations, minor code clarity issues) must be "
                "listed in the issues array but MUST NOT cause approved=false. The code can and "
                "should be shipped with medium/low issues present. Low-risk style preferences "
                "must never block approval. "
                "Use memory only to check known recurring mistakes; require evidence in the "
                "current generated files before declaring a defect or success."
            ),
        )
