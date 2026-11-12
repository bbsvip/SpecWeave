# SpecWeave v0.9.5 — Provider management & OpenAI OAuth diagnostics

## Tổng quan

Bản v0.9.5 kế thừa toàn bộ source v0.9.4, bổ sung xóa một model, xóa provider kèm các model, làm rõ đường sửa provider và cải thiện khả năng chẩn đoán OAuth OpenAI qua Pi. Không thêm OAuth cho các provider khác.

## Provider & Model

- Danh sách **Providers & Models** hiển thị nút **Sửa** và **Xóa** ngay trên thẻ provider.
- **Sửa** dẫn đến trang chi tiết sẵn có để thay tên, protocol OpenAI-compatible/OpenAI API, base URL, API key, trạng thái enabled; API key đang lưu không được đọc trở lại client.
- **Xóa** lấy preview từ server, hiển thị tổng số model, dự án bị ảnh hưởng, review liên quan và tác vụ còn hoạt động. Người dùng phải gõ đúng tên provider để xác nhận xóa vĩnh viễn.
- Trang chi tiết có nút **Xóa model** riêng, yêu cầu gõ đúng model ID.
- Khi xóa model đang được chọn, `Project.active_model_id` được reset; revision dự án được tăng.
- Khi còn review `queued`/`running` sử dụng model (bao gồm cấu hình model theo vai trò) hoặc chat đang chạy trên dự án bị ảnh hưởng, API trả **409**; không xóa dang dở.
- Những review đã hoàn tất **không bị xóa**. Model tham chiếu từ `review_jobs.model_id` chuyển thành `NULL` trước khi xóa, đồng thời snapshot tên provider/model được lưu trong `review_jobs.model_snapshot`. `spec_reviews.model_snapshot` giữ nguồn gốc model của review đồng bộ.
- Nếu provider là OpenAI OAuth: chỉ đúng chủ tài khoản được xóa, và phải **Disconnect** trước để đảm bảo không để lại credential trong vault.
- Các hành động thay đổi yêu cầu đăng nhập Admin, CSRF và ghi audit. Giao dịch DB đảm bảo thành công toàn bộ hoặc rollback.

### API mới

| Method | Path | Mục đích |
| --- | --- | --- |
| GET | `/api/providers/{id}/delete-preview` | Đếm ảnh hưởng trước khi xóa provider |
| DELETE | `/api/providers/{id}` | Xóa provider, body `{"confirm_name":"tên chính xác"}` |
| GET | `/api/providers/{id}/models/{mid}/delete-preview` | Đếm ảnh hưởng một model |
| DELETE | `/api/providers/{id}/models/{mid}` | Xóa model, body `{"confirm_model":"model-id chính xác"}` |
| POST | `/api/providers/{id}` | **Đã có từ trước**, giao diện sửa được hiển thị rõ hơn |

## Database migration

Revision mới: **`0007_provider_deletion`** (sau `0006_openai_oauth_owner`).

- `review_jobs.model_id` được chuyển thành nullable để vẫn giữ được kết quả review sau khi model đã xóa.
- `review_jobs.model_snapshot` (JSON) lưu thông tin model bị xóa.
- `spec_reviews.model_snapshot` (JSON) giữ metadata model cho review đã tạo.
- Alembic migration kiểm tra schema hiện có để tránh tạo cột trùng khi nâng cấp database từ bản đã tạo ORM schema trực tiếp.
- Không được dùng `alembic stamp head` để bỏ qua migration. Sao lưu PostgreSQL trước khi cập nhật.

## OpenAI OAuth: chẩn đoán v0.9.5

Mã `OAUTH_RUNTIME_FAILURE` bản cũ không đủ để khẳng định nguyên nhân gốc. Bản mới bổ sung:

1. **Chẩn đoán** trên trang Providers: kiểm tra Node imports, `models.login`, OpenAI OAuth adapter và quyền ghi local credential volume; không đọc token hoặc mở kết nối OpenAI.
2. `OAUTH_RUNTIME_FAILURE` ghi `phase` (`register_provider`, `provider_login`, `awaiting_manual_callback`, `persisting_credential`...) và `hint` từ danh sách đã kiểm soát như `INCOMPATIBLE_API`, `NETWORK_OR_PROXY`, `AUTH_REJECTED`, `CREDENTIAL_NOT_PERSISTED`.
3. Tất cả chi tiết gửi về UI đều thuộc allowlist; không trả raw error, authorization code, token hoặc callback URL trong diagnostic.
4. Ghim `@earendil-works/pi-coding-agent@1.5.2` và `@earendil-works/pi-ai@1.5.2` trong Dockerfile.runner, thêm build-time smoke test import, API factory và provider.
5. `AuthInteraction.prompt()` xử lý `AbortSignal` (cơ chế callback có thể chiến thắng manual prompt), tránh prompt treo tới timeout sau khi callback đã hoàn tất.

**Quan trọng:** chưa chứng minh OAuth đã đăng nhập thành công trên máy người dùng; cần chạy image mới và sử dụng nút Chẩn đoán + xem log `pi-runner`. Không có giả định rằng cấu hình Cloudflare Tunnel hoặc `COOKIE_SECURE` gây ra mã lỗi này.

## Nâng cấp GHCR

Push source mới lên GitHub. Khi workflow publish thành công, cập nhật tag image trong `.env` rồi:

```bash
docker compose -f compose.external-db.images.yaml --profile pi pull
docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build
```

Đảm bảo image mới đã chứa migration `0007`. `web` chạy Alembic lúc khởi động. Không xóa hoặc đổi `projects_data`, `openai_oauth_local`, `DATABASE_URL` hay `ENCRYPTION_KEY`.

## Kiểm thử

Bộ kiểm thử tự động có thêm các trường hợp sửa provider, khóa CSRF, xác nhận xóa, bảo toàn review history, xóa một model, chặn xóa khi có review hoặc chat đang chạy và lọc dữ liệu OAuth. Kiểm tra riêng module imports/cú pháp Python và JavaScript. Theo phạm vi đã thống nhất, Docker/Chromium/live OAuth trên host để người vận hành tự nghiệm thu.
