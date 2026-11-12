# SpecWeave v0.9.7 — OpenAI Sign in with ChatGPT: Pi installation identity

## Lỗi đã xác định

Ở v0.9.6, `source/openai_oauth.mjs` gọi `models.login('openai', 'oauth', interaction)` mà **không** cung cấp `LoginOptions.getDeviceId`. Phiên bản Pi 1.5.2 yêu cầu UUID ổn định cho luồng Sign in with ChatGPT. Luồng thất bại trước khi hiển thị URL ủy quyền, backend chỉ ghi `OAUTH_RUNTIME_FAILURE`.

Cách sửa dựa trên API của Pi/ThinkRail: **Pi vẫn sở hữu OAuth provider và credentials**, host chỉ tổ chức UI. SpecWeave giờ gọi:

```javascript
await models.login('openai', 'oauth', interaction, {
  getDeviceId: () => deviceId,
  agentName: 'SpecWeave',
});
```

`deviceId` được đọc hoặc tạo một lần trong **`/auth-local/.pi/agent/settings.json`** (cùng vị trí global settings của Pi), được lưu bền vững trong volume OAuth. Không phát sinh ID mới ở các lần kết nối sau hoặc restart container.

## Thay đổi

- `source/pi_device_id.mjs` mới: đọc/reuse Pi UUID, tạo UUID v4 với `randomUUID`, ghi file an toàn bằng tempfile/rename và mode `0600`; bảo tồn cấu hình Pi đang có, từ chối ghi đè UUID sai hoặc JSON bị hỏng.
- `source/openai_oauth.mjs`: truyền `getDeviceId` vào `models.login`, thêm các mã `OAUTH_DEVICE_ID`, `OAUTH_SETTINGS_INVALID` và trạng thái `device_identity`. Không in raw exception/token lên log.
- `source/openai_oauth_runtime.py`: log gồm các trường đã chuẩn hóa `code`, `phase`, `hint`, `error_type`; có thể phân biệt lỗi thiếu thiết bị và file settings hỏng. API diagnostics trả `device_id_ready` (boolean, không lộ UUID).
- `source/static/js/openai-oauth.js`: hiển thị các giai đoạn chờ browser/callback và trạng thái ID trong chẩn đoán.
- `Dockerfile.runner`: COPY helper mới; kiểm tra method login + adapter lúc build.
- CI: chạy Node contract tests cho luồng OAuth và trạng thái thiết bị.

Không có thay đổi database, không cần migration. Các provider API key khác không bị thay đổi.

## Kiểm thử offline (không dùng tài khoản OpenAI)

`node --test tests/test_openai_oauth_contract.mjs` chạy login broker **thật** với Pi provider **giả lập** để kiểm tra:

1. Có cung cấp `getDeviceId`, phát authorization URL và callback prompt, lưu vào Pi credential vault.
2. Reconnect vẫn dùng cùng một ID; settings có sẵn không bị mất nội dung.
3. UUID sai được phát hiện, không sửa ngầm.
4. JSON settings hỏng được phát hiện và không làm lộ nội dung nhạy cảm.

Kết quả tại môi trường build: 4/4 bài Node đạt; 82/82 bài Python đạt. Không có kiểm thử token exchange thật, Docker runtime hay Cloudflare Tunnel. Việc cấp quyền OAuth còn phụ thuộc tài khoản và dịch vụ OpenAI.

## Cách cập nhật trên GHCR

1. Đẩy source v0.9.7 lên GitHub, để Actions publish **image mới của Pi** và Web.
2. Nếu dùng tag ghim, cập nhật `SPECWEAVE_IMAGE_TAG` sang tag image mới đã publish.
3. Trên máy chủ, **giữ nguyên** `.env`, `ENCRYPTION_KEY`, database và volume `openai_oauth_local`.
4. Chạy:

```bash
docker compose -f compose.external-db.images.yaml --profile pi pull
docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build
```

5. Vào Providers & Models → OpenAI → **Chẩn đoán**. Nếu trước Connect báo `Stable Pi installation identity: NOT CREATED`, đó là bình thường: Connect sẽ tạo UUID lần đầu.
6. Chọn Connect / Reconnect. Sau bước khởi tạo phải thấy **authorization link**. Với browser ở máy khác, callback có thể không truy cập được `127.0.0.1` trong container: chờ Pi yêu cầu mã rồi dán URL chuyển hướng cuối vào UI SpecWeave. **Không dán callback URL/token vào chat, issue hoặc log công khai.**
7. Nếu vẫn lỗi, lấy các trường `code`, `phase`, `hint`, `error_type` từ log `pi-runner` **mà không gửi URL hoặc token**:

```bash
docker compose -f compose.external-db.images.yaml --profile pi logs --tail=100 pi-runner
```

Lỗi nếu còn xuất hiện ở `phase=provider_login` sau khi đã có ID thì có thể do mạng, callback, quyền tài khoản hoặc Pi/upstream OpenAI. Cần nhìn mã lỗi cụ thể, không đoán.

## Tham khảo

- https://github.com/JetBrains/thinkrail (Pi owns credentials/models; host owns GUI)
- https://github.com/earendil-works/pi/blob/main/packages/ai/src/models.ts (`Models.login(..., options)`)
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/test/settings-manager.test.ts (`getOrCreateDeviceId`, settings.json)
- https://github.com/agegr/pi-web/releases (đã sửa lỗi `requires a device ID` tương tự)
- https://pi.dev/docs/latest/providers (manual URL/code khi callback localhost không truy cập được)
