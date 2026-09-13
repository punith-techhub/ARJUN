"""Project-intent routing for new builds and edits to registered projects."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import BaseAgent
from .provider_pool import TaskComplexity


class ProjectRoute(BaseModel):
    """LLM classification of the requested project target."""

    mode: Literal["new", "existing", "chat"] = "existing"
    project_key: str = Field(default="", max_length=120)
    project_name: str = Field(default="", max_length=120)
    reason: str = Field(default="Routed request", max_length=500)

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "existing"
        v_lower = v.strip().lower()
        if v_lower in {"new", "create", "fresh"}:
            return "new"
        if v_lower in {"chat", "conversation", "question"}:
            return "chat"
        return "existing"

    @model_validator(mode="before")
    @classmethod
    def _normalize_route(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "mode" not in data:
                data["mode"] = "existing"
            if "reason" not in data:
                data["reason"] = "Routed request"
        return data


class ProjectRouterAgent:
    """Select a registered project or derive a new project name from user intent."""

    def __init__(self, base: BaseAgent) -> None:
        self.base = base

    async def route(self, request: str, project_catalog: str) -> ProjectRoute:
        """Route before planning so repository and Vercel context are correct."""
        return await self.base.generate_json(
            json.dumps(
                {
                    "user_request": request,
                    "registered_projects": project_catalog,
                },
                indent=2,
            ),
            response_model=ProjectRoute,
            max_tokens=256,
            complexity=TaskComplexity.LIGHT,
            system_instruction=(
                "You are Arjun's project router. Decide whether the user wants a NEW project, "
                "an EXISTING registered project, or is just making CHAT conversation. "
                "Select existing when the request says edit, update, fix, change, continue, add to, or refers to an old project. "
                "If the user names a registered project, use its exact project_key. "
                "If the request is a new website/app/product and no matching project is named, choose new. "
                "If the user is just saying hello, asking a general question, or saying something that is clearly not a coding task, choose chat. "
                "For chat mode, put your conversational response in the 'reason' field. "
                "Never invent a project_key that is not in the catalog. For mode=new, project_name must be a "
                "short lowercase kebab-case repository/project name derived from the request. "
                "Do not include secrets or code in the response."
            ),
        )

