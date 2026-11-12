# SpecWeave v1.0.3 — Pi-Native Background Conversations

SpecWeave is a self-hosted workspace for turning project ideas into implementation-ready specifications through guided AI conversations, research, requirements management, a traceability graph, BMAD-inspired workflows, human approvals, and document exports.

**Architecture:** All model inference and agent execution run exclusively through the **in-process Pi SDK** in the Node.js Pi Host inside the `web` container. FastAPI does not call LLMs directly, run a second agent, or maintain a fallback OpenAI-compatible agent. Local and remote OpenAI-compatible models remain available **through Pi's model runtime**. Pi owns agent sessions, context and compaction, thinking and tool events, and usage reporting. The frontend requires no Node.js build step.

> **Verification:** Local tests exercise the real Pi SDK with a local OpenAI-compatible test provider and the AI Discussion page in Edge. Docker builds and runtime, PostgreSQL deployment, and live model/OAuth integrations still need deployment verification.

## Features

- **Accounts and access control:** First-run administrator setup, login, session revocation, CSRF protection, Argon2id password hashing, and project owner/editor/viewer roles.
- **Project management:** New-project wizard, reusable presets, project CRUD, ownership transfer, and owner-only recovery or permanent deletion from project trash.
- **AI discussion:** Detached PostgreSQL-backed chat jobs that continue after tabs close, replayable SSE over EventSource (with reconnect and cursor), thinking when exposed by the model, tool-call activity, usage and time-to-first-token (TTFT) metrics, stop controls, persistent discussion history, and guided questions with recommended options.
- **Providers and models:** OpenAI-compatible API keys/endpoints, owner-scoped OpenAI OAuth, model discovery, and per-project model selection. Model execution is handled by Pi.
- **Planning and traceability:** Goals, requirements, acceptance criteria, decisions, coverage checks, a specification graph, and a roadmap.
- **Quality and deliverables:** BMAD-inspired draft documents, semantic review, background multi-role reviews, quality gates, human approval, Markdown document management, and exports.
- **Research and files:** SearXNG, HTTP research, Playwright/Chromium, and a project-scoped file manager with quotas and revision history.
- **Deployment:** Docker Compose with bundled or external PostgreSQL, plus GitHub Actions publishing the `-web` image to GHCR.


## Background chat and reconnect

Chat, decision answers and Pi thinking/tool/usage events are now **independent of the browser tab**. The web application creates a PostgreSQL-backed `agent_runs` job and immediately returns its ID. The existing **review-worker** service runs the Pi task, persists event frames in `chat_stream_events`, and the browser subscribes via an `EventSource` GET endpoint. Switching pages, disconnecting from the network or closing the tab only closes the observer connection; it **does not cancel the Pi run**.

The discussion page includes the active run ID and displays a Pi activity card immediately. Its subscriber replays persisted events from that run even if it finishes before the browser connects. Reconnecting uses SSE event IDs and `Last-Event-ID`. The **Stop** button requests cancellation and the subscriber displays the server's result.

Each discussion gets its own Pi SDK `AgentSession`, transcript and active tool capability. Different discussions can run concurrently. `SPECWEAVE_CHAT_CONCURRENCY` defaults to 4 per background worker and accepts values from 1 through 16. A worker or Pi Host restart can interrupt active inference. The interrupted job fails without replaying its prompt or tools.

The worker can make one additional Pi prompt in the same run when Discovery returns neither an interactive question nor a completed handoff. This uses the same session and the saved answer even if the browser has left. Pi can append short sections to drafts using the current document revision. Discovery checks for the requested drafts before registering a handoff for human review.

The `0010_detached_chat` Alembic migration creates the event log and lease fields. Back up PostgreSQL before deploying and keep all existing volumes. Tool call input/output JSON is pretty-printed in UTF-8 in the UI, including legacy escaped Unicode strings; the actual tool arguments are not changed. Full details: [`docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md`](docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md) (Vietnamese).

## Project trash

The project owner has 30 days from the time a project enters trash to restore it. The owner can also choose **Xóa vĩnh viễn** in **Thùng rác dự án**. Permanent deletion removes the project's database records and its directory on `projects_data`, including drafts, uploads, versions and Pi sessions. The system retains a purge audit event with the project ID and actor when available.

Alembic revision `0011_project_trash` backfills the deletion time from the most recent soft-delete audit event, then uses the last project update when the event is unavailable. Projects already past 30 days become eligible on upgrade. Deploy the updated `web` and `review-worker` together. The worker scans on startup and every five minutes, and retries interrupted purges. Review existing trash before upgrading if it contains a project you still need.

On the deployed Strata build tested on November 12, 2026, the third confirmed answer produced PRD and Architecture drafts. Two more document tool calls hit the provider's output limit, and the interview remained open. The fixes above are verified locally. Full handoff with Strata on the updated Docker deployment is pending.

## Architecture

```text
Browser (Jinja2 + JavaScript + SSE)
  ↕
FastAPI (authentication, authorization, PostgreSQL, documents,
         approvals, project-scoped tool gateway)
  ↕
Pi Agent Host (Node.js process inside web: createAgentSession,
               ModelRuntime, SessionManager)
  ↘ scoped tools → FastAPI
```

The Pi SDK runs without built-in shell/filesystem tools (`noTools: 'builtin'`). Only SpecWeave's project-scoped custom tools are exposed through the FastAPI gateway, protected by HMAC capabilities.

- **Project data:** `/data/projects/<project-uuid>`
- **Pi sessions:** `<project>/.system/pi-native/<discussion-id>/sessions`
- **Local OpenAI OAuth vault:** `/auth-local/.pi/agent/auth.json`

Provider API keys are supplied to the model runtime in memory; they are not written into Pi session files or `models.json`.

## Quick start: External PostgreSQL

Copy the configuration template:

```sh
cp .env.example .env
```

Set at least the following values in `.env`:

```ini
DATABASE_URL=postgresql+psycopg://your_user:your_password@your_db_host:5432/specweave
ENCRYPTION_KEY=YOUR_VALID_32_BYTE_FERNET_BASE64_KEY
BOOTSTRAP_TOKEN=YOUR_RANDOM_BOOTSTRAP_SECRET
PI_RUNNER_KEY=YOUR_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
COOKIE_SECURE=true
OPENAI_OAUTH_LOCAL_MODE=false
```

**Generate a new Fernet key only for a fresh installation:**

```sh
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

**Do not rotate `ENCRYPTION_KEY` casually when upgrading.** Changing it without a proper key-migration procedure prevents decryption of previously stored provider credentials. Never commit `.env` or secrets to Git.

### Build locally

The `web` image includes the Pi SDK and starts the Pi Host alongside FastAPI.

```sh
docker compose -f compose.external-db.yaml up -d --build
```

### Deploy prebuilt images from GHCR

After GitHub Actions has published the corresponding tag, configure:

```ini
SPECWEAVE_IMAGE_REPO=ghcr.io/your-user/your-repository
SPECWEAVE_IMAGE_TAG=v1.0.2
```

Set the repository name to lowercase and omit the `-web` suffix. The tag must actually exist in your GHCR packages. Then deploy:

```sh
docker compose -f compose.external-db.images.yaml pull
docker compose -f compose.external-db.images.yaml up -d --no-build
```

For **bundled PostgreSQL**, run `docker compose up -d --build`, or use `docker compose -f compose.images.yaml up -d` with prebuilt GHCR images.

Open `http://<server>:8080/first-run` to create the initial administrator account. If you expose SpecWeave through an HTTPS domain (for example, via Cloudflare Tunnel), use `COOKIE_SECURE=true`.

## Upgrading from v0.9.8 or v1.0.x

1. **Back up** PostgreSQL and the `projects_data` and `openai_oauth_local` volumes. Back up `postgres_data` too if you use bundled PostgreSQL.
2. Update the source and Compose file together, or use a `-web` image published from the same revision as the updated Compose file.
3. Preserve `.env`, `ENCRYPTION_KEY`, volumes, and existing project data. Keep `PI_RUNNER_KEY` strong and consistent across services.
4. Reuse the existing Compose project name so the `projects_data` and `openai_oauth_local` volumes are picked up. Deploy with `docker compose ... up -d --build --remove-orphans`, or pull the updated image and use `up -d --no-build --remove-orphans`. This retires the previous `pi-runner` container. The OAuth volume moves to `web` and keeps its existing data. Remove any obsolete Pi runner URL overrides from custom Compose files.
5. If upgrading from v0.9.8, Alembic revision `0009_pi_native` converts the old project `agent_backend` setting to `pi`. Existing discussion history is retained and used to seed new Pi sessions. Upgrading from v1.0.x does not require re-creating projects or clearing sessions.

Do **not** use `alembic stamp head` to bypass an unapplied migration. Do **not** delete the `openai_oauth_local` volume.

## Development without Python packaging

Keep code in `source/` and install dependencies at the repository root. Create a local Python environment and install the pinned Pi SDK with Node.js 22 or later.

```sh
python -m venv venv
# Windows PowerShell: .\venv\Scripts\Activate.ps1
# Linux/macOS: . venv/bin/activate
python -m pip install -r requirements.txt pytest pytest-asyncio
npm install --prefix . --no-save --no-package-lock --ignore-scripts @earendil-works/pi-coding-agent@1.5.2 @earendil-works/pi-ai@1.5.2 typebox
```

For a direct local run, set `DATABASE_URL`, `DATA_ROOT`, `ENCRYPTION_KEY`, `BOOTSTRAP_TOKEN`, and `PI_RUNNER_KEY` in the process environment. Point `PROJECTS_ROOT` to `DATA_ROOT/projects`, `PI_RUNNER_URL` to `http://127.0.0.1:8090`, and `PI_TOOL_URL` to `http://127.0.0.1:8080/api/internal/pi-tool`. Use writable directories outside project storage for `SPECWEAVE_PI_SAFE_RESOURCES_DIR` and `OPENAI_OAUTH_LOCAL_HOME`. Run each file in its own terminal with those settings.

```sh
node source/pi_host.mjs
python source/app.py
python source/review_worker.py
```

Exclude `venv/`, `node_modules/`, caches and runtime storage when scanning the source.

## Automated tests

```sh
python -m pytest -q tests
python -m compileall -q source tests migrations
node --test tests/test_openai_oauth_contract.mjs tests/test_pi_native_host.mjs
```

The Node contract tests stub the SDK boundary. Run `python scripts/e2e_pi_discussion.py` with local Pi dependencies and Playwright Chromium to verify the real SDK, detached worker, and discussion UI against a local provider fixture. Set `CHROMIUM_EXECUTABLE` to use an existing Chromium browser such as Edge.

## Continuous discovery in v1.0.2

The planning workflow is designed to continue within **one discussion and one Pi session**:

1. Pi evaluates the project context and asks a focused question with candidate answers and a recommendation.
2. The user selects, customizes, or defers an option in the chat interface.
3. Confirming an answer saves it and **queues the next Pi turn in the same request** via `/planning/questions/{qid}/answer-start`—no additional **Send** or **AI Suggest** click is required.
4. Pi analyzes the answer in the existing session, explains its implications, and asks the next unresolved question.
5. Once enough information has been collected, Pi can draft Markdown documents and call `finish_discovery` to generate any missing BMAD artifacts. All generated documents remain **drafts requiring human review**, never automatically approved.

The frontend also supports resuming a previously saved answer that was not yet processed by Pi. See [`docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md`](docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md) for the API behavior, edge cases, and test coverage.

## Documentation

- [`docs/RELEASE_v1.0.2_CONTINUOUS_DISCOVERY.vi.md`](docs/RELEASE_v1.0.2_CONTINUOUS_DISCOVERY.vi.md) — Continuous discovery, event flow, API, and validation notes (Vietnamese).
- [`docs/RELEASE_v1.0_PI_NATIVE.vi.md`](docs/RELEASE_v1.0_PI_NATIVE.vi.md) — Pi SDK architecture, migration, tool policy, and limitations (Vietnamese).
- [`docs/PRODUCT_ACCEPTANCE.v0.8.vi.md`](docs/PRODUCT_ACCEPTANCE.v0.8.vi.md) — Product acceptance checklist (Vietnamese).
- `docs/reference/` — Original master specification and UI/UX blueprint.
- `docs/` — Historical feature releases and implementation notes.

## Operational and security notes

- Publish only the FastAPI port. The worker reaches Pi over the Compose network and authenticates with `PI_RUNNER_KEY`. Run a single `web` container for a given session volume.
- OpenAI OAuth is optional, owner-scoped, and disabled by default. Enable `OPENAI_OAUTH_LOCAL_MODE` only in an explicitly supported, individual user-controlled deployment; follow the provider's applicable terms.
- Project tools are authorization-checked. Agent-generated decisions, requirements, and documents should be reviewed before approval or external handoff.
- Preserve PostgreSQL and persistent project/OAuth volumes across image updates.
- Passing automated tests does not establish production readiness; perform the deployment-specific checks documented above.
