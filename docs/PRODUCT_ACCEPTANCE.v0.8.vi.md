# SpecWeave v0.8 — Operator Acceptance Checklist

Đây là checklist anh tự kiểm tra trên host triển khai. Các mục bên dưới **chưa được tuyên bố đạt** chỉ vì script tồn tại.

## A. Setup, PostgreSQL, migrations

- [ ] Dùng PostgreSQL thật, không dùng SQLite cho hệ thống nhiều người dùng.
- [ ] `docker compose config` không báo lỗi; secret được cung cấp qua `.env` hoặc secret management của host.
- [ ] Có backup PostgreSQL và project data trước khi nâng cấp; thử restore vào host/volume thử nghiệm riêng.
- [ ] Database migration head là `0005_multi_model_review`; dữ liệu project, user và documents cũ còn nguyên.
- [ ] Container web, review-worker và Pi runner tùy chọn khởi động đúng user non-root; chỉ expose các port cần thiết.

## B. Đăng nhập và phân quyền

- [ ] Bootstrap Admin lần đầu, đăng nhập/logout/đổi password, thu hồi một phiên và logout-all.
- [ ] Admin quản lý user, user thường không truy cập API admin.
- [ ] Owner/editor/viewer chỉ làm được thao tác đúng quyền; project bị delete không lộ cho người ngoài.
- [ ] Chuyển ownership có yêu cầu xác nhận tên dự án và làm thay đổi quyền ngay lập tức.
- [ ] Thử session hết hạn, CSRF sai, đăng nhập sai liên tục; xác nhận phản hồi rate limit và log không chứa mật khẩu.

## C. Product journeys

- [ ] Wizard 5 bước: multi-select, nhập tùy chọn riêng, chọn model và tạo project; preset load/edit/delete.
- [ ] Guided Planning: hỏi có gợi ý 2–4 lựa chọn, chọn/hoãn/thảo luận và xuất decision briefs.
- [ ] Requirements/goals/decisions, review/approval, phase dependencies và cycle detection.
- [ ] AI research: SearXNG nguồn, HTTP fetch, browser fallback/proxy và ghi evidence.
- [ ] Document Studio: Markdown preview sanitized, tạo/sửa/revision/approved, sửa sau approval làm stale review.
- [ ] BMAD Draft Generator: preview, tạo 10 draft, conflict/overwrite; không thể ghi đè approved.
- [ ] Planning/Quality: rules, AI review (khi có model), trace suggestions, stage gates, reviewed export blocker.
- [ ] File Manager: batch upload, quota, versions, move, trash, restore, preview; chặn path traversal/symlink.
- [ ] Export ZIP có manifest, requirements, decisions, decision briefs, graph, review jobs/provenance và tài liệu.

## D. Model/agent/backend integration

- [ ] OpenAI-compatible provider/model list fetch và API key, model context/flag hoạt động với endpoint thật.
- [ ] Model `off/low/high/xhigh` map về provider/harness đúng; không mặc định model nào cũng hỗ trợ thinking.
- [ ] Pi RPC: hoạt động với Pi CLI đang được cài trong container; session riêng dự án/thảo luận.
- [ ] Pi tool allowlist: thử tool hợp lệ, chặn shell/path ngoài project, token sai/hết hạn/run đã hủy.
- [ ] Background review chạy 4 vai trò; có thể chọn model khác provider, theo dõi trạng thái, hủy và retry.
- [ ] Đánh giá token usage, request timeout, provider 429/5xx, output invalid và worker crash/restart.

## E. Browser and visual UX

- [ ] `python -m playwright install chromium` rồi chạy `python scripts/e2e_local.py` trên máy có thể truy cập localhost.
- [ ] Kiểm tra desktop/mobile (390×844, 768×1024, 1440×900) không overflow, menu thao tác đầy đủ.
- [ ] Test keyboard/focus/trap, contrast, aria labels, dialog focus, screenreader và lỗi JS console.
- [ ] Nếu dùng offline assets, chạy `python scripts/vendor_assets.py` trước build và xác minh app không gọi CDN.

## F. Production hardening

- [ ] TLS reverse proxy, `COOKIE_SECURE=true`, CSP/reverse-proxy headers, HSTS nếu phù hợp.
- [ ] Egress firewall chặn private/metadata IP từ Chromium/fetch, chặn direct access tới PostgreSQL và Pi runner.
- [ ] Storage permission 10001, disk/quota, backup schedule, retention/snapshot capacity và restore drill.
- [ ] Log/metrics/traces, alert API error ratio, review queue pending time, database saturation, disk full.
- [ ] Test concurrency: chat cùng discussion, review jobs trùng project, cùng file editor và nhiều upload.
- [ ] Kiểm tra dependency CVE, SAST/DAST, pentest, supply chain CDN, rate limit và incident runbook.

### Acceptance decision

Chỉ ghi "production-ready" khi có biên bản nghiệm thu hạng mục bắt buộc với người chịu trách nhiệm, ngày thực hiện và phiên bản build; những mục chưa xác minh cần ở trạng thái *open* hoặc *accepted risk*, không tự động coi là pass.
