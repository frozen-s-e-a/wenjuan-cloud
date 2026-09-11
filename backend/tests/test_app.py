import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from app.db import ROOT, DB_PATH, engine, transaction, rules, questions
from app.main import app
from app.manage import backup, inspect_database, restore, export_config
from app.text import analyze, DEFAULT_RULES


def open_question(admin, text='你希望怎样的未来？'):
    q = admin.post('/api/admin/questions', json={'text': text}).json()
    r = admin.patch(f"/api/admin/questions/{q['id']}", json={'status': 'open', 'version': q['version']})
    assert r.status_code == 200
    return r.json()


def answer(client, qid, text='自由 自由', request_id=None):
    assert client.get('/api/question/current').status_code == 200
    return client.post('/api/answers', json={'question_id':qid,'text':text,'request_id':request_id or str(uuid4())})


def stats(admin, qid):
    r = admin.get(f'/api/admin/questions/{qid}/stats'); assert r.status_code == 200
    return r.json()


def browser():
    return TestClient(app, base_url='http://localhost:8000')


def test_public_entry_admin_auth_and_csrf(client):
    assert client.get('/').status_code == 200
    assert client.get('/api/question/current').json()['question'] is None
    for path in ['/api/admin/questions','/api/admin/questions/1/stats','/api/admin/questions/1/answers','/api/admin/share.svg']:
        assert client.get(path).status_code == 401
    assert client.post('/api/admin/login', json={'username':'admin','password':'wrong'}).status_code == 401
    assert client.post('/api/admin/login', json={'username':'admin','password':'synthetic-test-password-123'}).status_code == 200
    assert client.post('/api/admin/questions', json={'text':'测试'}).status_code == 403
    assert client.post('/api/answers', content='x', headers={'content-type':'text/plain'}).status_code == 415
    assert client.post('/api/admin/login', json={'username':'admin','password':'x'}, headers={'Origin':'https://other.test'}).status_code == 403
    assert client.post('/api/answers', content='x'*17000, headers={'content-type':'application/json'}).status_code == 413
    assert client.get('/api/missing').status_code == 404


def test_multi_browser_idempotency_rounds(admin):
    q = open_question(admin); a, b = browser(), browser()
    rid = str(uuid4())
    first = answer(a, q['id'], request_id=rid); assert first.status_code == 200
    assert answer(a, q['id'], request_id=rid).json()['id'] == first.json()['id']
    assert answer(a, q['id'], '其他内容', rid).status_code == 409
    assert answer(a, q['id']).status_code == 409
    assert answer(b, q['id'], '自由 创造力').status_code == 200
    data = stats(admin, q['id']); assert data['valid_count'] == 2
    assert next(w['count'] for w in data['words'] if w['term']=='自由') == 2
    q2 = open_question(admin, '下一轮问题')
    newcomer = browser(); assert answer(newcomer, q['id']).status_code == 409
    assert answer(a, q['id'], request_id=rid).json()['duplicate']
    assert stats(admin, q2['id'])['valid_count'] == 0
    assert answer(a, q2['id'], '新的回答').status_code == 200
    for c in [a,b,newcomer]: c.close()


def test_moderation_and_rule_rebuild(admin):
    q = open_question(admin); a, b = browser(), browser()
    first = answer(a,q['id'],'AI AI 自由').json()['id']
    answer(b,q['id'],'人工智能 自由')
    data = stats(admin,q['id'])
    payload = {'version':data['version'],'stopwords':DEFAULT_RULES['stopwords'],'phrases':['人工智能'],'synonyms':{'ai':'人工智能'}}
    result = admin.put(f"/api/admin/questions/{q['id']}/rules",json=payload)
    assert result.status_code == 202
    for _ in range(100):
        data = stats(admin,q['id'])
        if data['job']['status'] != 'building': break
        time.sleep(.03)
    assert data['job']['status']=='active'
    assert data['rule_version']==2
    assert next(w['count'] for w in data['words'] if w['term']=='人工智能')==2
    rows=admin.get(f"/api/admin/questions/{q['id']}/answers",params={'term':'人工智能'}).json()
    assert rows['total']==2
    r=admin.patch(f'/api/admin/answers/{first}',json={'version':data['version'],'status':'excluded'})
    assert r.status_code==200
    data=stats(admin,q['id']); assert data['valid_count']==1 and data['total_count']==2
    assert admin.patch(f'/api/admin/answers/{first}',json={'version':data['version']-1,'status':'valid'}).status_code==409
    assert admin.patch(f'/api/admin/answers/{first}',json={'version':data['version'],'status':'valid'}).status_code==200
    assert admin.get('/api/admin/share.svg').headers['content-type'].startswith('image/svg+xml')
    assert admin.post('/api/admin/logout',json={}).status_code==200
    assert admin.get('/api/admin/session').status_code==401
    a.close(); b.close()


def test_concurrent_same_request_and_unique_open(admin):
    q=open_question(admin); participant=browser(); participant.get('/api/question/current')
    cookies=dict(participant.cookies); rid=str(uuid4())
    def send(_):
        c=browser(); c.cookies.update(cookies)
        try: return c.post('/api/answers',json={'question_id':q['id'],'text':'并发测试','request_id':rid})
        finally: c.close()
    with ThreadPoolExecutor(max_workers=4) as pool: responses=list(pool.map(send,range(4)))
    assert all(r.status_code==200 for r in responses)
    assert len({r.json()['id'] for r in responses})==1
    assert stats(admin,q['id'])['valid_count']==1
    participant.close()


def test_offline_lock_and_input_rules(admin):
    q=open_question(admin)
    result=subprocess.run([sys.executable,'-m','app.manage','migrate'],cwd=ROOT/'backend',capture_output=True,text=True)
    assert result.returncode!=0 and '正在由服务' in result.stderr
    assert answer(browser(),q['id'],' '*5).status_code==422
    assert answer(browser(),q['id'],'字'*101).status_code==422
    assert admin.put(f"/api/admin/questions/{q['id']}/rules",json={'version':q['version'],'stopwords':[],'phrases':[],'synonyms':{'a':'b','b':'a'}}).status_code==422


def test_backup_restore_and_clean_config_transfer(tmp_path):
    with TestClient(app,base_url='http://localhost:8000') as c:
        token=c.post('/api/admin/login',json={'username':'admin','password':'synthetic-test-password-123'}).json()['csrf']; c.headers['X-CSRF-Token']=token
        q=open_question(c); answer(c,q['id'],'SYNTHETIC_PRIVATE_ANSWER')
        target=tmp_path/'snapshot.db'; summary=backup(target)
        config=tmp_path/'config.json'; export_config(config)
        assert 'SYNTHETIC_PRIVATE_ANSWER' not in config.read_text()
    summary2=restore(target)
    assert summary2['counts']==summary['counts']
    with sqlite3.connect(DB_PATH) as conn: assert conn.execute('SELECT count(*) FROM admin_sessions').fetchone()[0]==0
    with TestClient(app,base_url='http://localhost:8000') as c:
        assert c.get('/api/health').status_code==200
    other=tmp_path/'fresh'; env={**os.environ,'APP_DATA_DIR':str(other)}
    result=subprocess.run([sys.executable,'-m','app.manage','import-config',str(config)],cwd=ROOT/'backend',env=env,capture_output=True,text=True)
    assert result.returncode==0, result.stderr
    fresh=inspect_database(other/'app.db'); assert fresh['counts']['questions']==1 and fresh['counts']['answers']==0
    result=subprocess.run([sys.executable,'-m','app.manage','import-config',str(config)],cwd=ROOT/'backend',env=env,capture_output=True,text=True)
    assert result.returncode!=0
    try: backup(target)
    except FileExistsError: pass
    else: raise AssertionError('Existing backups must not be overwritten')


def test_token_normalization():
    config=json.dumps({**DEFAULT_RULES,'phrases':['人工智能'],'synonyms':{'ai':'人工智能'}})
    result=analyze('AI ai ＡＩ 人工智能，自由自由',config)
    assert result.count('人工智能')==1 and result.count('自由')==1
