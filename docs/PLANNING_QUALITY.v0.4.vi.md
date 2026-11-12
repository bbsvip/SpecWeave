# SpecWeave 0.4 — Planning Quality, Evidence & BMAD Orchestration

**Mục đích:** bổ sung 5 năng lực còn thiếu của bản 0.3: semantic review, provenance evidence, auto trace candidate, workflow phê duyệt và database migration. Đây là chức năng **planning**, không phải hệ thống tự động triển khai source code.

**Trạng thái:** đã triển khai và có automated tests; kiểm thử live Docker / Playwright / providers trên host đích vẫn bắt buộc. Không tự nhận production-ready.

## 1. Nguyên tắc bất biến

1. Chỉ người dùng có quyền mới được phê duyệt; mọi ý kiến từ model, keyword matcher và external website đều là **proposal / unverified**.
2. AI không được tự đổi `approved` cho requirement, document, phase hoặc workflow checkpoint.
3. Tất cả records và query của Quality Intelligence được scope theo `project_id`, kể cả `model_review` và `TraceSuggestion`.
4. Nguồn web được coi là dữ liệu không đáng tin. Không diễn giải metadata source score là độ đúng sự thật của nội dung.
5. Fingerprint của semantic review phản ánh trạng thái spec đã đọc. Khi spec thay đổi, review có dấu `stale`, owner không được confirm finding cũ. Stage checkpoint dùng fingerprint theo **phạm vi của giai đoạn**, tránh việc viết UX làm mất hiệu lực discovery đã duyệt.
6. Reviewed export phải qua **deterministic readiness** từ 0.3 và **quality gate** từ 0.4. Draft export luôn khả dụng cho người có quyền.

## 2. Semantic Review Engine

Trang: `/projects/{pid}/quality` → **Kiểm tra đặc tả**.

- **Rules Review:** chạy offline, phát hiện acceptance criteria quá ngắn, yêu cầu bắt buộc thiếu mô tả và những tiêu đề decision được duyệt trùng nhau nhưng nội dung khác. Không suy diễn rằng hai quyết định thực sự mâu thuẫn.
- **AI Semantic Review:** sử dụng `Project.active_model_id`, tạo snapshot có giới hạn của goals, requirements, decisions, phases, documents (chỉ excerpt) và trace. Gọi endpoint OpenAI-compatible với JSON schema yêu cầu qua prompt; validate JSON trước khi lưu. **Không sử dụng agent tools, không chạy shell**.
- Chỉ các severity `high`, `warning`, `info`; categories được whitelist; mọi `source_id` buộc phải tồn tại **trong đúng project**. Model không được tự chèn tham chiếu chéo dự án.
- Kết quả lưu `spec_reviews` và `review_findings`. Owner có thể `confirmed`, `resolved`, `dismissed`, không có API để model tự duyệt.
- **Chặn reviewed export:** một finding mức `high` đã được owner xác nhận (`confirmed`) sẽ chặn handoff **kể cả khi fingerprint cũ** cho đến khi owner đánh dấu `resolved` hoặc `dismissed`. Không thể né gate bằng việc sửa một đoạn văn bản. Finding `proposed` chưa chặn, vì AI có thể sai.
- Nếu model trả JSON không hợp lệ hoặc HTTP lỗi: API trả 502 và không tạo review giả. Provider không hỗ trợ dạng completion này cần dùng Rules Review hoặc điều chỉnh adapter.

### Ví dụ phản hồi được chấp nhận

```json
{
  "summary": "Đã rà soát luồng xác thực và thanh toán",
  "findings": [
    {
      "severity": "warning",
      "category": "ambiguity",
      "description": "Chưa mô tả trạng thái đơn hàng khi thanh toán bị timeout",
      "recommendation": "Bổ sung trường hợp retry và chống duplicate payment",
      "source_type": "requirement",
      "source_id": "<UUID thực có trong dự án>",
      "evidence": "Trích dẫn hoặc chỉ ra phần thông tin bị thiếu"
    }
  ]
}
```

Cảnh báo: evidence do model cung cấp là **lời dẫn của model**, phải được người dùng kiểm tra, không phải nguồn đã xác minh độc lập.

## 3. Evidence Intelligence

- Source Research đã lưu tiếp tục là `research_items`, không thay đổi từ 0.3.
- Bảng `evidence_assessments` lưu source kind: `unknown`, `official_docs`, `primary`, `research`, `community`, `internal`; verification `unverified`, `checked`, `disputed`; publication date và ghi chú.
- Chỉ **owner** được đánh dấu source `checked` hoặc `disputed`; editor có thể bổ sung metadata chưa xác minh.
- Score 0–100 được tính từ tín hiệu có thể giải thích: HTTPS, tiêu đề, excerpt, nguồn được người dùng khai báo, publication date và verification state.
- `ResearchItem.created_at` là thời điểm lưu vào hệ thống, **không phải publication date**. Không dùng thời điểm này để giả làm độ mới của tài liệu.
- Hệ thống không tự kết luận một website `.gov`/`.edu` hoặc “official” chắc chắn đúng. Source kind do người dùng cung cấp, có thể sai.

## 4. Trace Suggestions

- Phân tích token trùng và cosine-like normalization, dành cho các cặp được phép: goal→requirement, requirement→phase, requirement→decision, research→requirement, requirement→document, decision→phase.
- Chỉ tạo **candidate** (`pending`) lưu trong bảng `trace_suggestions`; không lập trace link thật cho đến khi editor/owner bấm **Chấp nhận**.
- Khi chấp nhận phải validate lại cả hai node trong đúng project, loại quan hệ hợp lệ; link thật chỉ được tạo tối đa một lần theo unique key.
- Nếu link mới trỏ đến một phase đã được duyệt, phase quay về `draft`, tăng revision và xóa current approval (không xóa review history).
- Khi suggestion đã xử lý, không chấp nhận/từ chối lại và không tự tái tạo candidate từng bị từ chối.
- **Giới hạn:** scoring lexical không hiểu paraphrase/nghĩa trái. Mức 80% không có nghĩa xác suất liên kết đúng 80%; chỉ là điểm phù hợp theo thuật toán.

## 5. BMAD Workflow Gates

Lộ trình mặc định theo hướng BMAD-inspired:

| Thứ tự | Stage | Role | Điều kiện tối thiểu |
|---|---|---|---|
| 1 | `discovery` | Analyst | Có goal, có outcome, có goal được owner duyệt |
| 2 | `requirements` | Product Manager | Requirements, acceptance criteria và approvals của must/should |
| 3 | `solution_design` | Architect | Architecture/ADR/design document được duyệt + trace requirement→design/decision |
| 4 | `ux_design` | UX Designer | UX/design document approved, hoặc `profile.ui_required=false` |
| 5 | `spec_review` | Reviewer | Readiness 0.3 không có blocker và tất cả phases đã được duyệt |

Workflow: `not_started → submit → in_review → owner approve` hoặc `request_changes`; các stage không được bỏ qua nếu đã kích hoạt workflow. Chỉ owner có thể approve/request_changes. Nếu mục tiêu/tài liệu/yêu cầu thuộc giai đoạn bị thay đổi, checkpoint cũ được đánh dấu `stale` bằng fingerprint; có thể submit lại và owner duyệt lại.

**Review-required tùy chọn:** Wizard mới mặc định `quality_review_required=true` nên reviewed export cần có ít nhất một Rules/AI Review trùng fingerprint spec hiện tại. Dự án legacy không có flag vẫn được giữ hành vi cũ, có thể bật trong Project Settings.

**Backward compatibility:** dự án v0.3 chưa dùng Workflow Checkpoints không bị khóa `reviewed export` chỉ vì chưa mở workflow mới. Khi workflow đã được sử dụng, quality gate yêu cầu hoàn tất các stage.

**Lưu ý:** Đây là orchestrator dựa trên các vai trò BMAD, chưa cài đặt toàn bộ skill/command upstream. Chưa có multi-agent autonomous debate và không mặc định execute source code của dự án được lập kế hoạch.

## 6. API mới

Tất cả endpoint nằm dưới `/api/projects/{pid}/quality`, sử dụng session cookie, project ACL và CSRF khi ghi.

| Method | Path | Quyền | Tác dụng |
|---|---|---|---|
| GET | `/overview` | viewer+ | Reviews, findings, evidence score, candidates và workflow |
| POST | `/reviews/run` | editor+ | `{ "engine": "rules" | "model" }` |
| POST | `/findings/{fid}/status` | owner | `{ "status": "confirmed" | "resolved" | "dismissed" }` |
| POST | `/evidence/{rid}` | editor+ | Source kind, publication, verification, note; checked/disputed owner-only |
| POST | `/trace/suggest` | editor+ | `{ "limit": 35 }` (1–100) |
| POST | `/trace/{sid}/decision` | editor+ | `{ "action": "accept" | "reject" }` |
| POST | `/workflow/{stage}` | submit editor+, approve/request_changes owner | `{ "action": "submit" | "approve" | "request_changes", "notes": "..." }` |

Hệ thống ghi audit cho mọi thay đổi; các record mới có ID UUID và `project_id`.

## 7. Migration & Docker

Thêm `alembic>=1.15,<2` và hai revision:

- `0001_baseline`: khởi tạo tables legacy v0.3 trên DB mới; tiếp nhận DB v0.3 có sẵn mà **không xóa dữ liệu cũ**.
- `0002_quality`: tạo `spec_reviews`, `review_findings`, `evidence_assessments`, `trace_suggestions`, `workflow_checkpoints`.

Docker web thực hiện `python -m alembic -c /app/alembic.ini upgrade head` trong `entrypoint.sh` rồi mới chạy FastAPI. `SPECWEAVE_MIGRATIONS_MANAGED=true` tránh `create_all` tự tạo schema trước migration. Local direct-run vẫn cho phép `create_all` để smoke test/SQLite dev, nhưng production **phải dùng migrations**.

**Giới hạn migration hiện tại:** rev 0001/0002 dùng bảng metadata hiện tại với `checkfirst`, là đường bootstrap/adopt v0.3 để dữ liệu không mất; chưa phải migration history có khả năng tự rollback schema hay hỗ trợ thay đổi cột phức tạp. Phải backup trước khi nâng cấp.

```bash
# Khi đã đặt DATABASE_URL / biến .env cần thiết
python -m alembic -c alembic.ini current
python -m alembic -c alembic.ini upgrade head
```

### Backup trước khi nâng cấp (Compose PostgreSQL tích hợp)

```bash
# Chạy tại root project, Docker Compose và database hoạt động
sh scripts/backup-compose.sh ./backups
```

Script dừng web writer, cố gắng dừng Pi runner, dump PostgreSQL, archive volume project và ghi SHA256; tự khởi động lại web. Nếu Pi runner có chạy, khởi động lại bằng `docker compose --profile pi up -d pi-runner`. Đối với PostgreSQL ngoài, áp dụng `pg_dump` theo quy trình riêng và backup project volume tương ứng. **Backup phải được thử khôi phục trên môi trường staging** trước khi tin rằng có thể recovery.

## 8. Kiểm thử và giới hạn còn lại

- API + ACL + SSRF smoke tests cũ v0.3 được giữ nguyên; bổ sung tests cho new review gates, evidence metadata, cross-project isolation, trace approval, workflow stale, schema migration adoption và model output validation.
- UI script dùng JavaScript thuần, `textContent` khi hiện kết quả từ research/model để tránh chèn HTML.
- Chưa xác minh E2E Chromium screenshots, Docker live với model và SearXNG thực tế. Không coi cú pháp JS/pytest thay cho visual regression.
- Hiện chỉ có một HTTP web worker; login rate-limit và SSE cancel vẫn in-memory. Chưa có Redis/distributed locks đầy đủ, billing/token dashboard, store quota, OCR/DOCX/PDF parsing, Pi RPC custom tool parity và full upstream BMAD engine.
- Network SSRF protection ở mức application, cần firewall/egress network policy khi triển khai public.
- Dữ liệu tài liệu được đưa tới model mà project đang chọn khi chạy AI Review; người dùng phải tự cân nhắc yêu cầu bảo mật trước khi dùng remote provider.

## 9. Nguồn tham khảo

- BMAD Method: https://github.com/bmad-code-org/BMAD-METHOD
- Pi RPC: https://pi.dev/docs/latest/rpc
- Pi Extensions: https://pi.dev/docs/latest/extensions
- Alembic: https://alembic.sqlalchemy.org/
- PostgreSQL: https://www.postgresql.org/docs/current/backup-dump.html
- Playwright: https://playwright.dev/python/docs/intro
