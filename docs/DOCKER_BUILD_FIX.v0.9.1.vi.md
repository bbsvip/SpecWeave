# SpecWeave v0.9.1 — Docker Build Fix

## Lỗi đã sửa
Build `web` hoặc `review-worker` lỗi khi chạy `python -m playwright install --with-deps chromium`:

```text
playwright/driver/node: error while loading shared libraries: libatomic.so.1
exit code: 127
```

**Nguyên nhân:** image `python:3.12-slim-bookworm` thiếu thư viện runtime `libatomic.so.1`. Debian cung cấp thư viện này qua package `libatomic1`.

**Thay đổi:** cài `libatomic1` và `ca-certificates` bằng `apt-get` trong Dockerfile chính, **trước** bước `python -m playwright ...`. Không cần thay `Dockerfile.runner` hay database schema.

## Dùng với PostgreSQL ngoài

Kiểm tra `.env` có đầy đủ `DATABASE_URL`, `ENCRYPTION_KEY`, `BOOTSTRAP_TOKEN`; cấu hình Pi runner khi cần: `PI_RUNNER_URL=http://pi-runner:8090`, `PI_RUNNER_KEY` dài ít nhất 32 ký tự.

Lưu ý: tên bật OAuth chính xác là **`OPENAI_OAUTH_LOCAL_MODE=true`**, không phải `OPENAI_OAUTH_LOCAL`.

```sh
docker compose -f compose.external-db.yaml --profile pi build web review-worker pi-runner
docker compose -f compose.external-db.yaml --profile pi up -d
```

Hoặc chạy một lệnh:

```sh
docker compose -f compose.external-db.yaml --profile pi up -d --build
```

Không xóa các Docker volumes hoặc cơ sở dữ liệu trong quá trình sửa build.

## Phạm vi xác minh

Đã so sánh Dockerfile patch với source v0.9.0, kiểm tra ZIP và khớp tên biến môi trường. **Chưa chạy Docker build trong môi trường soạn patch.**
