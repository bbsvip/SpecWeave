# SpecWeave v0.9.8 — Full Chat Stream & Conversation UI

## Mục tiêu
Sửa các lỗi thực tế ở AI Discussion: text/options JSON bị in thẳng, các lượt chat dồn trái, user avatar lấy từ username, thinking/tool activity bị ẩn, usage/tok/s khó quan sát, và SSE error bị bộ giải mã nuốt mất.

## Agent Backend trong Project Settings

- `OpenAI-compatible`: FastAPI điều phối API `/v1/chat/completions` và các công cụ SpecWeave. Tool-call loop tối đa 6 lượt, stream usage nếu endpoint hỗ trợ.
- `Pi RPC`: Pi điều phối agent, prompt, session và custom tools; SpecWeave bridge sự kiện sang SSE. OAuth OpenAI luôn dùng Pi. Các built-in tools/shell của Pi vẫn bị tắt.
- Model selection riêng; backend không đồng nghĩa model. Chỉ hiển thị nội dung thinking khi provider/Pi thực sự trả về.

## Thay đổi backend

- OpenAI-compatible: chuyển tiếp `reasoning_content` / `reasoning` dạng text thành `thinking` event, và tên/ID/tham số/kết quả tool trong các `tool` event.
- Pi RPC: chuyển tiếp `thinking_delta`, `toolcall_start` / `toolcall_delta` / `toolcall_end`, `tool_execution_start` / `tool_execution_update` / `tool_execution_end`; tôn trọng semantics delta cộng dồn và update thay thế. `message_end` là authoritative cho text.
- API SSE: `start`, `status`, `timing`, `thinking`, `tool`, `delta`, `usage`, `final`, `planning_question`, `done`, `error`. `done.output_tps` chỉ tính từ output tokens thực của provider và thời gian chạy đo tại server; có thể là null.
- Ghi lại thinking/tool activity cho mỗi `AgentRun.events`, giới hạn 50.000 ký tự thinking, 64 mục event và 8.000 ký tự cho mỗi preview tool; trường secret được che.
- Hậu xử lý JSON options chỉ khi nhận diện được mảng hoặc object phương án thực sự; hiển thị Markdown dễ đọc. Nếu là decision brief hợp lệ, lưu thành PlanningQuestion tương tác để người dùng xác nhận.
- Lịch sử đã lưu được hiển thị dễ đọc mà không chỉnh sửa nội dung database gốc.

## Thay đổi frontend

- User bubble căn phải, tên “Anh” và avatar A. AI căn trong vùng nội dung chính.
- Thẻ run hoạt động hiển thị ngay từ khi gửi: spinner, phase, thinking (khi có), tool call input/output, usage và tiến độ.
- Thinking và tool có thể mở rộng/thu gọn. Sau khi hoàn thành, activity tự thu lại nhưng thống kê vẫn luôn hiển thị.
- Trong lúc sinh: hiển thị tok/s **ước tính từ số ký tự**, đánh dấu rõ "ước tính"; sau khi nhận token usage, hiển thị tốc độ trung bình tính trên số token do provider báo. Không giả vờ đó là tốc độ từng token thật.
- Tránh tự cuộn khi người dùng đã cuộn lên đọc lịch sử.
- Sửa SSE parser: callback báo lỗi không bị nhầm là JSON hỏng.

## Giới hạn và chính sách

- Provider không phát thinking text → chỉ hiển thị trạng thái xử lý, không bịa chain-of-thought.
- Provider không phát usage → token thực và tok/s thực hiển thị chưa có, chỉ có estimate trong lúc stream.
- Preview tool được giới hạn kích thước và có redact, không phải dump không giới hạn dữ liệu nhạy cảm.
- Mảng JSON options thiếu recommendation vẫn được trình bày thành Markdown, không tự gán phương án khuyến nghị giả. Chỉ decision brief hợp lệ mới tự tạo thẻ tương tác.
- Chưa chạy nghiệm thu với model thực tế, Docker hoặc browser trên host của người dùng.

## Triển khai

Không thay đổi schema. Không cần migration mới. Giữ nguyên `.env`, `ENCRYPTION_KEY`, PostgreSQL và volumes. Đẩy source v0.9.8 lên GitHub để workflow build GHCR image mới. Sau khi publish:

```bash
docker compose -f compose.external-db.images.yaml --profile pi pull
docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build
```

Vì backend Pi RPC đã thay đổi, cần cập nhật **cả web/review-worker và pi-runner image**.
