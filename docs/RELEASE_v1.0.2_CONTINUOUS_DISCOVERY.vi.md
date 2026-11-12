# SpecWeave v1.0.2 — Continuous Pi-Native Discovery

## Mục tiêu và phạm vi

Sửa lỗi sau khi xác nhận một lựa chọn, người dùng chỉ thấy bong bóng "Tôi chọn: ..." nhưng không có lượt phản hồi từ Pi. Giữ **Pi SDK là agent duy nhất**. Không bổ sung agent/model client riêng cho FastAPI; không dùng Pi RPC subprocess. Chat thường, hỏi tiếp, đề xuất phương án, viết tài liệu và kết thúc phỏng vấn đều chạy qua **cùng Pi `AgentSession`** của discussion tương ứng.

## Nguyên nhân trong v1.0.1

1. Giao diện gọi `POST /planning/questions/{qid}/answer` để lưu answer, **sau đó** mới gọi `POST /chat` để gửi tiếp. Nếu trình duyệt/JS/controller hoặc proxy gặp lỗi giữa hai request, phần user đã được lưu nhưng Pi chưa nhận câu trả lời.
2. Frontend tự hỏi tiếp bằng endpoint `POST /planning/questions/suggest` sử dụng *stateless Pi completion* không thuộc session chat chính. Điều này làm ngắt mạch context và không stream hoạt động tới hội thoại.
3. Sau tool `propose_planning_question`, backend chưa phát sự kiện `planning_question` nhất quán (tool ghi ở DB transaction khác; parser chỉ xử lý fallback JSON). Trường hợp này đôi khi để UI im lặng dù Pi đã tạo câu hỏi.
4. Trong hàm xử lý output có import `question_payload` cục bộ ở một nhánh, dẫn tới lỗi phạm vi biến nếu truy cập ở nhánh khác.
5. Frontend thử gọi agent lần tiếp theo khi `AbortController` của lượt trước vẫn đang tồn tại, khiến `talk()` trả `false` mà không tạo request.

## Đường chạy mới

1. Pi tạo một `PlanningQuestion` bằng custom tool `propose_planning_question` (2–4 phương án, đề xuất mặc định và đánh đổi). Frontend nhận qua `planning_question` SSE và hiển thị thẻ ngay trong chat.
2. Anh chọn/sửa/gửi câu trả lời: frontend gọi **một request SSE duy nhất** `POST /api/projects/{pid}/planning/questions/{qid}/answer-stream` (CSRF, ACL, model validation). Server lưu câu trả lời một lần, khởi tạo run và stream trực tiếp tiếp qua Pi.
3. Pi nhận nội dung câu trả lời trong **cùng discussion session**; stream `status`, `thinking` nếu model cung cấp, `tool`, `delta`, `usage`, `planning_question`, `done`.
4. Nếu lượt Pi chưa đưa ra câu hỏi mới và tính năng "Tự đề xuất câu hỏi tiếp theo" bật, frontend gửi **tối đa một** lượt `advance_planning=true` qua cùng endpoint chat/SSE và Pi session. Không chèn tin nhắn người dùng giả vào database. Nếu model chưa tạo thẻ, UI hiển thị nút thử lại thay vì chạy vòng lặp vô hạn.
5. Với session cũ chứa câu trả lời đã lưu nhưng chưa có `continuation_run_id`, UI tự thử khôi phục một lần khi mở session, không ghi trùng lựa chọn.
6. Khi đủ dữ kiện, Pi có thể dùng `write_document` để soạn bản Markdown chi tiết, sau đó `finish_discovery`. Tool cuối kiểm tra ít nhất ba lựa chọn đã trả lời, không có câu hỏi đang chờ, summary/evidence và không còn unknowns mà Pi khai báo. Hệ thống tạo bổ sung **những draft còn thiếu**, không sửa tài liệu đã approved. Trạng thái được lưu vào `project.profile._discovery`.
7. Frontend nhận `planning_complete` trong SSE, mở đường tới BMAD Artifacts/Document Studio. **Draft không đồng nghĩa được duyệt**; con người vẫn phải kiểm tra các đoạn `[CẦN XÁC NHẬN]` và phê duyệt.

## Các API

- `POST /api/projects/{pid}/planning/questions/{qid}/answer-stream` — mới. JSON body: `selected_ids`, `custom_answer`, `note`, `action="answer"`, `role`. Trả SSE, ghi câu trả lời một lần và chạy Pi.
- `POST /api/projects/{pid}/chat` — mở rộng `advance_planning: true` với `mode: "discovery"`, `discussion_id`. Không có `message` hoặc `continue_question_id` trong chế độ này. Chỉ gọi khi không còn câu hỏi pending và discovery chưa kết thúc.
- `GET /api/projects/{pid}/planning/discovery-progress?discussion_id=<uuid>` — mới; trả số câu đã trả lời/chờ/hoãn, số artifacts, trạng thái.
- `POST /api/projects/{pid}/chat` với `continue_question_id` vẫn được giữ để khôi phục phiên cũ/lỗi, có idempotency marker và từ chối nếu đã xử lý.

## Bảo vệ dữ liệu và giới hạn

- Tất cả endpoints giữ authentication, CSRF, ACL, project isolation.
- Không tự xác nhận quyết định hay phê duyệt tài liệu.
- `write_document` từ Pi không được ghi đè tài liệu `approved`.
- Không thực hiện tự động một vòng hỏi vô hạn. Nếu model không hỗ trợ tool calling, system prompt hướng dẫn tạo JSON có schema; backend chỉ parse khi hợp lệ, còn thiếu vẫn có nút yêu cầu đề xuất lại.
- Tiêu chí đủ dữ liệu dựa trên đánh giá của Pi và guard tối thiểu, **không phải chứng nhận PRD hoàn thiện/đúng kỹ thuật**. Các draft được xuất từ dữ liệu đã xác nhận và có thể chứa chỗ cần bổ sung.
- SSE stream qua HTTP request đang mở; chưa bổ sung replay bền vững sau mất kết nối. Khi lỗi, hệ thống giải phóng `continuation_run_id` để người dùng tiếp tục mà không ghi lại câu trả lời.
- Không có thay đổi schema PostgreSQL / Alembic migration mới.

## Các bước nâng cấp từ v1.0.1

1. Backup PostgreSQL và volume `projects_data`; không xóa dữ liệu.
2. Đẩy toàn bộ source v1.0.2 hoặc áp patch lên v1.0.1. Workflow GitHub Actions sẽ build **web** và **pi** images từ cùng commit (Pi Host custom tools được cập nhật).
3. Chờ GitHub Actions publish, đặt `SPECWEAVE_IMAGE_TAG=main` hoặc tag release đã publish.
4. Triển khai:

```sh
docker compose -f compose.external-db.images.yaml pull
docker compose -f compose.external-db.images.yaml up -d --no-build
```

5. Giữ nguyên `.env`, `DATABASE_URL`, `ENCRYPTION_KEY`, `PI_RUNNER_KEY`, `openai_oauth_local`.

## Checklist nghiệm thu trên máy của anh

- Mở session có một pending Decision Brief, chọn A rồi Xác nhận: phải xuất hiện thẻ Pi đang hoạt động ngay trong chat, sau đó có response/usage và câu hỏi kế tiếp. Không bấm "AI đề xuất" lần thứ hai.
- Thử một session đã bị mắc kẹt ở v1.0.1 (trả lời nhưng chưa có phản hồi): tải lại, kiểm tra tự tiếp tục.
- Khi Pi chỉ giải thích mà chưa tạo card, sẽ có lượt yêu cầu đề xuất tiếp theo **cùng session**, không được tự quay vòng mãi.
- Kiểm tra thinking/tool calls/usage trong thẻ AI (chỉ hiện dữ liệu thực sự do Pi/model cung cấp).
- Test 3+ lượt hỏi/đáp và cho Pi hoàn tất discovery: nhận `planning_complete`, xuất draft vào Documents/Artifacts, không chuyển sang approved.
- Dừng giữa lượt Pi, thử tiếp tục; không được có 2 bản sao tin nhắn người dùng.

## Kiểm thử tự động tại môi trường tạo bản vá

- 95 bài Python (gồm test 3 lượt hỏi/đáp, khôi phục retry, project isolation, tài liệu draft và tiến độ).
- 5 bài Node OAuth/Pi SDK host contract với Pi SDK giả lập.
- Parser SSE, cú pháp JavaScript, Python compile.
- Không tuyên bố đã xác minh với Pi npm SDK thật, model local, Docker hoặc Cloudflare Tunnel; phần live được để anh nghiệm thu.
