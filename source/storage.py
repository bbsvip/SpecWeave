"""Scoped, symlink-resistant project filesystem operations."""
import os
import re
import json
import shutil
import hashlib
import tempfile
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from fastapi import HTTPException

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "./data")).resolve()
PROJECTS_ROOT = DATA_ROOT / "projects"
RESERVED = {".system", "secrets", ".env", ".git", ".venv", "venv", "node_modules", "__pycache__"}
IGNORED = {".git", ".venv", "venv", "node_modules", "__pycache__"}
MAX_TEXT = 2 * 1024 * 1024


def storage_limits():
    """Quota applies to all physical project files, including snapshots and trash."""
    mb = max(1, min(int(os.environ.get('MAX_PROJECT_STORAGE_MB', '2048')), 1048576))
    count = max(50, min(int(os.environ.get('MAX_PROJECT_FILES', '50000')), 1000000))
    return mb * 1024 * 1024, count


def project_usage(project_id):
    """Count physical data without following symlinks; never walk venv/cache directories."""
    root = project_root(project_id)
    total = files = 0
    if not root.exists():
        return {'bytes': 0, 'files': 0}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED and not (Path(base) / d).is_symlink()]
        for name in names:
            item = Path(base)/name
            if item.is_symlink() or not item.is_file(): continue
            total += item.stat().st_size
            files += 1
    return {'bytes': total, 'files': files}


def enforce_project_quota(project_id, *, extra_bytes=0, extra_files=0):
    usage = project_usage(project_id)
    max_bytes, max_files = storage_limits()
    if usage['bytes']+extra_bytes > max_bytes:
        raise HTTPException(413, 'Project storage quota exceeded')
    if usage['files']+extra_files > max_files:
        raise HTTPException(413, 'Project file count quota exceeded')
    return usage

def project_root(project_id):
    if not re.fullmatch(r"[0-9a-f-]{36}", project_id):
        raise HTTPException(400, "ID dự án không hợp lệ")
    return PROJECTS_ROOT / project_id

def resolve(project_id, path="", allow_root=False):
    root = project_root(project_id)
    normalized = (path or "").replace("\\", "/").strip("/")
    parts = PurePosixPath(normalized).parts
    if any(p in ("..", ".") or p.startswith(".") or p.lower() in RESERVED for p in parts):
        raise HTTPException(400, "Đường dẫn không được phép")
    candidate = root.joinpath(*parts)
    if not normalized and not allow_root:
        raise HTTPException(400, "Thao tác trên thư mục gốc bị cấm")
    # Resolve existing path components to detect symlink traversal, including dangling symlinks.
    p = root
    for part in parts:
        p = p / part
        if p.is_symlink():
            raise HTTPException(400, "Không chấp nhận symbolic link")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise HTTPException(400, "Truy cập ngoài phạm vi dự án")
    return candidate

def init_project(project):
    root = project_root(project.id)
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("inputs", "docs/drafts", "docs/approved", "docs/ux", "docs/implementation", "notes", "research", "decisions", "memory", "sessions", "exports", "versions", ".system"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    atomic_text(root / "project.json", json.dumps({"id":project.id,"name":project.name,"profile":project.profile}, ensure_ascii=False, indent=2))
    atomic_text(root / "memory" / "rolling_summary.md", "# Project memory\n\nNo confirmed facts yet.\n")
    return root

def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def snapshot(project_id, path):
    target = resolve(project_id, path)
    if target.is_file():
        version = project_root(project_id) / "versions" / (hashlib.sha256(path.encode()).hexdigest()[:16])
        version.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        shutil.copy2(target, version / suffix)

def read_text(project_id, path):
    p = resolve(project_id, path)
    if not p.is_file() or p.stat().st_size > MAX_TEXT:
        raise HTTPException(400, "File không tồn tại hoặc quá giới hạn chỉnh sửa (2 MB)")
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File nhị phân không hỗ trợ chỉnh sửa văn bản")

def write_text(project_id, path, data, *, overwrite=True):
    if len(data.encode("utf-8")) > MAX_TEXT:
        raise HTTPException(413, "Nội dung quá giới hạn 2 MB")
    p = resolve(project_id, path)
    if p.exists() and p.is_dir(): raise HTTPException(409, "Trùng tên thư mục")
    if p.exists() and not overwrite: raise HTTPException(409, "File đã tồn tại")
    original_size = p.stat().st_size if p.exists() and p.is_file() else 0
    # Snapshot duplicates the old file before replacing it. Account for both.
    enforce_project_quota(project_id, extra_bytes=len(data.encode('utf-8')),
                          extra_files=1)  # one new file OR one snapshot of existing file
    if p.exists(): snapshot(project_id, path)
    atomic_text(p, data)
    return p

def list_entries(project_id, directory=""):
    p = resolve(project_id, directory, allow_root=True)
    if not p.is_dir(): raise HTTPException(404, "Không tìm thấy thư mục")
    out = []
    for item in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if item.is_symlink() or item.name.startswith(".") or item.name.lower() in RESERVED: continue
        rel = item.relative_to(project_root(project_id)).as_posix()
        out.append({"name":item.name, "path":rel, "is_dir":item.is_dir(), "size":item.stat().st_size if item.is_file() else 0})
    return out

def remove(project_id, path):
    target = resolve(project_id, path)
    if target.exists():
        if target.is_dir(): shutil.rmtree(target)
        else: target.unlink()
    else: raise HTTPException(404, "Không tìm thấy")

def move(project_id, source, destination):
    a = resolve(project_id, source)
    b = resolve(project_id, destination)
    if not a.exists(): raise HTTPException(404, "Không tìm thấy nguồn")
    if b.exists(): raise HTTPException(409, "Đích đã tồn tại")
    if b.resolve().is_relative_to(a.resolve()) and a.is_dir():
        raise HTTPException(400, "Không thể di chuyển thư mục vào chính nó")
    b.parent.mkdir(parents=True, exist_ok=True)
    a.rename(b)
