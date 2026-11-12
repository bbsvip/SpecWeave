# SpecWeave v1.0.3 — Pi-Native Background Conversations

SpecWeave is a self-hosted workspace for turning project ideas into implementation-ready specifications through guided AI conversations, research, requirements management, a traceability graph, BMAD-inspired workflows, human approvals, and document exports.

**Architecture:** All model inference and agent execution run exclusively through the **in-process Pi SDK** inside the `pi-runner` service. FastAPI does not call LLMs directly, run a second agent, or maintain a fallback OpenAI-compatible agent. Local and remote OpenAI-compatible models remain available **through Pi's model runtime**. Pi owns agent sessions, context and compaction, thinking and tool events, and usage reporting. The frontend requires no Node.js build step.

> **Release status:** The repository includes integration code and automated tests, but the Pi package installed from npm, actual Docker builds and runtime behavior, live OpenAI OAuth, and live model integrations have not been verified in the development environment used to prepare this release. Do not treat this release as production-validated. See [`docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md`](docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md) and [`docs/RELEASE_v1.0_PI_NATIVE.vi.md`](docs/RELEASE_v1.0_PI_NATIVE.vi.md) for the implementation details and validation checklist. These two documents are currently in Vietnamese.

## Features

- **Accounts and access control:** First-run administrator setup, login, session revocation, CSRF protection, Argon2id password hashing, and project owner/editor/viewer roles.
- **Project management:** New-project wizard, reusable presets, project CRUD, trash and recovery, and ownership transfer.
- **AI discussion:** Detached PostgreSQL-backed chat jobs that continue after tabs close, replayable SSE over EventSource (with reconnect and cursor), thinking when exposed by the model, tool-call activity, usage and time-to-first-token (TTFT) metrics, stop controls, persistent discussion history, and guided questions with recommended options.
- **Providers and models:** OpenAI-compatible API keys/endpoints, owner-scoped OpenAI OAuth, model discovery, and per-project model selection. Model execution is handled by Pi.
- **Planning and traceability:** Goals, requirements, acceptance criteria, decisions, coverage checks, a specification graph, and a roadmap.
- **Quality and deliverables:** BMAD-inspired draft documents, semantic review, background multi-role reviews, quality gates, human approval, Markdown document management, and exports.
- **Research and files:** SearXNG, HTTP research, Playwright/Chromium, and a project-scoped file manager with quotas and revision history.
- **Deployment:** Docker Compose with bundled or external PostgreSQL, plus GitHub Actions publishing cached `-web` and `-pi` images to GHCR.


## Background chat and reconnect (v1.0.3)

Chat, decision answers and Pi thinking/tool/usage events are now **independent of the browser tab**. The web application creates a PostgreSQL-backed `agent_runs` job and immediately returns its ID. The existing **review-worker** service runs the Pi task, persists event frames in `chat_stream_events`, and the browser subscribes via an `EventSource` GET endpoint. Switching pages, disconnecting from the network or closing the tab only closes the observer connection; it **does not cancel the Pi run**.

When returning to the same discussion, the UI discovers any active run and replays its stored events using SSE event IDs and `Last-Event-ID`. The explicit **Stop** button is the only normal action that sends cancellation to the agent. Do not disable the `review-worker` container: it now processes both AI review and chat jobs. A worker or Pi Host restart during an active inference can still interrupt the task; for safety, an interrupted run is marked failed rather than silently re-executed. This is intentional to avoid duplicate tool side effects.

The `0010_detached_chat` Alembic migration creates the event log and lease fields. Back up PostgreSQL before deploying and keep all existing volumes. Tool call input/output JSON is pretty-printed in UTF-8 in the UI, including legacy escaped Unicode strings; the actual tool arguments are not changed. Full details: [`docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md`](docs/RELEASE_v1.0.3_DETACHED_PI_CHAT.vi.md) (Vietnamese).

## Architecture

```text
Browser (Jinja2 + JavaScript + SSE)
  ↕
FastAPI (authentication, authorization, PostgreSQL, documents,
         approvals, project-scoped tool gateway)
  ↕
Pi Agent Host (separate Node.js service: createAgentSession,
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
PI_RUNNER_URL=http://pi-runner:8090
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

Pi is mandatory; no `--profile pi` flag is needed:

```sh
docker compose -f compose.external-db.yaml up -d --build
```

### Deploy prebuilt images from GHCR

After GitHub Actions has published the corresponding tag, configure:

```ini
SPECWEAVE_IMAGE_REPO=ghcr.io/your-user/your-repository
SPECWEAVE_IMAGE_TAG=v1.0.2
```

Set the repository name to lowercase and omit the `-web` / `-pi` suffix. The tag must actually exist in your GHCR packages. Then deploy:

```sh
docker compose -f compose.external-db.images.yaml pull
docker compose -f compose.external-db.images.yaml up -d --no-build
```

For **bundled PostgreSQL**, run `docker compose up -d --build`, or use `docker compose -f compose.images.yaml up -d` with prebuilt GHCR images.

Open `http://<server>:8080/first-run` to create the initial administrator account. If you expose SpecWeave through an HTTPS domain (for example, via Cloudflare Tunnel), use `COOKIE_SECURE=true`.

## Upgrading from v0.9.8 or v1.0.x

1. **Back up** PostgreSQL and the `projects_data` and `openai_oauth_local` volumes. Back up `postgres_data` too if you use bundled PostgreSQL.
2. Update the source to v1.0.2, or publish the `-web` and `-pi` images from **the same commit/tag**.
3. Preserve `.env`, `ENCRYPTION_KEY`, volumes, and existing project data. Keep `PI_RUNNER_KEY` strong and consistent across services.
4. With GHCR images, run `docker compose ... pull`, followed by `docker compose ... up -d --no-build` using the appropriate Compose file.
5. If upgrading from v0.9.8, Alembic revision `0009_pi_native` converts the old project `agent_backend` setting to `pi`. Existing discussion history is retained and used to seed new Pi sessions. Upgrading from v1.0.x does not require re-creating projects or clearing sessions.

Do **not** use `alembic stamp head` to bypass an unapplied migration. Do **not** delete the `openai_oauth_local` volume.

## Development without Python packaging

Keep application code inside `source/` and the virtual environment outside it, in `.venv/`. Run Python files directly; no editable package installation (`pip install -e .`) is required.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python source/app.py
```

The Pi Agent Host runs in a separate Node.js runtime with `@earendil-works/pi-coding-agent`. It can be started with `node source/pi_host.mjs` when its runtime configuration and project storage are correctly provided. The frontend does **not** require Node.js or a bundling/build pipeline.

When scanning or indexing the repository, exclude `.venv/`, `venv/`, `node_modules/`, caches, and other generated runtime directories.

## Automated tests

```sh
python -m pytest -q tests
python -m compileall -q source tests migrations
node --test tests/test_openai_oauth_contract.mjs tests/test_pi_native_host.mjs
```

The Pi Native Host contract tests use a **mock Pi SDK**. They do not constitute an end-to-end test of a real Pi installation or provider. GitHub Actions also performs SDK import/export checks during the image build. Validate Docker, live model connectivity, OpenAI OAuth, and the browser experience in your deployment environment.

## Continuous discovery in v1.0.2

The planning workflow is designed to continue within **one discussion and one Pi session**:

1. Pi evaluates the project context and asks a focused question with candidate answers and a recommendation.
2. The user selects, customizes, or defers an option in the chat interface.
3. Confirming an answer saves it and **streams the next Pi turn within the same request** via `/planning/questions/{qid}/answer-stream`—no additional **Send** or **AI Suggest** click is required.
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

- Keep the Pi runner reachable only through your trusted internal network; do not expose its management API publicly.
- OpenAI OAuth is optional, owner-scoped, and disabled by default. Enable `OPENAI_OAUTH_LOCAL_MODE` only in an explicitly supported, individual user-controlled deployment; follow the provider's applicable terms.
- Project tools are authorization-checked. Agent-generated decisions, requirements, and documents should be reviewed before approval or external handoff.
- Preserve PostgreSQL and persistent project/OAuth volumes across image updates.
- Passing automated tests does not establish production readiness; perform the deployment-specific checks documented above.
