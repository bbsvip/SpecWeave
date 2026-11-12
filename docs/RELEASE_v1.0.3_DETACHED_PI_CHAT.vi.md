# SpecWeave v1.0.3 — Detached Pi Chat & Unicode Tool Inspector

## Mục đích

Sửa hai vấn đề thực tế:

1. Người dùng rời trang/đóng tab khiến kết nối SSE cũ bị hủy và lượt Pi Agent bị gián đoạn.
2. Input/output công cụ chứa chuỗi JSON escaped Unicode (ví dụ `\\u1eadp`) hiển thị khó đọc và nằm trên một dòng dài.

## Thay đổi kiến trúc

```text
POST /api/projects/{pid}/chat/start
       hoặc .../planning/questions/{qid}/answer-start
    └── Kiểm tra session + ACL + CSRF + model + dữ liệu
        └── PostgreSQL: agent_runs (queued) + chat_stream_events
                ↓
review-worker / chat_worker_main (tách khỏi request/tab)
    └── Claim bằng SELECT ... FOR UPDATE SKIP LOCKED (PostgreSQL)
    └── Pi SDK qua Pi Host hiện có (KHÔNG xây LLM/agent thứ hai)
    └── Lưu từng event thinking/text/tool/usage/done theo cursor tăng dần
                ↓
GET /api/projects/{pid}/runs/{run_id}/events?after=<cursor>
    └── EventSource: Last-Event-ID, reconnect tự động, replay từ PostgreSQL
    └── UI khôi phục run qua GET /api/projects/{pid}/discussions/{did}/active-run
```

Lệnh Stop gọi `POST /api/projects/{pid}/runs/{run_id}/cancel` và hủy Pi. Đóng tab **chỉ ngắt EventSource**, không gọi cancel. Người dùng có thể điều hướng sang một trang khác và quay lại theo dõi đúng session.

## Database

Migration `0010_detached_chat`:

- `agent_runs.request_payload`, `requester_id`, `worker_owner`, `lease_expires_at`
- Bảng `chat_stream_events(id, run_id, event, created_at)`; `id` là event cursor.
- Chuyển các `agent_runs.status='running'` cũ không có worker thành `error` để không khóa vĩnh viễn discussion sau upgrade.

Cần sao lưu PostgreSQL trước khi nâng cấp. Không xóa `projects_data` hay OAuth vault. Sau update phải chạy `web`, `review-worker`, `pi-runner` cùng bộ image tương thích; worker chứa cả review queue và detached chat queue.

## API mới

- `POST /api/projects/{pid}/chat/start`: nhận payload cũ của chat, trả `{"run_id": "...", "status": "queued", "events_url": "..."}`.
- `POST /api/projects/{pid}/planning/questions/{qid}/answer-start`: lưu câu trả lời và enqueue cùng transaction (mỗi câu trả lời chỉ có một continuation run).
- `GET /api/projects/{pid}/discussions/{did}/active-run`: tìm job queued/running trong discussion.
- `GET /api/projects/{pid}/runs/{run_id}/events?after=0`: SSE có `id`, `data` (JSON); tự phát lại các event đã lưu, hỗ trợ `Last-Event-ID`.
- `POST /api/projects/{pid}/runs/{run_id}/cancel`: huỷ cả run đang chờ lẫn run đang chạy.

Hai endpoint SSE trước đây vẫn được giữ cho client cũ, nhưng **giao diện đã dùng đường detached**. Không nên phát triển frontend mới dựa trên endpoint trực tiếp cũ.

## Unicode

`formatToolValue` (frontend) và `pretty_tool` (Jinja history) cố gắng parse JSON, định dạng indent 2 và hiển thị tiếng Việt bằng Unicode thực; đồng thời xử lý chuỗi bị escape lồng nhau. **Không sửa nội dung tool args mà Pi thực thi**, chỉ sửa representation của UI.

## An toàn và giới hạn

- Check ACL khi khởi tạo, khi subscribe và tái kiểm tra định kỳ trên SSE; API đọc event không chấp nhận run khác dự án.
- Enqueue được serialize theo project, tối đa một queued/running run trong một discussion.
- Nếu worker chết, job running hết lease được đánh dấu lỗi an toàn. **Không tự replay prompt lên Pi** vì một lượt có tool side effects có thể đã được thực thi; người dùng có thể kiểm tra rồi gửi lại có chủ đích.
- Nếu Pi Host bị restart trong lúc inference, lượt chạy hiện tại có thể thất bại; đó là khác với việc đóng tab. Khả năng khôi phục inference đang chạy xuyên qua restart của Pi Host chưa được cam kết.
- Event log lưu trong PostgreSQL phục vụ replay; cần lập chính sách retention phù hợp khi chạy nhiều lượt lớn.

## Kiểm thử có thể chạy trên host

```bash
python -m pytest -q tests/test_background_chat_104.py tests/test_migrations.py
node --test tests/test_openai_oauth_contract.mjs tests/test_pi_native_host.mjs
node tests/test_sse_protocol.js
```

## Nghiệm thu thủ công anh tự thực hiện

1. Mở một discussion, gửi câu hỏi dài có tool calls; xác nhận thấy status/usage.
2. Đóng hẳn tab, kiểm tra `docker compose logs -f review-worker` và `pi-runner` (Pi vẫn chạy).
3. Mở lại discussion; xác nhận timeline và câu trả lời tiếp tục đến lúc hoàn thành mà không có user message trùng.
4. Thử mất kết nối SSE/Cloudflare Tunnel; kiểm tra replay `Last-Event-ID`.
5. Chọn một Decision Brief rồi chuyển trang trước khi Pi trả lời; quay lại xác nhận AI tiếp tục cùng session.
6. Kiểm tra tool JSON tiếng Việt dạng `\\uXXXX` và kết quả lịch sử sau reload.
7. Dùng **Dừng** và xác nhận tác vụ thực sự hủy; đóng tab đơn thuần không hủy.
