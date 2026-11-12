# SpecWeave v0.9.6 — Conversation Workspace & Continuous Planning

## Phạm vi

Bản cập nhật này hoàn thiện luồng **AI Discussion**, tập trung vào trải nghiệm chat hoạt động liên tục và có thể quan sát được. Thiết kế tham khảo một số nguyên tắc công khai của ThinkRail (không sao chép mã nguồn/giao diện): các bước hoạt động được thu gọn trong mỗi lượt chat, hiển thị model và usage, câu hỏi nhiều lựa chọn nằm ngay trong hội thoại, hỗ trợ Markdown/sơ đồ và phiên có lịch sử riêng.

Tài liệu tham khảo:

- https://thinkrail.ai/blog/the-chat/
- https://thinkrail.ai/blog/gui-overview/
- https://thinkrail.ai/
- https://pi.dev/docs/latest/json
- https://github.com/JetBrains/thinkrail

## Điều đã sửa

### 1. Trả lời Decision Brief → AI tự tiếp tục

Trước đây `POST /api/projects/{pid}/planning/questions/{qid}/answer` chỉ đánh dấu `answered` và ghi một `Message(role=user)`. UI hiển thị thông báo "đã lưu" nhưng không gọi model.

Bây giờ:

1. Người dùng chọn một hoặc nhiều phương án, điều chỉnh và xác nhận.
2. Backend ghi câu trả lời **đúng một lần**, giữ trạng thái chưa phê duyệt của Decision Center.
3. Frontend gọi `POST /api/projects/{pid}/chat` với `continue_question_id: qid` và `message: ""`.
4. Backend xác thực rằng câu hỏi đã được trả lời, thuộc **chính project và discussion**, chưa có `continuation_run_id`.
5. `AgentRun` được ghi ID vào `PlanningQuestion.answer.continuation_run_id` cùng transaction.
6. Không tạo một `Message(role=user)` thứ hai. Model nhận lịch sử chứa câu trả lời vừa xác nhận.
7. AI stream phản hồi, tóm tắt tác động và có thể tạo Decision Brief tiếp theo qua tool; nếu chưa có câu hỏi mới, UI tự yêu cầu `suggest` khi bật checkbox “Tự đề xuất câu hỏi tiếp theo”.

**Chống gửi trùng:** Tiếp tục cùng `qid` lần hai trả HTTP 409. Nếu session đã bận trước khi run bắt đầu, backend trả thông báo và giải phóng marker để người dùng có thể thử lại.

### 2. SSE có các sự kiện thống nhất

`POST /api/projects/{pid}/chat` trả `Content-Type: text/event-stream`, cache và proxy buffering được tắt.

| type | payload | Ý nghĩa |
| --- | --- | --- |
| `start` | `model`, `backend`, `context_window`, `continued` | Bắt đầu phiên xử lý |
| `status` | `phase`, `message` | Kết nối, đang trả lời, reasoning, tool, retry, compaction |
| `delta` | `text` | Nội dung model sinh theo từng đoạn |
| `tool` | `name`, `status`, kết quả rút gọn (nếu có) | Tiến độ tool call |
| `timing` | `first_token_ms` | Thời gian từ lúc server bắt đầu xử lý tới token đầu tiên |
| `usage` | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `source` (chỉ các trường đã nhận được) | Usage do provider báo |
| `final` | `text` | Toàn bộ kết quả cuối |
| `done` | `duration_ms`, `first_token_ms`, `usage` | Run hoàn thành |
| `error` | `message` | Run thất bại hoặc bị hủy |

Không suy đoán số token hay USD khi endpoint không trả usage/cost. Provider hỗ trợ `stream_options.include_usage` sẽ được yêu cầu báo usage. Nếu không hỗ trợ và trả 400/422, tự bỏ tùy chọn này rồi thử lại mà không dừng chat; nếu endpoint không hỗ trợ tool calling thì rút về chat thuần.

Với Pi RPC, xử lý `agent_start`, `turn_start`, `message_update`, `message_end`, `tool_execution_*`, `compaction_start`, `auto_retry_start` và `agent_settled`. Token `message_update.usage` là snapshot của lượt hiện tại, không cộng lặp qua mỗi delta. Token cuối theo `message_end.usage` được cộng vào các lượt đã hoàn tất; `agent_end` không dùng để kết thúc phiên do Pi có thể tự retry.

### 3. Chat UI

- Ba panel **Sessions / Conversation / Planning Context** vẫn giữ nguyên; sửa phần giữa theo hướng ThinkRail.
- Markdown hiển thị tăng dần, có sanitizer DOMPurify; code block có nút Copy.
- Code block `mermaid` có công cụ render diagram **theo yêu cầu** bằng thư viện CDN; mất mạng hoặc lỗi vẫn giữ được source. Mermaid ở chế độ `securityLevel: strict`.
- Hoạt động agent đặt trong `<details>` dưới mỗi câu trả lời; có trạng thái, tool, lỗi và usage.
- Sidebar phải hiển thị usage của run đang stream và bảy run gần nhất **thuộc chính discussion này**.
- Usage, TTFT, thời gian chạy và các event status/tool được lưu theo `AgentRun`, liên kết với tin nhắn bằng `Message.run_id`, xem lại được sau reload.
- Gửi heartbeat SSE mỗi 12 giây trong thời gian model chưa có dữ liệu để hạn chế reverse proxy/Cloudflare Tunnel ngắt kết nối nhàn rỗi. Heartbeat không hủy lượt gọi model.
- Khi kéo lên đọc tin cũ, stream không tự cuộn xuống; nút mũi tên xuống để trở về cuối.
- Quy tắc nhập: **Enter gửi, Shift+Enter xuống dòng**. Nút Dừng vẫn gọi server-side cancel.
- Interactive Decision Brief hiển thị ngay trong khung message, không tách thành panel dưới khung scroll.

### 4. Database

Migration mới `0008_conversation_stream` thêm vào `agent_runs`:

- `usage` JSON mặc định `{}`
- `events` JSON mặc định `[]`, chỉ lưu tối đa 64 trạng thái/tool đã rút gọn (không lưu output nhạy cảm của tool)
- `first_token_ms` INTEGER nullable
- `duration_ms` INTEGER nullable
- `messages.run_id` VARCHAR(36) nullable + FK đến `agent_runs`

Không xóa hoặc thay đổi dữ liệu đã có; bảng `planning_questions` đã có JSON `answer` để lưu correlation ID, không cần migration khác.

### 5. Triển khai

Source giữ cấu trúc `source/`, không cần `pip install -e .`; không tạo frontend build Node.js.

`Dockerfile.runner` được bổ sung `source/chat_events.py` vào image Pi để cả backend và Pi runner sử dụng cùng code chuẩn hóa usage. Do Dockerfile thay đổi, cần publish lại **cả web và pi-runner images**.

Nếu dùng GHCR:

```bash
docker compose -f compose.external-db.images.yaml --profile pi pull
docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build
```

Giữ `.env`, volumes, `ENCRYPTION_KEY`. Migration chạy lúc khởi động như trước.

### 6. Test / giới hạn

- Regression suite Python: **79 passed** (bao gồm các bài test migration và trace/heartbeat).
- JavaScript SSE chunk-framing tests: passed.
- Cú pháp JavaScript/Python: passed.
- Không chạy Docker runtime, OAuth live, Chromium UI E2E hoặc kiểm thử trên model thực — theo yêu cầu của chủ dự án.
- Usage chỉ đầy đủ khi provider thực sự hỗ trợ. Một số server không hỗ trợ OpenAI `stream_options` nên chat hoạt động nhưng không có token input/output chính xác.
- Mermaid import qua CDN khi mở sơ đồ; môi trường offline hiển thị source Mermaid mà không render sơ đồ.
- Câu hỏi tự đề xuất sau lượt xác nhận là một request model riêng, có thể phát sinh thêm token và mất thời gian.
- Đây là UI planning lấy cảm hứng từ ThinkRail, không phải bản tích hợp nguyên giao diện hay code React của ThinkRail.
