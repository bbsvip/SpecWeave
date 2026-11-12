# SpecWeave v0.8.0 — Product Completion Release

**Phân loại:** Self-hosted product beta / release candidate; kiểm thử tự động được thực hiện trên môi trường SQLite và source. **Không tuyên bố production-ready** khi chưa có nghiệm thu host, PostgreSQL runtime và security review.

## Mục tiêu và các thay đổi chính

### 1. Hoàn thiện hành trình sử dụng
- **Preset Library:** danh sách, tạo, chỉnh sửa, xóa, tải preset vào Project Wizard; dữ liệu riêng người dùng.
- **Project lifecycle:** thùng rác dự án, khôi phục, quản lý thành viên từ Project Settings, đổi vai trò, thu hồi quyền và chuyển chủ sở hữu có xác nhận tên dự án.
- **Account:** sửa tên/email, xem và thu hồi từng phiên, cài đặt appearance riêng thiết bị.
- **Provider Detail:** quản lý base URL, API key, trạng thái và metadata từng model; model có thể bật/tắt, đặt context window và flag reasoning.
- **Roadmap/Architecture:** bổ sung trang xem milestones, dependencies, architecture/trace với liên kết sang phần chỉnh sửa hiện có.
- **Navigation:** chuẩn hóa sidebar và liên kết tới các màn hình mới.

### 2. Bộ sinh draft tài liệu BMAD
- Tạo 10 draft từ dữ liệu thực của dự án: Product Brief, PRD, Architecture, UX, API/Data, Security, Roadmap, Acceptance, ADR, Handoff.
- Có API preview riêng và checklist chọn tài liệu; nêu rõ phần dữ liệu thiếu bằng `[CẦN XÁC NHẬN]`.
- Không được ghi đè approved document; draft trùng chỉ được ghi đè khi người dùng chủ động chọn, tăng revision và snapshot bản cũ.
- Project-scoped, ACL/CSRF và lock lúc tạo; kiểm tra trước cả batch đối với conflict/quota.

### 3. Multi-provider / multi-role Review
- Trên Quality & BMAD Review có dropdown model riêng cho Analyst, Architect, QA, Synthesis.
- Worker đọc cấu hình từng vai trò, lưu `ReviewJob.role_models` và `ReviewJobStep.model_id` làm provenance.
- API job và export `planning/review-jobs.json` chứa trạng thái, usage ước tính, model ID/role, summary, timestamp (không xuất API key).
- Review vẫn chạy tuần tự, không phải multi-agent debate song song. Synthesis dùng các nhận xét đã lưu của các bước trước.

### 4. Quota, offline assets và an toàn dữ liệu
- Giới hạn file/lượt và tổng dung lượng/số file của dự án, gồm cả snapshots/trash. Giao diện hiển thị usage.
- Upload nhiều file được đưa qua staging; lỗi validation hoặc quota dọn toàn bộ staging, không để lại một phần batch.
- Trước khi sinh nhiều tài liệu, kiểm tra quota và tránh xung đột approved documents.
- Kèm `scripts/vendor_assets.py` để download browser dependencies pin version (chạy riêng trên máy có mạng); nếu chưa vendor, UI vẫn dùng CDN.
- Chặn model không tồn tại hoặc đã bị tắt khi chọn trên project; giới hạn JSON profile 64 KiB.

### 5. Migrations
- Thêm Alembic revision `0005_multi_model_review` sau `0004_chat_coordination`.
- Thay đổi additive, giữ dữ liệu v0.7 (đọc hướng dẫn backup trong README); không thử rollback tự động, restore backup khi cần rollback.

## API mới và mở rộng

| Route | Ý nghĩa |
|---|---|
| `GET /presets`, `POST /api/presets/{id}` | Quản lý template cá nhân |
| `GET /projects/trash`, `POST /api/projects/{id}/restore` | Khôi phục project đã xóa mềm |
| `POST/DELETE /api/projects/{id}/members/{user_id}` | Chỉnh và thu hồi vai trò thành viên |
| `POST /api/projects/{id}/transfer-ownership` | Chuyển owner với xác nhận tên dự án |
| `POST /api/profile`, `DELETE /api/profile/sessions/{id}` | Hồ sơ và phiên đăng nhập |
| `GET /settings/providers/{id}`, `POST /api/providers/{id}/models/{id}` | Chi tiết provider/model |
| `GET /projects/{id}/artifacts` | Trình tạo BMAD drafts |
| `GET /api/projects/{id}/artifacts?key=prd` | Preview nội dung draft |
| `POST /api/projects/{id}/artifacts/generate` | Tạo nhiều draft có preflight |
| `GET /projects/{id}/architecture`, `/roadmap` | Các màn hình giải pháp/roadmap |
| `POST /api/projects/{id}/quality/jobs` | Nhận `role_models` map (optional) |

## Tình trạng kiểm thử

- Test cũ v0.7 vẫn có trong suite; test mới bao phủ artifact approvals/conflicts, preset/profile/session, thành viên/owner/trash, provider, upload quota, ACL và multi-role review provenance.
- Test chạy bằng `PYTHONPATH=source pytest -q tests/test_app.py` trong môi trường tạo source.
- Kiểm tra syntax Python, JavaScript và Alembic head là phần có thể tự động hóa mà không cần deploy.
- Chưa kiểm thử runtime Docker/Chromium/LLM/Pi; do người vận hành tự nghiệm thu theo yêu cầu.

## Các giới hạn còn mở

1. **OAuth:** không triển khai generic OAuth vì tùy từng provider; Pi runner hiện nhận cấu hình compatible. Các provider native OAuth cần integration riêng theo authorization flow được công bố và cần người dùng tương tác.
2. **BMAD upstream:** đã hỗ trợ vai trò, stage gates, checklist, draft artifacts và human approval, nhưng không thực thi toàn bộ upstream BMAD workflows/skill package theo đúng phiên bản.
3. **Transaction boundary:** cập nhật DB + filesystem không phải một giao dịch ACID duy nhất; cần backup/restore drill và xử lý crash recovery khi vận hành thật.
4. **Security:** chưa có pen-test, SAST/DAST độc lập, kiểm tra egress firewall ngoài process, monitoring phân tán qua OpenTelemetry.
5. **Verification:** không xác minh kết nối thực tế của Pi RPC, SearXNG, model và browser do giới hạn môi trường theo yêu cầu người dùng.

## Chú ý vận hành

Trước khi nâng từ v0.7, sao lưu PostgreSQL + `projects_data`, giữ `.env` và volume, build source mới rồi theo dõi migration head `0005_multi_model_review`. Với external PostgreSQL cần backup bên ngoài Compose. Không xóa volume để "reset" trừ khi thực sự muốn mất dữ liệu.
