# SpecWeave v1.0.0 — Pi-Native Planning Workspace

Ứng dụng self-hosted để xây dựng đặc tả dự án bằng các cuộc trao đổi có hướng dẫn với AI, nghiên cứu, requirements, spec graph, BMAD-inspired workflows, phê duyệt và xuất tài liệu.

**Thay đổi quan trọng:** toàn bộ model inference và agent execution chạy **duy nhất trong Pi SDK in-process** của container `pi-runner`. FastAPI không tự gọi LLM, không tự chạy agent thứ hai, không duy trì OpenAI-compatible fallback agent. Các provider local vẫn dùng được thông qua **Pi ModelRuntime**. Pi cung cấp session, context/compaction, thinking/tool events và usage. Frontend không yêu cầu Node.js để build.

> **Trạng thái phát hành:** code tích hợp và automated tests được cung cấp, nhưng chưa xác minh Pi package từ npm, Docker build/runtime, OAuth OpenAI thật hoặc model thật trong môi trường biên soạn này. Không mặc định kết luận production-ready. Xem `docs/RELEASE_v1.0_PI_NATIVE.vi.md`.

## Chức năng sản phẩm

- First-run Admin, login, session revocation, CSRF, Argon2id, quyền project owner/editor/viewer.
- Project wizard, preset, CRUD, trash/restore, chuyển chủ sở hữu.
- Discussion chat streaming: thinking (khi provider hỗ trợ), tool calls, usage/TTFT, SSE, stop, session history, guided questions có phương án được đề xuất.
- Model Manager: OpenAI-compatible API key/endpoint, OpenAI OAuth **riêng tư theo chủ tài khoản**, model list, model lựa chọn từng dự án.
- Planning: goals, requirements/criteria, quyết định, traceability graph, coverage và roadmap.
- BMAD-inspired docs, Semantic Review và multi-role review nền, quality gates, approval, Markdown draft và export.
- SearXNG, HTTP research, Playwright/Chromium, File Manager nhiều dự án có quota và versioning.
- GitHub Actions → GHCR image `-web` và `-pi` có cache; PostgreSQL trong Compose hoặc kết nối ngoài.

## Kiến trúc

```text
Browser (Jinja2 + JavaScript + SSE)
  ↕
FastAPI (auth, ACL, PostgreSQL, documents, approvals, project-scoped tool gateway)
  ↕
Pi Agent Host (Node.js riêng: createAgentSession, ModelRuntime, SessionManager)
  ↘ scoped tools → FastAPI
```

Pi SDK `noTools: 'builtin'` chặn tool shell/file hệ thống, chỉ custom tools của SpecWeave qua FastAPI có HMAC capability. Data của từng dự án ở `/data/projects/<uuid>`, Pi sessions ở `<project>/.system/pi-native/<discussion-id>/sessions`. OAuth vault riêng ở `/auth-local/.pi/agent/auth.json`. API keys của provider không ghi vào Pi session files hoặc `models.json` mà chuyển vào memory runtime.

## Chạy Compose: PostgreSQL ngoài

```sh
cp .env.example .env
```

Cấu hình tối thiểu:

```ini
DATABASE_URL=postgresql+psycopg://your_user:your_password@your_db_host:5432/specweave
ENCRYPTION_KEY=VALID_32_BYTE_FERNET_BASE64
BOOTSTRAP_TOKEN=YOUR_RANDOM_BOOTSTRAP_SECRET
PI_RUNNER_URL=http://pi-runner:8090
PI_RUNNER_KEY=YOUR_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
COOKIE_SECURE=true
OPENAI_OAUTH_LOCAL_MODE=false
```

Tạo Fernet key **mới** chỉ cho bản cài mới (không đổi key nếu đã lưu provider credentials):

```sh
python3 -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Build local (Pi service là bắt buộc):

```sh
docker compose -f compose.external-db.yaml up -d --build
```

Dùng GHCR image đã publish từ repository:

```ini
SPECWEAVE_IMAGE_REPO=ghcr.io/your-user/your-repository
SPECWEAVE_IMAGE_TAG=v1.0.0
```

```sh
docker compose -f compose.external-db.images.yaml pull
docker compose -f compose.external-db.images.yaml up -d --no-build
```

Không cần `--profile pi`: image Pi luôn được chạy. Với PostgreSQL tích hợp: `docker compose up -d --build`, hoặc `docker compose -f compose.images.yaml up -d` nếu dùng GHCR.

Khởi tạo tài khoản: `http://<server>:8080/first-run`. Với Cloudflare Tunnel dùng domain HTTPS và `COOKIE_SECURE=true`.

## Nâng cấp từ v0.9.8

1. **Backup** PostgreSQL và các volumes `projects_data`, `openai_oauth_local` (và `postgres_data` nếu DB tích hợp).
2. Thay code bằng v1.0.0; CI publish cả hai images từ **cùng commit/tag**.
3. Giữ `.env`, `ENCRYPTION_KEY`, volumes và dữ liệu cũ; cấu hình Pi strong key.
4. `docker compose ... pull`, rồi `docker compose ... up -d --no-build`.
5. Alembic `0009_pi_native` chỉ chuyển trường `agent_backend` cũ sang `pi`; không xóa lịch sử. Pi session bắt đầu từ history đã lưu trước đó và tiếp tục ở bộ nhớ/volume của Pi.

Không sử dụng `alembic stamp head` để bỏ qua bước migration. Không xóa `openai_oauth_local`.

## Phát triển không package hóa Python

Code ở `source/`, môi trường chạy ở `.venv/` và **không nằm trong source**:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python source/app.py
```

Pi Agent Host cần Node.js riêng với `@earendil-works/pi-coding-agent` và chạy `node source/pi_host.mjs` với config + project volume đúng. Không có `pip install -e .` và không có frontend Node.js build.

## Kiểm thử tự động

```sh
python -m pytest -q tests
python -m compileall -q source tests migrations
node --test tests/test_openai_oauth_contract.mjs tests/test_pi_native_host.mjs
```

`tests/test_pi_native_host.mjs` dùng Pi SDK mock để kiểm tra hợp đồng; **không phải** end-to-end với Pi thật. GitHub Actions còn build/preflight các SDK exports, còn anh tự nghiệm thu Docker, live model, OAuth và browser theo release notes.

## Tài liệu

- [`docs/RELEASE_v1.0_PI_NATIVE.vi.md`](docs/RELEASE_v1.0_PI_NATIVE.vi.md): migration, kiến trúc Pi SDK, tool policy, giới hạn và nghiệm thu.
- [`docs/PRODUCT_ACCEPTANCE.v0.8.vi.md`](docs/PRODUCT_ACCEPTANCE.v0.8.vi.md): checklist cũ còn hữu ích cho vận hành.
- `docs/reference/`: master spec và UI/UX blueprint gốc.
- `docs/`: lịch sử feature releases trước đây.
