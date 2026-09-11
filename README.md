# Arjun — Autonomous Telegram Developer Bot

Arjun is a Telegram-based autonomous software delivery agent. Send a text or voice request such as “build a full-stack booking app” or “update the authentication flow in my existing app”; it routes the request to a new or registered project, creates a new GitHub repository when needed, plans the architecture, generates complete code, reviews it, creates its working branch, runs a remote Vercel build, promotes the verified commit to production, and returns the live URL.

## What it does

1. Authenticates the Telegram user against `TELEGRAM_ALLOWED_USERS`.
2. Converts text or an in-memory OGG voice note into one developer request.
3. Plans the repository changes, technology stack, and test strategy.
4. Generates complete frontend, backend, database, configuration, and test files when the request is full-stack.
5. Reviews the result and permits automatic correction loops.
6. Creates the isolated GitHub working branch automatically and refreshes branch/file SHAs before committing.
7. Finds or provisions the Vercel project automatically when the token has project-creation permission.
8. Synchronizes only explicitly allow-listed worker environment variables to Vercel.
9. Builds the working branch remotely on Vercel, reads build logs, and automatically repairs failed builds.
10. Merges the verified branch into `DEFAULT_BRANCH` and creates the production deployment.
11. Edits one Telegram message with architecture, technology, live progress, GitHub links, and the final website link. Voice requests also receive a spoken acknowledgement.
12. Serializes continuous commands, remembers verified task outcomes, and stores recurring reviewer/Vercel failures as durable repair lessons.
13. Detects required application environment variables, asks for missing values in Telegram, encrypts them at rest, and syncs them directly to Vercel.
14. Pauses for material technology choices; accepts an option, or compares the options automatically when you reply `DO`.
15. Maintains a durable project registry so each new project keeps its own GitHub repository and Vercel project, while edits stay inside the selected old project.

### New projects versus existing projects

Arjun routes every request before it reads files. A request such as “build a new expense tracker” creates a new initialized private GitHub repository under the owner in `GITHUB_REPO`, registers it, creates/links a Vercel project for that repository, commits the implementation, remote-tests it, and sends the new production URL. A request such as “update my expense tracker login” selects the registered `expense-tracker` project and commits/deploys into that same repository and Vercel project. Arjun also performs bounded read-only discovery of repositories accessible to the GitHub token, so an older repository that was not created by Arjun can be selected and registered. If an edit is still not unambiguous, Arjun asks which project to use instead of guessing.

`GITHUB_AUTO_CREATE_REPOSITORIES=true` and the GitHub token must have permission to create repositories. `VERCEL_AUTO_CREATE_PROJECT=true` and one-time Vercel/GitHub authorization are still required for automatic Vercel project linking. The registry is stored in `ARJUN_STATE_DB_PATH`, so the cloud worker must use persistent storage.

The bot does not execute generated code on the worker. Vercel is the isolated remote build/deployment validator; the reviewer is an AI quality gate. No agent can safely invent private credentials, so Arjun either reads explicitly configured worker secrets or requests missing application variables interactively through Telegram.

### Memory and continuity

Arjun stores task status, commit/deployment links, recent requests, verified project facts, and recurring failure fingerprints in `ARJUN_STATE_DB_PATH` (SQLite). It does not store generated source files or send the database to Gemini. The planner and coder receive a bounded memory summary on the next task, but current GitHub files and actual Vercel logs always take precedence; memory is a warning/continuity aid, not permission to invent code.

Commands sent while another build is running are queued per worker so two tasks cannot update the same GitHub branch simultaneously. For “make the previous page blue” style follow-ups, the recent task history supplies continuity and the live repository snapshot supplies the truth.

## Setup

### 1. Create credentials

- Telegram: message [@BotFather](https://t.me/BotFather), create a bot, and copy its token. Your numeric Telegram user ID can be found with a trusted ID bot or Telegram API utility.
- Free LLM Providers & Key Pools (Supports multiple free models and keys to scale rate limits):
  - **Google Gemini**: Get free keys from [Google AI Studio](https://aistudio.google.com/apikey) (15 RPM, 1M TPM per key). Set comma-separated `GEMINI_API_KEYS=key1,key2...`.
  - **Groq**: Get free keys from [Groq Console](https://console.groq.com/keys) (fast Llama 3.3 70B & Whisper audio). Set `GROQ_API_KEYS=gsk_1,gsk_2...`.
  - **OpenRouter**: Get free keys from [OpenRouter](https://openrouter.ai/keys) for free-tier models (`:free`). Set `OPENROUTER_API_KEYS=sk-or-1...`.
  - **Model Priority & Fallback**: Set `LLM_MODELS=gemini-2.0-flash,llama-3.3-70b-versatile,gemini-1.5-flash`.
- GitHub: create a personal access token with repository contents/write access for the target repository. A classic token needs the `repo` scope.

When multiple keys or models are provided, Arjun automatically rotates keys to distribute usage evenly, and immediately fails over to the next key/model if one encounters TPM/RPM rate limits. A safe connectivity check is:

```bash
python -c "import asyncio; from config.settings import Settings; from agents.base import BaseAgent; s=Settings.from_environment(); a=BaseAgent(s); print('LLM Output:', asyncio.run(a.generate_text('Reply OK', system_instruction='Connectivity check', attempts=1))[:20]); asyncio.run(a.close())"
```

If this reports authentication failure, check the keys in your `.env` or cloud worker environment.

`GITHUB_REPO` must remain in `owner/repository` form, for example `punithpgowda7-dev/ArjunAgent`. Arjun sends that exact slug to Vercel when creating a linked project. The Vercel GitHub App must be installed for the personal account or organization that owns the repository; a Vercel access token alone cannot grant Vercel access to a private GitHub repository.

### 2. Configure locally

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env  # Windows
# cp .env.example .env  # Linux/macOS
```

Fill in `.env`. `TELEGRAM_ALLOWED_USERS` is a comma-separated whitelist, for example `123456789,987654321`. Never commit `.env`.

### 3. Give Arjun deployment authority

Create a Vercel access token with permission to create projects and deployments. Set `VERCEL_PROJECT_ID` blank and optionally set `VERCEL_PROJECT_NAME`; Arjun will find or create a Vercel project for `GITHUB_REPO` through the Vercel API. The Vercel account must have authorized GitHub access to that repository once—OAuth authorization cannot safely be bypassed by a bot token. If the project already exists, set its ID and Arjun will use it.

Arjun creates an isolated working branch automatically. It preview-builds that branch, merges it into `DEFAULT_BRANCH` only after the preview is ready, then runs the production deployment. GitHub branch creation is performed through the Git reference API and does not require manual branch setup.

To sync application secrets, put their values in the worker's secret environment settings and list their names, for example:

```text
DATABASE_URL=...
NEXT_PUBLIC_API_URL=...
VERCEL_SYNC_ENV_KEYS=DATABASE_URL,NEXT_PUBLIC_API_URL
```

The values are sent directly from the worker to Vercel and are never included in Gemini prompts or GitHub files. After an environment-variable update, Arjun redeploys because Vercel applies changed variables to new deployments.

### Telegram credential requests and architecture decisions

For a request such as “add Gmail verification with Nodemailer”, the planner lists the required variables (for example `GMAIL_USER`, `GMAIL_APP_PASSWORD`, or OAuth variables). If they are missing, Arjun pauses and sends a Telegram message explaining each variable. Reply with one `KEY=value` per line. The bot validates that only requested uppercase keys are accepted, deletes the plaintext reply on a best-effort basis, encrypts the values with `ARJUN_SECRET_KEY`, and sends them only to Vercel's environment-variable API. It never puts the values in a Gemini prompt, generated file, commit, log, or Telegram progress message. Use a Gmail App Password or OAuth credentials rather than a normal Gmail password.

If the planner identifies a material choice such as PostgreSQL vs MongoDB, it sends labeled options. Reply `A`, `B`, or the option text to choose. Reply `DO` when you want Arjun to compare repository compatibility, security, maintenance, performance, testability, and free-tier cost and choose one. The chosen decision is remembered for future tasks in that repository.

Generate and set the encryption key once on the cloud worker:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the printed value as `ARJUN_SECRET_KEY`. If it is missing, Arjun refuses to accept credentials so it cannot accidentally store plaintext secrets.

### 4. Run

```bash
python main.py
```

The configured agent branch is created from `DEFAULT_BRANCH` if it does not exist. Each requested file is created or updated with the generated commit message. The final Telegram response contains the commit, branch, file links, and clickable production URL when Vercel succeeds.

## Free worker deployment and laptop-off operation

The included `Dockerfile` is suitable for a long-running worker. Once this worker is running in the cloud, the laptop can be completely off: Telegram delivers your phone command to the cloud worker, which calls Gemini/GitHub/Vercel and sends the progress and final link back. The laptop is only needed for the one-time deployment or later bot-code updates.

Set the same environment variables in the platform secret/environment settings and use:

```text
docker build -t arjun-agent .
docker run --env-file .env arjun-agent
```

- **Koyeb/Render:** create a worker from this GitHub repository, use Docker deployment, and add the environment variables. If the free worker sleeps or has ephemeral storage, it is not suitable for guaranteed 24/7 operation or durable memory without that provider’s persistent-disk option.
- **Oracle Cloud Always Free:** for the strict “$0 and laptop-off” goal, use an Always Free VM, install Docker, clone the repository, add `.env`, mount a persistent host directory at `/app/data`, then run the container under systemd or Docker restart policy. This keeps the SQLite lessons after a restart.

Example persistent Docker run on the VM:

```bash
docker run -d --name arjun --restart unless-stopped \
  --env-file .env \
  -v /opt/arjun-data:/app/data \
  arjun-agent:latest
```

Telegram long polling avoids a public webhook endpoint. Keep exactly one worker instance running per bot token unless you intentionally coordinate deployments.

## Security and operational notes

- Keep `TELEGRAM_ALLOWED_USERS` restricted and rotate tokens if they are exposed.
- Use a Vercel token with only the required project/team scope. The GitHub repository must be linked to the Vercel project for Git-source deployments.
- The GitHub token should be limited to the target repository where possible.
- Generated paths are checked against absolute and parent-traversal paths before writes.
- Gemini calls retry transient rate-limit/provider errors with exponential backoff and jitter.
- A failed review never reaches GitHub. A GitHub failure is reported as failed, not completed.
- `MAX_DEBUG_ATTEMPTS=2` controls how many failed Vercel builds receive automatic repair attempts. If credentials, billing limits, or an external service is the cause, the bot reports the failure rather than fabricating a fix.
- The free Gemini tier has quotas and rate limits; the bot cannot guarantee unlimited 24/7 throughput at $0.
- “Learning” is persistent operational memory and verified repair history, not unrestricted model training. No free API can guarantee zero hallucinations; the repository snapshot, exact file-plan gate, reviewer gate, secret scanner, remote build, smoke test, bounded repair loop, and queued writes reduce and expose them.
- Arjun cannot create real third-party credentials from nothing. Database schemas and application code can be generated, and allow-listed values can be copied from worker secrets to Vercel, but a database provider/API key still needs one-time user provisioning.
