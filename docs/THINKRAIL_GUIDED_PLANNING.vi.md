# SpecWeave — Guided Planning / Decision Briefs

**Phiên bản áp dụng:** 0.2.0 · **Ngày:** 2026-11-11 · **Trạng thái:** triển khai bản đầu (alpha), còn các nâng cấp trong backlog.

## 1. Nghiên cứu ThinkRail và phương pháp kết hợp

Nguồn chính:

- [JetBrains ThinkRail](https://github.com/JetBrains/thinkrail): IDE dựa trên Pi, gồm interactive `ask_user_question`, nút *Discuss*, inline diagrams/option comparisons, spec graph và kế hoạch có các bước duyệt.
- [ThinkRail goal-and-requirements](https://github.com/JetBrains/thinkrail/blob/main/goal-and-requirements.md): specs là nguồn sự thật duy trì qua các phiên; yêu cầu approval trước tool có tác động dữ liệu; tools có UI độc lập.
- [ThinkRail GUI overview](https://thinkrail.ai/blog/gui-overview/): interactive cards, mockups trước/sau và kế hoạch có review từng bước.
- [Superpowers brainstorming](https://github.com/obra/superpowers/blob/main/skills/brainstorming/SKILL.md): chỉ hỏi khi câu trả lời thay đổi thiết kế và không thể suy ra từ project; đề xuất phương án hợp lý và lý do trước khi hỏi người dùng.
- [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD): các vai trò Analyst/PM/Architect/UX, elicitation và giao tài liệu có cấu trúc.

**Kết luận:** ThinkRail là cảm hứng về cách tương tác (structured questions, option cards, spec continuity), không nên bê nguyên IDE, Bun/React hoặc cho phép shell vào SpecWeave. Vì yêu cầu sản phẩm khác nhau, SpecWeave lưu decision brief trong PostgreSQL, dùng giao diện Jinja2/JavaScript thuần và phát triển planner theo BMAD. Không sao chép source ThinkRail.

### Cơ chế ba tầng

1. **Context discovery:** đọc hồ sơ dự án, những quyết định đã phê duyệt, câu trả lời gần đây, research snippets thuộc cùng project. Không hỏi trùng.
2. **Proposal-first elicitation:** khi còn một quyết định quan trọng, tạo *decision brief* gồm 2–4 phương án, ưu/nhược, khuyến nghị, lý do, tác động và giả định chưa xác minh. Cần đánh dấu chính xác một lựa chọn khuyến nghị. Không ép người dùng tự nghĩ ra lời giải từ số không.
3. **Human-in-the-loop:** người dùng có thể chọn, kết hợp nhiều lựa chọn nếu phù hợp, trả lời riêng, thảo luận để yêu cầu AI chứng minh, hoặc hoãn. Không tự duyệt spec/decision.

Nếu bối cảnh đã đủ, agent nên đề xuất một hướng thiết kế và tiếp tục soạn bản nháp, **không cưỡng ép phỏng vấn**. Chỉ hỏi các vấn đề có thể làm thay đổi phạm vi, kiến trúc, rủi ro, UX hoặc tiêu chí nghiệm thu.

## 2. Đã code trong v0.2.0

- `PlanningQuestion` lưu `project_id`, `discussion_id`, trạng thái, 2–4 phương án, phương án khuyến nghị, reason/impact, assumptions, evidence links và câu trả lời.
- `planning.py` chuẩn hóa JSON nghiêm ngặt: số phương án, recommendation index, pros/cons, độ dài và trùng câu hỏi pending.
- Tool `propose_planning_question` của OpenAI-compatible backend, cho agent tạo card qua tool calling khi đủ điều kiện.
- API `/planning/questions/suggest` sinh decision brief bằng chat completion JSON từ provider/model đã chọn, **không cần Pi custom tools**. Ngay cả khi Project Settings chọn Pi RPC, nút AI đề xuất vẫn gọi endpoint OpenAI-compatible do người dùng đã cấu hình (không phải Pi OAuth session).
- GET questions và POST answer/defer với CSRF và project-level ACL. Câu trả lời được gắn thành user Message để agent tiếp tục với context đúng.
- UI decision brief trong trang Discussion: chọn một/nhiều; input trả lời tùy chỉnh và ghi chú; *Thảo luận thêm* (soạn prompt chưa gửi); *Tạm hoãn*.
- `plan_prompt` nạp answered/deferred questions gần nhất và research snippets thuộc dự án, tránh hỏi lại. Snippets là dữ liệu không đáng tin, không được xem như instruction.
- Chỉ chấp nhận URL citation đã tồn tại trong bảng `research_items` của **chính dự án** (source allowlist). Nếu không có nguồn, phải ghi giả định hoặc nêu rõ dựa trên hồ sơ.
- Export có `planning/decision-briefs.json` và `planning/decision-briefs.md`, kể cả các lựa chọn đang chờ xử lý.

### API

| Method | Path tương đối từ `/api/projects/{pid}` | Mục đích |
|---|---|---|
| GET | `/planning/questions?discussion_id={did}` | Câu hỏi pending + lịch sử gần đây |
| POST | `/planning/questions/suggest` | AI tạo decision brief; request `{ "discussion_id": "..." }` |
| POST | `/planning/questions/{qid}/answer` | Chọn hoặc hoãn; request `{ "action": "answer", "selected_ids": ["o1"], "custom_answer": "", "note": "" }` |

Auth/session/CSRF kế thừa từ FastAPI app. Non-owner có quyền editor mới được trả lời/suggest. Một discussion có tối đa một câu hỏi pending trong luồng bình thường.

### Nguyên tắc dữ liệu

- **AI recommended != user selected != approved Decision**.
- `answered` chỉ là câu trả lời xác nhận trong phiên thảo luận; nội dung đi vào context, **không tạo Decision approved**.
- Dữ liệu câu hỏi của một dự án không được lẫn vào dự án khác, kể cả trong export và project memory.
- Không lưu bất kỳ nguồn URL nào do model bịa; source phải trùng với research URL trước đó của dự án.
- Input của LLM hiển thị bằng `textContent`/DOM nodes, không render HTML tùy ý.

## 3. Kiểm thử

Trong quá trình triển khai:

- Pytest: **11/11** đạt, gồm auth, ACL, file, doc/export, chat SSE giả lập, project isolation, câu hỏi pending/trùng, option validation, answer/defer và gợi ý với model HTTP giả lập.
- Python `compileall` và JavaScript `node --check`: đạt.
- **Chưa** kiểm tra inference thật, performance/long-context, Chromium screenshot/visual regression, Pi RPC production. Cần acceptance test trên máy có Docker/Chromium/provider.

## 4. Roadmap tiếp theo (chưa code)

1. **Provider robustness:** structured output qua nhiều provider khác nhau, schema validation/retry, streaming và timeout/cancellation có request ID.
2. **Plan canvas:** project-wide phase graph BMAD; từng bước có trạng thái draft/review/approved, dependency và reviewer; không nhầm lịch sử chat với kế hoạch triển khai.
3. **Requirement coverage engine:** chỉ hỏi gaps từ PRD và kiểm tra traceability `goal → FR/NFR → ADR → API/data → acceptance`; validation trước export.
4. **Evidence ranking:** nghiên cứu có uy tín/recency, phân biệt external facts và product assumptions; lý do đề xuất phải có evidence hoặc marked assumption.
5. **Visual comparison:** vẽ wireframe/diagram cho lựa chọn UI/UX, review before/after và comment neo vào từng quyết định.
6. **Pi custom tool parity:** tùy chọn tích hợp Pi extension interactive tools qua mailbox RPC được kiểm soát; cho phép từng tool mà không bật shell.
7. **Review loop:** human feedback, lịch sử sửa đề xuất, diff của spec và kiểm tra mâu thuẫn khi một lựa chọn làm thay đổi decisions khác.
8. **Database migrations / concurrency:** Alembic, unique partial index cho câu hỏi pending, worker-locks phân tán, QA/e2e Playwright trên Docker.

## 5. Checklist nghiệm thu thủ công

1. Tạo dự án và chọn model có OpenAI-compatible `/chat/completions`.
2. Vào Discussion → **AI đề xuất**; kiểm tra có một decision brief với lựa chọn khuyến nghị và ít nhất hai lựa chọn thực tế.
3. Bấm **Thảo luận thêm**: composer được điền sẵn nhưng không tự gửi và câu hỏi chưa bị đánh dấu answered.
4. Chọn một lựa chọn, ghi chú/điều chỉnh, xác nhận; reload trang và kiểm tra lịch sử cùng context trong cuộc trò chuyện.
5. Bấm **AI đề xuất** lần nữa, xác nhận AI không hỏi lại nguyên văn điều đã trả lời.
6. Hoãn một câu hỏi; kiểm tra pending biến mất nhưng audit/export còn lưu lịch sử.
7. Tạo dự án thứ hai, xác minh không thể đọc/ghi card dự án thứ nhất bằng API.
8. Export ZIP và kiểm tra `planning/decision-briefs.md` và `.json`.

