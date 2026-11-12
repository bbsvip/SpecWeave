# SpecWeave v0.5.0 — Adversarial Review

## Mục tiêu
Thêm phản biện đa góc nhìn vào Quality & BMAD Review, sử dụng cùng model đã chọn. Đây là **ba lượt gọi tuần tự**, không phải ba model độc lập.

## Quy trình
1. Người có quyền sửa chọn **Review 3 góc nhìn** và xác nhận chi phí token.
2. Analyst kiểm tra mục tiêu, tác nhân, phạm vi và giả định.
3. Architect kiểm tra kiến trúc, tích hợp, bảo mật, dữ liệu và khôi phục lỗi.
4. QA kiểm tra acceptance criteria, edge cases, testability và accessibility.
5. Hợp nhất finding trùng theo nguồn và nội dung; giữ mức nghiêm trọng cao nhất và ghi lại vai trò phát hiện.
6. Lưu SpecReview engine `adversarial`, finding `proposed`. Chỉ owner mới được xác nhận hoặc đóng.
7. Nếu spec thay đổi trong thời gian review, HTTP 409, không lưu kết quả cũ.

## API
`POST /api/projects/{project_id}/quality/reviews/run` với `{"engine":"adversarial"}`.
Cần quyền sửa dự án, CSRF và model hợp lệ. Kết quả gồm `id`, `engine`, `findings`, `summary`.

## Giới hạn
- Chưa có tranh luận qua lại giữa các vai trò; mỗi lượt đọc cùng snapshot dự án.
- Chưa có thống kê chi phí riêng cho từng lượt, retry/cancel/background job.
- Chưa xác minh bằng model thật hoặc Docker runtime.
- Tính năng dùng HTTP OpenAI-compatible, chưa qua Pi RPC.
- Không tự động duyệt finding hay spec.

## Backlog
- Reviewer đối chất các finding mâu thuẫn và lưu provenance từng lượt.
- Token budget, background jobs, cancel/retry.
- Pi RPC custom tools và OAuth hợp lệ.
- Playwright E2E, backup/restore, rate limit đa worker.
