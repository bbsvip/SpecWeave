# SpecWeave v0.3.0 — Planning Intelligence Engine

**Trạng thái:** triển khai được ở mức alpha, cần acceptance test thực tế trên hệ thống Docker, PostgreSQL và model của người dùng. Ngày: 2026-11-11.

## 1. Phạm vi đợt nâng cấp

Tiếp tục phát triển từ SpecWeave v0.2.0 (BMAD-inspired + ThinkRail-inspired Guided Planning), xây hệ thống chuyển từ **hỏi đáp → dữ liệu có cấu trúc → kiểm tra khoảng trống → traceability → review → handoff**.

Điểm đã thực hiện:

1. **Project goals:** Mục tiêu, kết quả mong đợi, chỉ số đo lường và phê duyệt bởi owner.
2. **Deterministic coverage:** Phát hiện các khoảng trống quan trọng mà không dùng một LLM để tự cho điểm dự án.
3. **Spec graph:** Liên kết goal, requirement, decision, document, phase và research evidence bằng UUID; tất cả kiểm tra `project_id`.
4. **Roadmap / plan canvas:** Tạo và sửa phase, deliverables và acceptance criteria; phụ thuộc được kiểm tra cycle.
5. **Human review:** `draft → in_review → approved` hoặc `needs_changes`; lưu người review, thời gian, revision và nhận xét; chỉ owner được duyệt.
6. **Change invalidation:** Khi sửa nội dung phase, requirement, trace link tới phase hoặc dependency, phê duyệt phase có liên quan bị vô hiệu hóa. Không xóa lịch sử review.
7. **Reviewed handoff gate:** ZIP `mode=draft` vẫn luôn khả dụng với người có quyền; `mode=reviewed` chỉ chủ sở hữu được xuất và chỉ khi không còn blocker theo bộ quy tắc hiện có.
8. **Bản xuất kế hoạch:** `planning/readiness.{json,md}`, `planning/traceability-graph.json`, `planning/goals.json`, `planning/phases.json` và Decision Briefs từ v0.2.
9. **AI context:** Mỗi phiên chat nhận tối đa 8 vấn đề ưu tiên từ bộ kiểm tra dự án; agent có thể đề xuất bước tiếp theo, không được tự phê duyệt.
10. **UI:** Trang `/projects/{id}/intelligence` có chỉ số, issues, goals, phase review, dependency editor, SVG trace graph (không cần Node/D3), trace link management, review history.

## 2. Tách vai trò của AI và hệ thống phê duyệt

- `proposed` trong Decision Brief chỉ là phương án AI khuyến nghị.
- `answered` trong Planning Questions chỉ xác nhận một câu trả lời trong chat.
- `approved` của Goal, Requirement, Document, Phase là thay đổi cụ thể có kiểm tra quyền, audit và trạng thái trong database.
- Báo cáo `ready=true` **chỉ có nghĩa** dự án không bị chặn bởi các quy tắc hiện tại. Nó không phải sự bảo đảm rằng thiết kế, mã, nghiên cứu, an toàn pháp lý hay số liệu đã đúng.
- Lúc export `reviewed`, API kiểm tra lại readiness tại thời điểm tạo ZIP, không tin trạng thái hiển thị trên UI trước đó.

## 3. Data model (tables mới)

| Bảng | Trường trọng tâm | Chức năng |
|---|---|---|
| `project_goals` | `project_id`, `title`, `outcome`, `metric`, `status` | Mục tiêu được duyệt độc lập |
| `trace_links` | project_id, source_type/id, target_type/id, relation | Trace graph, unique key trong project |
| `plan_phases` | project_id, title, summary, deliverables, acceptance, status, revision | Kế hoạch và optimistic revision |
| `phase_dependencies` | project_id, phase_id, prerequisite_id | DAG dependencies; chặn chu trình |
| `phase_reviews` | project_id, phase_id, reviewer_id, action, revision, comment, created_at | Lịch sử review | 

Các đối tượng này được tạo qua `Base.metadata.create_all()` khi app khởi động. Việc **thêm bảng** tương thích với SQLite/PostgreSQL đang có từ v0.2, vì không thay đổi cột bảng cũ. Đây chưa phải một migration framework đầy đủ; production cần backup database và sau đó đưa về Alembic trước khi thay đổi schema phức tạp hơn.

## 4. Các kiểm tra mặc định

**Blockers:** không có mục tiêu/goal chưa được duyệt, không có requirements, requirement must/should thiếu acceptance criteria hoặc chưa duyệt, requirement chưa nối với phase, không có tài liệu approved, không có phase hoặc phase chưa duyệt, phase thiếu deliverable/acceptance/trace, prerequisite chưa duyệt, dependency cycle, planning question còn pending.

**Warnings:** mục tiêu chưa mô tả outcome, requirement không trace về goal, requirement không có link tới quyết định/tài liệu, quyết định proposed chưa được duyệt.

Không có thang điểm chất lượng % giả. UI hiển thị số blocker/warning, mỗi vấn đề có mã và mô tả để phục vụ triage.

## 5. API mới

Tất cả endpoint đều nằm dưới `/api/projects/{pid}/intelligence`; sử dụng session cookie, project ACL và CSRF với phương thức thay đổi dữ liệu.

| Method | Path con | Quyền |
|---|---|---|
| GET | `/report` | viewer/editor/owner |
| GET | `/graph` | viewer/editor/owner |
| POST | `/goals` | editor/owner |
| POST | `/goals/{gid}/status` | owner |
| POST | `/links` | editor/owner |
| DELETE | `/links/{lid}` | editor/owner |
| POST | `/phases` | editor/owner |
| GET | `/phases/{phase_id}` | viewer/editor/owner |
| POST | `/phases/{phase_id}` | editor/owner, cần `revision` |
| POST | `/phases/{phase_id}/dependencies` | editor/owner |
| DELETE | `/phases/{phase_id}/dependencies/{id}` | editor/owner |
| POST | `/phases/{phase_id}/review` | `submit`: editor/owner; `approve`/`request_changes`: owner |

**Example link:**

```json
{
  "source_type": "goal", "source_id": "<goal_uuid>",
  "target_type": "requirement", "target_id": "<requirement_uuid>",
  "relation": "supports", "note": "Mục tiêu được triển khai bởi yêu cầu này"
}
```

**Review:**

```json
{"action": "submit", "revision": 1, "comment": "Ready for review"}
```

Sau đó owner có thể `approve` hoặc `request_changes` (bắt buộc ghi lý do cho `request_changes`). API trả `409` nếu revision thay đổi hoặc chưa thỏa điều kiện.

**Export:**

- `POST /api/projects/{pid}/export?mode=draft` — không yêu cầu không còn blockers; luôn chứa báo cáo readiness.
- `POST /api/projects/{pid}/export?mode=reviewed` — chỉ owner và `ready=true`; nếu chưa đạt, trả JSON HTTP 409 với `issues`.

## 6. Hướng dẫn kiểm thử nhanh

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q tests
```

Kiểm tra thủ công:

1. Đăng nhập, tạo dự án qua wizard và vào **Planning Intelligence**.
2. Tạo Goal, nhập outcome rồi owner duyệt.
3. Tạo Requirement có acceptance criteria; duyệt tại Requirement Center.
4. Tạo phase có summary/deliverable/acceptance; nối `requirement → phase`; thử tạo cycle (phải bị từ chối).
5. Nối `goal → requirement`, `requirement → document` hoặc decision; kiểm tra SVG graph.
6. Gửi review phase, owner phê duyệt; sửa phase hoặc một requirement liên quan và xác nhận status bị reset.
7. Tạo/duyệt PRD trong Document Studio; kiểm tra báo cáo readiness và export reviewed.
8. Đổi sang user viewer, xác minh chỉ xem; editor tạo/sửa nhưng không thể approve.
9. Xuất ZIP nháp/đã duyệt; kiểm tra manifest, graph và readiness files.

## 7. Vẫn còn trong backlog

- Alembic với migration history/recovery hoàn chỉnh, DB locks/transactions phân tán và optimistic locking cho tất cả thực thể.
- Giải quyết conflict đa người dùng trực tiếp, semantic contradiction detection bằng model/agent review, evidence ranking theo độ tin cậy và nguồn gốc.
- Pi custom tool parity, workflow BMAD upstream đầy đủ, nhiều reviewer và comment anchoring theo đoạn nội dung.
- Import tự động requirements từ tài liệu uploads, OCR, DOCX/PDF extraction, graph auto-link (hiện link được thêm thủ công).
- End-to-end browser screenshot/visual regression, QA responsive và live provider integration trên host triển khai.
- RBAC tinh chỉnh, safety egress layer cho browser, per-project quotas, sandbox và backup/restore production.
