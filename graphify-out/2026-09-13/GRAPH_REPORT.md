# Graph Report - ARJUN  (2026-09-13)

## Corpus Check
- 29 files · ~20,071 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 505 nodes · 995 edges · 26 communities (19 shown, 7 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 65 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f562ee16`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Orchestrator
- VercelError
- InteractionBroker
- Setup
- ._connect
- SecretStoreError
- ._get_repository
- TelegramHandler
- BaseAgent
- _HealthHandler
- ProjectRegistry
- orchestrator.py
- ProjectRoute
- ReviewIssue
- agents/__init__.py
- services/__init__.py
- utils/__init__.py
- CoderAgent
- rules/graphify.md
- workflows/graphify.md
- start.sh
- LLMProviderPool
- package.json
- main
- parse_json_response
- .validate_repository_name

## God Nodes (most connected - your core abstractions)
1. `BaseAgent` - 40 edges
2. `Orchestrator` - 38 edges
3. `GitHubService` - 29 edges
4. `VercelService` - 29 edges
5. `Settings` - 28 edges
6. `MemoryService` - 27 edges
7. `TelegramHandler` - 27 edges
8. `ProjectManager` - 26 edges
9. `SecretStore` - 24 edges
10. `CoderAgent` - 17 edges

## Surprising Connections (you probably didn't know these)
- `TelegramHandler` --uses--> `LLMAgentError`  [INFERRED]
  services/telegram_handler.py → agents/base.py
- `BaseAgent` --uses--> `Settings`  [INFERRED]
  agents/base.py → config/settings.py
- `ProjectManager` --uses--> `BaseAgent`  [INFERRED]
  services/project_service.py → agents/base.py
- `TelegramHandler` --uses--> `OrchestrationError`  [INFERRED]
  services/telegram_handler.py → agents/orchestrator.py
- `TelegramHandler` --uses--> `ChatResponse`  [INFERRED]
  services/telegram_handler.py → agents/orchestrator.py

## Import Cycles
- None detected.

## Communities (26 total, 7 thin omitted)

### Community 0 - "Orchestrator"
Cohesion: 0.07
Nodes (32): CoderOutput, Structured code changes and the single commit message., OrchestrationError, Orchestrator, AskUserCallback, RuntimeError, Run the actual workflow after the repository queue grants access., Raised when a task cannot safely reach GitHub. (+24 more)

### Community 1 - "VercelError"
Cohesion: 0.08
Nodes (22): EnvironmentSyncResult, Any, RuntimeError, Make an API request and turn provider errors into safe exceptions., Find the configured project or create/link it to the GitHub repository., Normalize a Vercel project response., Convert a GitHub repository name into a valid stable Vercel project name., Normalize a GitHub URL or full name to the Vercel owner/repository format. (+14 more)

### Community 2 - "InteractionBroker"
Cohesion: 0.10
Nodes (13): InteractionBroker, InteractionTimeout, PendingQuestion, Small per-user question broker for Telegram human-in-the-loop decisions., Raised when the user does not answer a blocking build question in time., One unanswered question and its private response future., Route the next authorized Telegram message back to the waiting build., Register a question before its Telegram prompt is sent. (+5 more)

### Community 3 - "Setup"
Cohesion: 0.15
Nodes (12): 1. Create credentials, 2. Configure locally, 3. Give Arjun deployment authority, 4. Run, Arjun — Autonomous Telegram Developer Bot, Free worker deployment and laptop-off operation, Memory and continuity, New projects versus existing projects (+4 more)

### Community 4 - "._connect"
Cohesion: 0.10
Nodes (12): _now(), Connection, Persist the terminal result without storing generated source code., Create a stable, secret-safe fingerprint for a recurring failure., Upsert one verified failure lesson, increasing its recurrence count., Return a sortable UTC timestamp., Store a concise fact only after it came from repository/build evidence., Return bounded evidence from prior tasks for future agent prompts. (+4 more)

### Community 5 - "SecretStoreError"
Cohesion: 0.13
Nodes (11): Fernet, Connection, RuntimeError, Read a key, preferring a runtime environment value over encrypted storage., Return only requested values; never expose the complete secret store., Encrypt and persist a validated allow-list of user-supplied values., Parse only requested KEY=value lines without logging their values., Raised when secrets cannot be safely persisted or decrypted. (+3 more)

### Community 6 - "._get_repository"
Cohesion: 0.08
Nodes (20): OrchestrationResult, GitHub delivery plus the optional Vercel deployment result., GitHubPromotionResult, GitHubWriteResult, Any, Return a bounded repository tree snapshot and optionally a Graphify…, Read only planned text files and cap each snapshot sent to the model., Reject absolute or parent-traversing repository paths. (+12 more)

### Community 7 - "TelegramHandler"
Cohesion: 0.14
Nodes (17): DEFAULT_TYPE, Message, Handle .txt file uploads — read the file and treat its content as a coding…, Run orchestration and edit one progress message through each phase., Authenticate users, report progress in-place, and route tasks., Send a blocking question and wait for the user's next authorized message., Route a message to a waiting task instead of accidentally starting a new task., Log framework-level errors without leaking secrets to Telegram. (+9 more)

### Community 8 - "BaseAgent"
Cohesion: 0.09
Nodes (21): BaseAgent, compact_model_schema(), LLMAgentError, Any, AsyncOpenAI, BaseException, BaseModel, RuntimeError (+13 more)

### Community 9 - "_HealthHandler"
Cohesion: 0.33
Nodes (4): BaseHTTPRequestHandler, _HealthHandler, Minimal JSON health endpoint for Render / UptimeRobot pings., Silence default stderr logging for health pings.

### Community 11 - "ProjectRegistry"
Cohesion: 0.17
Nodes (9): ProjectRecord, ProjectRegistry, Connection, Return all registered project identities., Insert or update a project mapping., Remember the Vercel identity created/found for a project., Persisted identity of one user project., Store project aliases and deployment identities in the durable state DB. (+1 more)

### Community 12 - "orchestrator.py"
Cohesion: 0.05
Nodes (57): Shared LLM client wrapper and structured-response primitives., Implementation/code-generation agent., ChatResponse, Workflow coordinator for planning, coding, review, and GitHub delivery., Raised when the user makes conversation instead of a coding task., PlannerAgent, Architecture and file-change planning agent., Turn a natural-language request into a bounded, implementable file plan. (+49 more)

### Community 13 - "ProjectRoute"
Cohesion: 0.11
Nodes (14): ProjectRoute, Any, BaseModel, field_validator, model_validator, LLM classification of the requested project target., Route before planning so repository and Vercel context are correct., GitHubRepositoryCreation (+6 more)

### Community 16 - "ReviewIssue"
Cohesion: 0.28
Nodes (6): Any, BaseModel, field_validator, model_validator, Actionable issue found during review., ReviewIssue

### Community 20 - "CoderAgent"
Cohesion: 0.07
Nodes (26): CoderAgent, CommitMessage, GeneratedFile, Any, BaseModel, field_validator, model_validator, Generate complete code files from a plan and optional review feedback. (+18 more)

### Community 25 - "LLMProviderPool"
Cohesion: 0.11
Nodes (14): LLMProviderPool, LLMTarget, AsyncOpenAI, BaseException, Retrieve or construct a cached AsyncOpenAI client for a target., Return candidate targets, ordered by task complexity and availability., Mark a target in cooldown to prevent repeated failures., Extract retry-after seconds from error text or header if present. (+6 more)

### Community 26 - "package.json"
Cohesion: 0.12
Nodes (16): author, bugs, url, description, homepage, keywords, license, main (+8 more)

### Community 27 - "main"
Cohesion: 0.50
Nodes (4): main(), Validate configuration and start long-polling., Background thread that pings the Render external URL to prevent sleep., _self_ping()

### Community 30 - "parse_json_response"
Cohesion: 0.33
Nodes (6): parse_json_response(), Any, Defensive extraction helpers for model responses., Remove a surrounding or embedded Markdown code fence, if present., Parse strict JSON or recover the first valid object/array from model prose., strip_markdown_fence()

## Knowledge Gaps
- **25 isolated node(s):** `name`, `version`, `description`, `main`, `test` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Orchestrator` connect `Orchestrator` to `VercelError`, `InteractionBroker`, `SecretStoreError`, `TelegramHandler`, `orchestrator.py`, `CoderAgent`?**
  _High betweenness centrality (0.141) - this node is a cross-community bridge._
- **Why does `TelegramHandler` connect `TelegramHandler` to `Orchestrator`, `BaseAgent`, `InteractionBroker`, `orchestrator.py`?**
  _High betweenness centrality (0.119) - this node is a cross-community bridge._
- **Why does `GitHubService` connect `orchestrator.py` to `Orchestrator`, `ProjectRoute`, `._get_repository`?**
  _High betweenness centrality (0.119) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `BaseAgent` (e.g. with `LLMProviderPool` and `TaskComplexity`) actually correct?**
  _`BaseAgent` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `Orchestrator` (e.g. with `CoderAgent` and `CoderOutput`) actually correct?**
  _`Orchestrator` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `GitHubService` (e.g. with `Orchestrator` and `Settings`) actually correct?**
  _`GitHubService` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `VercelService` (e.g. with `Orchestrator` and `ProjectManager`) actually correct?**
  _`VercelService` has 4 INFERRED edges - model-reasoned connections that need verification._