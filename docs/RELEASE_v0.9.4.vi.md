# SpecWeave v0.9.4 — OpenAI OAuth, encryption config và UI hotfix

## Nguyên nhân
- `ENCRYPTION_KEY` chưa đúng định dạng Fernet nên thêm provider trả 500 tại `security.py`. Đây không phải lỗi Cloudflare hay `COOKIE_SECURE`.
- Pi runtime trả `OpenAI login failed` nhưng subprocess che toàn bộ stderr và mapping error thành chuỗi chung; khi phiên đã thất bại, `/answer` báo 404. Không thể kết luận nguyên nhân gốc chỉ từ log cũ.
- Trang `/projects/trash` truyền `active='projects'`, khiến sidebar đánh dấu sai.
- `pr.profile` chứa nested object đang dùng `value|string`, tạo biểu diễn Python dict khó đọc.

## Thay đổi
1. `security.validate_encryption_key()` kiểm tra ngay khi app khởi động và trước Alembic từ entrypoint. Chỉ trả lỗi cấu hình; tuyệt đối không in key. Không tự sinh key để tránh mất dữ liệu mã hóa cũ.
2. Pi OAuth báo mã lỗi cố định đã duyệt và lưu diagnostic code, không chuyển raw exception/access token/callback URL sang frontend hoặc logs. HTTP 409 cho phiên đã kết thúc. Giao diện khóa thao tác nhập mã với phiên lỗi và hướng dẫn bắt đầu lại.
3. Sidebar có active state riêng cho Thùng rác; hỗ trợ aria-current.
4. Profile dạng list hiển thị chips, nested dict hiển thị JSON có indent, metadata audit đọc trong details; .json trong file editor có nút Format JSON.
5. Không đổi migrations, database schema, provider keys, Docker Compose volumes hoặc OAuth storage.

## Cách nâng cấp
- Backup `.env`, DB, project volume và OAuth volume. Không in/chia sẻ nội dung `.env` hoặc credential.
- Giữ nguyên `ENCRYPTION_KEY` nếu trước đây đã mã hóa API keys. Nếu key hiện đang không hợp lệ và chắc chắn chưa lưu bất kỳ secret nào có thể giải mã, tạo Fernet key mới: `python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'`.
- Dùng Compose local hoặc image GHCR v0.9.4 tùy quy trình. Khi mở OpenAI Connect, xem mã lỗi và logs đã lọc từ `pi-runner`. Nếu vẫn lỗi, cần thông tin mã diagnostic, không cần cung cấp mã OAuth.
- `COOKIE_SECURE=true` phù hợp với HTTPS domain qua Cloudflare Tunnel, không liên quan hai lỗi trên.

## Kiểm thử
- Automated Python API/UI smoke tests, syntax checks Python/JS/YAML.
- Chưa kiểm chứng OAuth thật hoặc Docker runtime trên host; anh tự nghiệm thu như đã thống nhất.
