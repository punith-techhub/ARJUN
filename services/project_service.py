"""Persistent project registry and per-project runtime construction."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from agents.base import BaseAgent
from agents.project_router import ProjectRoute, ProjectRouterAgent
from config.settings import Settings

from .github_service import GitHubRepositoryCreation, GitHubService
from .memory_service import MemoryService
from .secret_service import SecretStore
from .vercel_service import VercelService

AskUserCallback = Callable[[str, bool], Awaitable[str]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    """Persisted identity of one user project."""

    key: str
    repository: str
    default_branch: str
    vercel_project_id: str = ""
    vercel_project_name: str = ""


@dataclass(slots=True)
class ProjectRuntime:
    """Services configured for exactly one repository/project pair."""

    record: ProjectRecord
    settings: Settings
    github: GitHubService
    vercel: VercelService
    memory: MemoryService
    secrets: SecretStore

    async def close(self) -> None:
        """Close clients owned by this task runtime."""
        await self.github.close()
        await self.vercel.close()
        await self.memory.close()
        await self.secrets.close()


class ProjectRegistry:
    """Store project aliases and deployment identities in the durable state DB."""

    def __init__(self, database_path: str, default_repository: str, default_branch: str) -> None:
        self.database_path = database_path
        self.default_repository = default_repository
        self.default_branch = default_branch
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._ensure_default()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_key TEXT PRIMARY KEY,
                    repository TEXT NOT NULL UNIQUE,
                    default_branch TEXT NOT NULL,
                    vercel_project_id TEXT NOT NULL DEFAULT '',
                    vercel_project_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def slug(value: str) -> str:
        """Normalize a human project name into a safe alias/repository name."""
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return (normalized or "arjun-project")[:80]

    def _ensure_default(self) -> None:
        owner, name = self.default_repository.split("/", 1)
        key = self.slug(name)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO projects(project_key, repository, default_branch)
                VALUES (?, ?, ?)
                """,
                (key, self.default_repository, self.default_branch),
            )

    def _list(self) -> list[ProjectRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT project_key, repository, default_branch,
                       vercel_project_id, vercel_project_name
                FROM projects ORDER BY project_key
                """
            ).fetchall()
        return [
            ProjectRecord(
                key=row["project_key"],
                repository=row["repository"],
                default_branch=row["default_branch"],
                vercel_project_id=row["vercel_project_id"],
                vercel_project_name=row["vercel_project_name"],
            )
            for row in rows
        ]

    async def list(self) -> list[ProjectRecord]:
        """Return all registered project identities."""
        return await asyncio.to_thread(self._list)

    async def register(
        self,
        *,
        key: str,
        repository: str,
        default_branch: str,
        vercel_project_id: str = "",
        vercel_project_name: str = "",
    ) -> ProjectRecord:
        """Insert or update a project mapping."""
        return await asyncio.to_thread(
            self._register,
            key,
            repository,
            default_branch,
            vercel_project_id,
            vercel_project_name,
        )

    def _register(
        self,
        key: str,
        repository: str,
        default_branch: str,
        vercel_project_id: str,
        vercel_project_name: str,
    ) -> ProjectRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    project_key, repository, default_branch,
                    vercel_project_id, vercel_project_name, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_key) DO UPDATE SET
                    repository = excluded.repository,
                    default_branch = excluded.default_branch,
                    vercel_project_id = CASE WHEN excluded.vercel_project_id <> ''
                        THEN excluded.vercel_project_id ELSE projects.vercel_project_id END,
                    vercel_project_name = CASE WHEN excluded.vercel_project_name <> ''
                        THEN excluded.vercel_project_name ELSE projects.vercel_project_name END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, repository, default_branch, vercel_project_id, vercel_project_name),
            )
        return ProjectRecord(key, repository, default_branch, vercel_project_id, vercel_project_name)

    async def update_vercel(self, key: str, project_id: str, project_name: str) -> None:
        """Remember the Vercel identity created/found for a project."""
        await asyncio.to_thread(self._update_vercel, key, project_id, project_name)

    def _update_vercel(self, key: str, project_id: str, project_name: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE projects SET vercel_project_id = ?, vercel_project_name = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE project_key = ?
                """,
                (project_id, project_name, key),
            )


class ProjectManager:
    """Route requests and create the isolated services needed by each project."""

    def __init__(self, settings: Settings, base_agent: BaseAgent) -> None:
        self.settings = settings
        self.router = ProjectRouterAgent(base_agent)
        self.registry = ProjectRegistry(
            settings.state_db_path,
            settings.github_repo,
            settings.default_branch,
        )

    async def runtime_for(
        self,
        request: str,
        *,
        user_id: int | None,
        ask_user: AskUserCallback | None,
    ) -> ProjectRuntime:
        """Resolve or provision the repository before architecture planning begins."""
        del user_id
        records = await self.registry.list()
        discovery_client = GitHubService(self.settings)
        try:
            try:
                discovered = await discovery_client.list_accessible_repositories()
            except Exception:
                logger.warning("GitHub repository discovery failed; using project registry only", exc_info=True)
                discovered = ()
        finally:
            await discovery_client.close()
        known_repositories = {item.repository.casefold() for item in records}
        available = [
            item for item in discovered if item.repository.casefold() not in known_repositories
        ][:100]
        catalog = "\n".join(
            f"- key={record.key}; repository={record.repository}"
            for record in records[:8]
        )
        if available:
            catalog += "\n" + "\n".join(
                f"- available_existing_key={item.repository.rsplit('/', 1)[-1]}; repository={item.repository}"
                for item in available[:8]
            )
        if len(catalog) > 1000:
            catalog = catalog[:1000] + "\n- (catalog truncated)"
        route = await self.router.route(request, catalog)
        record = self._find_record(route, records)
        if record is None:
            discovered_match = self._find_discovered(route, available)
            if discovered_match is not None:
                record = await self.registry.register(
                    key=self.registry.slug(discovered_match.repository.rsplit("/", 1)[-1]),
                    repository=discovered_match.repository,
                    default_branch=discovered_match.default_branch,
                )
        if route.mode == "chat":
            from agents.orchestrator import ChatResponse
            raise ChatResponse(route.reason)
            
        if route.mode == "existing":
            if record is None:
                if ask_user is None:
                    raise RuntimeError(
                        "The request refers to an existing project, but no registered project matched it"
                    )
                options = "\n".join(
                    f"- {item.key}: {item.repository}" for item in records
                )
                if available:
                    options += "\n" + "\n".join(
                        f"- {item.repository.rsplit('/', 1)[-1]}: {item.repository}"
                        for item in available
                    )
                answer = await ask_user(
                    "📁 Which existing project should I edit?\n" + options
                    + "\nReply with the project key.",
                    False,
                )
                record = self._find_record_by_text(answer, records)
                if record is None:
                    discovered_match = next(
                        (
                            item
                            for item in available
                            if answer.casefold().strip()
                            in {
                                item.repository.casefold(),
                                item.repository.rsplit("/", 1)[-1].casefold(),
                            }
                        ),
                        None,
                    )
                    if discovered_match is not None:
                        record = await self.registry.register(
                            key=self.registry.slug(discovered_match.repository.rsplit("/", 1)[-1]),
                            repository=discovered_match.repository,
                            default_branch=discovered_match.default_branch,
                        )
                if record is None:
                    raise RuntimeError("No registered project matched that project key")
        else:
            if not self.settings.github_auto_create_repositories:
                raise RuntimeError(
                    "Automatic GitHub repository creation is disabled by GITHUB_AUTO_CREATE_REPOSITORIES"
                )
            project_name = self.registry.slug(route.project_name or self._fallback_name(request))
            if "/" in self.settings.github_repo:
                owner = self.settings.github_repo.split("/", 1)[0]
            else:
                try:
                    owner = (self.settings.github_token and discovery_client.client.get_user().login) or "arjun"
                except Exception:
                    owner = "arjun"
            repository = f"{owner}/{project_name}"
            existing = next((item for item in records if item.repository.casefold() == repository.casefold()), None)
            if existing is not None:
                # A user may delete a repository outside Arjun. Do not let the
                # durable registry turn that deleted identity into a permanent
                # failure; verify it and recreate it when it is gone.
                checker_settings = self.settings.model_copy(
                    update={"github_repo": existing.repository}
                )
                checker = GitHubService(checker_settings)
                try:
                    await checker.verify_connection()
                except Exception:
                    logger.info(
                        "Registered repository is unavailable; recreating project repository=%s",
                        existing.repository,
                    )
                    existing = None
                finally:
                    await checker.close()
            # Also check freshly discovered GitHub repos (repos on GitHub but not yet in DB).
            # Without this, requesting a project whose repo already exists on GitHub but is
            # absent from the local registry causes a 422 "name already exists" from the API.
            if existing is None:
                discovered_match = next(
                    (item for item in discovered if item.repository.casefold() == repository.casefold()),
                    None,
                )
                if discovered_match is not None:
                    logger.info(
                        "Repository already exists on GitHub; registering without creation repository=%s",
                        discovered_match.repository,
                    )
                    existing = await self.registry.register(
                        key=project_name,
                        repository=discovered_match.repository,
                        default_branch=discovered_match.default_branch,
                    )
            if existing is not None:
                record = existing
            else:
                creator = GitHubService(self.settings)
                try:
                    created = await creator.create_repository(
                        name=project_name,
                        description="Created by Arjun from a Telegram development request",
                        private=self.settings.github_new_repo_private,
                    )
                finally:
                    await creator.close()
                record = await self.registry.register(
                    key=project_name,
                    repository=created.repository,
                    default_branch=created.default_branch,
                )
        return self._runtime(record)

    def _runtime(self, record: ProjectRecord) -> ProjectRuntime:
        runtime_settings = self.settings.model_copy(
            update={
                "github_repo": record.repository,
                "default_branch": record.default_branch,
                "vercel_project_id": record.vercel_project_id,
                "vercel_project_name": record.vercel_project_name,
            }
        )
        return ProjectRuntime(
            record=record,
            settings=runtime_settings,
            github=GitHubService(runtime_settings),
            vercel=VercelService(runtime_settings),
            memory=MemoryService(runtime_settings.state_db_path, record.repository),
            secrets=SecretStore(runtime_settings.state_db_path, runtime_settings.arjun_secret_key, record.repository),
        )

    @staticmethod
    def _find_record(route: ProjectRoute, records: list[ProjectRecord]) -> ProjectRecord | None:
        target = (route.project_key or route.project_name).casefold().strip()
        if not target:
            return records[0] if len(records) == 1 else None
        return next(
            (
                item
                for item in records
                if target in {item.key.casefold(), item.repository.casefold(), item.repository.rsplit("/", 1)[-1].casefold()}
            ),
            None,
        )

    @staticmethod
    def _find_record_by_text(answer: str, records: list[ProjectRecord]) -> ProjectRecord | None:
        target = answer.casefold().strip()
        return next(
            (
                item
                for item in records
                if target in {item.key.casefold(), item.repository.casefold(), item.repository.rsplit("/", 1)[-1].casefold()}
            ),
            None,
        )

    @staticmethod
    def _find_discovered(
        route: ProjectRoute,
        repositories: tuple[GitHubRepositoryCreation, ...],
    ) -> GitHubRepositoryCreation | None:
        """Match a router key/name against freshly discovered GitHub repositories."""
        target = (route.project_key or route.project_name).casefold().strip()
        if not target:
            return None
        return next(
            (
                item
                for item in repositories
                if target
                in {
                    item.repository.casefold(),
                    item.repository.rsplit("/", 1)[-1].casefold(),
                }
            ),
            None,
        )

    @staticmethod
    def _fallback_name(request: str) -> str:
        """Produce a deterministic repository name if the router returns no name."""
        words = re.findall(r"[a-z0-9]+", request.casefold())
        ignored = {"build", "create", "make", "a", "an", "the", "website", "web", "app", "application"}
        selected = [word for word in words if word not in ignored][:5]
        return "-".join(selected) or "arjun-project"
