# SpecWeave v0.9.2 — Sửa migration cho PostgreSQL sạch

## Lỗi được sửa

Khi chạy `docker compose -f compose.external-db.yaml --profile pi up -d --build` trên PostgreSQL mới, Alembic revision `0001_baseline` tạo `review_jobs` quá sớm. Bảng `review_jobs` chứa khóa ngoại `review_id → spec_reviews.id`, nhưng `spec_reviews` thuộc revision `0002_quality` chưa được tạo. PostgreSQL báo:

```
psycopg.errors.UndefinedTable: relation "spec_reviews" does not exist
CREATE TABLE review_jobs (... FOREIGN KEY(review_id) REFERENCES spec_reviews (id))
```

**Nguyên nhân:** `0001_baseline.py` lặp qua metadata của toàn bộ ORM v0.9 và chỉ loại trừ năm bảng Quality; một số bảng thêm ở các phiên bản sau (đặc biệt `review_jobs`) vô tình được tạo từ revision `0001`.

## Sửa đổi

- `migrations/versions/0001_baseline.py`: khóa danh sách các bảng thuộc baseline, không tạo bảng Quality (0002) và các bảng review queue / login attempts (0003).
- `migrations/versions/0002_quality.py`: giữ nguyên, tạo `spec_reviews` và các bảng Quality.
- `migrations/versions/0003_review_jobs.py`: giữ nguyên, tạo `review_jobs` **sau** `spec_reviews`, cùng `review_job_steps` và `login_attempts`.
- `tests/test_migrations.py`: kiểm thử thứ tự phụ thuộc FK, cài mới từ database sạch, chạy migration lặp và nhận schema cũ chưa có `alembic_version`.
- Không sửa database models, không xóa bảng, không thay đổi dữ liệu hay quyền của dự án.

## Nâng cấp / thử lại sau lỗi

1. Sao lưu database đang chứa dữ liệu trước khi thay đổi source. Với PostgreSQL hoàn toàn mới, chưa có dữ liệu, không cần xóa database.
2. Thay bằng source v0.9.2; giữ nguyên `.env`, `DATABASE_URL`, volumes và `compose.external-db.yaml`.
3. Chạy lại:

```bash
docker compose -f compose.external-db.yaml --profile pi up -d --build
```

4. Kiểm tra log:

```bash
docker compose -f compose.external-db.yaml logs --tail=150 web
```

Sau khi thành công, Alembic ghi version `0006_openai_oauth_owner` vào bảng `alembic_version`. Nếu có quyền truy cập PostgreSQL:

```sql
SELECT version_num FROM alembic_version;
SELECT to_regclass('public.spec_reviews'), to_regclass('public.review_jobs');
```

**Không chạy `alembic stamp head` để bỏ qua lỗi**: thao tác này chỉ đánh dấu version, không tạo những bảng còn thiếu.

Trong PostgreSQL, DDL trong transaction thường được rollback khi migration lỗi. Migration v0.9.2 cũng dùng `checkfirst=True` khi tạo bảng, nên phù hợp với việc chạy lại trên schema còn một phần bảng. Nếu database của anh có nhiều schema, hãy bảo đảm `search_path` và quyền CREATE đúng với schema dự kiến.

## Kiểm thử đã thực hiện

- 3 kiểm thử regression cho migration (SQLite, kiểm tra phụ thuộc FK).
- 58 bài kiểm thử ứng dụng có sẵn.
- **61/61 bài kiểm thử đạt.**
- Không chạy PostgreSQL, Docker hay OAuth thật trong môi trường xây dựng. Anh sẽ xác minh các thành phần đó trên host riêng.

## Phạm vi

Bản vá chỉ sửa nguyên nhân Alembic khởi tạo database sạch. Không thay đổi cấu hình OAuth OpenAI, SearXNG, Dockerfile sửa `libatomic1` từ v0.9.1, hay cơ chế database ngoài.
