# SpecWeave v0.9.3 — AI Project Planning Workspace

**Self-hosted project discovery, guided decisions, AI-assisted research, requirement planning, architecture, quality review, human approval, and implementation handoff.** The application is built with FastAPI, Jinja2, native JavaScript, PostgreSQL, optional Pi RPC and Playwright. No frontend Node.js build step or Python editable package installation.

> **Release classification: product beta / self-hosted release candidate.** Code and backend tests are supplied; deployment, Chromium, Docker, provider calls, Pi CLI integration, backup/restore drills and external security review must be performed by the operator. Passing SQLite tests is not equivalent to production readiness. The project has **BMAD-inspired workflows**, not a bit-for-bit replacement for BMAD Method upstream. OpenAI-only OAuth is implemented as an **opt-in, local, owner-controlled Pi integration**; all other providers remain API-key based. OpenAI authorization and eligibility are required. The OAuth flow has not been live-tested in this environment.

## Included product flows

| Area | User-facing behavior |
|---|---|
| Accounts | First-run admin, username/password login, Argon2id, DB sessions, CSRF, login throttling, session revocation, password management, admin account management |
| Projects | Library, 5-step wizard with custom multi-selects, personal presets, project settings, archive, trash/restore, owner transfer, owner/editor/viewer permissions |
| Guided planning | AI discussion, stage/role controls, suggested 2–4 choices with pros/cons and recommendation, user decision capture, memory proposals, research and project-scoped AI tools |
| Requirements | Goals, requirements/acceptance, decisions, phases, dependencies, change invalidation, review gates |
| Spec graph | Traceability nodes/edges, suggestions requiring human confirmation, coverage/readiness reports |
| Documents | Markdown edit/preview, revisions, approval, 10 deterministic BMAD-inspired draft artifacts, original/reference files and export |
| Research | SearXNG search, HTTP fetch and Playwright fallback, evidence assessments, browser proxy and source metadata |
| Quality | Rules, semantic AI review, multi-role async review (Analyst, Architect, QA, Synthesis), optional model per role, persisted steps/usage/status, job cancel/retry, findings and human decisions |
| File manager | Folders, batch upload, guarded text editing, file versions, move/rename, trash/restore/purge, PDF/image preview, project storage quotas |
| Operations | Admin dashboard/activity/audit, Alembic migrations, Compose for built-in or external PostgreSQL, backup/restore scripts for built-in Compose, CI backend checks, browser E2E script |
| Appearance | Light/dark/theme density preferences; optional pinned static assets downloaded in advance |

The **AI can propose**, but **only users approve**. Generated BMAD documents are *drafts*. Draft export is always possible for authorized users; reviewed export requires deterministic readiness and quality gates.

## Getting started with Docker Compose

1. Unzip this release into a clean directory. Do **not** copy virtual environments or untracked project data from an old source folder.
2. Keep existing PostgreSQL and `projects_data` volumes if upgrading. **Back up before upgrading**.
3. Configure `.env`:

```sh
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(36))"
```

Use independent random values for `ENCRYPTION_KEY` (Fernet), `BOOTSTRAP_TOKEN`, `POSTGRES_PASSWORD`, `PI_RUNNER_KEY` (if Pi is used). Never commit `.env`. A URL-safe PostgreSQL password avoids quoting problems in `DATABASE_URL`.

```sh
docker compose up -d --build
```

Open `http://localhost:8080/first-run`, create the initial administrator, and log in. Then configure **Providers & Models**, create your first project and assign its active model under Project Settings. Add SearXNG and/or browser proxy under Integrations when needed.

**External PostgreSQL:** Configure a PostgreSQL SQLAlchemy `DATABASE_URL` in `.env` and use:

```sh
docker compose -f compose.external-db.yaml up -d --build
```

The external database must already exist. The app requires a sufficiently privileged migration user at startup, or pre-applied Alembic migrations. Consult the actual Compose file before using Portainer and keep `.env` secrets out of Git.

**Optional Pi RPC runner:** Configure a long `PI_RUNNER_KEY` and `PI_RUNNER_URL=http://pi-runner:8090`, then:

```sh
docker compose --profile pi up -d --build
```

Pi runner is a separate JavaScript runtime; it is **not** a Node.js frontend build. The runner is configured with custom OpenAI-compatible model metadata and API key. Its built-in shell/file tools, extra extensions and MCP are disabled; all custom project tools pass through the backend's scoped/HMAC-protected capability endpoint. OpenAI-only OAuth can be enabled separately in an owner-controlled local runner; see `docs/RELEASE_v0.9_OPENAI_OAUTH.vi.md`. Other providers remain API-key based. Validate Pi CLI and model/provider compatibility on the target host.


## Fast deployment using prebuilt GHCR images (v0.9.3)

GitHub Actions now tests the project, builds two Docker images (`-web`, `-pi`) once on GitHub-hosted runners and publishes them to GHCR using layer caching. Both `web` and `review-worker` use the same `-web` image. **No local Docker build is needed when deploying these images.**

After the workflow has successfully published images from your GitHub repo, add this to `.env` (lowercase owner/repository):

```ini
SPECWEAVE_IMAGE_REPO=ghcr.io/<github-owner>/<github-repository>
SPECWEAVE_IMAGE_TAG=main
```

For **external PostgreSQL with Pi** (the deployment supported by `compose.external-db.yaml` previously):

```sh
docker compose -f compose.external-db.images.yaml --profile pi pull
docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build
```

Or `bash scripts/deploy-images.sh external pi`. This keeps the Compose project name, mounts and existing volumes unchanged. The local-build Compose files remain available. A version or `sha-` tag is recommended over `main` for controlled rollouts. See [`docs/GITHUB_ACTIONS_GHCR.v0.9.3.vi.md`](docs/GITHUB_ACTIONS_GHCR.v0.9.3.vi.md) for GitHub setup, GHCR login, Portainer, tags, rollback and limitations.

## Local developer setup (no Python packaging)

`source/` is the application, `.venv/` is an independent runtime environment. Never include virtual environments, caches or node_modules in source scans.

```sh
python -m venv .venv
# Linux / macOS:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

Configure `DATA_ROOT`, `DATABASE_URL` (SQLite is permitted **only** for local development/tests; PostgreSQL for production), `ENCRYPTION_KEY`, `BOOTSTRAP_TOKEN`, and `COOKIE_SECURE` via environment variables. Run app directly:

```sh
python source/app.py
# separate shell with the same environment:
python source/review_worker.py
```

If deploying with a reverse proxy, terminate TLS and set `COOKIE_SECURE=true`. Enforce egress network policy for web fetch/Chromium, network ingress restrictions for database and runner, and regular key rotation/backup procedures.

## Provider and model setup

- **Provider Manager:** Add `base_url` (typically ending `/v1`), optional API key, kind and enable flag. Keys are encrypted at rest by Fernet.
- **Model Manager:** Fetch available IDs from the provider, add multiple models, set context-window and reasoning metadata, individually enable/disable.
- **Project Settings:** Choose the active model, thinking level and agent backend (`compatible` or `pi`).
- **Quality & BMAD Review:** A distinct enabled model can be selected for Analyst, Architect, QA and Synthesis. Each model ID is persisted per review step and is exportable.
- **Authentication boundary:** Model-provider API-key management is separate from SpecWeave user login; OpenAI OAuth login is available as a separate local-only provider (disabled by default). No OAuth for other providers. This is not web app SSO.

## BMAD-inspired handoff process

1. **Discovery:** Project wizard, guided questions, verified project goals, research.
2. **Requirements:** PRD, requirements, acceptance criteria, decisions, unresolved issues.
3. **Solution Design:** Architecture, API/data contracts, threat/reliability concerns and traceability.
4. **UX Design:** UX and accessibility checks when the product needs UI (`ui_required=false` can disable the stage requirement).
5. **Spec Review:** Rules, AI critique, evidence, trace candidates, multi-role review and human approvals.
6. **Artifact generation:** Project → **BMAD Draft Generator** → select from 10 documents → preview → generate drafts → manually edit and approve in Document Studio.
7. **Export:** Draft or Reviewed Handoff ZIP; includes project metadata, requirements, planning, source assessments, trace graph, review jobs/role-model provenance and artifact files.

The generator only transforms persisted project facts and emits `[CẦN XÁC NHẬN]` for missing facts. It does not invent completed architecture details. Approved documents cannot be overwritten by generator. To regenerate an existing draft, explicitly select overwrite; prior text is snapshotted and revision advanced.

## Project storage and upload limits

`.env.example` supports:

```ini
MAX_UPLOAD_MB=40
MAX_UPLOAD_FILES=30
MAX_PROJECT_STORAGE_MB=2048
MAX_PROJECT_FILES=50000
```

Limits are server-enforced across uploads and text writes. Usage includes snapshots/trash, so cleaning old versions/trash may be necessary. The File Manager displays per-project usage. Multi-file upload validation is preflighted before promoting uploaded files, with staged temporary files cleaned on validation failure. Storage is rooted under a UUID project directory, and symbolic links, traversal and protected folders are denied.

## Offline interface assets (optional)

The default UI loads **version-pinned CDN dependencies**. On an Internet-connected build machine, prepare local assets before building Docker:

```sh
python scripts/vendor_assets.py
```

The script downloads a fixed allowlist of CSS/JavaScript/font dependencies into `source/static/vendor/` and creates a `.ready` marker only on completion. This action was **not performed for the delivered ZIP**; the release otherwise uses the CDN. Vendor files are fetched from the public network and should be independently audited before air-gapped production deployment.

## Database migrations and upgrade

Alembic revisions are additive, current head **`0006_openai_oauth_owner`**:

- `0001_baseline` — base project schema
- `0002_quality` — quality and workflow schema
- `0003_review_jobs` — persistent background review jobs and login throttling
- `0004_chat_coordination` — DB-scoped chat cancellation/coordination
- `0005_multi_model_review` — per-role model choices and per-step model ID provenance
- `0006_openai_oauth_owner` — nullable provider owner ID for the private OpenAI OAuth connection (no token columns)

**Do not run tests against production data.** Upgrading retains existing project volumes; never replace or delete the Docker volumes when changing the source. For bundled Compose use `sh scripts/backup-compose.sh ./backups` before upgrading; restore script exists but has not been verified against a live database in this environment. With external DB, use your PostgreSQL operator's backup/restore process.

## Testing and operator acceptance

```sh
PYTHONPATH=source python -m pytest -q tests/test_app.py
python -m compileall -q source tests migrations
find source/static/js -name '*.js' -print0 | xargs -0 -r -n1 node --check
python scripts/e2e_local.py
```

The last command requires Chromium and runs against a **temporary SQLite instance**, never a deployed project. The test runner deliberately omits live model calls and external SearXNG. For an actionable deployment checklist, see [`docs/PRODUCT_ACCEPTANCE.v0.8.vi.md`](docs/PRODUCT_ACCEPTANCE.v0.8.vi.md). The code checks are reported in the release notes; environment-specific runtime checks remain with the operator.

## Security boundaries and limitations

- Secure deployment requires HTTPS reverse proxy, secure cookies, network isolation, outbound egress restrictions and trusted persistent storage.
- AI-proposed findings, relations, questions and drafts are **not automatically approved**.
- Project content is scoped by membership; ownership transfer requires confirming the project name.
- Document snapshots/versioning are not substitutes for atomic DB+filesystem transactions across process crashes; keep backups.
- SSRF-resistant application URL checks are a defense-in-depth measure, **not** a substitute for egress firewall rules or Chromium sandbox policies.
- Not part of this release: OAuth for non-OpenAI providers, bit-for-bit upstream BMAD workflow execution, verified live Pi tool parity/provider integrations, production penetration tests, and deployment-time browser/Docker/backup restore tests.

## Documentation

- `docs/RELEASE_v0.9_OPENAI_OAUTH.vi.md` — OpenAI-only OAuth design, setup, API, privacy/terms considerations and acceptance checklist
- `docs/RELEASE_v0.8.vi.md` — previous product features
- `docs/IMPLEMENTATION_STATUS.vi.md` — feature and evidence matrix
- `docs/PRODUCT_ACCEPTANCE.v0.8.vi.md` — host acceptance / security / load checklist
- `docs/reference/PROJECT_ARCHITECT_MASTER_SPEC.vi.md` — original full project design
- `docs/reference/UI_UX_SCREEN_BLUEPRINT.vi.md` — screen blueprint
- `docs/PLANNING_INTELLIGENCE.v0.3.vi.md`, `docs/PLANNING_QUALITY.v0.4.vi.md`, `docs/RELEASE_v0.6.vi.md`, `docs/RELEASE_v0.7.vi.md` — historical technical decisions

## References

- BMAD: https://github.com/bmad-code-org/BMAD-METHOD
- Pi RPC: https://pi.dev/docs/latest/rpc
- Pi CLI: https://pi.dev/docs/latest/cli
- SearXNG: https://docs.searxng.org/dev/search_api.html
- Playwright: https://playwright.dev/python/docs/docker
- Impeccable: https://github.com/pbakaus/impeccable


## OpenAI / ChatGPT OAuth (opt-in; only OpenAI)

The OpenAI OAuth option uses Pi's supported sign-in mechanism, with credentials stored **locally** in the Pi runner's private `auth.json` (0600) rather than in PostgreSQL. This release does not add OAuth to GitHub, Google, Anthropic, or any other provider, and does not alter their API-key flow.

**Eligibility warning:** OpenAI's Sign in with ChatGPT authorization/terms may limit which third-party apps and accounts are allowed. This source code does not grant OAuth permission. Only enable with an eligible, authorized OpenAI app/account on a personal runtime you control. Do not use on a shared/managed server or share the resulting ChatGPT subscription with other users.

After backup, set these in `.env`:

```ini
OPENAI_OAUTH_LOCAL_MODE=true
PI_RUNNER_URL=http://pi-runner:8090
PI_RUNNER_KEY=GENERATE_A_STRONG_RANDOM_SECRET_AT_LEAST_32_CHARS
```

Start the optional runner with `docker compose --profile pi up -d --build`, then open **Providers & Models → OpenAI · Đăng nhập bằng ChatGPT → Connect** as the owner/admin. Follow browser login (paste the redirect URL/code only if Pi prompts). On success fetch model IDs from Pi and select one in Project Settings. Guided Planning, AI review and chat use the same local OAuth runner with owner checks.

For `compose.external-db.yaml`, use `docker compose -f compose.external-db.yaml --profile pi up -d --build`. Disconnect removes **local** credentials only (not remote provider authorization). See [`docs/RELEASE_v0.9_OPENAI_OAUTH.vi.md`](docs/RELEASE_v0.9_OPENAI_OAUTH.vi.md) for full setup, security and acceptance details.

## v0.9.2 — Fresh PostgreSQL migration fix

Fix an Alembic initialization error on empty PostgreSQL databases (`relation "spec_reviews" does not exist` while creating `review_jobs`). Migration `0001_baseline` now creates only baseline tables; `0002_quality` creates `spec_reviews`, and `0003_review_jobs` creates `review_jobs` after its referenced table exists. No changes to application data or provider/OAuth configuration. See `docs/MIGRATION_FIX.v0.9.2.vi.md`.


## v0.9.4 — OAuth diagnostics, Fernet startup validation, UI fixes

- `ENCRYPTION_KEY` must be a **32-byte random Fernet key encoded with URL-safe Base64**. Generate locally (do not share or commit it):

  ```bash
  python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
  ```

- The app now validates `ENCRYPTION_KEY` during startup (and the container entrypoint validates before Alembic migration) instead of allowing a later HTTP 500 on provider creation. **Never rotate an existing encryption key without a secret-re-encryption procedure.**
- OpenAI OAuth UI now exposes safe diagnostic codes (`OAUTH_MODULE_ERROR`, `OAUTH_NETWORK`, `OAUTH_VAULT_PERMISSION`, `OAUTH_REJECTED` etc.). On a terminal login, `/answer` returns HTTP 409 with a code instead of a misleading 404. Credential values/authorization callback URLs are not logged. OAuth success is still subject to supported Pi version and a real account on the deployment host.
- On the **Thùng rác dự án** page, sidebar active state now highlights Thùng rác instead of Tất cả dự án. Project profile nested data and admin audit metadata show readable pretty JSON. The File Manager automatically formats `.json` in its editor (in-memory only, until Save), plus a Format JSON button; presets also have a formatter.
- **Cloudflare Tunnel + `COOKIE_SECURE=true`** remains supported when using HTTPS. This setting is unrelated to Fernet/OAuth subprocess errors.

If running images from GHCR, push this source to GitHub to publish a new image, then set `SPECWEAVE_IMAGE_TAG` to the published tag and execute `docker compose -f compose.external-db.images.yaml --profile pi pull && docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build`. If using local build, run `docker compose -f compose.external-db.yaml --profile pi up -d --build`. Preserve `.env` and existing volumes.
