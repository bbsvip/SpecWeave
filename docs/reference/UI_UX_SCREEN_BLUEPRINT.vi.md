# PROJECT ARCHITECT — UI/UX SCREEN BLUEPRINT & INTERACTION SPEC

> **Phiên bản:** 1.0 — Draft for Review  
> **Ngày:** 2026-11-11  
> **Tài liệu gốc:** `PROJECT_ARCHITECT_MASTER_SPEC.vi.md`  
> **Trạng thái:** Bản đặc tả UX / UI, **chưa phải giao diện đã code**  
> **Audience:** UI/UX designer, frontend engineer, FastAPI/Jinja2 implementer, Playwright tester và coding agent.

---

## 1. Mục đích, triết lý UI và tổ chức

### 1.1 Trải nghiệm mong muốn

Web app có cảm giác là **workspace chuyên nghiệp để trao đổi, nghiên cứu và duyệt quyết định**, không phải một chat app đơn giản cộng thêm vài trang file. Người dùng phải luôn nhìn thấy: **đang làm giai đoạn nào; AI đang làm gì; những gì đã xác nhận; những việc nào chưa chốt; tài liệu đang tạo; chi phí/công cụ sử dụng**.

### 1.2 Nguyên tắc layout

- **Project-first navigation:** người dùng luôn biết đang ở dự án nào; switch project nhanh trong topbar/sidebar.
- **Discussion-first workspace:** trên trang thảo luận, chat chiếm vị trí chính; facts/decisions/docs ở panel bên cạnh, không buộc đổi tab để tham chiếu.
- **Contextual details:** thay vì tạo nhiều modal khó theo dõi, dùng right-side drawer/panel cho chi tiết, bottom sheet trên mobile.
- **One system for inputs:** mọi form control cùng token height/border/radius/focus states; multi-select và text input đồng bộ.
- **Progressive disclosure:** wizard hỏi các thông tin cơ bản trước; phần advanced có thể mở rộng; AI tiếp tục hỏi thứ còn thiếu sau khi tạo dự án.
- **Explicit approvals:** draft khác approved bằng label/status và quyền thay đổi thật, không chỉ đổi màu.
- **Readable documents:** preview/editor cùng hierarchy, floating TOC, full-screen focus mode.
- **Accessible interaction:** keyboard, aria labels, visible focus, reduced motion, hit area tối thiểu hợp lý.

### 1.3 Design tokens đề xuất (không phải khóa cứng)

| Token | Giá trị hướng dẫn |
|---|---|
| `font-sans` | System UI / Inter fallback, Vietnamese glyph complete; tải một font chính có license phù hợp |
| `font-mono` | ui-monospace / JetBrains Mono fallback |
| Main surface | Light gần trắng, dark slate-charcoal; các lớp surface chênh vừa đủ |
| Primary brand | Blue/indigo dịu hoặc teal chuyên nghiệp; chọn một palette duy nhất sau mockup |
| Text | Primary, secondary, muted (không dùng opacity thấp đến mức thiếu contrast) |
| Success/warning/error | semantic, không chỉ phụ thuộc màu |
| Spacing scale | 4, 8, 12, 16, 20, 24, 32, 40 px |
| Control height | 40 px default, 36 px dense, 44 px mobile touch |
| Radius | 8 px controls, 12 px panels, 16 px dialogs |
| Shadow | subtle elevation; tránh card lồng card nhiều tầng |
| Breakpoints | mobile <768, tablet 768–1199, desktop ≥1200 |
| Density | default comfortable; optional compact cho table/data panes |
| Theme | light, dark, system; tất cả charts/diagrams tuân tokens |

### 1.4 App shell dùng chung

```text
DESKTOP (1440+)
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  Logo  [Project switcher ▾] [Search /]                Jobs  Theme  Help  User ▾         │ Topbar 60
├─────────────────┬───────────────────────────────────────────────────────────────────────┤
│ Global/Project  │ Breadcrumb → Section                                      actions    │
│ navigation      ├───────────────────────────────────────────────────────────────────────┤
│                 │                                                                       │
│ Dashboard       │                SCREEN CONTENT (variable layout)                       │
│ Projects        │                                                                       │
│ Presets         │                                                                       │
│ Providers       │                                                                       │
│ Settings        │                                                                       │
│                 │                                                                       │
│ [active project]│                                                                       │
│ Overview        │                                                                       │
│ Discussion      │                                                                       │
│ Requirements    │                                                                       │
│ Decisions       │                                                                       │
│ Documents       │                                                                       │
│ Files           │                                                                       │
│ Architecture    │                                                                       │
│ Roadmap         │                                                                       │
│ Activity        │                                                                       │
└─────────────────┴───────────────────────────────────────────────────────────────────────┘
```

**App shell:** trái 240–264px, collapse còn ~64px; topbar 56–64px; route breadcrumbs; page title + optional actions; toasts góc phù hợp; confirm modal. Sidebar hiển thị Global navigation khi chưa ở project và thêm nhóm Project navigation khi vào project. Không duplicate menu vô ích.

```text
MOBILE (<768)
┌──────────────────────────────┐
│ ☰  Project ▾       Jobs  👤  │
├──────────────────────────────┤
│ Breadcrumb / page title      │
├──────────────────────────────┤
│                              │
│      MAIN CONTENT            │
│      1 COLUMN                │
│                              │
├──────────────────────────────┤
│  Context/details as tabs     │
└──────────────────────────────┘
```

Navigation drawer, context side panel → full-screen sheet/tab; sticky footer actions chỉ khi cần, không che nội dung. Chat composer bám đáy kèm safe-area và không bị keyboard mobile che khuất.

---

## 2. Sitemap và các trang phải có

| ID | Route | Trang | Priority |
|---|---|---|---|
| UI-01 | `/first-run` | Bootstrap Admin | P1 |
| UI-02 | `/login` | Sign in | P1 |
| UI-03 | `/dashboard` | Overview dashboard | P1 |
| UI-04 | `/projects` | Project library | P1 |
| UI-05 | `/projects/new` | New Project Wizard | P1 |
| UI-06 | `/projects/{id}/overview` | Project home | P1 |
| UI-07 | `/projects/{id}/discussions/{did}` | AI Discussion Workspace | P2 |
| UI-08 | `/projects/{id}/requirements` | Requirements catalog | P3 |
| UI-09 | `/projects/{id}/decisions` | Decision center | P3 |
| UI-10 | `/projects/{id}/research` | Research explorer | P3 |
| UI-11 | `/projects/{id}/documents/{docid}` | Document Studio | P4 |
| UI-12 | `/projects/{id}/files` | File Manager | P1 |
| UI-13 | `/projects/{id}/architecture` | Architecture & UX diagrams | P4 |
| UI-14 | `/projects/{id}/roadmap` | Roadmap, dependencies & gates | P4 |
| UI-15 | `/projects/{id}/activity` | Activity & model runs | P2/P3 |
| UI-16 | `/projects/{id}/settings` | Project settings and members | P1/P2 |
| UI-17 | `/presets` | Saved templates | P1 |
| UI-18 | `/providers` | Provider manager | P2 |
| UI-19 | `/providers/{id}` | Provider detail and models | P2 |
| UI-20 | `/settings/research` | SearXNG search settings | P3 |
| UI-21 | `/settings/browser` | Playwright & proxy settings | P3 |
| UI-22 | `/settings/appearance` | Appearance/UI preferences | P1 |
| UI-23 | `/profile` | User profile and sessions | P1 |
| UI-24 | `/admin/users` | Admin users | P1 |
| UI-25 | `/admin/audit` | Security and action audit | P1/P5 |
| UI-26 | `/projects/{id}/exports` | Export center | P4 |

---

## 3. UI-01 — Bootstrap Admin (first run)

**Purpose:** tạo admin đầu tiên an toàn và khóa onboarding path vĩnh viễn sau khi initialized.

```text
┌─────────────────────────────────────────────────────────────┐
│                    Project Architect                       │
│          First-time server initialization                  │
│                                                             │
│    Admin username       [ admin                         ]    │
│    Admin email          [ admin@example.test            ]    │
│    Password             [ ••••••••••••               👁  ]    │
│    Confirm password     [ ••••••••••••               👁  ]    │
│    [ ] I understand this creates the initial admin           │
│                                                             │
│                         [ Initialize instance ]             │
└─────────────────────────────────────────────────────────────┘
```

**Layout:** centered auth surface max-width 440–480px, brand section phía trên, security notice không quá dài. Không có public signup. Tùy môi trường khởi tạo yêu cầu one-time secret/CLI gate; route không được cho phép remote unauthenticated một cách vô điều kiện.

**Interactions:** password strength feedback, show/hide password, focus auto vào username, submit with CSRF and throttling, display generic initialization error. Sau success redirect login hoặc authenticated setup (quyết định ở P1).

**States:** uninitialized, initializing, success, already initialized (404/redirect), secret invalid, validation errors. Mobile full-width with 16px padding.

---

## 4. UI-02 — Login

```text
DESKTOP
┌──────────────────────────────┬───────────────────────────────┐
│ Brand / product statement    │  Sign in                      │
│                              │                               │
│ Planning that stays          │  Username or email            │
│ traceable.                   │  [________________________]    │
│                              │  Password                     │
│  Discover → Decide →         │  [_____________________👁 ]     │
│  Design → Handoff            │  [ ] Remember this device      │
│                              │                               │
│                              │  [ Sign in                   ] │
│                              │  Contact administrator         │
└──────────────────────────────┴───────────────────────────────┘
MOBILE: hide decorative left panel; show compact brand above form.
```

**Components:** visible input labels, error summary inline, password toggle, caps lock warning, session timeout warning on redirect. No OAuth model provider buttons on web login. No social login claims.

**Behavior:** submit disabled while pending, clear password after error, generic credentials error; wrong attempts throttled, no account enumeration; on success return to safe internal `next` route (validate against open redirect).

**Acceptance:** keyboard enter login, password manager/autofill works, focus and mobile sizes consistent, auth cookie only server-managed.

---

## 5. UI-03 — Dashboard

**Goal:** tiếp tục công việc nhanh, nhìn được toàn cảnh dự án và các stage đang chờ xử lý.

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Dashboard                                      [+ New Project]       │
│ Welcome back · 3 decisions awaiting your review                      │
├──────────────┬──────────────┬──────────────┬────────────────────────┤
│ Projects     │ In progress  │ Need review  │ Docs ready for export  │
│  12          │  4           │  3           │  2                     │
├──────────────────────────────────────────────────────────────────────┤
│ Continue working                                                     │
│ [Project Alpha 70%] [Project Beta Discovery] [Project Gamma Review]  │
├───────────────────────────────────────┬──────────────────────────────┤
│ Project pipeline / stage statuses     │ Recent Activity              │
│ [small chart or progress rows]        │ Decisions, documents, runs   │
├───────────────────────────────────────┴──────────────────────────────┤
│ Recent documents · Drafts needing attention                           │
└──────────────────────────────────────────────────────────────────────┘
```

**Metrics:** count projects, active, awaiting decisions, approved docs, runs this week, per-provider usage (optional, only when data exists). Chart phải có legend/tooltip, số liệu dễ đọc; dùng dữ liệu thật không tạo KPI giả. Recent projects row card nhỏ có name, last stage, last activity, pending questions và `Continue`.

**Actions:** New Project, Continue last discussion, open pending approvals, filter projects by status. Empty state có illustration tối giản + CTA. Errors gọn và retry. Trên mobile metric cards 2 columns hoặc 1, charts scroll-free.

---

## 6. UI-04 — Project Library

**Purpose:** tất cả project, phân quyền, CRUD lifecycle.

```text
Projects [Search______________] [Status ▾] [Type ▾] [Owner ▾] [+ New]
View: [Cards] [Table]                                  Sort: Last active ▾
┌──────────────────────────────┬───────────────────────────────┐
│ Project Alpha               │ Project Beta                  │
│ Web + Android · Discovery   │ Agent + CLI · Design          │
│ 6 open questions            │ 2 pending approvals           │
│ Last updated ...    [...]   │ Last updated ...      [...]    │
└──────────────────────────────┴───────────────────────────────┘
```

**Features:** card/table toggle, search debounced, filters tags/type/platform/stage, saved sort, pagination/virtualization if many. Menu per project: Open, Duplicate (profile + optional docs), Rename, Archive, Export, Delete with retention/confirmation. Project visibility only according ACL.

**No data:** empty state with `Create Project` and `Use Preset` options. Delete dialog shows counts of files and runs; requires typing project name for permanent delete. Archive reversible.

---

## 7. UI-05 — New Project Wizard (core interaction)

**Overall:** 5 steps + review; stepper shows progress, Save draft, Back/Next, use preset, sticky action footer. Không dùng một form khổng lồ một lần. Mỗi category dùng searchable chips multi-select + `Add custom`.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ New Project                          [Use preset ▾] [Save as preset ♡]    │
│ ① Identity ━ ② Platforms ━ ③ Tech ━ ④ Experience ━ ⑤ Planning ━ Review  │
├────────────────────────────────────────────────────────────────────────────┤
│ Name                      [_______________________]                       │
│ Product type (multi)      [+ Web app ×] [+ Mobile app ×] [+ CLI] [+...]   │
│ Problem statement         [                                          ]     │
│ Target audience           [owner ×] [contractor ×] [+ Add]                │
│                                                                              │
│ Advanced requirements ▾                                                   │
├────────────────────────────────────────────────────────────────────────────┤
│ [Save Draft]                                    [Back] [Next →]           │
└────────────────────────────────────────────────────────────────────────────┘
```

### Step A — Identity & problem

Fields: project name, slug auto suggest editable, summary, problem, goal, target users, lifecycle, business context, success signals, constraints, existing materials. Help icon explains each field; optional `Ask AI to suggest` returns **suggestions to select**, not automatic fill.

### Step B — Platforms & deployment

**Panels:** (1) Product delivery/platform, (2) Devices, (3) Production deployment, (4) Hosting/network, (5) Scaling/security. Checkbox chips with grouped headings. User may choose multiple across all groups. Explicit labels differentiate **OS chạy client** vs **OS chạy production server** vs **package deployment**. Example:

```text
Target platform: [Web ×] [Android ×] [Windows ×]
Deploy to:       [Docker Compose ×] [Linux server ×] [Windows VM ×]
Hosting:         [Self-host ×] [Private LAN ×]
Connectivity:    [Online-first ×]
```

Warnings dạng non-blocking for contradictory inputs (e.g. serverless + Windows native service). `Not decided` checkbox riêng không xóa lựa chọn đã chọn nếu user muốn ghi vấn đề chưa chốt.

### Step C — Technology preferences

Dùng **tag selector theo từng tech layer**, không lẫn Python (language) với PostgreSQL (DB) hoặc Redis (cache). Mỗi tag có context menu `Required`, `Preferred`, `Avoid`; một công nghệ không thể vừa required vừa avoided, UI yêu cầu giải quyết xung đột.

```text
Languages          [Python (required) ×] [Go (preferred) ×]
Backend            [FastAPI (preferred) ×]
Frontend           [HTMX ×] [Jinja2 ×]
Databases          [PostgreSQL ×] [Redis ×]
Infrastructure     [Docker Compose ×] [Nginx ×]
Constraints        [No Node frontend build ×] [+]
```

### Step D — UX / product experience

Dynamic based on product category; web/app/game có UI style and visual reference upload, CLI/API project chuyển sang developer experience/operation. Multi-select style, color palette references, typography, screen purpose, accessibility, localization, navigation, density, preferred interaction, supporting devices. Optional text field to express free-form style.

### Step E — Planning configuration

Provider/model (nullable until configured), thinking, planning depth, research policy, docs selection, language, acceptance strictness, expected deliverables. Model dropdown shows `supports tools/vision/reasoning` without inventing capabilities.

### Review

Summary as grouped read-only cards; highlight conflicts/open questions, per-section edit links; `Create & start discovery`, `Create without chat` actions. Saving preset optional after creation; do not auto-save private files/secrets in presets.

**Wizard validation:** name required, slug valid/unique under policy, values persisting across back/refresh, multi-choice state stable, `Save Draft` recovery. Mobile stepper horizontal scroll and one field per row.

---

## 8. UI-06 — Project Overview

**Purpose:** chủ dự án nhìn tình trạng, không phải đọc toàn bộ chat.

```text
[ Project Alpha ▾ ]  Overview                       [Start Discussion] [Export]
Project description + owners + tech badges
┌────────────────────────────────┬───────────────────────────────────┐
│ Planning stages                │ What needs your attention         │
│ ✓ Intake                      │ 3 questions unanswered           │
│ ● Discovery                    │ 2 documents waiting for review    │
│ ○ Requirements                 │ 1 architecture trade-off         │
│ ○ Design                       │                                   │
└────────────────────────────────┴───────────────────────────────────┘
┌────────────────────────────────┬───────────────────────────────────┐
│ Latest decisions               │ Latest documents                  │
│ [Rows with status & time]      │ [Draft / Approved / updated]      │
└────────────────────────────────┴───────────────────────────────────┘
```

**Sections:** project synopsis, stage timeline, requirement coverage progress (with actual denominator and explanation), questions unresolved, decisions, docs/research changes, last 5 agent runs, people/permissions summary. `Needs attention` first when non-empty. Avoid fake percentage if project just created; show `Not evaluated`.

**Actions:** Start new discussion (choose mode), Continue latest, Approve stage (only when gate passed), Edit profile, View decision, Export.

---

## 9. UI-07 — Discussion Workspace (quan trọng nhất)

### Desktop three-pane

```text
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ Project Alpha / Discussion: Authentication choices            Model ▾ Thinking ▾ ... │
├───────────────────┬───────────────────────────────────────────┬───────────────────────┤
│ SESSION NAV       │ CONVERSATION                              │ CONTEXT TABS          │
│                   │                                           │                       │
│ + New discussion  │ AI  What user roles exist?               │ [Facts] [Questions]   │
│ Discovery         │     Suggested options: Admin / Member    │ [Decisions] [Files]   │
│ Research          │     [Admin+Member] [Single-user] [Other] │                       │
│ Requirements      │                                           │ Approved facts:       │
│ Architecture      │ You  Multi-user, 3 project roles          │ 14                    │
│ UX Planning       │                                           │                       │
│                   │ AI  Based on that choice ...              │ Open questions: 5     │
│                   │ [Research sources] [Propose decision]    │ Pending decisions: 2  │
│                   │                                           │                       │
│                   ├───────────────────────────────────────────┤                       │
│                   │ [Attach 📎] [Message / command...]   [▶]   │ [Open doc draft]      │
└───────────────────┴───────────────────────────────────────────┴───────────────────────┘
```

**Sizes:** left 220–260px, context 280–340px, center flexible min-width ~540px desktop; user can collapse side panels independently; `Focus chat` mode expands center. Tablet: left as popover, context as drawer. Mobile: single chat page with `Sessions` and `Context` header buttons, safe-area-aware composer.

### Header/toolbar

- Project and discussion name, status (Idle/Running/Waiting review/Error), mode chip (Discovery/Research/Compare/Draft/Critique), current provider/model and thinking, overflow (Rename/Fork/Archive/Export chat).
- Changing model only affect future turns; if run active allow schedule switch, not silently restart.
- Expand `Run details` to see token usage, tool calls, search/fetch logs with redactions and citations.

### Messages

- AI answers clear typography, Markdown/table/code blocks/Mermaid previews; assistant evidence citations clickable open Research drawer.
- User messages with edit/fork (not mutate previous turn without audit).
- Structured question card: one main question, why it matters, recommended answer, multi-choice radio/checkbox or free text, `Not decided`.
- Option comparison card: columns/summary of alternatives + tradeoffs + `Propose selection`, no automatic approval.
- Decision proposal card: title, context, recommended option, source links, `Approve`, `Request changes`, `Reject` with confirmation as needed.
- Tool progress as collapsible activity log: `Search 2 queries`, `Fetch 4 URLs`, `Read requirements.md`; never show credentials/full sensitive paths.
- System checkpoint divider with `Context updated`, `PRD draft updated`, `Gate ready`.

### Composer

- Textarea auto-resize, attach files (existing project file chooser or upload), mode switch, send/cancel, keyboard behavior: Enter send / Shift+Enter newline (configurable).
- When agent running: `Stop` and optionally `Queue message`, visible progress; prevent accidental duplicates.
- Tool permission prompts if needed: action, exact files affected, allow once/deny, preview diff.

### Error/recovery

`Provider offline`, `Model refused tool`, `Session interrupted`, `Browser timeout`, `Tool denied`, `Run cancelled`, `Context approaching limit`, `Needs human decision`. UI shows retry/continue, event streaming reconnect, no duplicated assistant blocks.

### Mobile behavior

Context tabs accessible via bottom sheet, message bubbles nearly full width, code blocks horizontal scroll, question choice list vertical, sticky composer remains visible with virtual keyboard.

---

## 10. UI-08 — Requirements Catalog

**Purpose:** xem spec theo yêu cầu có ID, owner, trạng thái, liên kết chứ không chỉ file Markdown.

```text
Requirements [Search] [Priority ▾] [Status ▾] [Type ▾] [Add] [Validate]
┌────────┬────────────────────────────┬──────────┬─────────┬───────────┐
│ ID     │ Requirement                │ Priority │ Status  │ Coverage  │
├────────┼────────────────────────────┼──────────┼─────────┼───────────┤
│ REQ-01 │ Password login             │ P0       │ Review  │ 3/4 links │
│ REQ-02 │ Multi-select wizard        │ P0       │ Confirm │ 4/4 links │
└────────┴────────────────────────────┴──────────┴─────────┴───────────┘
```

**Detail drawer:** description, actor, business rule, flows, acceptance criteria, linked decisions, linked API/docs/tests, change history, comments. Add/edit/approve with optimistic revision. Coverage meter must show which links are required and which missing, not subjective AI percentage.

**Bulk actions:** assign priority, mark needs-review, export filtered, group by module or feature. Mobile: requirement cards rather than 7-column table.

---

## 11. UI-09 — Decision Center

**Purpose:** mọi lựa chọn kiến trúc/nghiệp vụ có rationale và version, dễ tái xem.

```text
[Decisions] [Pending 3] [Approved 12] [Superseded] [+ New]
┌───────────────────────────────┬──────────────────────────────────┐
│ Decision list                 │ Decision detail                  │
│ DEC-001 Auth model — Pending  │ Problem / criteria               │
│ DEC-002 Database — Approved   │ Options: A vs B vs C             │
│ DEC-003 Browser — Approved    │ Impact: Docs / Routes / Tests   │
│                               │ [Approve] [Ask for changes]     │
└───────────────────────────────┴──────────────────────────────────┘
```

**Columns/details:** code, topic, status, owner, date, affected documents; compare options cards with rating labels. `Approve` logs actor/time and freezes decision revision; `Supersede` creates new decision link, does not rewrite past. If AI proposes decision contrary to current approved choice, show conflict banner and `Initiate change request`.

---

## 12. UI-10 — Research Explorer

```text
Research [Query________________________] [Search] [Engines ▾] [Language ▾]
┌────────────────────────────────────┬──────────────────────────────────┐
│ Research runs and sources          │ Source preview / summary         │
│ ▸ Search 2026-11-11                │ Title, URL, fetched time          │
│   1. Official docs                │ Highlighted excerpt             │
│   2. GitHub README                │ Claims supported                │
│   3. Third-party benchmark        │ [Open original] [Cite in doc]    │
└────────────────────────────────────┴──────────────────────────────────┘
```

**Features:** query history, source cards with title/domain/time/engine, excerpt, reliability category (official docs/community/blog), citations, duplicate/canonical grouping, manual URL fetch, re-fetch, compare snapshots. SearXNG config errors clearly distinguished `403 JSON not enabled` vs network fail vs empty results. Do not call third-party content “verified” based only on search rank.

**UI browser:** Playwright fetch status/events; screenshots optional for research and mandatory for UI testing use case. Proxy indicator when configured. No Lightpanda anywhere in UI.

---

## 13. UI-11 — Document Studio

### Three modes

- `Reading`: rendered Markdown + TOC + references, read-only for Approved.
- `Editing`: CodeMirror with live preview split (default) or source only; unsaved indicator.
- `Review`: side-by-side diff current/previous or agent-proposed patch, accept/reject per hunk where practical.

```text
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ [Documents tree]  PRD.md  [Draft v8]     [Preview] [Edit] [Diff] [Submit review]    │
├───────────────────┬────────────────────────────────────────┬────────────────────────┤
│ docs/             │ Editor / rendered Markdown             │ Outline / metadata     │
│  brief.md         │ # Product Requirements                 │ 1. Problem             │
│  PRD.md           │ ## Functional Requirements             │ 2. Scope               │
│  architecture.md  │ ...                                    │ 3. Functional ...      │
│  ux/              │ Mermaid diagram renderer               │ Linked decisions       │
│                   │                                        │ Version history        │
└───────────────────┴────────────────────────────────────────┴────────────────────────┘
```

**Actions:** Add doc, duplicate template, save, generate specific section with preview, compare, restore, submit for approval, approve, export single, copy stable link. Version history shows author, timestamp, change summary, content diff. Accessibility: read mode has meaningful heading levels; keyboard shortcuts Ctrl+S and ESC modal.

**Conflict handling:** if revision changed since editor load, save returns 409, shows three-way compare or prompt; never overwrite user change. AI patches always displayed as diff; approved doc editing requires re-open revision request.

**Mobile:** tree accessible via drawer, edit/preview switch instead of 3 panes; full-width textarea/editor.

---

## 14. UI-12 — File Manager (rất quan trọng)

### Desktop file explorer

```text
Files / project-alpha / inputs /                     [+ Folder] [Upload] [Actions ▾]
┌────────────────────┬────────────────────────────────────┬─────────────────────────┐
│ FOLDER TREE        │ FILE LIST                          │ PREVIEW / PROPERTIES    │
│ ▼ project-alpha    │ Name           Size   Modified     │ requirements.md         │
│   ▸ config         │ 📁 images                            │ [Preview] [Edit]        │
│   ▼ inputs         │ 📄 requirements.md  48K  Today     │ [Download] [History]    │
│     ▸ images       │ 🖼 screenshot.png   2M   Yesterday │                         │
│   ▸ docs           │ ...                                │ Markdown/PDF/Image/etc  │
│   ▸ research       │                                    │                         │
│   ▸ decisions      │                                    │                         │
│   ▸ memory         │                                    │                         │
└────────────────────┴────────────────────────────────────┴─────────────────────────┘
```

**Toolbar:** breadcrumb, create folder, upload file(s), upload folder (if supported), refresh, search, sort, view list/grid, toggle hidden (only user-visible hidden files; protected `.system` never shown), selected items actions.

**List columns:** Name, Type, Size, Modified, Origin (`User` / `Agent`), Version, Status, menu. Stable file ID in URLs; virtual path shown. Icons not sufficient alone to convey type/status.

**Context menu:** Open, Preview, Edit (supported text types), Download, Copy path, Rename, Move/Copy, Version history, Move to Trash. Right preview supports Markdown with internal link safety, JSON formatted, images, PDF, plain text; huge files show partial preview warning.

**Upload UI:** drag/drop overlay; upload queue with filename/type/size/progress, collisions `Replace as new version` / `Keep both` / `Cancel`, virus/MIME/quota errors; a rejected file must not appear as successful in UI.

**Folder operations:** cannot move folder into itself or descendant, path conflict resolution, copy/move with progress, undo/trash. Upload from project wizard lands in `inputs/`.

**Protected trees:** user can browse/edit `config/`, `inputs/`, `docs/`, `research/`, `decisions/` as ACL permits; `memory/` exposed through a safe Memory Editor, not raw Pi credentials; `sessions/` visible via conversation history/export only; `.system` and secrets are never generic browse targets. This avoids exposing raw transcript/credential implementation files.

**Edit behavior:** context menu `Edit` opens file editor with saved revision, line numbers, syntax mode by file extension, explicit Save, Diff, History; autosave optional for drafts only.

**Mobile:** no permanent tree, file list one column, file preview full-screen, actions in overflow menu, bottom action button `Upload`.

**Test cases:** hidden symlink, `../`, collision, 2-tabs concurrent saves, filename 255+ chars, big PDF, binary unsupportable, network interrupted mid-upload, viewer attempts write.

---

## 15. UI-13 — Architecture & UX Diagrams

**Purpose:** quick view architecture relationships and user flows from spec without requiring third-party design SaaS.

```text
Architecture    [System] [Services] [Data] [User flows] [Screens]
┌───────────────────────────────────────────────────────────┬────────────────────────┐
│ Diagram canvas                                           │ Source / Notes         │
│ [Mermaid rendered, zoom, pan, fit, reset]                │ Diagram version        │
│                                                           │ Linked requirements    │
│ Browser ────── API ───── Database                        │ Validate Mermaid       │
│                 └───── Pi Runner                         │ [Edit source]          │
└───────────────────────────────────────────────────────────┴────────────────────────┘
```

**Modes:** C4-like context/components, data flow, sequence, ERD, user journeys, screen inventory and navigation map. MVP: Mermaid source+preview; pixel-level mockup generation **không** nằm trong v1 mặc định. Validation errors show exact line and fallback source text.

**UX Design panel:** style direction, tokens, components, states, responsive rules, references; review rubric inspired by Impeccable `shape/critique/audit`. Người dùng có thể chọn `Create UX spec` chứ không yêu cầu AI build HTML thật.

**Mobile:** diagrams fit-to-width with pan/zoom controls; source code in separate tab.

---

## 16. UI-14 — Roadmap / stage gates

```text
Roadmap                [Gantt] [Board] [List] [Dependencies]
[✓ Intake] → [✓ Discovery] → [● PRD] → [○ Architecture] → [○ UX] → [○ Handoff]
Current gate: Requirements Review                [View blockers] [Approve Gate]
┌────────────────────────────┬─────────────────────────────┐
│ Phase                       │ Dependencies / blockers      │
│ P1 Project Foundation       │ Auth schema → ACL           │
│ P2 Agent Runner             │ P1 project IDs              │
│ ...                         │ ...                         │
└────────────────────────────┴─────────────────────────────┘
```

**Data:** phases, epic list, estimated effort qualitative (do not fake exact deadlines), dependencies, owner, criteria, status, blocker. `Approval` only after automatic checks and human confirm. `Override gate` if authorized, requires rationale.

**Visualization:** gantt optional later; MVP board/list with dependency edges and Mermaid timeline works without heavy scheduling code.

---

## 17. UI-15 — Activity and agent runs

**Purpose:** transparency and debugging without dumping raw API secrets.

```text
Activity [Runs] [Research] [File changes] [Approvals] [Errors]
Time         Actor      Event                    Project/Link
11:22        AI         web_fetch complete       source #12
11:20        Owner      DEC-004 approved         decision
11:11        AI         PRD.md draft v3          document
```

**Run inspector drawer:** run id, model, provider, thinking, start/end, latency, tool sequence, token/cost data if known, errors, links to citations/docs; filter per mode/status. Do not persist full secret-bearing tool arguments. Ability to replay read-only event timeline and cancel active run.

---

## 18. UI-16 — Project Settings and members

**Tabs:** General, Profile/Technology, Team & Access, Agent/Model, Research, Files/Quota, Workflow/Gates, Danger Zone.

- **General:** name, slug, description, status, archive.
- **Profile:** edit wizard categories with multi-select and revision history; if a confirmed field changes, create impact review list.
- **Team:** add existing users, select owner/editor/viewer, remove, transfer ownership (confirm), activity.
- **Agent:** project default provider/model/thinking, budget policy, research mode, compaction/retrieval size (advanced).
- **Files:** quota, usage, retention, protected folders visible as informational only.
- **Danger:** archive, permanently delete after confirmation with admin/owner gate.

Settings page must not expose other projects’ models/secrets unintentionally. Admin/system-level provider changes use separate routes.

---

## 19. UI-17 — Presets Library

```text
Presets [Search] [Favorite ★] [Category ▾] [+ New Preset]
[★ Python Web + Docker] [AI Agent + API] [Cross-platform App]
```

**Preset card:** name, tags, owner, version, visibility, last updated, `Use for project`, Edit, Duplicate, Export JSON, Delete. Preset editor uses same grouped multi-select controls as Wizard; `Save from current project` includes selectable fields, excludes files and secrets.

---

## 20. UI-18/19 — Providers & Models

### Providers list

```text
AI Providers [Add Provider] [Search] [Status ▾]
Name          API Type           Base URL           Models   Status      Actions
Local LLM     OpenAI-compatible  http://.../v1      5        Connected   [...]
OpenAI        Built-in           Managed            8        Connected   [...]
Another       Compatible         https://...        0        Needs auth  [...]
```

### Unified Add Provider modal/page

```text
Add Provider
Name [___________________]
Type [OpenAI-compatible ▾]   API [OpenAI Responses / Chat Completions ▾]
Base URL [https://___________________/v1]
Authentication [API Key ▾]  Token [••••••••••••]
[Test Connection] [Fetch Models]
Available models (multi-select)
[✓ model-a] [✓ model-b] [ ] model-c  [Search models...]
[+ Add model manually]           [Save provider and selected models]
```

**UX must-haves:** typed provider protocols, masked keys, validation and test feedback, model multi-select after fetch, custom `Add manual model` panel, override model metadata, ability to disable/delete, usage statuses, `Try model` with short test only if enabled. Discover models automatically only when endpoint supports it; errors actionable.

**OAuth Connect:** button appears only for supported provider-native flows; device code/redirect states, cancellation, expiry and disconnect. Credentials **never** rendered or downloadable. Tooltip explains this is AI account/provider auth, not login to web app.

**Model detail:** context, output limit, tool/vision/reasoning support, supported thinking levels, aliases, estimated costs if known, health checks, assigned projects (admin scope only). Per-project dropdown lists only models permitted for project/user.

---

## 21. UI-20/21 — Search & Browser Settings

### Search settings

`SearXNG URL`, endpoint, JSON test, request timeout, languages (multi), engines (multi), categories (multi), safesearch, result limit, time range, max queries/run, retry, user-agent if necessary, domain restrictions. `Test SearXNG` displays raw connection status and sample source count, but no huge payload.

### Browser settings

`Playwright engine: Chromium`, headless on, lazy launch, page timeout, fetch max bytes, concurrency, cache/retry, proxy mode (`none`, `HTTP(S)`, `SOCKS5`), proxy host/port/credentials secure. Toggle JS execution for research (default on when Chromium used), block heavy media optionally, screenshot size, UI testing viewport presets. `Test Browser` opens an allowlisted public sample and returns status/screenshot, not arbitrary private host.

Security options: SSRF allow/deny, restrict internal network by default, redirect revalidation, network egress, ignoreHTTPS errors **off**, robots/site terms respect. UI explicitly warns if admin enables local-network access.

---

## 22. UI-22 — Appearance

Light/dark/system, theme preview mini cards, content density, font scale within bounds, reduced motion inherited from OS with override, editor/reading width. Global appearance preference per user, **không** tự động thay `DESIGN.md` hay UI settings của các dự án được lên kế hoạch.

---

## 23. UI-23 — Profile & Sessions

Sections: display name, email, role, password change, active browser sessions with `current` marker, revoke other sessions, logout all, security events. Password change requires current password and invalidates sessions according to policy. Mask IP if privacy policy; do not expose device fingerprint as identity guarantee.

---

## 24. UI-24/25 — Administration

### Users

`Add User`, filter role/status, table columns username/email/role/status/last login, actions reset password/disable/enable/promote with confirmation. Account creation gives temporary one-time password/activation procedure; no plain password persist.

### Audit

Append-only style event table: timestamp, actor, project, action, object, outcome, source IP metadata, request id. Filter, export redacted JSON/CSV (admins only). Security notice regarding logs and retention. `Approve`/file-delete/secret changes must be visible.

### System health (optional admin tab)

PostgreSQL connected, Pi runner health/version, SearXNG health, Playwright test, disk usage, queue length, last backup, migration version. No secrets. Useful for Docker admin/Portainer deployment.

---

## 25. UI-26 — Export Center

```text
Export Project: Alpha
Export profile: (●) Full specification  ( ) Implementation only  ( ) Custom
Documents  [✓ PRD] [✓ ARCHITECTURE] [✓ UX] [✓ API] [✓ ROADMAP]
References [ ] User uploads  [✓ Research citations]
Validation [✓ 34/34 MUST linked] [!] 2 noncritical questions
[Preview file list] [Generate ZIP]
Previous exports: v1.0 approved ...  [Download] [Compare]
```

**Export validation:** show reviewed/approved status, unanswered critical questions, missing docs; block official Approved export if critical gates unmet unless authorized waiver. ZIP generation asynchronous for large projects with progress/cancel; after download link, no secret content. Export history shows SHA-256 manifest, timestamp and person.

---

## 26. Component inventory chung

| Component | Behavior contract |
|---|---|
| `AppShell` | Sidebar/topbar/breadcrumb/route slot, responsive focus management |
| `ProjectSwitcher` | Search recent/accessible projects, keyboard navigate |
| `TagMultiSelect` | Multiple chips, custom input, keyboard delete, async options, hidden value binding |
| `TechPreferenceChip` | Required/preferred/avoid with conflict check |
| `ProviderModelPicker` | Grouped searchable provider/models, capability flags, thinking values |
| `StatusPill` | State text + accessible semantics, no color-only meaning |
| `StageTimeline` | Current stage, approvals/blockers, click details |
| `QuestionCard` | Why, suggested answers, skip/not decided, submit |
| `DecisionCard` | Options and rationale, approve/reject/request changes |
| `CitationPreview` | Domain/date/excerpt, source link, report source issue |
| `AgentRunIndicator` | Running/tool/cancel/reconnect/errors |
| `FileTree` | Expand/collapse, drag/drop actions restricted, keyboard |
| `FileDropzone` | Progress per file, types/quota/errors/retry |
| `MarkdownEditor` | CodeMirror, preview, diff/revision, Ctrl+S |
| `DiagramViewer` | Mermaid, zoom, export SVG/PNG if safe, error fallback |
| `ConfirmDialog` | Impact summary, explicit label when destructive |
| `EmptyState` | Purposeful explanation + next action |
| `ErrorState` | Concrete retry/support action, request ID for debug |
| `AuditTimeline` | Searchable event list with redacted details |
| `ToastSystem` | Announce success/error via aria-live without taking focus |

**Implementation rule:** không viết một `select`/input file riêng cho mỗi trang; reuse shared macros/partials và một nguồn CSS tokens. `hx-` attributes vẫn phải hỗ trợ keyboard/form semantics; htmx errors hiển thị trong proper alert regions.

---

## 27. State machine và interaction edge cases

### 27.1 UI async states

- `idle`: action available.
- `submitting`: disable duplicate submit, spinner + accessible status.
- `queued`: show place in queue if known.
- `running`: stream events, stop enabled.
- `waiting_human`: explicit choice required.
- `succeeded`: show result and link, not only toast.
- `failed_retryable`: retry with reason/last safe state.
- `failed_terminal`: actionable error, preserve draft.
- `disconnected`: reconnect/replay, no duplicate events.

### 27.2 Route guards

Unauthenticated → login with safe internal return path. Viewer tries edit → clear permission denied, no editable affordance. Archived project → read-only. Deleted file → 404/410 not leaking existence of another project's file. Approval stale revision → conflict view.

### 27.3 Confirmation matrix

| Action | UI behavior |
|---|---|
| Edit draft | Save without modal; diff/revision tracked |
| Create new doc | Immediate with title validation |
| Move file | Preview destination, path conflict dialog |
| Delete file | Move to trash with Undo toast |
| Permanently delete project | Strong confirm typing name + owner/admin permission |
| Approve document/decision | Explicit confirmation for locked record, show impact |
| Change provider key | Mask secret, verify role, log event, reconnect test optional |
| Switch model during active run | Queue for next turn, show future effective value |
| Regenerate AI section | Preview patch diff, user accepts or rejects |
| Logout all | Confirm + immediate revocation |

---

## 28. UI quality / accessibility / performance checks

### 28.1 Viewport matrix

| Viewport | Required flows |
|---|---|
| 360 × 800 | Login, project list, wizard, mobile chat composer, File Manager |
| 390 × 844 | Login, dashboard, docs editing mode, context sheet |
| 768 × 1024 | tablet navigation, wizard multi-select, doc/source panel |
| 1024 × 768 | sidebar collapse, 2-pane chat, provider table |
| 1440 × 900 | 3-pane chat, file manager, document studio |
| 1920 × 1080 | maximum widths and line lengths, whitespace discipline |

### 28.2 Impeccable workflow

- `shape`: trước khi khóa layout, chốt user goal, content hierarchy, interaction states.
- `document`: chốt shared design tokens, typography, component specs và `DESIGN.md`.
- `critique`: phản biện các flow cốt lõi login/wizard/chat/doc/file.
- `audit`: accessibility, responsive, error states, performance.
- `polish/harden/adapt`: sau khi chức năng đúng, chỉnh spacing, overflow, responsive, empty/errors.
- `detect`: nếu được chạy trong build/review tooling và tương thích với source/template; không được coi detector là bảo đảm tự động tuyệt đối cho Jinja2/HTML. Với rendered pages dùng Chromium screenshots + axe-core và manual review.

### 28.3 UX acceptance checklist

- [ ] Input field labels/width/height/focus state nhất quán trên mọi trang.
- [ ] Mọi control icon-only có accessible name và tooltip hợp lý.
- [ ] Contrast, aria-live messages, keyboard focus, tab order, modal focus trap.
- [ ] Không có horizontal overflow ở mobile (ngoại trừ code/diagram có viewport riêng).
- [ ] Sidebar/context không che composer hay editor.
- [ ] Placeholder data được phân biệt với dữ liệu dự án thật.
- [ ] Tabulator/Chartjs lazy-load và cleanup khi partial HTML bị thay.
- [ ] `prefers-reduced-motion` được tôn trọng.
- [ ] Tải trang/research progress có skeleton/progress, không nhảy layout lớn.
- [ ] Browser test screenshot sau thay đổi lớn, manual keyboard flows và axe-core violations review.
- [ ] Không dùng nhiều CSS frameworks hoặc icon sets gây bất nhất.

---

## 29. User flows bắt buộc để kiểm thử

### Flow A — First run/login

`Open app → First-run gated setup → Admin created → Login → Dashboard → Profile password change → Logout → Login`.

### Flow B — New project from preset

`Dashboard → New Project → Use favorite preset → Select Web + Android + Windows → Edit runtime/deployment choices → Upload references → Select Model/Thinking → Review warnings → Create → Project Overview → Start Discovery`.

### Flow C — Guided discovery to approved decision

`Start discussion → AI asks question → User chooses + explains → AI proposes options → Research sources → Decision proposed → User requests change → Revised proposal → Approve → Requirement links updated`.

### Flow D — File workflow

`Open Files → Upload Markdown → Preview → Edit → Save → Rename → Move → View Version History → Restore old version → Download`.

### Flow E — Document review and export

`Open PRD draft → Agent proposes patch → Compare and accept → Validate coverage → Submit Review → Approve → Export Full ZIP → Verify manifest`.

### Flow F — Provider switching

`Providers → Add OpenAI-compatible URL + key → Test → Fetch models → Select multiple → Save → Project model setting → Chat → Switch thinking/model for next turn → Audit usage`.

### Flow G — Permission negative

`User B with Viewer → Open project → File edit disabled → Direct PATCH API returns 403 → Direct URL to private project returns 404/403 per chosen policy`.

### Flow H — Network/run recovery

`Chat while research → disconnect/reconnect browser → SSE replay to last event → no duplicate messages → continue run or show terminal error → reopen page keeps history`.

---

## 30. UI deliverables trước khi lập trình

| Deliverable | Mô tả |
|---|---|
| Sitemap approved | Routes and navigation hierarchy |
| Global design system | Tokens, colors, type, spacing, controls, dark mode |
| Wireframe set | Login, Dashboard, New Project, Discussion, Document, File Manager, Providers, Project Overview |
| Interaction matrix | Actions + permissions + async states + error recovery |
| Responsive design | 360/390/768/1024/1440 px behavior |
| Reusable component inventory | Single source of UI truth for Jinja/Tabler |
| Acceptance flows | A–H above for E2E implementation |
| Visual QA plan | Playwright Chromium screenshot, axe-core, Impeccable review where available |

**Thiết kế cần được review/duyệt trước khi tạo source code**, đặc biệt là bố cục Discussion Workspace, New Project Wizard và File Manager vì ba màn hình quyết định chất lượng trải nghiệm toàn ứng dụng.

---

## 31. Liên kết nguồn nền tảng

- [BMAD Method](https://docs.bmad-method.org/)
- [Impeccable](https://github.com/pbakaus/impeccable)
- [Tabler UI](https://github.com/tabler/tabler)
- [HTMX](https://htmx.org/)
- [Alpine.js](https://alpinejs.dev/)
- [Tabulator](https://tabulator.info/)
- [FilePond](https://pqina.nl/filepond/)
- [CodeMirror](https://codemirror.net/)
- [Mermaid](https://mermaid.js.org/)
- [Playwright Python](https://playwright.dev/python/)
- [axe-core](https://github.com/dequelabs/axe-core)

*Hết UI/UX Blueprint v1.0.*
