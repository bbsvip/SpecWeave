# SpecWeave v1.0.0 — Pi Native Agent Engine

## Phạm vi của bản phát hành

Đây là **chuyển đổi thực thi agent**, không chỉ đổi tên `agent_backend`:

- Tất cả lượt chat trong project discussion → một Pi SDK `AgentSession` theo `(project_id, discussion_id)`.
- Tạo Guided Planning Question → Pi SDK phiên tạm (không có tools).
- Semantic Review, multi-role Analyst/Architect/QA/Synthesis, background review jobs → Pi SDK phiên tạm (không có tools).
- OpenAI OAuth model → Pi native credential/model runtime; OAuth login dùng provider Pi, credentials ở volume cục bộ.
- Local/OpenAI-compatible models → Pi `models.json` cấu hình động cho từng project/session và API key **chỉ trong bộ nhớ runtime**.
- Reasoning, text delta, tool execution, usage, cancel → stream sự kiện từ Pi SDK sang SSE; FastAPI không có agent/model caller riêng.
- Tất cả tools nghiệp vụ đi qua backend kiểm tra project, run HMAC capability, quyền và allowlist; Pi `bash`, `read`, `write`, `edit` mặc định bị tắt. Không cấp shell cho AI.
- Lưu phiên Pi vào `.system/pi-native/<discussion_id>/sessions` bên trong volume project, tách biệt theo UUID project, và giữ nguyên lịch sử SQL trước nâng cấp.

## Luồng thực thi

```text
Browser — SSE/JSON → FastAPI (Auth, ACL, DB, audit, planning, review)
                             │
                         Pi Transport (không inference)
                             │
                   pi-runner: Node Pi SDK Agent Host
                    ├─ ModelRuntime (OAuth hoặc API key)
                    ├─ SessionManager (persistent / in-memory)
                    ├─ AgentSession (Pi tạo message/compaction/tools)
                    └─ Custom tools → HTTP tool gateway → FastAPI
```

### Những thành phần đã loại bỏ

- Không còn `compatible_stream()` tự gọi `/chat/completions` trong FastAPI.
- Không còn Pi RPC subprocess tạo mới theo mỗi lượt chat.
- Không còn model HTTP client riêng trong AI Review hoặc Guided Planning.
- Không còn `Agent Backend` selector trong Project Settings: chỉ **Pi SDK Native**.
- Đã bỏ executable cũ `source/pi_runner.py` và `source/pi_tools.ts`. `openai_oauth_runtime.py` chỉ còn phương thức hồi quy OAuth lịch sử, không có model caller và không được import vào application runtime.

## Migration và tương thích

- **Alembic**: `0009_pi_native` (sau `0008_conversation_stream`) chuyển các `projects.agent_backend` cũ sang `pi`; không xóa bảng hay project data.
- **Conversation history**: tin nhắn và AgentRun cũ giữ nguyên trong PostgreSQL. Lần đầu Pi session mới chạy, backend bootstrap một đoạn history gần nhất từ database để tiếp tục ngữ cảnh; các lượt sau Pi tự quản lý history/compaction.
- **Project data**: vẫn ở volume `projects_data`; metadata phiên Pi tạo trong `.system/pi-native/`. Không xóa dự án, source documents hoặc bản nháp.
- **OAuth**: giữ nguyên volume `openai_oauth_local` và key Fernet của web. *Không xóa volume khi nâng cấp.*
- **Docker/GHCR**: `pi-runner` là service **bắt buộc** (không cần `--profile pi`). Cập nhật cả image `-web` và `-pi` cùng commit.
- **Rollback**: migration chuyển dữ liệu không mất mát nhưng marked one-way; rollback có kiểm soát đòi hỏi backup database và project/OAuth volumes trước nâng cấp.

## Triển khai dùng GHCR (PostgreSQL ngoài)

1. Backup PostgreSQL ngoài và volume project/OAuth theo quy trình của anh.
2. Đẩy source v1.0.0 lên repository GitHub. Workflow CI chạy test rồi build `-web`, `-pi` và publish GHCR.
3. Đảm bảo `.env` có `DATABASE_URL`, `ENCRYPTION_KEY` (giữ key cũ), `PI_RUNNER_KEY` (chuỗi ngẫu nhiên >=32 ký tự), `PI_RUNNER_URL=http://pi-runner:8090`, `SPECWEAVE_IMAGE_REPO`, `SPECWEAVE_IMAGE_TAG`.
4. Chạy:

```sh
docker compose -f compose.external-db.images.yaml pull
docker compose -f compose.external-db.images.yaml up -d --no-build
```

Đường OpenAI OAuth vẫn cần `OPENAI_OAUTH_LOCAL_MODE=true` nếu muốn đăng nhập ChatGPT được hỗ trợ. Pi OAuth phải được cấp quyền hợp lệ; không dùng chung tài khoản thuê bao giữa các user. Provider khác tiếp tục API key như trước.

## Kiểm thử trong môi trường biên soạn

- Unit/API/migration tests: mock Pi transport, không có model thật.
- Node host contract: tạo Pi SDK **giả lập**, kiểm tra `/complete`, `/run` SSE thinking/tool/usage, HMAC header, scope và OAuth off.
- OAuth Node contract: mô phỏng `models.login`, xác nhận device ID ổn định, vault, và không lộ credential.
- Không thể xác nhận Pi package runtime sống vì môi trường không tải npm; GitHub Actions build có preflight import SDK và sẽ phát hiện sai API/export sớm.
- Không xác minh Docker runtime, trình duyệt Chromium, Cloudflare Tunnel, proxy, token refresh hoặc model thật theo thống nhất với anh.

## Những giới hạn cần lưu ý

- **Một Pi Host process** cho mỗi bộ volume sessions, chưa hỗ trợ nhiều replicas Pi cùng sửa một session. FastAPI có lease cho lượt chat; Pi Host có mutex trong tiến trình.
- SSE không có replay bền vững từng event ở Pi Host; FastAPI vẫn lưu message/usage sau hoàn thành. Khi connection mất giữa chừng, agent có thể cần can thiệp để resume chính xác.
- Phiên Pi mới không replay toàn bộ lịch sử cũ vô hạn; chỉ bootstrap một cửa sổ tin nhắn gần nhất theo cơ chế đã kiểm soát kích thước.
- Tool results hiển thị khi Pi trả sự kiện; những trường mà provider không cung cấp không thể tự biến thành chính xác.
- OAuth login vẫn có subprocess riêng cho tương tác browser/code sử dụng **Pi provider auth**; subprocess này **không chạy agent, model hay tool loop**.
- `Pi SDK` package được ghim `@earendil-works/pi-coding-agent@1.5.2`. Nếu thư viện thay đổi, preflight build và hợp đồng host cần được đối chiếu lại với version thực tế.

## References

- Pi SDK: https://pi.dev/docs/latest/sdk
- ThinkRail: https://github.com/JetBrains/thinkrail
- Models configuration: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models-configuration.md
