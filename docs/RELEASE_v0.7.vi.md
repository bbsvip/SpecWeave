# SpecWeave v0.7 — Operational visibility & cross-worker chat coordination

## Đã triển khai

1. **Chat coordination:** `source/chat_coordination.py` sử dụng PostgreSQL session advisory lock theo discussion ID, giữ kết nối riêng trong thời gian streaming; các web worker khác không thể đồng thời chạy cùng discussion. SQLite dùng asyncio lock cục bộ để phát triển. Tin nhắn người dùng chỉ được ghi sau khi giành được khóa.
2. **Durable cancellation:** `agent_runs.cancel_requested` lưu trong database; các worker đọc trạng thái từ DB trong quá trình streaming, không dựa vào set trong RAM. Migration `0004_chat_coordination` bổ sung cột này.
3. **Operations dashboard:** `/admin/operations` và `/api/admin/operations` chỉ dành cho admin, hiển thị số lượng users, projects, provider, model, chat runs và review jobs, hoạt động 24 giờ. Không đưa API key hoặc nội dung chat vào thống kê.
4. **Project Activity:** `/projects/{pid}/activity` và `/api/projects/{pid}/activity`, kiểm tra ACL, chỉ đọc các hoạt động và runs của dự án đang mở.
5. **CI:** `.github/workflows/ci.yml` chạy pytest, Python compile, JS syntax và báo cáo dependency vulnerabilities (báo cáo advisory, không chặn build vì kết quả có thể phụ thuộc feed).

## Migration từ v0.6

Sao lưu PostgreSQL và project volume. Giữ nguyên `.env`, secret và volume. Thay source rồi chạy `alembic upgrade head` (entrypoint Docker hiện có tự chạy migration). Alembic head mới: `0004_chat_coordination`. **Không đổi tên các volume**.

## Giới hạn quan trọng

- Chưa thể xác nhận hoàn thành tất cả 26 màn hình của UI blueprint. Các trang còn thiếu cần triển khai theo blueprint và nghiệm thu UX.
- Không thể tuyên bố tích hợp *đầy đủ* BMAD upstream; hệ thống hiện có workflow lấy cảm hứng từ BMAD. Muốn hỗ trợ đúng upstream phải pin phiên bản và viết adapter cho command/artifact/schema, cùng test tương thích.
- Không có OAuth provider phổ quát. Cần tích hợp riêng từng nhà cung cấp **chỉ khi họ công khai hỗ trợ OAuth cho ứng dụng bên thứ ba**. OpenAI-compatible endpoint hiện dùng API key.
- Chưa có kiểm thử Docker/Chromium/model thật theo yêu cầu bỏ qua nghiệm thu môi trường. PostgreSQL advisory lock cũng chưa được integration-test với PostgreSQL thật.
- Monitoring mới là dashboard tổng hợp từ DB, chưa có OpenTelemetry, tracing chi tiết, Prometheus hay cảnh báo.
- Không có đánh giá bảo mật toàn diện hoặc xác nhận production-ready.
- Chưa có offline fallback cho tất cả CDN assets; cần tải về và pin/check integrity để chạy hoàn toàn không Internet.
- Không hỗ trợ multi-model reviewer arbitration hay đối thoại tự do giữa các reviewer; review hiện là chuỗi vai trò.

## Bảo mật khi triển khai

Đặt `COOKIE_SECURE=true` sau HTTPS, không expose PostgreSQL/Pi runner ra Internet, chặn egress đến mạng riêng từ browser fetch bằng firewall/container policy, thay secret mặc định, hạn chế số worker theo connection pool. Kiểm tra CSP và CDN trước khi dùng trong môi trường offline. Chạy backup/restore drill và thử các luồng cancel/retry sau khi triển khai.
