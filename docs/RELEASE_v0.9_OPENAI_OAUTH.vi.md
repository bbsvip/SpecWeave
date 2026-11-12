# SpecWeave v0.9.0 — OpenAI-only OAuth qua Pi local runtime

**Ngày:** 2026-11-12  
**Phạm vi:** Chỉ thêm kết nối tài khoản OpenAI/ChatGPT khi nhà cung cấp cho phép, qua thư viện OAuth của Pi; giữ nguyên toàn bộ provider API-key/OpenAI-compatible. Không thêm OAuth GitHub, Google, Anthropic hoặc bất kỳ provider nào khác.

## 1. Cơ chế và giới hạn quyền sử dụng

- Đây là **đăng nhập tài khoản OpenAI để chạy model trong SpecWeave**, không phải Google/Microsoft SSO để đăng nhập chính web app.
- OpenAI Sign in with ChatGPT **không tự động được cấp quyền cho mọi ứng dụng bên thứ ba**. Người triển khai phải xác nhận phương thức/ứng dụng có quyền sử dụng theo tài liệu và điều khoản OpenAI hiện hành. Chỉ vận hành khi OpenAI cho phép.
- OpenAI yêu cầu persistent authentication token nằm **tại runtime local do người dùng kiểm soát**, không ở server managed/remote. Chế độ OAuth **mặc định tắt** và thích hợp với **một người sở hữu trên máy riêng**. Không bật trên hosting dùng chung, nhiều chủ tài khoản hoặc máy không do người dùng kiểm soát.
- Không chia sẻ gói ChatGPT với người dùng khác. Chủ tài khoản phải trực tiếp gọi chat, guided planning, review; background review chỉ chạy nếu do chính chủ tài khoản khởi tạo. Người dùng khác không thể chọn hay gọi OAuth model của chủ dự án.
- Disconnect xóa credential **trên máy local**, không tự thu hồi ủy quyền phía OpenAI. Nếu cần thu hồi hoàn toàn, vào cài đặt bảo mật/kết nối ở tài khoản OpenAI nếu có, theo cơ chế OpenAI hiện hành.
- Không đăng ký hoặc giả lập OAuth client riêng, không yêu cầu mật khẩu/cookie ChatGPT; Pi phụ trách flow đăng nhập, PKCE, token refresh theo capability hiện tại.

Nguồn: https://openai.com/policies/sign-in-with-chatgpt-terms/ và https://github.com/earendil-works/pi/blob/main/packages/ai/README.md. Hãy kiểm tra lại chính sách/quyền sử dụng trước khi bật chế độ này.

## 2. Kiến trúc triển khai

```
Browser (chỉ admin chủ tài khoản)
  │ CSRF + session; code redirect chỉ đi qua POST khi Pi yêu cầu
  ▼
FastAPI web (các endpoint /api/openai/oauth/*)
  │ HMAC/PI_RUNNER_KEY trên mạng nội bộ; không trả credential
  ▼
Pi runner duy nhất (OPENAI_OAUTH_LOCAL_MODE=true)
  ├── openai_oauth.mjs — gọi thư viện pi-ai OAuth chính thức
  ├── ~/.pi/agent/auth.json — credential local, file mode 0600
  ├── pi --provider openai --mode rpc — chat và tools có scope
  └── pi --provider openai --mode rpc --no-builtin-tools — planning/review
```

- Credential chỉ nằm trong `openai_oauth_local` volume của runner. PostgreSQL chỉ có `Provider(kind='openai-oauth',owner_id,enabled,base_url='pi://openai')` và `Model`; `encrypted_key` rỗng.
- Runner truy cập `auth.json` bằng HOME riêng; không mount volume OAuth sang `web` hoặc `review-worker`.
- `PI_RUNNER_KEY` phải dài tối thiểu 32 ký tự; không expose port 8090 ra Internet. Dùng mạng nội bộ Docker/LAN có kiểm soát.
- Credential được làm mới bởi Pi; thực thi OAuth trong runner chỉ một worker và được tuần tự hóa để tránh hai lượt làm mới/đăng nhập ghi đè nhau. Không chạy đồng thời nhiều Pi runner sử dụng cùng vault.
- OAuth không sử dụng `api_key`/`base_url` tùy chỉnh. Các provider khác vẫn được dùng theo API-key như cũ.

## 3. Bật chế độ trên host cá nhân

1. Sao lưu PostgreSQL và volume dữ liệu SpecWeave; cập nhật source lên v0.9. Giữ lại `.env` và các volume cũ.
2. Trong `.env`, cấu hình:

```ini
OPENAI_OAUTH_LOCAL_MODE=true
PI_RUNNER_URL=http://pi-runner:8090
PI_RUNNER_KEY=REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS
```

3. Dùng `docker compose --profile pi up -d --build`. Với PostgreSQL ngoài, dùng `docker compose -f compose.external-db.yaml --profile pi up -d --build`.
4. Mở `/settings/providers` dưới tài khoản Admin sẽ là **chủ duy nhất** của kết nối OpenAI OAuth. Chọn **Connect / Reconnect**.
5. Mở URL OpenAI được trả về; nếu OAuth flow yêu cầu redirect về localhost của máy chủ, dán **URL cuối** hoặc mã được yêu cầu vào giao diện; **không nhập mật khẩu ChatGPT hoặc cookie vào SpecWeave**.
6. Sau khi Pi báo hoàn tất, provider OpenAI OAuth được kích hoạt. Dùng **Fetch models** (catalog Pi) để chọn model hỗ trợ; không thể nhập ID tùy ý ngoài catalog này.
7. Trong Project Settings, chủ dự án chọn OpenAI OAuth model, rồi sử dụng Chat, AI đề xuất, Semantic/BMAD Review. Các tác vụ OAuth đều đi qua Pi, không chuyển token vào request của trình duyệt hoặc DB.
8. Nếu cần xóa kết nối, chọn **Disconnect**. Các model cũ được giữ trong DB nhưng provider bị disabled, không gọi lại trước khi kết nối lại.

**Lưu ý:** Nếu `PI_RUNNER_URL` để trống hoặc key quá ngắn, web sẽ trả 503. Nếu OAuth chưa được OpenAI cho phép, Pi báo lỗi hoặc không cấp scope, không có cách bypass hợp lệ trong ứng dụng.

## 4. API (owner/admin only)

| Method | Route | Chức năng |
|---|---|---|
| GET | `/api/openai/oauth/status` | Trạng thái có credential local, enabled, owner-bound; không trả token |
| POST | `/api/openai/oauth/start` | Tạo login state trong Pi runner; tạo disabled owner-bound provider |
| GET | `/api/openai/oauth/login/{login_id}` | Poll sự kiện đã lọc, `offset` chống gửi trùng |
| POST | `/api/openai/oauth/login/{login_id}/answer` | Chuyển callback code/URL tới Pi nếu cần; không lưu DB |
| POST | `/api/openai/oauth/cancel` | Dừng phiên đăng nhập |
| POST | `/api/openai/oauth/activate` | Bật provider nếu Pi local ghi nhận credential |
| POST | `/api/openai/oauth/disconnect` | Xóa OAuth credential local và disable provider |
| GET | `/api/providers/{id}/discover` | Với OAuth: danh sách model thực tế từ Pi |
| POST | `/api/providers/{id}/models` | Với OAuth: chỉ chấp nhận ID từ Pi catalog |

Tất cả mutation trong web yêu cầu CSRF/session; relay nội bộ yêu cầu `PI_RUNNER_KEY` đủ mạnh; Pi không exposed authentication tokens trong JSON công khai. Browser không có `PI_RUNNER_KEY`.

## 5. Tác vụ hỗ trợ

- **Chat:** Model OpenAI OAuth tự chuyển sang Pi RPC ngay cả khi project đang chọn backend compatible; custom tool bridge giới hạn theo project/run.
- **Guided Planning:** Dùng Pi one-shot không có tool để tạo gợi ý; model owner-bound.
- **Semantic Review (AI):** Pi one-shot qua Model Router; bắt buộc chủ tài khoản chạy.
- **Adversarial/BMAD Review:** Inline và background queue đều kiểm tra `job.requester_id`, chủ dự án và chủ credential; các model OAuth chọn riêng theo vai trò vẫn được kiểm soát.
- **Đổi owner dự án:** Tự bỏ lựa chọn OpenAI OAuth model của chủ cũ; owner mới không thể dùng token chủ cũ.
- **Provider khác:** Không thay đổi OAuth, không thay đổi các API-key flow trước đó.

## 6. Database migration

- Head Alembic mới: `0006_openai_oauth_owner`, parent `0005_multi_model_review`.
- Bổ sung `providers.owner_id` nullable để giữ nguyên provider API-key có sẵn.
- Không có cột access token, refresh token hay authorization code.
- Database schema được nâng qua migration khi ứng dụng start theo Compose; nhớ backup trước nâng cấp.

## 7. Kiểm thử và phạm vi chưa xác minh

- Automated backend smoke tests bao gồm: disabled-by-default, login owner-scoped, không ghi token DB, ACL dự án và chuyển owner, whitelist URL, disconnect giữ nguyên credential provider khác, xác nhận chỉ cho model từ Pi catalog, và quyền runner.
- Chỉ xác minh Python/JS syntax, API mock, unit/integration tests trên SQLite, migration/schema ở mức code và YAML parsing. Không gọi OAuth thật, không chạy Docker/Chromium/LLM thật theo yêu cầu của người vận hành.
- OAuth khả dụng thực tế phụ thuộc **phiên bản Pi được cài, model và account scope, chính sách OpenAI và cấu hình host**. Không cam kết mọi tài khoản Plus/Pro sẽ đăng nhập hoặc gọi được một model bất kỳ.
- Không có nhiều người kết nối OpenAI OAuth độc lập vào cùng một web instance. Kiến trúc local một chủ là cố ý để tuân thủ ranh giới credential và không chia sẻ subscription.

## 8. Checklist nghiệm thu trên host do anh thực hiện

- [ ] Đúng Pi version hỗ trợ OpenAI OAuth (`@earendil-works/pi-ai`, `@earendil-works/pi-coding-agent`).
- [ ] Host/vault thật sự do người dùng kiểm soát, không phải managed/shared remote deployment.
- [ ] OpenAI cho phép ứng dụng và scope yêu cầu; flow được chấp thuận.
- [ ] Bật flag, cấu hình key mạnh, kiểm tra runner chỉ nghe mạng nội bộ.
- [ ] Connect, browser callback/manual code, reconnect, disconnect; không có token trong DB, logs, browser network responses hoặc exports.
- [ ] Fetch model và thử chat, Guided Planning, Semantic Review, 4-role background review.
- [ ] Test token hết hạn, tự làm mới, revoke phía OpenAI, Pi restart, worker restart và model unavailable.
- [ ] User khác không thể chọn model OAuth, khởi tạo background review hay đọc credential; chuyển owner phải gỡ model.
- [ ] Backup/restore chỉ project+DB, **không chuyển vault OAuth** sang host không do chủ sở hữu kiểm soát.
