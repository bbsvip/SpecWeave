"""Session, password and encryption utilities."""
import hashlib
import os
import secrets
from datetime import timedelta
from fastapi import HTTPException, Request
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from cryptography.fernet import Fernet, InvalidToken
from db import AuthSession, User, utcnow

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
SESSION_COOKIE = "specweave_session"
DAYS = int(os.environ.get("SESSION_DAYS", "7"))


def hash_password(password):
    if len(password) < 12:
        raise ValueError("Mật khẩu cần ít nhất 12 ký tự")
    return ph.hash(password)


def verify_password(saved, raw):
    try:
        return ph.verify(saved, raw)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False


def new_session(db, user, request):
    token = secrets.token_urlsafe(40)
    row = AuthSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                      csrf_token=secrets.token_hex(32), expires_at=utcnow() + timedelta(days=DAYS),
                      user_agent=request.headers.get("user-agent", "")[:250])
    db.add(row)
    db.flush()
    return token, row


def current_auth(db, request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None, None
    row = db.query(AuthSession).filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest(), revoked_at=None).first()
    if not row or row.expires_at < utcnow() or not row.user.enabled:
        return None, None
    return row.user, row


def require_auth(db, request):
    user, session = current_auth(db, request)
    if not user:
        raise HTTPException(401, "Vui lòng đăng nhập")
    return user, session


def check_csrf(request, session, supplied):
    if not session or not supplied or not secrets.compare_digest(str(supplied), session.csrf_token):
        raise HTTPException(403, "CSRF không hợp lệ")
    # Strict Origin when supplied; SameSite is not sole CSRF defense.
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit
        host = request.headers.get("host", "")
        if urlsplit(origin).netloc != host:
            raise HTTPException(403, "Nguồn request không hợp lệ")


def cipher():
    key = os.environ.get("ENCRYPTION_KEY", "").encode()
    if not key:
        raise RuntimeError("ENCRYPTION_KEY chưa cấu hình")
    return Fernet(key)


def encrypt(value):
    return cipher().encrypt(value.encode()).decode() if value else ""


def decrypt(value):
    if not value:
        return ""
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Không giải mã được provider secret")
