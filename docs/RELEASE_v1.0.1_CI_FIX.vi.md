# SpecWeave v1.0.1 — GitHub Actions Pi Host test hotfix

## Lỗi

GitHub Actions `validate > JavaScript checks` thất bại sau khi 89 Python tests đã pass:

```text
Error: EACCES: permission denied, mkdir '/runner'
    at ... pi_host.mjs:273
```

`pi_host.mjs` tạo resource directory tại `/runner/safe-resources` theo layout Docker. GitHub-hosted runner chạy test trực tiếp ở tài khoản không có quyền ghi `/runner`, vì vậy Pi Host subprocess thoát trước khi endpoint `/health` sẵn sàng.

## Bản vá

- `source/pi_host.mjs`: cho phép cấu hình `SPECWEAVE_PI_SAFE_RESOURCES_DIR` cho môi trường test, mặc định vẫn là `/runner/safe-resources` trên Docker. Từ chối đường dẫn trỏ vào `PROJECTS_ROOT` để cách ly tài nguyên Pi với file dự án.
- `tests/test_pi_native_host.mjs`: tạo sandbox resource directory bên trong `mkdtemp()` và truyền biến môi trường cho tiến trình Node con. Không yêu cầu root/sudo.
- Không thay đổi Dockerfile, Compose, database schema, Pi session path hoặc volume OAuth.

## Kiểm thử

- `node --test tests/test_openai_oauth_contract.mjs tests/test_pi_native_host.mjs`: **5/5 passed** (Node 22.16.0, local).
- `node --check`: mọi file JS được CI kiểm tra đều hợp lệ.
- Kiểm tra cú pháp bốn Compose và GitHub workflow: thành công.
- `python -m pytest -q tests`: in ra **89 passed in 31.13s**, nhưng lệnh tại môi trường kiểm thử hiện tại chưa thoát sạch trước timeout; GitHub Actions trong log người dùng đã hoàn thành 89/89 và thoát bình thường.
- Chưa chạy GitHub Actions publish/Docker build thực tế; cần xác nhận sau khi đẩy mã nguồn.

## Triển khai

Giải nén **patch-only** đè vào thư mục gốc repository, nơi có `source/` và `tests/`; hoặc thay bằng full source v1.0.1. Sau đó commit và push lên `main`. Workflow sẽ chạy validate trước rồi mới build/publish Docker image.

Không cần thay `.env`, không cần migration và không cần xóa PostgreSQL, project volume hay OAuth vault.
