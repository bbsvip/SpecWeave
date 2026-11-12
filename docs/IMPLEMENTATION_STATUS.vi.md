# SpecWeave v0.8.0 — Implementation status

**Phân loại:** Product beta / self-hosted release candidate. **Không khẳng định production-ready.**

| Tính năng | Bằng chứng trong source | Trạng thái |
|---|---|---|
| Login/admin/CSRF/sessions/throttling | `source/app.py`, `security.py`, `db.py`, tests | Đã code + backend tests |
| Project CRUD/wizard/presets/trash/team/ownership | app routes, `product.js`, `project_new.html`, `presets.html`, `project_settings.html` | Đã code + backend tests |
| Provider/model API key, fetch, model metadata | `app.py`, `providers.html`, `provider_detail.html` | Đã code + mock/HTTP tests; live endpoint chưa xác minh |
| AI Discussion & Guided Planning | `ai.py`, `planning.py`, `discussion.js` | Đã code + tests, live model chưa xác minh |
| Research SearXNG + Playwright | `research.py`, `integrations.html` | Code + một phần tests; browser/network chưa xác minh |
| Pi RPC custom tools | `pi_runner.py`, `pi_tools.ts` | Code + backend-capability tests; Pi CLI end-to-end chưa xác minh |
| Goals/reqs/decisions/phase gates/Spec Graph | `intelligence.py`, `planning_engine.py` | Đã code + tests |
| Documents/file manager/trash/versions/quota | `storage.py`, `app.py`, UI | Đã code + tests; crash recovery chưa chứng minh |
| Draft BMAD artifact generation | `artifact_builder.py`, `artifacts.html` | Đã code + tests; templates BMAD-inspired, không phải upstream BMAD đầy đủ |
| Human approvals/review/findings/trace/evidence | `planning_engine.py`, `quality.html` | Đã code + tests |
| Review worker async + per-role multi-model | `review_queue.py`, `review_worker.py`, migration `0005` | Đã code + mocked model tests, multi-provider live chưa xác minh |
| Export and provenance | `app.py` includes `planning/review-jobs.json` | Đã code + tests |
| Operations activity/audit, backup/restore scripts | `operations.html`, `activity.html`, `scripts/` | Code; actual backup/restore chưa xác minh |
| Appearance/local browser assets | `appearance.html`, `scripts/vendor_assets.py` | Code; assets chưa vendor trong gói vì môi trường không truy cập CDN |
| Docker/Chromium/LLM/server runtime | Compose, Dockerfile, E2E script | **Để người dùng tự nghiệm thu** |
| Full native provider OAuth | — | **Chưa triển khai** (provider-specific, không giả lập generic OAuth) |
| Upstream BMAD engine compatibility | — | **Chưa tương đương upstream**; có BMAD-inspired workflow và artifact templates |
| External security review/production load | — | **Chưa nghiệm thu** |

Để tránh hiểu nhầm: 49 backend tests thành công không xác nhận rằng mọi luồng UI/live integration đều hoạt động trên môi trường triển khai thật. Xem `PRODUCT_ACCEPTANCE.v0.8.vi.md`.


## v0.9.0 OpenAI-only OAuth

- Code: OpenAI-only Connect/Reconnect/Disconnect owner-admin UI; Pi login broker and local credential vault; provider/model routing for chat, Guided Planning, Semantic/BMAD Review; CSRF/ACL, owner transfer safeguards, real Pi catalog discovery and migration `0006_openai_oauth_owner`.
- Tests: backend mock/unit/integration tests pass; Python/JS syntax check performed. Status checks indicate **presence** of local credential, not a promise that OpenAI accepted a model call.
- Not implemented: OAuth for other providers, multi-user ChatGPT OAuth, OpenAI application authorization/partnership enrollment, external model/live OAuth verification. OAuth disabled by default; official OpenAI scope restrictions and local storage requirements apply.
- Operator owns: Docker/Pi runtime validation, browser callback, eligibility, token refresh, actual model calls and security review. See `docs/RELEASE_v0.9_OPENAI_OAUTH.vi.md`.
