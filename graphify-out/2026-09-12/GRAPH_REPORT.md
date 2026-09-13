# Graph Report - ARJUN  (2026-08-26)

## Corpus Check
- 27 files · ~16,844 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 445 nodes · 860 edges · 26 communities (19 shown, 7 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 55 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `885b096f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Orchestrator
- VercelService
- telegram_handler.py
- Setup
- MemoryService
- SecretStore
- ._get_repository
- TelegramHandler
- BaseAgent
- GitHubService
- Settings
- ProjectRegistry
- project_service.py
- github_service.py
- ProjectRoute
- GeneratedFile
- ReviewIssue
- agents/__init__.py
- services/__init__.py
- utils/__init__.py
- ._normalize_action
- rules/graphify.md
- workflows/graphify.md
- .close
- start.sh

## God Nodes (most connected - your core abstractions)
1. `Orchestrator` - 36 edges
2. `BaseAgent` - 35 edges
3. `GitHubService` - 27 edges
4. `VercelService` - 27 edges
5. `TelegramHandler` - 26 edges
6. `MemoryService` - 25 edges
7. `ProjectManager` - 24 edges
8. `Settings` - 23 edges
9. `SecretStore` - 22 edges
10. `build_application()` - 17 edges

## Surprising Connections (you probably didn't know these)
- `TelegramHandler` --uses--> `LLMAgentError`  [INFERRED]
  services/telegram_handler.py → agents/base.py
- `BaseAgent` --uses--> `Settings`  [INFERRED]
  agents/base.py → config/settings.py
- `ProjectManager` --uses--> `BaseAgent`  [INFERRED]
  services/project_service.py → agents/base.py
- `VoiceTranscriber` --uses--> `BaseAgent`  [INFERRED]
  utils/audio.py → agents/base.py
- `TelegramHandler` --uses--> `OrchestrationError`  [INFERRED]
  services/telegram_handler.py → agents/orchestrator.py

## Import Cycles
- None detected.

## Communities (26 total, 7 thin omitted)

### Community 0 - "Orchestrator"
Cohesion: 0.05
Nodes (52): CoderAgent, CoderOutput, Implementation/code-generation agent., Structured code changes and the single commit message., Generate complete code files from a plan and optional review feedback., Implement the plan one file at a time so Groq TPM limits are not exceeded., Orchestrator, AskUserCallback (+44 more)

### Community 1 - "VercelService"
Cohesion: 0.07
Nodes (27): EnvironmentSyncResult, Any, RuntimeError, Make an API request and turn provider errors into safe exceptions., Find the configured project or create/link it to the GitHub repository., Normalize a Vercel project response., Convert a GitHub repository name into a valid stable Vercel project name., Normalize a GitHub URL or full name to the Vercel owner/repository format. (+19 more)

### Community 2 - "telegram_handler.py"
Cohesion: 0.05
Nodes (35): LLMAgentError, Shared LLM client wrapper and structured-response primitives., Raised when the LLM cannot produce a usable response., ChatResponse, OrchestrationError, RuntimeError, Raised when a task cannot safely reach GitHub., Raised when the user makes conversation instead of a coding task. (+27 more)

### Community 3 - "Setup"
Cohesion: 0.15
Nodes (12): 1. Create credentials, 2. Configure locally, 3. Give Arjun deployment authority, 4. Run, Arjun — Autonomous Telegram Developer Bot, Free worker deployment and laptop-off operation, Memory and continuity, New projects versus existing projects (+4 more)

### Community 4 - "MemoryService"
Cohesion: 0.11
Nodes (15): MemoryService, _now(), Connection, Persist the terminal result without storing generated source code., Create a stable, secret-safe fingerprint for a recurring failure., Upsert one verified failure lesson, increasing its recurrence count., Return a sortable UTC timestamp., Store a concise fact only after it came from repository/build evidence. (+7 more)

### Community 5 - "SecretStore"
Cohesion: 0.13
Nodes (15): Fernet, Connection, RuntimeError, Encrypted storage for user-supplied application environment variables., Read a key, preferring a runtime environment value over encrypted storage., Return only requested values; never expose the complete secret store., Encrypt and persist a validated allow-list of user-supplied values., Parse only requested KEY=value lines without logging their values. (+7 more)

### Community 6 - "._get_repository"
Cohesion: 0.08
Nodes (20): OrchestrationResult, GitHub delivery plus the optional Vercel deployment result., GitHubPromotionResult, GitHubWriteResult, Any, Read only planned text files and cap each snapshot sent to the model., Reject absolute or parent-traversing repository paths., Fetch current refs and create the agent branch from the configured base if… (+12 more)

### Community 7 - "TelegramHandler"
Cohesion: 0.15
Nodes (16): DEFAULT_TYPE, Message, Handle .txt file uploads — read the file and treat its content as a coding…, Run orchestration and edit one progress message through each phase., Authenticate users, report progress in-place, and route tasks., Send a blocking question and wait for the user's next authorized message., Route a message to a waiting task instead of accidentally starting a new task., Log framework-level errors without leaking secrets to Telegram. (+8 more)

### Community 8 - "BaseAgent"
Cohesion: 0.11
Nodes (14): BaseAgent, compact_model_schema(), Any, BaseModel, RuntimeError, Shrink reserved completion tokens so prompt + max_tokens fits the TPM limit., Generate text with exponential backoff and jitter for transient failures., Generate, extract, and validate a JSON response against a Pydantic model. (+6 more)

### Community 9 - "GitHubService"
Cohesion: 0.15
Nodes (13): GitHubService, Open a PR from the agent branch, when an operator explicitly requests it., Release the PyGithub HTTP client., Perform safe branch creation and file updates without blocking Telegram., ProjectManager, ProjectRecord, ProjectRuntime, AskUserCallback (+5 more)

### Community 10 - "Settings"
Cohesion: 0.15
Nodes (11): Application configuration package., get_settings(), BaseModel, field_validator, Validated environment-backed application settings., Runtime configuration for the Telegram developer bot., Return the process-wide immutable-by-convention settings object., Require the GitHub owner/repository format. (+3 more)

### Community 11 - "ProjectRegistry"
Cohesion: 0.21
Nodes (7): ProjectRegistry, Connection, Return all registered project identities., Insert or update a project mapping., Remember the Vercel identity created/found for a project., Store project aliases and deployment identities in the durable state DB., Normalize a human project name into a safe alias/repository name.

### Community 12 - "project_service.py"
Cohesion: 0.29
Nodes (4): ProjectRouterAgent, Project-intent routing for new builds and edits to registered projects., Select a registered project or derive a new project name from user intent., Persistent project registry and per-project runtime construction.

### Community 13 - "github_service.py"
Cohesion: 0.20
Nodes (7): GitHubRepositoryCreation, Non-blocking wrapper around PyGithub branch and file operations., Create and initialize a repository under the configured GitHub owner., Create a new initialized repository without blocking the Telegram loop., List bounded repository metadata visible to the authenticated GitHub user., Discover existing repositories without changing GitHub state., Identity of a repository created for a new Arjun project.

### Community 14 - "ProjectRoute"
Cohesion: 0.18
Nodes (8): ProjectRoute, Any, BaseModel, field_validator, model_validator, LLM classification of the requested project target., Route before planning so repository and Vercel context are correct., Match a router key/name against freshly discovered GitHub repositories.

### Community 15 - "GeneratedFile"
Cohesion: 0.24
Nodes (8): CommitMessage, GeneratedFile, Any, BaseModel, field_validator, model_validator, A complete repository file emitted by the coder., Single-line commit summary for the generated files.

### Community 16 - "ReviewIssue"
Cohesion: 0.28
Nodes (6): Any, BaseModel, field_validator, model_validator, Actionable issue found during review., ReviewIssue

### Community 20 - "._normalize_action"
Cohesion: 0.40
Nodes (3): Any, field_validator, model_validator

## Knowledge Gaps
- **12 isolated node(s):** `start.sh script`, `graphify`, `Workflow: graphify`, `New projects versus existing projects`, `Memory and continuity` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Orchestrator` connect `Orchestrator` to `VercelService`, `telegram_handler.py`, `MemoryService`, `SecretStore`, `TelegramHandler`, `GitHubService`?**
  _High betweenness centrality (0.169) - this node is a cross-community bridge._
- **Why does `GitHubService` connect `GitHubService` to `Orchestrator`, `._get_repository`, `Settings`, `project_service.py`, `github_service.py`?**
  _High betweenness centrality (0.138) - this node is a cross-community bridge._
- **Why does `TelegramHandler` connect `TelegramHandler` to `Orchestrator`, `telegram_handler.py`, `Settings`, `MemoryService`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `Orchestrator` (e.g. with `CoderAgent` and `CoderOutput`) actually correct?**
  _`Orchestrator` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `BaseAgent` (e.g. with `Settings` and `CoderAgent`) actually correct?**
  _`BaseAgent` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `GitHubService` (e.g. with `Orchestrator` and `Settings`) actually correct?**
  _`GitHubService` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `VercelService` (e.g. with `Orchestrator` and `ProjectManager`) actually correct?**
  _`VercelService` has 4 INFERRED edges - model-reasoned connections that need verification._