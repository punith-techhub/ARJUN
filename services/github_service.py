"""Non-blocking wrapper around PyGithub branch and file operations."""

from __future__ import annotations

import asyncio
import os
import posixpath
import shutil
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from github import Github
from github.GithubException import GithubException

from config.settings import Settings

if TYPE_CHECKING:
    from agents.coder import GeneratedFile


@dataclass(frozen=True, slots=True)
class GitHubWriteResult:
    """Links returned after a successful GitHub write."""

    commit_url: str
    branch_url: str
    file_urls: tuple[str, ...]
    repository_name: str
    repository_id: int
    branch: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class GitHubPromotionResult:
    """Result of promoting the tested working branch to the production branch."""

    branch: str
    commit_sha: str
    commit_url: str
    branch_url: str


@dataclass(frozen=True, slots=True)
class GitHubRepositoryCreation:
    """Identity of a repository created for a new Arjun project."""

    repository: str
    default_branch: str


class GitHubService:
    """Perform safe branch creation and file updates without blocking Telegram."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = Github(settings.github_token)
        self.repository: Any = None
        self._write_lock = asyncio.Lock()

    def _get_repository(self) -> Any:
        """Resolve the configured repository and fail early with a useful error."""
        if self.repository is None:
            if not self.settings.github_repo:
                raise ValueError(
                    "GITHUB_REPO is not configured in your environment. "
                    "Please set GITHUB_REPO=your_username/your_repo in Render environment variables."
                )
            try:
                self.repository = self.client.get_repo(self.settings.github_repo)
            except GithubException as error:
                if error.status == 404:
                    raise ValueError(
                        f"GitHub repository '{self.settings.github_repo}' was not found. "
                        "Check that the repo exists and your GITHUB_TOKEN has access to it."
                    ) from error
                if error.status in {401, 403}:
                    raise ValueError(
                        f"GitHub authentication failed ({error.status}). "
                        "Check that your GITHUB_TOKEN is valid and has 'repo' permissions."
                    ) from error
                msg = getattr(error, "data", {}).get("message", str(error)) if isinstance(getattr(error, "data", None), dict) else str(error)
                raise ValueError(f"GitHub error accessing '{self.settings.github_repo}': {msg}") from error
        return self.repository

    def _read_branch(self, repo: Any) -> Any:
        """Use the agent branch for context when available, otherwise the base branch."""
        try:
            return repo.get_branch(self.settings.agent_working_branch)
        except GithubException as error:
            if error.status != 404:
                raise
            try:
                return repo.get_branch(self.settings.default_branch)
            except GithubException as base_err:
                if base_err.status == 404:
                    return None
                raise

    async def _repository_context(self) -> str:
        """Return a bounded repository tree snapshot and optionally a Graphify architecture report."""
        repo = self._get_repository()
        branch = self._read_branch(repo)
        if branch is None:
            return "Empty repository (no commits or files yet)."
        
        # Always get the standard file list for exact file paths
        try:
            tree = repo.get_git_tree(branch.commit.sha, recursive=True)
            paths = [
                item.path
                for item in tree.tree
                if item.type == "blob" and item.path
            ][:250]
        except Exception:
            return "Empty repository (no commits or files yet)."
        
        suffix = "\n... (tree truncated at 250 files)" if len(tree.tree) > 250 else ""
        standard_context = (
            f"Context branch: {branch.name}\n"
            "Existing repository files:\n"
            + "\n".join(f"- {path}" for path in paths)
            + suffix
        )
        
        # Only use Graphify if the repository is large enough to need architectural clustering
        if len(tree.tree) < 20:
            return standard_context
            
        graphify_cli = "graphify"
        has_graphify = shutil.which(graphify_cli) is not None
        
        if has_graphify:
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    # Shallow clone for analysis
                    clone_url = f"https://oauth2:{self.settings.github_token}@github.com/{repo.full_name}.git"
                    process = await asyncio.create_subprocess_exec(
                        "git", "clone", "--depth", "1", "--branch", branch.name, clone_url, tmpdir,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await process.communicate()
                    
                    if process.returncode == 0:
                        ext = await asyncio.create_subprocess_exec(
                            graphify_cli, "extract", "--code-only", tmpdir,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                        )
                        await ext.communicate()
                        
                        cls = await asyncio.create_subprocess_exec(
                            graphify_cli, "cluster-only", tmpdir,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                        )
                        await cls.communicate()
                        
                        report_path = os.path.join(tmpdir, "graphify-out", "GRAPH_REPORT.md")
                        if os.path.exists(report_path):
                            with open(report_path, "r", encoding="utf-8") as f:
                                report_content = f.read()
                            return f"{standard_context}\n\n=== GRAPHIFY ARCHITECTURE REPORT ===\n{report_content}"
            except Exception:
                pass 
                
        return standard_context

    def _file_context(self, paths: list[str]) -> str:
        """Read only planned text files and cap each snapshot sent to the model."""
        repo = self._get_repository()
        branch = self._read_branch(repo)
        chunks: list[str] = [f"Context branch: {branch.name}"]
        for raw_path in paths[:50]:
            path = self._safe_path(raw_path)
            try:
                existing = repo.get_contents(path, ref=branch.name)
                if isinstance(existing, list):
                    chunks.append(f"FILE {path}: directory (not readable as one file)")
                    continue
                content = existing.decoded_content.decode("utf-8", errors="replace")
                if len(content) > 20_000:
                    content = content[:20_000] + "\n... (file truncated)"
                chunks.append(f"FILE {path}:\n```text\n{content}\n```")
            except GithubException as error:
                if error.status == 404:
                    chunks.append(f"FILE {path}: does not exist yet")
                    continue
                raise
        return "\n\n".join(chunks)

    @staticmethod
    def _safe_path(path: str) -> str:
        """Reject absolute or parent-traversing repository paths."""
        raw_path = path.replace("\\", "/")
        if raw_path.startswith("/") or any(part == ".." for part in raw_path.split("/")):
            raise ValueError(f"Unsafe repository path: {path}")
        normalized = posixpath.normpath(raw_path)
        if not normalized or normalized == ".":
            raise ValueError(f"Unsafe repository path: {path}")
        return normalized

    def _ensure_branch_sync(self):
        """Fetch current refs and create the agent branch from the configured base if needed."""
        repo = self._get_repository()
        try:
            base = repo.get_branch(self.settings.default_branch)
        except GithubException as base_err:
            if base_err.status == 404:
                # Repo is brand new and completely empty! Initialize default branch with an initial commit.
                try:
                    repo.create_file(
                        path="README.md",
                        message="Initial commit from Arjun",
                        content=f"# {repo.name}\n\nCreated by Arjun.\n",
                        branch=self.settings.default_branch,
                    )
                    base = repo.get_branch(self.settings.default_branch)
                except Exception:
                    raise ValueError(
                        f"Repository '{repo.full_name}' is empty and could not be initialized with default branch '{self.settings.default_branch}'."
                    ) from base_err
            else:
                raise

        try:
            branch = repo.get_branch(self.settings.agent_working_branch)
        except GithubException as error:
            if error.status != 404:
                raise
            repo.create_git_ref(
                ref=f"refs/heads/{self.settings.agent_working_branch}",
                sha=base.commit.sha,
            )
            branch = repo.get_branch(self.settings.agent_working_branch)
        return repo, branch

    def _commit_files(self, files: list["GeneratedFile"], message: str) -> GitHubWriteResult:
        """Write files sequentially, refreshing the branch and each file SHA before updates."""
        repo, branch = self._ensure_branch_sync()
        file_urls: list[str] = []
        last_commit_url = ""

        for generated in files:
            path = self._safe_path(generated.filepath)
            branch = repo.get_branch(self.settings.agent_working_branch)
            try:
                existing = repo.get_contents(path, ref=self.settings.agent_working_branch)
                if isinstance(existing, list):
                    raise ValueError(f"Expected a file but found a directory: {path}")
                result = repo.update_file(
                    path=path,
                    message=message,
                    content=generated.content,
                    sha=existing.sha,
                    branch=self.settings.agent_working_branch,
                )
            except GithubException as error:
                if error.status != 404:
                    raise
                result = repo.create_file(
                    path=path,
                    message=message,
                    content=generated.content,
                    branch=self.settings.agent_working_branch,
                )
            commit = result["commit"]
            last_commit_url = commit.html_url
            file_urls.append(
                f"{repo.html_url}/blob/{self.settings.agent_working_branch}/{path}"
            )

        branch = repo.get_branch(self.settings.agent_working_branch)

        return GitHubWriteResult(
            commit_url=last_commit_url,
            branch_url=f"{repo.html_url}/tree/{self.settings.agent_working_branch}",
            file_urls=tuple(file_urls),
            repository_name=repo.full_name,
            repository_id=int(repo.id),
            branch=self.settings.agent_working_branch,
            commit_sha=branch.commit.sha,
        )

    async def verify_connection(self) -> str:
        """Verify repository access without touching any remote state."""
        return await asyncio.to_thread(lambda: self._get_repository().full_name)

    def _create_repository(
        self,
        name: str,
        description: str,
        private: bool,
    ) -> GitHubRepositoryCreation:
        """Create and initialize a repository under the configured GitHub owner."""
        authenticated_user = self.client.get_user()
        if "/" in self.settings.github_repo:
            owner = self.settings.github_repo.split("/", 1)[0]
        else:
            owner = authenticated_user.login
        target_owner = authenticated_user.login
        try:
            if not owner or authenticated_user.login.casefold() == owner.casefold():
                repository = authenticated_user.create_repo(
                    name=name,
                    description=description[:350],
                    private=private,
                    auto_init=True,
                )
            else:
                try:
                    organization = self.client.get_organization(owner)
                    repository = organization.create_repo(
                        name=name,
                        description=description[:350],
                        private=private,
                        auto_init=True,
                    )
                    target_owner = owner
                except GithubException as org_err:
                    if org_err.status in {403, 404}:
                        # owner is not an organization or token lacks org permissions;
                        # safely fall back to creating the repository under the authenticated user
                        target_owner = authenticated_user.login
                        repository = authenticated_user.create_repo(
                            name=name,
                            description=description[:350],
                            private=private,
                            auto_init=True,
                        )
                    else:
                        raise
        except GithubException as exc:
            # 422 "name already exists on this account" — the repo is already there.
            # Fetch and reuse it instead of propagating the error.
            if exc.status == 422:
                try:
                    repository = self.client.get_repo(f"{target_owner}/{name}")
                except Exception:
                    repository = self.client.get_repo(f"{authenticated_user.login}/{name}")
            else:
                raise
        return GitHubRepositoryCreation(
            repository=repository.full_name,
            default_branch=repository.default_branch or self.settings.default_branch,
        )

    async def create_repository(
        self,
        *,
        name: str,
        description: str,
        private: bool,
    ) -> GitHubRepositoryCreation:
        """Create a new initialized repository without blocking the Telegram loop."""
        return await asyncio.to_thread(self._create_repository, name, description, private)

    def _list_accessible_repositories(self) -> tuple[GitHubRepositoryCreation, ...]:
        """List bounded repository metadata visible to the authenticated GitHub user."""
        try:
            # Removed 'type="all"' to prevent the GitHub 422 API conflict with affiliation
            repositories = self.client.get_user().get_repos(
                affiliation="owner,collaborator,organization_member",
                sort="updated",
                direction="desc",
            )
            found: list[GitHubRepositoryCreation] = []
            for repository in repositories:
                found.append(
                    GitHubRepositoryCreation(
                        repository=repository.full_name,
                        default_branch=repository.default_branch or self.settings.default_branch,
                    )
                )
                if len(found) >= 200:
                    break
            return tuple(found)
        except Exception:
            return ()

    async def list_accessible_repositories(self) -> tuple[GitHubRepositoryCreation, ...]:
        """Discover existing repositories without changing GitHub state."""
        return await asyncio.to_thread(self._list_accessible_repositories)

    async def repository_context(self) -> str:
        """Fetch repository structure using Graphify or GitHub API."""
        return await self._repository_context()

    async def file_context(self, paths: list[str]) -> str:
        """Fetch current contents for the files selected by the planner."""
        return await asyncio.to_thread(self._file_context, paths)

    async def commit_files(
        self,
        files: list["GeneratedFile"],
        commit_message: str,
    ) -> GitHubWriteResult:
        """Commit generated files on the configured agent branch."""
        if not files:
            raise ValueError("No generated files were supplied")
        async with self._write_lock:
            return await asyncio.to_thread(self._commit_files, files, commit_message)

    def _promote_working_branch(self, message: str) -> GitHubPromotionResult:
        """Merge the tested working branch into the configured production branch."""
        repo = self._get_repository()
        base_name = self.settings.default_branch
        working_name = self.settings.agent_working_branch
        base = repo.get_branch(base_name)
        if working_name == base_name:
            sha = base.commit.sha
            return GitHubPromotionResult(
                branch=base_name,
                commit_sha=sha,
                commit_url=f"{repo.html_url}/commit/{sha}",
                branch_url=f"{repo.html_url}/tree/{base_name}",
            )

        head = repo.get_branch(working_name)
        comparison = repo.compare(base_name, working_name)
        if comparison.ahead_by > 0:
            merge = repo.merge(
                base=base_name,
                head=working_name,
                commit_message=message,
            )
            merged_commit = getattr(merge, "commit", None)
            sha = str(getattr(merged_commit, "sha", "") or head.commit.sha)
        else:
            sha = base.commit.sha
        return GitHubPromotionResult(
            branch=base_name,
            commit_sha=sha,
            commit_url=f"{repo.html_url}/commit/{sha}",
            branch_url=f"{repo.html_url}/tree/{base_name}",
        )

    async def promote_working_branch(self, message: str) -> GitHubPromotionResult:
        """Promote only after the Vercel preview build has succeeded."""
        async with self._write_lock:
            return await asyncio.to_thread(self._promote_working_branch, message)

    async def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        base: str | None = None,
    ) -> str:
        """Open a PR from the agent branch, when an operator explicitly requests it."""
        def create() -> str:
            repo = self._get_repository()
            pull = repo.create_pull(
                title=title,
                body=body,
                head=self.settings.agent_working_branch,
                base=base or self.settings.default_branch,
            )
            return pull.html_url

        return await asyncio.to_thread(create)

    async def close(self) -> None:
        """Release the PyGithub HTTP client."""
        close = getattr(self.client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)
