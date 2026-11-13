# SpecWeave

SpecWeave is a self-hosted workspace for turning project ideas into implementation-ready specifications through guided AI conversations, research, requirements management, a traceability graph, BMAD-inspired workflows, human approvals, and document exports.

SpecWeave runs its own Python harness. The background worker uses the selected API-key or ChatGPT OAuth provider for model inference and controls Discovery, document checkpoints and review. PostgreSQL stores the conversation and execution state.

> **Verification:** The native harness image `7591f78` was deployed and tested in Brave against Strata. A fresh room-booking QA project completed three confirmed questions and handed off all four requested draft documents at revision 1. Automated tests cover transport, draft handoff, answer branch changes and recovery after a versioned file write. Local browser tests cover SSE replay, Stop, discussion isolation, scroll intent, docked questions, persistent history and reopening answers on desktop and mobile. Production validation of the follow-up history and composer changes remains pending.

## Features

- **Accounts and access control:** First-run administrator setup, login, session revocation, CSRF protection, Argon2id password hashing, and project owner/editor/viewer roles.
- **Project management:** New-project wizard, reusable presets, project CRUD, ownership transfer, and owner-only recovery or permanent deletion from project trash.
- **AI discussion:** Detached PostgreSQL-backed chat jobs that continue after tabs close, replayable SSE over EventSource (with reconnect and cursor), thinking when exposed by the model, tool-call activity, usage and time-to-first-token (TTFT) metrics, stop controls, persistent discussion history, and guided questions with recommended options.
- **Providers and models:** Native ChatGPT / Codex OAuth sign-in, OpenAI-compatible API keys/endpoints, model discovery, per-project model selection, and advanced limits, protocol, compatibility and sampling settings.
- **Planning and traceability:** Goals, requirements, acceptance criteria, decisions, coverage checks, a specification graph, and a roadmap.
- **Quality and deliverables:** BMAD-inspired draft documents, semantic review, background multi-role reviews, quality gates, human approval, Markdown document management, and exports.
- **Research and files:** SearXNG, HTTP research, Playwright/Chromium, and a project-scoped file manager with quotas and revision history.
- **Deployment:** Docker Compose with bundled or external PostgreSQL, plus GitHub Actions publishing the `-web` image to GHCR.


## Discovery, background chat and recovery

1. The browser submits a message or confirms a decision. Confirming an answer and enqueueing its continuation happen in one transaction.
2. The worker acquires the discussion lease and builds the next request from its saved answers, approved decisions, project profile and relevant notes. It validates the proposed question and allows only one open question in that discussion.
3. Once there are at least three confirmed answers and the structured evaluation identifies no material unknowns, the worker prepares each requested draft independently. It retries a truncated or invalid draft once with tighter instructions. Existing and approved documents are preserved.
4. A checkpoint records the exact draft before its atomic file write. If the worker dies before the database commit, the next claim can reconcile the matching file and register it once. The worker checks the Discovery inputs and verifies all requested files before marking the handoff ready for human review.

### Question history and changing an answer

Each question and its answer share one card in the conversation timeline. The card shows the selected answer and notes, expandable options and recommendations, and prior answer versions. **Quay lại trả lời** reopens an answered or deferred question. Later questions move to the previous branch, whose questions and answers remain available in history. Each replacement answer is recorded separately. Existing pre-upgrade answers are captured when first reopened.

The project must be idle before changing its branch. After confirming a replacement answer, Discovery evaluates the updated branch and refreshes affected unapproved drafts using versioned writes and revision checks. Approved documents retain their approval and require human review against the changed answers. The progress panel marks the changed branch until the new handoff.

The current question stays above the composer while messages scroll independently. Context and tradeoffs can be expanded in that panel. Chat follows incoming output until the user scrolls up. Sending a message, selecting an option or pressing the down arrow resumes following. Reading older question–answer cards retains the reading position.

Deploy web and worker together with Alembic revision `0014_planning_history`. This adds answer history to the existing questions. Production validation of these follow-up UI changes remains pending.

The browser follows persisted `chat_stream_events` over SSE using event IDs and `Last-Event-ID`. Closing the tab leaves the job running. Stop requests cancel the upstream stream. An expired Discovery lease can resume once. Ordinary chat and tool executions with an uncertain outcome are not automatically replayed. `SPECWEAVE_CHAT_CONCURRENCY` defaults to 4 and accepts 1 through 16.

## Project trash

The project owner has 30 days from the time a project enters trash to restore it. The owner can also choose **Xóa vĩnh viễn** in **Thùng rác dự án**. Permanent deletion removes the project's database records and its directory on `projects_data`, including drafts, uploads, versions and legacy session files. The system retains a purge audit event with the project ID and actor when available.

Alembic revision `0011_project_trash` backfills the deletion time from the most recent soft-delete audit event, then uses the last project update when the event is unavailable. Projects already past 30 days become eligible on upgrade. Deploy the updated `web` and `review-worker` together. The worker scans on startup and every five minutes, and retries interrupted purges. Review existing trash before upgrading if it contains a project you still need.

## Advanced custom model configuration

Open **Settings → Providers → provider details → model**. Set **Context window** and **Max output** from the provider's capabilities. SpecWeave reserves output space, prioritizes recent complete exchanges and budgets Discovery and review separately. A 256k context is `262144` tokens. Max output has its own limit.

The adapter supports streaming **Chat Completions** and **Responses**. For Strata's Qwen model, use Chat Completions with `{"maxTokensField":"max_tokens","thinkingFormat":"qwen-chat-template"}` if the endpoint accepts the chat-template thinking option. Chat Completions uses configured sampling parameters. Responses sends its supported temperature and top-p fields. A model setting change applies to the next run.

## ChatGPT / Codex OAuth

Open **Settings → Providers → Continue with ChatGPT**. Complete the authorization in the linked ChatGPT page and paste its complete loopback redirect URL into the provider form. On a remote Docker host, the redirect can show an unavailable localhost page; its address bar still contains the URL for the form. The connected provider supports model discovery and selection for native chat, Discovery and background reviews.

The native OAuth implementation follows OpenAI's public-client Authorization Code flow with PKCE and dynamic registration. It verifies state, nonce, ID-token signatures, issuer, audience and the ChatGPT-plan scope before activating the connection. Models stream through the Responses API. Provider-row locks serialize refresh-token rotation across web and worker. Credentials are encrypted with `ENCRYPTION_KEY` in the shared `openai_oauth_local` volume. `OPENAI_OAUTH_LOCAL_MODE` enables the feature and defaults to true in Compose. Each connection belongs to its owner.

OAuth model discovery reads the account catalog's `models` array, shows entries with `visibility: "list"` in server order, displays `display_name` and submits `slug` as the model ID. API-key providers use the `data[].id` contract. Regression fixtures cover both shapes, hidden entries, empty catalogs and malformed responses.

Automated HTTP fixtures verify authorization, invalid callbacks, signature rejection, token refresh and native inference. Interactive authorization with a real ChatGPT account and production deployment of this restoration are pending. [OpenAI sign-in documentation](https://developers.openai.com/siwc/token-sharing-open-source/sign-in), [models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

## Runtime architecture

```text
Browser → FastAPI (ACL, enqueue, SSE replay)
                       ↕
          PostgreSQL (answers, runs, events)
                       ↕
  Python worker → model transport → selected provider
       ↳ project tools and versioned draft storage
```

The worker chooses the actions it exposes by mode. Discovery uses structured responses and owns every write and handoff. Other modes expose a bounded set of project tools, with external research subject to project policy. Review uses the same model transport and a bounded JSON snapshot. API keys remain encrypted in PostgreSQL and are decrypted for the selected provider request. ChatGPT OAuth credentials reside in an encrypted local vault shared by web and worker.

The native harness loads its trusted, application-owned English instructions from `source/harness_instructions/SYSTEM.md` and `AGENTS.md`; user-facing questions and drafts remain Vietnamese unless requested otherwise. Discovery planning and draft writing have no directly callable tools; ordinary conversations receive the tools allowed for their selected mode. Project files and external content remain reference data, not system instructions. A valid Discovery draft plan can include unresolved questions: they are retained in the handoff and added to drafts for human review, rather than silently treated as confirmed facts. The three-answer minimum and pending-question gate still apply. Live model verification remains necessary after deployment; prompting alone cannot guarantee model behavior.

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
COOKIE_SECURE=true
```

**Generate a new Fernet key only for a fresh installation:**

```sh
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

**Do not rotate `ENCRYPTION_KEY` casually when upgrading.** Changing it without a proper key-migration procedure prevents decryption of previously stored provider credentials. Never commit `.env` or secrets to Git.

### Build locally

The same Python image runs FastAPI and the worker with different commands.

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

## Upgrading from the Pi runtime

1. Drain active work and back up PostgreSQL and `projects_data`. Preserve `.env`, `ENCRYPTION_KEY` and the Compose project name.
2. Update the Compose file and deploy matching images for `web` and `review-worker`. Migration `0013_native_harness` switches existing projects and removes private execution fields from unfinished legacy run payloads. It retains conversations, decisions and files.
3. ChatGPT / Codex OAuth is available through the native harness. Existing provider/model records remain selectable by their owner. Sign in once through **Settings → Providers → Continue with ChatGPT** to grant the native connection permission to use the ChatGPT plan. Both services mount the existing `openai_oauth_local` volume. Its earlier Pi credentials remain available for rollback.
4. To roll back this runtime migration, drain new runs, run `python -m alembic downgrade 0012_custom_model_config` with the new image, then restart the previous Compose and image with the original volumes. Retain the backup until validation finishes.

## Development and verification

```sh
python -m venv venv
# Windows PowerShell: .\venv\Scripts\Activate.ps1
# Linux/macOS: . venv/bin/activate
python -m pip install -r requirements.txt pytest pytest-asyncio
python -m pytest -q tests
python -m compileall -q source tests migrations
node tests/test_sse_protocol.js
```

For direct development, set `DATABASE_URL`, `DATA_ROOT`, `ENCRYPTION_KEY`, and `BOOTSTRAP_TOKEN`. Run `python source/app.py` and `python source/review_worker.py` with the same database and project storage. The Node command above only tests browser JavaScript.

Run `python scripts/e2e_harness_discussion.py` with Playwright Chromium to test the browser against the native worker and an isolated SSE provider. Set `CHROMIUM_EXECUTABLE` to use an existing Chromium browser.

## Documentation

- [`docs/RELEASE_v1.0.2_CONTINUOUS_DISCOVERY.vi.md`](docs/RELEASE_v1.0.2_CONTINUOUS_DISCOVERY.vi.md) — Continuous discovery, event flow, API, and validation notes (Vietnamese).
- [`docs/RELEASE_v1.0_PI_NATIVE.vi.md`](docs/RELEASE_v1.0_PI_NATIVE.vi.md) — Pi SDK architecture, migration, tool policy, and limitations (Vietnamese).
- [`docs/PRODUCT_ACCEPTANCE.v0.8.vi.md`](docs/PRODUCT_ACCEPTANCE.v0.8.vi.md) — Product acceptance checklist (Vietnamese).
- `docs/reference/` — Original master specification and UI/UX blueprint.
- `docs/` — Historical feature releases and implementation notes.

## Operational and security notes

- Publish the web port. The worker connects directly to PostgreSQL, project storage and the configured provider.
- Limit external research through the project policy and control network egress for browser fetches.
- Preserve PostgreSQL and persistent project volumes across image updates.
