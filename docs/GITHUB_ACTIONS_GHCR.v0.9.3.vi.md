# SpecWeave v0.9.3 — Build Docker image bằng GitHub Actions / GHCR

## Mục tiêu

Mọi bước tốn thời gian như cài Python dependencies, Playwright Chromium và Pi/NPM được thực hiện trên **GitHub Actions runner**, không chạy trên host triển khai. Source vẫn dùng `source/` tách khỏi môi trường ảo, không có frontend Node.js.

**Hai image cho ba service ứng dụng:**

| Service Docker | Image |
|---|---|
| `web` | `ghcr.io/<owner>/<repository>-web:<tag>` |
| `review-worker` | Giống `web` (không build image riêng) |
| `pi-runner` (profile `pi`) | `ghcr.io/<owner>/<repository>-pi:<tag>` |
| `db` (Compose nội bộ) | `postgres:17-alpine` từ registry upstream |

`<owner>/<repository>` **phải viết chữ thường**. Ví dụ repo `https://github.com/ExampleOrg/specweave` cho image `ghcr.io/exampleorg/specweave-web:main`.

## Các file mới

- `.github/workflows/docker-publish.yml`: kiểm thử source trước, build đồng thời 2 image, push GHCR bằng `GITHUB_TOKEN`, cache tách theo image qua BuildKit (`type=gha`).
- `compose.external-db.images.yaml`: PostgreSQL ngoài, **không có `build:`**; giữ nguyên Compose project name `specweave-external` và các volume cũ.
- `compose.images.yaml`: PostgreSQL bên trong, project name `specweave`, cùng volume/network của `compose.yaml` cũ.
- `scripts/deploy-images.sh`: tiện ích `pull` rồi `up --no-build`.
- `tests/test_image_delivery.py`: đối chiếu tính tương đương cấu hình build/image, workflow tagging và cache.

## 1. Đưa source lên GitHub

Repository phải chứa `Dockerfile`, `Dockerfile.runner`, `compose*.yaml`, `.github/workflows/`, `source/`, `migrations/` ở **thư mục gốc**. Nếu hiện đặt source bên trong một thư mục con của repository, chuyển nội dung đó lên root hoặc điều chỉnh `context` workflow.

Không commit `.env`, file SQLite, `venv`, `.venv`, `node_modules`, Docker volume hoặc OAuth credential. GHCR nhận được **image** chứ không nhận các bí mật runtime.

## 2. Kích hoạt workflow

Đẩy commit lên `main` (hoặc `master`) để tự build và publish. Workflow cũng chạy khi đẩy tag dạng `v*`, ví dụ `git tag v0.9.3 && git push origin v0.9.3`. Có thể chạy thủ công trong tab **Actions → Build and publish SpecWeave images → Run workflow**.

**Không cần cấu hình PAT/secret để GitHub Actions push GHCR**: job có `packages: write` và sử dụng `secrets.GITHUB_TOKEN` có sẵn. Nếu org/repository khóa quyền GitHub Actions hoặc quyền package, admin cần bật quyền tương ứng trong Settings và đảm bảo package cho phép repository này publish.

Mỗi lần build xuất các tag tương ứng:

- `:main` (hoặc `:master`) — bản mới nhất của nhánh.
- `:latest` — chỉ khi chạy trên **nhánh mặc định**.
- `:v0.9.3` — khi push tag Git `v0.9.3`.
- `:sha-xxxxxxx` — chỉ đúng commit tương ứng.

Chỉ cập nhật host **sau khi cả hai build trong workflow đều thành công**. Nếu một component publish thất bại, không triển khai tag nhánh (`main`) cho đến khi rerun thành công; nên dùng tag `v*` hoặc `sha-*` để có bản phát hành nhất quán. Ghim tag `v*` hoặc `sha-*` để có triển khai dễ kiểm soát. Hiện workflow tập trung `linux/amd64` để tránh build/emulation ARM64 tốn thời gian; server ARM64 cần bổ sung platform trước.

### Lưu ý GHCR Private/Public

Packages mới thường có visibility mặc định theo GitHub. Nếu image private, trên **máy triển khai** cần token GitHub (classic PAT có `read:packages`, được SSO authorize nếu org yêu cầu):

```sh
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io \
  -u "$GITHUB_USERNAME" --password-stdin
```

Không lưu PAT trong `.env` của SpecWeave; chỉ dùng để đăng nhập Docker registry trên host. Nếu image public, thường không cần đăng nhập để pull. Không cần Docker Hub.

## 3. Cấu hình `.env` cho host triển khai

Giữ nguyên tất cả biến hiện có (`DATABASE_URL`, `ENCRYPTION_KEY`, `PI_RUNNER_KEY`, `OPENAI_OAUTH_LOCAL_MODE`, `SEARXNG_URL`, ...), rồi thêm:

```ini
SPECWEAVE_IMAGE_REPO=ghcr.io/<github-owner>/<github-repository>
SPECWEAVE_IMAGE_TAG=main
```

Thay `<...>` bằng tên thật, viết chữ thường. Nếu vừa tạo release tag Git `v0.9.3`, thay bằng `SPECWEAVE_IMAGE_TAG=v0.9.3`.

**Không đổi tên project Compose hoặc volume.** Các file Compose image đã giữ đúng project name từ file build tương ứng. Điều này giúp tránh khởi tạo nhầm `projects_data` / `openai_oauth_local` mới khi chuyển từ build local sang image pull.

### Trường hợp anh đang dùng PostgreSQL ngoài và Pi

```sh
docker compose -f compose.external-db.images.yaml --profile pi pull
docker compose -f compose.external-db.images.yaml --profile pi up -d --no-build
```

Hoặc gọn hơn:

```sh
bash scripts/deploy-images.sh external pi
```

Không dùng thêm `--build`. Lệnh trên chỉ tải image và chạy lại container nếu image/config thay đổi. PostgreSQL ngoài không bị sửa/xóa; migrations vẫn được `entrypoint.sh` thực hiện như trước khi `web` khởi động.

### Không dùng Pi

```sh
docker compose -f compose.external-db.images.yaml pull
docker compose -f compose.external-db.images.yaml up -d --no-build
```

### Dùng PostgreSQL nội bộ

```sh
docker compose -f compose.images.yaml --profile pi pull
docker compose -f compose.images.yaml --profile pi up -d --no-build
```

### Triển khai bằng Portainer

Trong **Stacks**, sử dụng nội dung `compose.external-db.images.yaml` (hoặc bản internal DB), thêm đầy đủ environment variables cần thiết trong phần Stack environment hoặc thông qua `.env` được quản lý riêng. Chú ý `SPECWEAVE_IMAGE_REPO` và `SPECWEAVE_IMAGE_TAG`, cùng các secrets ứng dụng. GHCR private cần cấu hình registry credential ở Portainer trước khi deploy; chỉ cấu hình image, **không** bật build trong stack. Pi có profile `pi`; nếu Portainer không kích hoạt profile đó, có thể bỏ dòng `profiles: [pi]` trong stack riêng để chạy Pi, nhưng không cần chỉnh source.

## 4. Rollback và kiểm tra image

Để rollback, chọn tag phiên bản cũ còn tồn tại trong GHCR và chạy `pull`/`up` lại. **Lưu ý:** rollback image không rollback database schema đã nâng cấp. Luôn backup PostgreSQL và `projects_data`, `openai_oauth_local` trước khi nâng phiên bản hoặc thay đổi migrations.

```sh
docker compose -f compose.external-db.images.yaml --profile pi images
docker compose -f compose.external-db.images.yaml --profile pi ps
```

## 5. Giới hạn và lỗi thường gặp

- Image GHCR chỉ xuất hiện sau khi workflow trên GitHub **thành công**. `manifest unknown` thường do tag chưa build, lỗi publish một component, hoặc sai repo/tag.
- `unauthorized` hoặc `denied` thường do GHCR private hoặc thiếu permission `read:packages` trên host.
- `permission_denied: packages: write` trong Actions cần kiểm tra quyền workflow và quyền package của repository.
- Cache GHA làm nhanh các lần build khi Dockerfile/dependency không đổi; **lần build đầu vẫn phải cài Chromium/NPM đầy đủ**. Cache có thể bị xóa theo quota hoặc chính sách lưu cache. Thời gian `pull` trên host còn phụ thuộc tốc độ mạng và dung lượng image; không cam kết thời gian cụ thể.
- Các Compose image không chứa `build:`, nên không thể vô tình build trên server. Nếu muốn build cục bộ, vẫn dùng `compose.external-db.yaml` hoặc `compose.yaml` như trước.
- OpenAI OAuth local-only còn phải tuân thủ các giới hạn/quyền của provider; việc chuyển sang GHCR **không làm thay đổi** cơ chế này. Credentials chỉ nằm trong volume runtime của Pi, không nằm trong image publish.
- Chưa thực hiện Docker build/push thật hoặc kiểm tra registry trên máy anh; cần nghiệm thu trong GitHub Actions và host.

## Nguồn kỹ thuật

- Docker build-push-action: https://github.com/docker/build-push-action
- Docker GHA cache: https://docs.docker.com/build/cache/backends/gha/
- Docker Compose pull: https://docs.docker.com/reference/cli/docker/compose/pull/
- GitHub GHCR publishing: https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images
