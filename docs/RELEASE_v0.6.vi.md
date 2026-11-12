# SpecWeave v0.6.0 — Durable Review, Scoped Pi Tools & Recovery

> 11/11/2026 · Bản alpha để nghiệm thu trên máy triển khai. Không phải chứng nhận production readiness.

## Tóm tắt

Bản này tiếp tục triển khai những thiếu sót của v0.5 bằng các tính năng có API, giao diện, database migration, worker, kiểm thử tích hợp và tài liệu. Trọng tâm là không giữ HTTP request xuyên suốt quá trình model review, không mất tiến độ review khi trình duyệt đóng, mở custom tools an toàn cho Pi, phục hồi file đã xóa, sao lưu/khôi phục và rate limiting cho login.

## 1. Kiến trúc Review Job

### API

- `POST /api/projects/{pid}/quality/jobs` với `{"output_budget":12000}`: tạo review, kiểm tra CSRF, editor/owner, dự án có model đang hoạt động, chỉ một job active mỗi dự án.
- `GET /api/projects/{pid}/quality/jobs`: danh sách job và các bước đã chạy, viewer trở lên.
- `GET /api/projects/{pid}/quality/jobs/{job_id}`: chi tiết job, trạng thái và token output.
- `POST /api/projects/{pid}/quality/jobs/{job_id}/cancel`: gửi yêu cầu hủy, không làm hỏng dữ liệu review trước đó.
- `POST /api/projects/{pid}/quality/jobs/{job_id}/retry`: retry job lỗi theo phân quyền, giữ bước đã hoàn thành, không ghi đè kết quả đã duyệt.

Tất cả job và step gắn project ID, lưu trong PostgreSQL. Worker riêng `python source/review_worker.py` dùng `SELECT ... FOR UPDATE SKIP LOCKED` trên PostgreSQL để lease công việc, commit trước khi gọi HTTP model. Vòng phản biện: **Analyst → Architect → QA → Synthesis**, với context từ các bước trước. Mỗi bước lưu summary, findings, output token usage, nếu provider không trả usage thì đánh dấu ước tính. Không giữ kết nối SSE với trình duyệt; UI poll tiến độ.

**Chống kết quả cũ:** fingerprint snapshot đặc tả được so sánh khi chạy và trước khi lưu; nếu thay đổi, job chuyển `stale`, không tự áp finding lên spec mới. AI findings vẫn là `proposed`, chỉ người dùng có quyền mới xác nhận.

**Giới hạn thực tế:** output budget không tính input/context token; số tiền không được dự toán. Cancel trong lượt HTTP hiện tại có thể chờ lượt gọi đó kết thúc; không hủy ngay lập tức request provider. Không có distributed live chat SSE lock, dù background review worker có lease. Việc sửa spec trong khi đang phân tích có thể tốn token trước khi phát hiện stale.

## 2. Pi RPC — chỉ custom tool an toàn

- `source/pi_runner.py` dùng `--no-builtin-tools`, `--no-extensions`, `--extension /runner/pi_tools.ts`, explicit `--tools` allowlist, `--no-mcp`, `--no-skills`, `--no-context-files` và `--no-approve`.
- `source/pi_tools.ts` đăng ký `search_web`, `fetch_url`, `list_project_files`, `read_project_file`, `write_document`, `propose_decision`, `propose_requirement`, `propose_memory`, `propose_planning_question`.
- Tools gọi API nội bộ `POST /api/internal/pi-tool`, mang `X-Specweave-Run` và `X-Specweave-Token`. Token HMAC-SHA256 được tạo từ `PI_RUNNER_KEY`, run ID, project ID, discussion ID và thời điểm bắt đầu. API lấy project từ bản ghi AgentRun, KHÔNG nhận project ID từ model. Chỉ chấp nhận run đang active dưới 30 phút, kiểm tra allowlist và args, có giới hạn kích thước payload, dùng lại policy project file, draft-only và SSRF của compatible backend.
- `PI_TOOL_URL` mặc định `http://web:8080/api/internal/pi-tool` trong Compose; nếu runner khác mạng, cấu hình đường dẫn phù hợp và bảo vệ mạng nội bộ. Không public endpoint này cùng PI_RUNNER_KEY.
- Home config Pi tách theo từng discussion; session ID cũng tách. Pi không được tự chạy shell/bash hoặc đọc file ngoài tool allowlist.

**Đã kiểm thử:** logic API với HMAC, trạng thái run, allowlist, project isolation, draft proposal. TypeScript transpile không lỗi cú pháp. **Chưa chạy được Pi CLI + extension + model thật trên Docker**: phải nghiệm thu thực tế trước khi đánh dấu tool parity đã xác minh end-to-end. Pi là runtime Node.js duy nhất, không có frontend build bằng Node.

Nguồn CLI/extension: https://pi.dev/docs/latest/cli, https://pi.dev/docs/latest/extensions, https://pi.dev/docs/latest/rpc

## 3. File Manager & Login

- Thùng rác có UI/API liệt kê file/thư mục, phục hồi vào đường dẫn cũ/đường dẫn khác, xóa vĩnh viễn chỉ owner.
- Di chuyển thư mục chứa tài liệu sẽ cập nhật `Document.path` theo đường dẫn mới và đánh dấu tài liệu cần duyệt lại; khôi phục thư mục có tài liệu tạo lại `Document` ở dạng draft. Không tự cấp lại approval cũ.
- Preview trực tiếp PDF và một số định dạng raster ảnh (không preview SVG/HTML nguy hiểm).
- Login throttling dùng bảng `login_attempts`, HMAC-hash username/IP thay vì lưu plaintext, giúp phối hợp nhiều process ở mức dữ liệu. Chưa có edge rate limiting ở reverse proxy, cần thêm khi public.

## 4. Migrations và cấu hình

Migration chain: `0001_baseline` → `0002_quality` → **`0003_review_jobs`**. V0.6 tạo `review_jobs`, `review_job_steps`, `login_attempts`. Alembic chạy trong `entrypoint.sh` của web. `review-worker` đợi web chạy trước; không nên chạy nhiều process web khi SSE locks vẫn trong bộ nhớ.

**Cài mới:**

```sh
cp .env.example .env
# tạo ENCRYPTION_KEY, BOOTSTRAP_TOKEN, POSTGRES_PASSWORD và PI_RUNNER_KEY >=32 chars nếu dùng Pi
docker compose up -d --build
# thêm Pi nếu cần
docker compose --profile pi up -d --build
```

**Nâng cấp từ v0.5:**

```sh
sh scripts/backup-compose.sh ./backups
# thay source bằng v0.6, GIỮ nguyên .env và named volumes
docker compose up -d --build
# kiểm tra
curl -fsS http://localhost:8080/health
```

**Phục hồi** (chỉ PostgreSQL tích hợp, ghi đè DB và project volume):

```sh
SPECWEAVE_RESTORE_CONFIRM=RESTORE_SPECWEAVE sh scripts/restore-compose.sh ./backups/specweave-YYYYMMDDTHHMMSSZ.sha256
```

Script khôi phục kiểm tra SHA256, tên entry archive, từ chối links, dừng writers trước khi ghi đè. **Không được chạy trên môi trường production nếu chưa thử restore trên môi trường staging và xác nhận dữ liệu backup.** PostgreSQL ngoài không được script này hỗ trợ; dùng `pg_dump`/`pg_restore` và snapshot volume riêng.

## 5. Bài kiểm thử và mức độ xác minh

- `PYTHONPATH=source pytest -q tests/test_app.py`: **37/37 pass** với DB SQLite và project volume tạm. Có kiểm thử migration 0003 giữ dữ liệu cũ, queue/cancel/retry/stale, trash/document path, login throttle, Pi HMAC allowlist và phạm vi dự án.
- `python -m compileall -q source`: pass.
- `node --check source/static/js/*.js`: pass.
- TypeScript `source/pi_tools.ts`: kiểm tra transpile cú pháp bằng TypeScript compiler, chưa kiểm tra type resolution với Pi binary.
- `python -m alembic -c alembic.ini heads`: `0003_review_jobs`.
- `python scripts/e2e_local.py`: **CHƯA PASS** vì Chromium trong môi trường tạo source trả `net::ERR_BLOCKED_BY_ADMINISTRATOR` khi truy cập `127.0.0.1`. Script để anh chạy lại trên host có Chromium và localhost không bị chặn.
- **Chưa thực thi thực tế:** Docker build/up, PostgreSQL Docker migration, Pi CLI, provider/model thật, SearXNG live, Playwright visual screenshot, backup/restore và tải đồng thời nhiều người dùng.

## 6. Checklist nghiệm thu trên host đích

1. Đặt secrets và backup dữ liệu cũ, build `web`, `db`, `review-worker`, và tùy chọn `pi-runner`.
2. Xác nhận `/health` và `alembic_version=0003_review_jobs` trong PostgreSQL.
3. Login admin, tạo project, chọn provider/model, chạy Quality background job; đóng tab rồi mở lại để kiểm tra tiến độ tiếp tục.
4. Thử cancel/retry và sửa requirement khi review đang chạy để xác nhận stale.
5. Trong File Manager upload file/thư mục, xem preview PDF/ảnh, chuyển thư mục có Document, trash→restore, kiểm tra approval bị vô hiệu hóa.
6. Chọn Pi backend, thử `list_project_files`, `propose_planning_question`, `write_document` và xác minh không có lệnh bash, đọc secrets, hay truy cập project khác.
7. Kiểm thử SearXNG + Playwright fetch công khai và xác minh private-network URL bị chặn.
8. Chạy `python scripts/e2e_local.py`, kiểm tra ảnh chụp desktop/mobile, JS console và responsiveness bằng Chrome thật.
9. Backup, khôi phục trên staging, so sánh project files, documents, review jobs và quyền truy cập.
10. Triển khai sau reverse proxy HTTPS có `COOKIE_SECURE=true`, egress policy, log và monitoring, không public trực tiếp Pi runner.

## 7. Backlog chưa hoàn tất

- Full BMAD upstream automation và đồng bộ canonical workflows/skills; hiện là BMAD-inspired có human gates.
- OAuth browser-based provider (OpenAI/ChatGPT) và headless token exchange an toàn; hiện chỉ API key/OpenAI-compatible.
- Distributed locks/cancel cho live Discussion SSE; hiện 1 web process.
- Quota lưu trữ/file scan, hạn mức chi phí model chính xác, worker heartbeats/cancel giữa cuộc gọi, monitoring và backup/restore test trên Docker thật.
- Visual regression/axe, đầy đủ các màn hình theo master spec (26 blueprint), offline vendoring asset CDN.

**Kết luận:** Đây là gói source triển khai được để chạy thử/tiếp tục phát triển, không phải chứng chỉ hoàn thiện toàn bộ yêu cầu ban đầu hoặc sẵn sàng public production.
