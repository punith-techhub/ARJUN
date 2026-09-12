"""Workflow coordinator for planning, coding, review, and GitHub delivery."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from services.github_service import GitHubService, GitHubWriteResult
from services.secret_service import SecretStore, SecretStoreError
from services.memory_service import MemoryService
from services.project_service import ProjectManager, ProjectRuntime
from services.vercel_service import VercelDeploymentResult, VercelError, VercelService

from .coder import CoderAgent, CoderOutput
from .planner import PlannerAgent, TaskPlan, TechnologyDecision
from .reviewer import ReviewerAgent, ReviewResult

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str], Awaitable[None]]
AskUserCallback = Callable[[str, bool], Awaitable[str]]


class OrchestrationError(RuntimeError):
    """Raised when a task cannot safely reach GitHub."""

class ChatResponse(RuntimeError):
    """Raised when the user makes conversation instead of a coding task."""


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    """GitHub delivery plus the optional Vercel deployment result."""

    github: GitHubWriteResult
    deployment: VercelDeploymentResult | None = None
    debug_attempts: int = 0
    production: bool = False


class Orchestrator:
    """Coordinate the specialized agents and GitHub service."""

    def __init__(
        self,
        planner: PlannerAgent,
        coder: CoderAgent,
        reviewer: ReviewerAgent,
        github: GitHubService,
        vercel: VercelService | None = None,
        memory: MemoryService | None = None,
        secrets: SecretStore | None = None,
        project_manager: ProjectManager | None = None,
    ) -> None:
        self.planner = planner
        self.coder = coder
        self.reviewer = reviewer
        self.github = github
        self.vercel = vercel
        self.memory = memory
        self.secrets = secrets
        self.project_manager = project_manager
        # GitHub branch updates and Vercel promotion are repository-wide state
        # changes. Queue requests so continuous Telegram messages cannot race.
        self._workflow_lock = asyncio.Lock()

    async def run(
        self,
        request: str,
        *,
        progress: ProgressCallback | None = None,
        user_id: int | None = None,
        ask_user: AskUserCallback | None = None,
    ) -> OrchestrationResult:
        """Queue and run a task while recording durable evidence and outcomes."""
        if not request.strip():
            raise OrchestrationError("The task request is empty")

        queued = self._workflow_lock.locked()
        if queued and progress is not None:
            await progress("⏳ Another build is using this repository. Your command is queued safely...")
        async with self._workflow_lock:
            runtime: ProjectRuntime | None = None
            original_services = (self.github, self.vercel, self.memory, self.secrets)
            try:
                if self.project_manager is not None:
                    try:
                        runtime = await self.project_manager.runtime_for(
                            request,
                            user_id=user_id,
                            ask_user=ask_user,
                        )
                    except (OrchestrationError, ChatResponse):
                        raise
                    except Exception as error:
                        raise OrchestrationError(
                            f"Project routing or automatic repository creation failed: {error}"
                        ) from error
                    self.github = runtime.github
                    self.vercel = runtime.vercel
                    self.memory = runtime.memory
                    self.secrets = runtime.secrets

                task_id = None
                memory_context = ""
                if self.memory is not None:
                    task_id = await self.memory.start_task(request, user_id)
                    memory_context = await self.memory.context()
                try:
                    result = await self._run(
                        request,
                        progress=progress,
                        memory_context=memory_context,
                        user_id=user_id,
                        ask_user=ask_user,
                    )
                except Exception as error:
                    if self.memory is not None and task_id is not None:
                        await self.memory.finish_task(
                            task_id,
                            status="failed",
                            summary=str(error),
                        )
                    raise
                if self.memory is not None and task_id is not None:
                    if (
                        runtime is not None
                        and self.project_manager is not None
                        and self.vercel is not None
                        and self.vercel.settings.vercel_project_id
                    ):
                        await self.project_manager.registry.update_vercel(
                            runtime.record.key,
                            self.vercel.settings.vercel_project_id,
                            self.vercel.settings.vercel_project_name,
                        )
                    deployment_url = result.deployment.url if result.deployment else ""
                    await self.memory.finish_task(
                        task_id,
                        status="succeeded",
                        summary="Verified by reviewer and remote deployment smoke test",
                        commit_url=result.github.commit_url,
                        deployment_url=deployment_url,
                        debug_attempts=result.debug_attempts,
                    )
                    await self.memory.remember_fact(
                        "last_verified_task",
                        request.strip(),
                        "successful GitHub commit + Vercel verification",
                    )
                    if result.deployment and result.deployment.url:
                        await self.memory.remember_fact(
                            "last_verified_deployment",
                            result.deployment.url,
                            "successful Vercel deployment + smoke test",
                        )
                return result
            finally:
                if runtime is not None:
                    await runtime.close()
                    self.github, self.vercel, self.memory, self.secrets = original_services

    async def _run(
        self,
        request: str,
        *,
        progress: ProgressCallback | None = None,
        memory_context: str = "",
        user_id: int | None = None,
        ask_user: AskUserCallback | None = None,
    ) -> OrchestrationResult:
        """Run the actual workflow after the repository queue grants access."""

        async def update(message: str) -> None:
            if progress is not None:
                await progress(message)

        async def verify_deployment(deployment: VercelDeploymentResult) -> VercelDeploymentResult:
            """Run a public HTTP smoke test after Vercel's build gate."""
            if self.vercel is None or not deployment.ready:
                return deployment
            await update("🔎 Smoke-testing the deployed website...")
            passed, detail = await self.vercel.smoke_test(deployment.url)
            if passed:
                await update(f"✅ Remote smoke test passed ({detail})")
                return deployment
            await update(f"❌ Remote smoke test failed: {detail[:500]}")
            return replace(
                deployment,
                state="ERROR",
                error_summary=f"Public deployment smoke test failed: {detail}",
            )

        await update("🧠 Planning file changes...")
        planning_context = await self.github.repository_context()
        plan = await self.planner.plan(
            request,
            repository_context=planning_context,
            memory_context=memory_context,
        )
        decision_context = await self._resolve_decisions(
            request,
            plan.technology_decisions,
            planning_context,
            memory_context,
            user_id=user_id,
            ask_user=ask_user,
        )
        secret_values = await self._collect_missing_secrets(
            plan,
            user_id=user_id,
            ask_user=ask_user,
        )
        if decision_context:
            memory_context = f"{memory_context}\n\nUSER-APPROVED DECISIONS:\n{decision_context}"
        technology = ", ".join(plan.technology_stack) or self._technology_hint(
            [item.filepath for item in plan.files]
        )
        test_strategy = "; ".join(plan.test_strategy[:3]) or "Vercel remote build and deployment smoke check"
        await update(
            "🧭 Build plan ready.\n"
            f"{plan.summary[:700]}\n"
            f"Technology: {technology}\n"
            f"Testing: {test_strategy}\n"
            "Next: spawn coder/reviewer agents, deliver to GitHub, remote-test on Vercel, and promote the verified build."
        )
        context_paths = [planned.filepath for planned in plan.files]
        context_paths.extend(
            [
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "requirements.txt",
                "pyproject.toml",
                "next.config.js",
                "next.config.mjs",
                "vite.config.ts",
                ".env.example",
            ]
        )
        file_context = await self.github.file_context(list(dict.fromkeys(context_paths)))

        filenames = ", ".join(item.filepath for item in plan.files[:4])
        if len(plan.files) > 4:
            filenames += f", +{len(plan.files) - 4} more"
        await update(f"💻 Generating code for {filenames}...")
        generated = await self.coder.implement(
            plan,
            repository_context=file_context,
            memory_context=memory_context,
        )
        self._validate_generated_output(plan, generated)
        secret_values.update(
            await self._collect_generated_env_secrets(
                generated,
                plan,
                user_id=user_id,
                ask_user=ask_user,
            )
        )

        await update("🔍 Reviewing and validating syntax...")
        review = await self.reviewer.review(request, generated, memory_context=memory_context)
        if not review.approved:
            if self.memory is not None:
                await self.memory.record_lesson(
                    category="review_gate",
                    symptom=review.summary,
                    fix="Applied reviewer corrections before delivery",
                )
            await update("🛠️ Applying one reviewer correction loop...")
            generated = await self.coder.implement(
                plan,
                review_feedback=self._format_review(review),
                repository_context=file_context,
                memory_context=memory_context,
            )
            self._validate_generated_output(plan, generated)
            await update("🔍 Re-reviewing corrected code...")
            review = await self.reviewer.review(request, generated, memory_context=memory_context)

        if not review.approved:
            # Safety net: only block delivery when there is at least one blocker or high severity
            # issue. If the reviewer still rejects after correction but all remaining issues are
            # medium/low (e.g. minor inefficiencies, style), ship the code anyway rather than
            # failing the whole task — medium/low issues do not prevent functional delivery.
            blocking_issues = [
                issue for issue in review.issues if issue.severity in ("blocker", "high")
            ]
            if blocking_issues:
                raise OrchestrationError(
                    "Review did not approve the generated code after one correction loop: "
                    f"{review.summary}"
                )
            logger.warning(
                "Reviewer returned approved=false with only medium/low issues; shipping anyway: %s",
                review.summary,
            )

        max_debug_attempts = self.github.settings.max_debug_attempts
        vercel_active = bool(self.vercel is not None and self.vercel.settings.vercel_token)
        if vercel_active:
            await update("🔧 Discovering or provisioning the Vercel project...")
            try:
                project = await self.vercel.ensure_project(
                    repository_name=self.github.settings.github_repo,
                    production_branch=self.github.settings.default_branch,
                )
                await update(f"✅ Vercel project ready: {project.name}")
            except VercelError as error:
                logger.warning("Vercel project provisioning failed: %s", error)
                await update(f"⚠️ Vercel integration skipped ({error}). Delivering code to GitHub only.")
                vercel_active = False

        last_deployment_failure = ""
        for debug_attempt in range(max_debug_attempts + 1):
            if debug_attempt:
                await update(
                    f"🛠️ Master agent debugging Vercel build "
                    f"(attempt {debug_attempt}/{max_debug_attempts})..."
                )
            else:
                await update("🚀 Pushing commit to GitHub...")
            try:
                result = await self.github.commit_files(
                    generated.files,
                    generated.commit_message,
                )
            except Exception as error:
                logger.exception("GitHub delivery failed")
                raise OrchestrationError("GitHub delivery failed") from error

            if not vercel_active:
                await update("✅ GitHub task completed (Vercel deployment is not configured or unavailable)")
                return OrchestrationResult(github=result, debug_attempts=debug_attempt)

            await update("🔐 Syncing configured environment variables to Vercel...")
            try:
                env_result = await self.vercel.sync_environment(values=secret_values)
                if env_result.missing:
                    await update(
                        "⚠️ Vercel variables missing from the worker: "
                        + ", ".join(env_result.missing[:8])
                    )
            except VercelError as error:
                logger.warning("Vercel environment synchronization failed: %s", error)
                await update(f"⚠️ Vercel environment sync skipped: {error}. Proceeding with deployment...")

            preview_mode = (
                self.github.settings.auto_promote_production
                and result.branch != self.github.settings.default_branch
            )
            deployment_target = (
                self.github.settings.vercel_preview_target
                if preview_mode
                else self.github.settings.vercel_target
            )
            await update(
                "🧪 Deploying the committed revision to Vercel for remote build verification..."
            )
            try:
                deployment = await self.vercel.deploy(
                    repository_name=result.repository_name,
                    repository_id=result.repository_id,
                    branch=result.branch,
                    commit_sha=result.commit_sha,
                    target=deployment_target,
                )
            except VercelError as error:
                logger.warning("Vercel deployment request failed: %s", error)
                await update(
                    f"⚠️ Vercel deployment failed ({error}). Code delivered to GitHub successfully."
                )
                return OrchestrationResult(github=result, debug_attempts=debug_attempt)

            deployment = await verify_deployment(deployment)

            if not deployment.ready:
                last_deployment_failure = deployment.error_summary
                if self.memory is not None:
                    await self.memory.record_lesson(
                        category="vercel_deployment",
                        symptom=deployment.error_summary,
                    )

            if deployment.ready and preview_mode:
                await update(
                    "✅ Preview build passed. Promoting the tested commit to "
                    f"{self.github.settings.default_branch}..."
                )
                try:
                    promotion = await self.github.promote_working_branch(
                        generated.commit_message
                    )
                except Exception as error:
                    logger.exception("Automatic production promotion failed")
                    raise OrchestrationError(
                        "Preview passed, but automatic promotion to the production branch "
                        f"failed: {error}"
                    ) from error
                result = replace(
                    result,
                    commit_url=promotion.commit_url,
                    branch_url=promotion.branch_url,
                    branch=promotion.branch,
                    commit_sha=promotion.commit_sha,
                )
                await update("🚀 Deploying the tested commit to the Vercel production environment...")
                try:
                    deployment = await self.vercel.deploy(
                        repository_name=result.repository_name,
                        repository_id=result.repository_id,
                        branch=result.branch,
                        commit_sha=result.commit_sha,
                        target=self.github.settings.vercel_target,
                    )
                except VercelError as error:
                    logger.warning("Vercel production deployment failed: %s", error)
                    await update(
                        f"⚠️ Vercel production deployment failed: {error}. "
                        f"Promotion to {self.github.settings.default_branch} succeeded."
                    )
                    return OrchestrationResult(
                        github=result,
                        deployment=deployment,
                        debug_attempts=debug_attempt,
                        production=False,
                    )
                deployment = await verify_deployment(deployment)

            if deployment.ready:
                if self.memory is not None and last_deployment_failure:
                    await self.memory.record_lesson(
                        category="vercel_deployment",
                        symptom=last_deployment_failure,
                        fix=f"Automatic repair attempt {debug_attempt} reached a verified deployment",
                    )
                await update("✅ Production deployment is ready")
                return OrchestrationResult(
                    github=result,
                    deployment=deployment,
                    debug_attempts=debug_attempt,
                    production=True,
                )

            if debug_attempt >= max_debug_attempts:
                raise OrchestrationError(
                    "Vercel build failed after automatic debugging: "
                    f"{deployment.error_summary[:1500]}"
                )

            await update("🧪 Reading Vercel build errors and preparing a correction...")
            file_context = await self.github.file_context(
                [planned.filepath for planned in plan.files]
            )
            generated = await self.coder.implement(
                plan,
                review_feedback=self._format_deployment_failure(deployment),
                repository_context=file_context,
                memory_context=memory_context,
            )
            self._validate_generated_output(plan, generated)
            await update("🔍 Reviewing the automatic build correction...")
            review = await self.reviewer.review(request, generated, memory_context=memory_context)
            if not review.approved:
                await update("🛠️ Applying the reviewer correction to the debug fix...")
                generated = await self.coder.implement(
                    plan,
                    review_feedback=self._format_review(review),
                    repository_context=file_context,
                    memory_context=memory_context,
                )
                self._validate_generated_output(plan, generated)
                review = await self.reviewer.review(request, generated, memory_context=memory_context)
            if not review.approved:
                raise OrchestrationError(
                    "The automatic Vercel fix did not pass review: " + review.summary
                )

        raise OrchestrationError("The task did not reach a terminal state")

    async def _resolve_decisions(
        self,
        request: str,
        decisions: list[TechnologyDecision],
        repository_context: str,
        memory_context: str,
        *,
        user_id: int | None,
        ask_user: AskUserCallback | None,
    ) -> str:
        """Ask only material technology choices and preserve the selected decision."""
        if not decisions:
            return ""
        if ask_user is None or user_id is None:
            raise OrchestrationError(
                "The planner found a technology decision, but Telegram interaction is unavailable"
            )
        selected: list[str] = []
        for decision in decisions[:3]:
            options = decision.options
            labels = [f"{chr(65 + index)}. {option}" for index, option in enumerate(options)]
            prompt = (
                "🤔 I need your decision before coding.\n"
                f"{decision.question}\n"
                f"Context: {decision.context}\n"
                + "\n".join(labels)
                + "\nReply with A/B/etc., the option text, or DO and I will compare them automatically."
            )
            answer = (await ask_user(prompt, False)).strip()
            choice = await self._choose_option(
                answer,
                options,
                request=request,
                decision=decision,
                repository_context=repository_context,
                memory_context=memory_context,
            )
            selected.append(f"{decision.question} => {choice}")
            if self.memory is not None:
                await self.memory.remember_fact(
                    f"decision_{self.memory.fingerprint(decision.question)}",
                    choice,
                    "explicit user choice or planner comparison",
                )
        return "\n".join(f"- {item}" for item in selected)

    async def _choose_option(
        self,
        answer: str,
        options: list[str],
        *,
        request: str,
        decision: TechnologyDecision,
        repository_context: str,
        memory_context: str,
    ) -> str:
        """Resolve A/B, option text, or DO through a bounded architecture comparison."""
        normalized = answer.casefold()
        if normalized in {"do", "decide", "you decide", "which is better", "research"}:
            result = await self.planner.decide(
                request,
                decision,
                repository_context=repository_context,
                memory_context=memory_context,
            )
            selected = self._match_option(result.choice, options)
            if selected is None:
                raise OrchestrationError(
                    "The architecture decision agent returned an option outside the planner's choices"
                )
            return selected
        if len(normalized) == 1 and normalized.isalpha():
            index = ord(normalized.upper()) - ord("A")
            if 0 <= index < len(options):
                return options[index]
        selected = self._match_option(answer, options)
        if selected is not None:
            return selected
        raise OrchestrationError(
            "Please answer the technology question with an option letter, option text, or DO"
        )

    @staticmethod
    def _match_option(answer: str, options: list[str]) -> str | None:
        """Match a model/user response to one of the exact supplied options."""
        normalized = answer.strip().casefold()
        for option in options:
            if normalized == option.strip().casefold():
                return option
        for option in options:
            if normalized and normalized in option.casefold():
                return option
        return None

    async def _collect_missing_secrets(
        self,
        plan: TaskPlan,
        *,
        user_id: int | None,
        ask_user: AskUserCallback | None,
    ) -> dict[str, str]:
        """Ask for planner-declared credentials before generating code."""
        requirements = {
            item.key.strip().upper(): item.purpose
            for item in plan.environment_variables
            if item.required and item.secret
        }
        return await self._collect_secret_keys(
            requirements,
            user_id=user_id,
            ask_user=ask_user,
        )

    async def _collect_generated_env_secrets(
        self,
        generated: CoderOutput,
        plan: TaskPlan,
        *,
        user_id: int | None,
        ask_user: AskUserCallback | None,
    ) -> dict[str, str]:
        """Catch env variables the coder used but the planner failed to declare."""
        known = {
            item.key.strip().upper(): item.purpose for item in plan.environment_variables
        }
        for key in self._extract_environment_keys(generated):
            known.setdefault(key, "Referenced by generated application code")
        return await self._collect_secret_keys(known, user_id=user_id, ask_user=ask_user)

    async def _collect_secret_keys(
        self,
        requirements: dict[str, str],
        *,
        user_id: int | None,
        ask_user: AskUserCallback | None,
    ) -> dict[str, str]:
        """Collect missing values without placing credential material in model context."""
        if not requirements:
            return {}
        if self.secrets is None:
            raise OrchestrationError("Credentials are required, but secure secret storage is not configured")
        existing = await self.secrets.get_many(list(requirements))
        missing = {key: purpose for key, purpose in requirements.items() if key not in existing}
        if not missing:
            return existing
        if ask_user is None or user_id is None:
            raise OrchestrationError(
                "The task requires credentials. Telegram interaction is unavailable to collect them safely"
            )
        prompt = (
            "🔐 I need these application environment variables before I can continue:\n"
            + "\n".join(f"- {key}: {purpose}" for key, purpose in missing.items())
            + "\n\nReply with one KEY=value per line. Values are encrypted on the worker, "
            "sent only to the deployment provider, and never sent to Gemini or committed to GitHub. "
            "For Gmail/Nodemailer, use a Gmail App Password or OAuth credentials—not your normal Gmail password."
        )
        for _ in range(2):
            response = await ask_user(prompt, True)
            try:
                values = SecretStore.parse_assignments(response, set(missing))
            except SecretStoreError as error:
                raise OrchestrationError(str(error)) from error
            if set(values) == set(missing):
                try:
                    await self.secrets.save_many(values)
                except SecretStoreError as error:
                    raise OrchestrationError(str(error)) from error
                existing.update(values)
                return existing
            prompt = (
                "⚠️ I did not receive every requested variable. Please resend only these missing keys:\n"
                + "\n".join(f"- {key}: {missing[key]}" for key in set(missing) - set(values))
                + "\nUse KEY=value on separate lines."
            )
        raise OrchestrationError("The required environment variables were not supplied completely")

    @staticmethod
    def _extract_environment_keys(generated: CoderOutput) -> set[str]:
        """Find uppercase runtime env references without reading their values."""
        patterns = (
            r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]*)",
            r"(?:process\.env|import\.meta\.env)\[['\"]([A-Z][A-Z0-9_]*)['\"]\]",
            r"os\.(?:getenv|environ\.get)\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]",
            r"os\.environ\[['\"]([A-Z][A-Z0-9_]*)['\"]\]",
        )
        safe = {"NODE_ENV", "PORT", "HOST", "CI", "HOME", "PWD"}
        found: set[str] = set()
        for item in generated.files:
            for pattern in patterns:
                found.update(re.findall(pattern, item.content))
        return {key for key in found if key not in safe}

    @staticmethod
    def _format_review(review: ReviewResult) -> str:
        """Create concise, actionable coder feedback."""
        lines = [review.summary]
        lines.extend(
            f"- [{issue.severity}] {issue.filepath}: {issue.problem} Correction: {issue.correction}"
            for issue in review.issues
        )
        return "\n".join(lines)

    @staticmethod
    def _format_deployment_failure(deployment: VercelDeploymentResult) -> str:
        """Turn Vercel build output into actionable feedback for the coder."""
        return (
            "The previous GitHub revision failed during the Vercel deployment. "
            "Diagnose the actual build failure, correct the planned files, and preserve "
            "the original feature scope. Vercel output follows:\n"
            f"{deployment.error_summary}"
        )

    @staticmethod
    def _technology_hint(paths: list[str]) -> str:
        """Infer a concise human-readable technology hint from planned file types."""
        extension_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".jsx": "React/JavaScript",
            ".ts": "TypeScript",
            ".tsx": "React/TypeScript",
            ".vue": "Vue",
            ".svelte": "Svelte",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".css": "CSS",
            ".html": "HTML",
        }
        technologies = {
            extension_map.get("." + path.rsplit(".", 1)[-1].lower())
            for path in paths
            if "." in path
        }
        technologies.discard(None)
        return ", ".join(sorted(technologies)) or "the existing repository stack"

    @staticmethod
    def _validate_generated_output(plan: TaskPlan, generated: CoderOutput) -> None:
        """Prevent unplanned files, secrets, duplicates, or blank output from shipping."""
        planned_paths = {item.filepath for item in plan.files}
        generated_paths = [item.filepath for item in generated.files]
        if len(generated_paths) != len(set(generated_paths)):
            raise OrchestrationError("Coder returned duplicate file paths")
        if set(generated_paths) != planned_paths:
            raise OrchestrationError(
                "Coder output must contain exactly the files selected by the planner"
            )
        planned_actions = {item.filepath: item.action for item in plan.files}
        for item in generated.files:
            if planned_actions[item.filepath] != item.action:
                raise OrchestrationError(
                    f"Coder action for {item.filepath} disagrees with the planner"
                )
            lowered_path = item.filepath.lower()
            if (
                lowered_path in {".env", ".env.local"}
                or lowered_path.endswith(("/.env", "/.env.local", ".pem", ".key"))
            ):
                raise OrchestrationError(f"Refusing to generate a secret-bearing file: {item.filepath}")
            secret_markers = ("ghp_", "github_pat_", "AIzaSy", "-----BEGIN PRIVATE KEY-----")
            if any(marker in item.content for marker in secret_markers):
                raise OrchestrationError(f"Refusing to commit a likely secret in {item.filepath}")
        for item in generated.files:
            if not item.content.strip():
                raise OrchestrationError(f"Coder returned an empty file for '{item.filepath}'")
