"""Architecture and file-change planning agent."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import BaseAgent
from .provider_pool import TaskComplexity


class PlannedFile(BaseModel):
    """One file in the requested implementation plan."""

    filepath: str = Field(min_length=1)
    action: Literal["create", "update"] = "update"
    purpose: str = Field(default="Update file logic")
    implementation_notes: list[str] = Field(default_factory=list)

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
    def _normalize_planned_file(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {
                "filepath": data,
                "action": "update",
                "purpose": "Update file",
                "implementation_notes": [],
            }
        if isinstance(data, dict):
            # Resolve filepath aliases
            if "filepath" not in data or not data["filepath"]:
                for alt in ("path", "file", "filename", "name", "file_path", "target_file"):
                    if alt in data and data[alt]:
                        data["filepath"] = str(data[alt])
                        break
            if "purpose" not in data or not data["purpose"]:
                for alt in ("description", "desc", "reason", "summary", "notes", "goal"):
                    if alt in data and data[alt]:
                        data["purpose"] = str(data[alt])
                        break
                if "purpose" not in data or not data["purpose"]:
                    data["purpose"] = "Implement requested changes"
            if "implementation_notes" in data and isinstance(data["implementation_notes"], str):
                data["implementation_notes"] = [data["implementation_notes"]]
        return data


class EnvironmentRequirement(BaseModel):
    """An application configuration value the implementation may need."""

    key: str = Field(min_length=2, max_length=128)
    purpose: str = Field(min_length=1)
    required: bool = True
    secret: bool = True


class TechnologyDecision(BaseModel):
    """A material architecture choice that should be confirmed by the user."""

    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2, max_length=4)
    context: str = Field(min_length=1)


class TaskPlan(BaseModel):
    """Structured architecture plan produced before code generation."""

    summary: str = Field(default="Execute requested code changes")
    files: list[PlannedFile] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=lambda: ["Fulfill user request"])
    risks: list[str] = Field(default_factory=list)
    technology_stack: list[str] = Field(default_factory=list)
    test_strategy: list[str] = Field(default_factory=list)
    environment_variables: list[EnvironmentRequirement] = Field(default_factory=list)
    technology_decisions: list[TechnologyDecision] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_task_plan(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "summary" not in data or not data["summary"]:
                for alt in ("plan", "description", "overview", "title", "task"):
                    if alt in data and data[alt]:
                        data["summary"] = str(data[alt])
                        break
                if "summary" not in data or not data["summary"]:
                    data["summary"] = "Execute requested code changes"
            if "files" not in data or not data["files"]:
                for alt in ("planned_files", "changed_files", "file_list", "file_changes", "target_files"):
                    if alt in data and data[alt]:
                        data["files"] = data[alt]
                        break
            if "acceptance_criteria" in data:
                if isinstance(data["acceptance_criteria"], str):
                    data["acceptance_criteria"] = [data["acceptance_criteria"]]
            else:
                data["acceptance_criteria"] = ["Code changes fulfill the developer request safely"]
            if not data.get("acceptance_criteria"):
                data["acceptance_criteria"] = ["Code changes fulfill the developer request safely"]
            if "technology_stack" in data and isinstance(data["technology_stack"], str):
                data["technology_stack"] = [data["technology_stack"]]
            if "test_strategy" in data and isinstance(data["test_strategy"], str):
                data["test_strategy"] = [data["test_strategy"]]
            if "risks" in data and isinstance(data["risks"], str):
                data["risks"] = [data["risks"]]
        return data


class TechnologyDecisionResult(BaseModel):
    """The planner's recommendation when the user asks Arjun to decide."""

    choice: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class PlannerAgent:
    """Turn a natural-language request into a bounded, implementable file plan."""

    def __init__(self, base: BaseAgent) -> None:
        self.base = base

    async def plan(
        self,
        request: str,
        *,
        repository_context: str = "",
        memory_context: str = "",
    ) -> TaskPlan:
        """Create a plan with paths, purposes, logic, and acceptance criteria."""
        
        # New Brain Expansion Phase: Pre-planning investigation with Antigravity SDK
        investigation_notes = ""
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key and self.base.settings.gemini_api_keys:
            gemini_key = self.base.settings.gemini_api_keys[0]
        if gemini_key and gemini_key.startswith("AIza"):
            os.environ["GEMINI_API_KEY"] = gemini_key
            try:
                from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig
                
                # Spin up an Antigravity Agent with MCP tools for deep investigation
                mcp_path = os.path.join(os.getcwd(), "mcp_config.json")
                config = LocalAgentConfig(
                    api_key=gemini_key,
                    system_instructions=(
                        "You are an architecture investigator. Analyze the user request and repository context. "
                        "Use your tools (like Figma MCP or sequential-thinking) to break down the problem. "
                        "Output brief architectural notes to guide the JSON code generation phase."
                    ),
                    capabilities=CapabilitiesConfig(mcp_config_path=mcp_path if os.path.exists(mcp_path) else None)
                )
                async with Agent(config) as mcp_agent:
                    inv_prompt = f"REQUEST:\n{request}\n\nREPO:\n{repository_context}\n\nInvestigate and summarize key findings."
                    response = await mcp_agent.chat(inv_prompt)
                    
                    notes_chunks = []
                    async for token in response:
                        notes_chunks.append(token)
                    investigation_notes = "".join(notes_chunks)
            except ImportError:
                pass
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"MCP Investigation failed: {e}")

        prompt = (
            f"USER REQUEST:\n{request}\n\n"
            f"REPOSITORY CONTEXT:\n{repository_context}\n\n"
            f"MEMORY CONTEXT:\n{memory_context}"
        )
        if investigation_notes.strip():
            prompt += f"\n\nINVESTIGATION NOTES (MCP):\n{investigation_notes}"
            
        return await self.base.generate_json(
            prompt,
            response_model=TaskPlan,
            max_tokens=3072,
            complexity=TaskComplexity.HEAVY,
            system_instruction=(
                "You are the Planner agent in a production software repository. "
                "Analyze the developer request and plan a complete implementation. "
                "CRITICAL - MODERN DESIGN & UI/UX EXCELLENCE: "
                "Never plan primitive, unstyled 1990s HTML. Even when the user asks for a simple or static app/game, "
                "mandate production-grade visuals and polish by default: "
                "1. Styling & Assets: Plan Tailwind CSS CDN (<script src='https://cdn.tailwindcss.com'></script>), "
                "Google Fonts (Inter/Outfit), and Lucide Icons (<script src='https://unpkg.com/lucide@latest'></script>). "
                "Use sleek modern dark/light palettes (slate/zinc), glassmorphism (backdrop-blur), and responsive flex/grid. "
                "2. Interactive Games: Mandate a smooth game loop (requestAnimationFrame), crisp canvas rendering, "
                "retro/arcade HUD, procedural synth audio via browser native Web Audio API (AudioContext for flap, score, hit - zero external audio dependencies), "
                "particle explosions, floating score text, pause menu, and localStorage high scores. "
                "3. Mobile & Desktop: Ensure full mobile-touch and keyboard responsiveness. "
                "CRITICAL - LEAN FILE ARCHITECTURE (SPEED & RELIABILITY): "
                "For web apps, games, and static sites, keep the plan LEAN: exactly 2 to 3 files: "
                "1. index.html (Tailwind CDN, Google Fonts, Lucide Icons, canvas/game container, HUD overlay, responsive layout). "
                "2. app.js (complete game loop, canvas rendering, player controls, localStorage high scores, and procedural Web Audio API SFX). "
                "3. (Optional) style.css only if custom CSS animations are needed beyond Tailwind. "
                "DO NOT split a simple web app/game into 5+ separate files (like audio.js, hud.js, game.js) because cross-file mismatches cause review failures and slow builds. "
                "2 cohesive files are 10x faster to generate and 100% bug-free. "
                "CRITICAL - SIMPLICITY SIGNALS: If the user says 'simple', 'static', "
                "'no database', 'no .env', 'no environment variables', 'no login', "
                "'no backend', or 'plain HTML', you MUST plan a fully static implementation "
                "using only HTML, CSS, and JavaScript files. Do NOT plan Flask, Django, "
                "SQLite, PostgreSQL, any server framework, or any environment variables. "
                "The environment_variables list MUST be empty [] for static sites. "
                "Use localStorage or in-memory JS state for any data the user needs to store. "
                "CRITICAL - ENV VARIABLES: Only add items to environment_variables when the "
                "request genuinely requires a secret API key, OAuth token, or external service "
                "credential. Never invent environment variables for a path, a port, or a "
                "configuration value that can be hardcoded or has a safe default. "
                "For a full-stack request (when user does NOT say simple/static), explicitly "
                "cover the frontend, backend/API, database schema and access layer, environment "
                "configuration, deployment configuration, and tests. "
                "Respect the existing repository context supplied by the user. Treat only "
                "current repository files and explicit build output as codebase evidence. "
                "Use prior memory for warnings and continuity, never as proof that a file "
                "still exists. Use repository-relative POSIX paths; never include secrets, generated "
                "binaries, or vague placeholder files. Each file must have concrete "
                "implementation notes. Select a practical technology stack compatible with "
                "the repository and make acceptance criteria and test strategy executable. "
                "Add technology_decisions only for genuinely material choices with two or more "
                "reasonable options; never ask the user about trivial implementation details. "
                "Keep the plan compact: at most 3 files for a simple/static site or game, short notes, "
                "and empty technology_decisions unless a real choice exists."
            ),
        )

    async def decide(
        self,
        request: str,
        decision: TechnologyDecision,
        *,
        repository_context: str = "",
        memory_context: str = "",
    ) -> TechnologyDecisionResult:
        """Compare options using repository evidence when the user replies DO."""
        prompt = (
            f"REQUEST:\n{request}\n\nDECISION:\n{json.dumps(decision.model_dump(), indent=2)}\n\n"
            f"REPOSITORY EVIDENCE:\n{repository_context}\n\nMEMORY WARNINGS:\n{memory_context}"
        )
        return await self.base.generate_json(
            prompt,
            response_model=TechnologyDecisionResult,
            max_tokens=512,
            complexity=TaskComplexity.STANDARD,
            system_instruction=(
                "You are the architecture decision agent. Choose exactly one supplied option. "
                "Compare compatibility with the current repository, security, operational "
                "complexity, maintenance, performance, free-tier cost, and testability. "
                "Do not invent unavailable credentials or claim that an unobserved package is installed."
            ),
        )

    @staticmethod
    def as_prompt(plan: TaskPlan) -> str:
        """Serialize a plan for the coder agent without losing structure."""
        return json.dumps(plan.model_dump(), indent=2)
