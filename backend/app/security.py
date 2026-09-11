import hashlib
import hmac
import os
import secrets
import time
from collections import OrderedDict, deque
from threading import Lock, Semaphore

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import select

from .db import engine, sessions

password_hasher = PasswordHash.recommended()
password_slots = Semaphore(2)
limiter_lock = Lock()
buckets = OrderedDict()

def secret():
    value = os.getenv('APP_SECRET', '')
    if len(value) < 32:
        raise RuntimeError('APP_SECRET 必须至少 32 字符；请运行 python scripts/setup.py')
    return value.encode()

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def csrf(token):
    return hmac.new(secret(), ('csrf:' + token).encode(), hashlib.sha256).hexdigest()

def participant(request):
    token = request.cookies.get('participant', '')
    try:
        value, signature = token.split('.')
        expected = hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()
        if len(value) == 43 and hmac.compare_digest(signature, expected):
            return digest(value)
    except ValueError:
        pass
    return None

def new_participant():
    value = secrets.token_urlsafe(32)
    return value + '.' + hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()

def rate_limit(key, limit, seconds=60):
    now = time.monotonic()
    with limiter_lock:
        queue = buckets.setdefault(key, deque())
        buckets.move_to_end(key)
        while queue and queue[0] <= now - seconds:
            queue.popleft()
        if len(queue) >= limit:
            raise HTTPException(429, '操作太频繁，请稍后再试', headers={'Retry-After': str(seconds)})
        queue.append(now)
        while len(buckets) > 4096:
            buckets.popitem(last=False)

def require_admin(request: Request):
    token = request.cookies.get('admin_session', '')
    with engine.connect() as conn:
        row = conn.execute(select(sessions).where(sessions.c.id_hash == digest(token), sessions.c.expires_at > int(time.time()))).mappings().first()
    if not row:
        raise HTTPException(401, '请先登录管理员控制台')
    if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), csrf(token)):
        raise HTTPException(403, '安全校验失败，请刷新页面后重试')
    return dict(row)
