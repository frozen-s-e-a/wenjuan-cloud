import json
import logging
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update

from .db import ROOT, admins, answers, engine, questions, rules, sessions, terms, transaction
from .security import (csrf, digest, new_participant, participant, password_hasher, password_slots,
                       rate_limit, require_admin, secret)
from .text import DEFAULT_RULES, analyze, normalize

logger = logging.getLogger('wenjuan')
executor = None


def now():
    return datetime.now(timezone.utc).isoformat()


def question(conn, qid):
    row = conn.execute(select(questions).where(questions.c.id == qid)).mappings().first()
    if not row:
        raise HTTPException(404, '题目不存在')
    return dict(row)


def expect_version(row, version):
    if row['version'] != version:
        raise HTTPException(409, '数据已更新，请刷新后再操作')


def bump(conn, qid):
    conn.execute(update(questions).where(questions.c.id == qid).values(version=questions.c.version + 1))


def create_question(conn, text, hint, config=None):
    qid = conn.execute(questions.insert().values(text=text, hint=hint, status='paused', created_at=now(), version=1)).inserted_primary_key[0]
    rid = conn.execute(rules.insert().values(question_id=qid, version=1, config=json.dumps(config or DEFAULT_RULES, ensure_ascii=False), status='active', created_at=now())).inserted_primary_key[0]
    conn.execute(update(questions).where(questions.c.id == qid).values(rule_id=rid))
    return question(conn, qid)


@asynccontextmanager
async def _lifespan(app):
    global executor
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rules')
    secret()
    origin = os.getenv('PUBLIC_ORIGIN', 'http://localhost:8000')
    if os.getenv('APP_ENV') == 'production' and (not origin.startswith('https://') or os.getenv('COOKIE_SECURE') != 'true'):
        raise RuntimeError('生产环境必须配置 HTTPS PUBLIC_ORIGIN 和 COOKIE_SECURE=true')
    with engine.connect() as conn:
        if conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() != '0001':
            raise RuntimeError('数据库版本不匹配，请先执行迁移')
    with transaction() as conn:
        # Interrupted rebuilds never become visible. They can be retried by the administrator.
        conn.execute(update(rules).where(rules.c.status == 'building').values(status='failed'))
        conn.execute(delete(sessions).where(sessions.c.expires_at < int(time.time())))
    yield
    executor.shutdown(wait=True)


@asynccontextmanager
async def lifespan(app):
    from .locking import service_lock
    with service_lock():
        async with _lifespan(app):
            yield


app = FastAPI(title='问卷云', version='0.1.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class RequestGuard:
    """Bound request bodies even for chunked transfer; API writes are JSON and same-origin."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        api = scope['path'].startswith('/api/')
        write = scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
        if api and write:
            origin = headers.get(b'origin', b'').decode()
            trusted = os.getenv('PUBLIC_ORIGIN', 'http://localhost:8000').rstrip('/')
            if headers.get(b'sec-fetch-site') == b'cross-site' or (origin and origin != trusted):
                return await JSONResponse({'detail': '请求来源不受信任'}, 403)(scope, receive, send)
            if not headers.get(b'content-type', b'').startswith(b'application/json'):
                return await JSONResponse({'detail': '请使用 JSON 请求'}, 415)(scope, receive, send)
            body = b''
            while True:
                msg = await receive()
                if msg['type'] == 'http.disconnect':
                    return
                body += msg.get('body', b'')
                if len(body) > 16384:
                    return await JSONResponse({'detail': '请求内容过大'}, 413)(scope, receive, send)
                if not msg.get('more_body'):
                    break
            async def buffered():
                return {'type': 'http.request', 'body': body, 'more_body': False}
            receive = buffered
        async def guarded_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] += [
                    (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")]
                if api:
                    message['headers'].append((b'cache-control', b'no-store'))
            await send(message)
        await self.app(scope, receive, guarded_send)


app.add_middleware(RequestGuard)


class AnswerInput(BaseModel):
    question_id: int
    text: str = Field(min_length=1, max_length=100)
    request_id: UUID

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('回答不能全为空白')
        return value.strip()


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class QuestionInput(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    hint: str = Field(default='输入一个词或一句话', max_length=200)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('题目不能为空')
        return value.strip()


class QuestionState(BaseModel):
    status: Literal['open', 'paused']
    version: int


class AnswerState(BaseModel):
    status: Literal['valid', 'excluded']
    version: int


class RuleInput(BaseModel):
    version: int
    stopwords: list[str] = Field(max_length=300)
    phrases: list[str] = Field(max_length=200)
    synonyms: dict[str, str]

    @field_validator('stopwords', 'phrases')
    @classmethod
    def words(cls, value):
        if any(not w.strip() or len(w) > 40 for w in value):
            raise ValueError('每个词应为 1—40 字符')
        return sorted(set(normalize(w) for w in value))

    @field_validator('synonyms')
    @classmethod
    def mapping(cls, value):
        if len(value) > 200 or any(not k.strip() or not v.strip() or len(k) > 40 or len(v) > 40 for k, v in value.items()):
            raise ValueError('同义词最多 200 组，每个词为 1—40 字符')
        value = {normalize(k): normalize(v) for k, v in value.items() if normalize(k) != normalize(v)}
        if any(v in value for v in value.values()):
            raise ValueError('请直接映射到最终词语，不支持链式或循环映射')
        return value


def cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite='lax' if name == 'participant' else 'strict', secure=os.getenv('COOKIE_SECURE') == 'true', path='/')


@app.get('/api/health')
def health():
    with engine.connect() as conn:
        conn.execute(select(questions.c.id).limit(1))
    return {'status': 'ok', 'version': '0.1.0'}


@app.get('/api/question/current')
def current(request: Request, response: Response):
    phash = participant(request)
    if not phash:
        cookie(response, 'participant', new_participant(), 31536000)
    with engine.connect() as conn:
        row = conn.execute(select(questions).where(questions.c.status == 'open')).mappings().first()
        submitted = bool(row and phash and conn.execute(select(answers.c.id).where(answers.c.question_id == row['id'], answers.c.participant_hash == phash)).first())
    return {'question': dict(row) if row else None, 'submitted': submitted}


@app.post('/api/answers')
def submit(body: AnswerInput, request: Request):
    phash = participant(request)
    if not phash:
        raise HTTPException(400, '请允许本站 Cookie 并刷新页面后提交')
    rate_limit('answer:' + phash, 15)
    request_id = str(body.request_id)
    for _ in range(3):
        with engine.connect() as conn:
            q = question(conn, body.question_id)
            config = conn.execute(select(rules.c.config).where(rules.c.id == q['rule_id'])).scalar_one()
        words = analyze(body.text, config)
        with transaction() as conn:
            # Retry succeeds even after the round closes, only for the original browser/payload.
            prior = conn.execute(select(answers).where(answers.c.question_id == body.question_id, answers.c.request_id == request_id)).mappings().first()
            if prior:
                if prior['raw_text'] != body.text or prior['participant_hash'] != phash:
                    raise HTTPException(409, '该请求编号已用于其他回答')
                return {'id': prior['id'], 'duplicate': True}
            latest = question(conn, body.question_id)
            if latest['status'] != 'open':
                raise HTTPException(409, '题目已暂停或切换，请刷新题目；你的输入已保留')
            if latest['rule_id'] != q['rule_id']:
                continue
            if conn.execute(select(answers.c.id).where(answers.c.question_id == body.question_id, answers.c.participant_hash == phash)).first():
                raise HTTPException(409, '本浏览器已经回答过本轮问题')
            aid = conn.execute(answers.insert().values(question_id=body.question_id, raw_text=body.text, participant_hash=phash, request_id=request_id, status='valid', created_at=now())).inserted_primary_key[0]
            if words:
                conn.execute(terms.insert(), [{'answer_id': aid, 'rule_set_id': q['rule_id'], 'term': w} for w in words])
            bump(conn, body.question_id)
            return {'id': aid, 'duplicate': False}
    raise HTTPException(409, '分词规则正在切换，请稍后重试')


@app.post('/api/admin/login')
def login(body: LoginInput, request: Request, response: Response):
    ip = request.client.host if request.client else 'unknown'
    rate_limit('login:' + ip, 5)
    rate_limit('login:global', 60)
    with engine.connect() as conn:
        user = conn.execute(select(admins).where(admins.c.username == body.username)).mappings().first()
    with password_slots:
        if not user or not password_hasher.verify(body.password, user['password_hash']):
            raise HTTPException(401, '用户名或密码错误')
    token = secrets.token_urlsafe(32)
    with transaction() as conn:
        conn.execute(delete(sessions).where(sessions.c.expires_at < int(time.time())))
        conn.execute(sessions.insert().values(id_hash=digest(token), admin_id=user['id'], expires_at=int(time.time()) + 28800))
    cookie(response, 'admin_session', token, 28800)
    return {'csrf': csrf(token)}


@app.get('/api/admin/session', dependencies=[Depends(require_admin)])
def session(request: Request):
    return {'csrf': csrf(request.cookies['admin_session'])}


@app.post('/api/admin/logout', dependencies=[Depends(require_admin)])
def logout(request: Request, response: Response):
    with transaction() as conn:
        conn.execute(delete(sessions).where(sessions.c.id_hash == digest(request.cookies['admin_session'])))
    response.delete_cookie('admin_session', path='/')
    return {'ok': True}


@app.get('/api/admin/share.svg', dependencies=[Depends(require_admin)])
def share_qr():
    import io
    import qrcode
    import qrcode.image.svg
    image = qrcode.make(os.getenv('PUBLIC_ORIGIN', 'http://localhost:8000').rstrip('/') + '/', image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    return Response(content=output.getvalue(), media_type='image/svg+xml', headers={'Content-Disposition': 'attachment; filename="wenjuan-cloud-qr.svg"'})


@app.get('/api/admin/questions', dependencies=[Depends(require_admin)])
def list_questions():
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(questions).order_by(questions.c.id.desc())).mappings()]


@app.post('/api/admin/questions', dependencies=[Depends(require_admin)])
def new_question(body: QuestionInput):
    with transaction() as conn:
        return create_question(conn, body.text, body.hint)


@app.patch('/api/admin/questions/{qid}', dependencies=[Depends(require_admin)])
def change_question(qid: int, body: QuestionState):
    with transaction() as conn:
        q = question(conn, qid)
        expect_version(q, body.version)
        if body.status == 'open':
            conn.execute(update(questions).where(questions.c.status == 'open', questions.c.id != qid).values(status='paused', version=questions.c.version + 1))
        conn.execute(update(questions).where(questions.c.id == qid).values(status=body.status, version=questions.c.version + 1))
        return question(conn, qid)


@app.get('/api/admin/questions/{qid}/stats', dependencies=[Depends(require_admin)])
def stats(qid: int, since: int | None = None):
    with engine.connect() as conn:
        # Explicit snapshot: summary, question version and terms describe the same DB state.
        conn.exec_driver_sql('BEGIN')
        q = question(conn, qid)
        job = conn.execute(select(rules.c.id, rules.c.status, rules.c.version).where(rules.c.question_id == qid).order_by(rules.c.version.desc()).limit(1)).mappings().one()
        if since == q['version']:
            return {'unchanged': True, 'version': q['version'], 'job': dict(job)}
        active = (answers.c.question_id == qid) & (answers.c.status == 'valid')
        count, last = conn.execute(select(func.count(), func.max(answers.c.created_at)).where(active)).one()
        all_count = conn.execute(select(func.count()).select_from(answers).where(answers.c.question_id == qid)).scalar_one()
        query = select(terms.c.term, func.count().label('count')).select_from(terms.join(answers)).where(active, terms.c.rule_set_id == q['rule_id']).group_by(terms.c.term).order_by(func.count().desc(), terms.c.term)
        word_count = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        top = [dict(row) for row in conn.execute(query.limit(80)).mappings()]
        rule = conn.execute(select(rules).where(rules.c.id == q['rule_id'])).mappings().one()
        return {'question': q, 'version': q['version'], 'valid_count': count, 'total_count': all_count, 'word_count': word_count, 'latest_at': last, 'words': top, 'rule_version': rule['version'], 'rules': json.loads(rule['config']), 'job': dict(job), 'updated_at': now()}


@app.get('/api/admin/questions/{qid}/words', dependencies=[Depends(require_admin)])
def word_list(qid: int, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        q = question(conn, qid)
        base = select(terms.c.term, func.count().label('count')).select_from(terms.join(answers)).where(answers.c.question_id == qid, answers.c.status == 'valid', terms.c.rule_set_id == q['rule_id']).group_by(terms.c.term)
        total = conn.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        rows = conn.execute(base.order_by(func.count().desc(), terms.c.term).limit(page_size).offset((page - 1) * page_size)).mappings()
        return {'total': total, 'items': [dict(r) for r in rows]}


@app.get('/api/admin/questions/{qid}/answers', dependencies=[Depends(require_admin)])
def answer_list(qid: int, term: str | None = Query(None, max_length=100), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        q = question(conn, qid)
        query = select(answers.c.id, answers.c.raw_text, answers.c.status, answers.c.created_at).where(answers.c.question_id == qid)
        if term:
            query = query.where(answers.c.status == 'valid', answers.c.id.in_(select(terms.c.answer_id).where(terms.c.rule_set_id == q['rule_id'], terms.c.term == term)))
        total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = conn.execute(query.order_by(answers.c.id.desc()).limit(page_size).offset((page - 1) * page_size)).mappings()
        return {'total': total, 'items': [dict(r) for r in rows], 'version': q['version']}


@app.patch('/api/admin/answers/{aid}', dependencies=[Depends(require_admin)])
def moderate(aid: int, body: AnswerState):
    with transaction() as conn:
        row = conn.execute(select(answers).where(answers.c.id == aid)).mappings().first()
        if not row:
            raise HTTPException(404, '回答不存在')
        q = question(conn, row['question_id'])
        expect_version(q, body.version)
        conn.execute(update(answers).where(answers.c.id == aid).values(status=body.status))
        bump(conn, q['id'])
    return {'ok': True}


def rebuild(qid, rid, config):
    try:
        last = 0
        while True:
            # New answers keep using the active rules; include their tail before atomic activation.
            with transaction() as conn:
                rows = list(conn.execute(select(answers.c.id, answers.c.raw_text).where(answers.c.question_id == qid, answers.c.id > last).order_by(answers.c.id).limit(100)).mappings())
                if not rows:
                    old_id = question(conn, qid)['rule_id']
                    conn.execute(update(rules).where(rules.c.id == old_id).values(status='archived'))
                    conn.execute(update(rules).where(rules.c.id == rid).values(status='active'))
                    conn.execute(update(questions).where(questions.c.id == qid).values(rule_id=rid, version=questions.c.version + 1))
                    break
            entries = [{'answer_id': row['id'], 'rule_set_id': rid, 'term': term} for row in rows for term in analyze(row['raw_text'], config)]
            with transaction() as conn:
                if entries:
                    conn.execute(terms.insert(), entries)
            last = rows[-1]['id']
        # Only retain active/staging contributions; rule configurations remain versioned.
        with transaction() as conn:
            stale = select(rules.c.id).where(rules.c.question_id == qid, rules.c.status.in_(['archived', 'failed']))
            conn.execute(delete(terms).where(terms.c.rule_set_id.in_(stale)))
    except Exception:
        logger.error('规则重算失败；轮次=%s，规则=%s', qid, rid)
        with transaction() as conn:
            conn.execute(update(rules).where(rules.c.id == rid, rules.c.status == 'building').values(status='failed'))
            bump(conn, qid)


@app.put('/api/admin/questions/{qid}/rules', dependencies=[Depends(require_admin)], status_code=202)
def update_rules(qid: int, body: RuleInput):
    config = json.dumps(body.model_dump(exclude={'version'}), ensure_ascii=False)
    with transaction() as conn:
        q = question(conn, qid)
        expect_version(q, body.version)
        if conn.execute(select(rules.c.id).where(rules.c.status == 'building')).first():
            raise HTTPException(409, '已有规则正在重算，请稍后再试')
        version = conn.execute(select(func.max(rules.c.version)).where(rules.c.question_id == qid)).scalar_one() + 1
        rid = conn.execute(rules.insert().values(question_id=qid, version=version, config=config, status='building', created_at=now())).inserted_primary_key[0]
        bump(conn, qid)
    executor.submit(rebuild, qid, rid, config)
    return {'id': rid, 'status': 'building', 'version': version}


# Explicit SPA routes, no blanket fallback swallowing unknown API endpoints.
DIST = Path(os.getenv('FRONTEND_DIST', str(ROOT / 'frontend' / 'dist')))
if (DIST / 'assets').is_dir():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')


@app.get('/')
@app.get('/admin')
def page():
    if not (DIST / 'index.html').exists():
        raise HTTPException(503, '前端尚未构建，请先运行启动脚本')
    return FileResponse(DIST / 'index.html', headers={'Cache-Control': 'no-cache'})
