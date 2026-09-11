"""Offline maintenance: run with the web service stopped for restore/import/admin changes."""
import argparse
import getpass
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, update

from .db import ROOT, DB_PATH, engine, transaction, admins, sessions, questions, rules
from .security import password_hasher
from .locking import service_lock
from contextlib import nullcontext, closing


def migrate():
    command.upgrade(Config(str(ROOT / 'backend' / 'alembic.ini')), 'head')


def init_admin(username='admin', reset=False, password=None):
    with engine.connect() as conn:
        existing = conn.execute(select(admins).where(admins.c.username == username)).mappings().first()
    if existing and not reset:
        print(f'管理员 {username} 已存在，保留原密码。')
        return
    if not password:
        password = getpass.getpass('设置管理员密码（至少 12 字符）: ')
        if getpass.getpass('再次输入密码: ') != password:
            raise ValueError('两次密码不一致')
    if len(password) < 12:
        raise ValueError('管理员密码必须至少 12 字符')
    with transaction() as conn:
        hashed = password_hasher.hash(password)
        if existing:
            conn.execute(update(admins).where(admins.c.id == existing['id']).values(password_hash=hashed))
            conn.execute(delete(sessions).where(sessions.c.admin_id == existing['id']))
        else:
            conn.execute(admins.insert().values(username=username, password_hash=hashed))
    print(f'管理员 {username} 已设置。')


def seed():
    from .main import create_question
    with transaction() as conn:
        if not conn.execute(select(questions.c.id).limit(1)).first():
            q = create_question(conn, '如果用一个词描述你期待的未来，会是什么？', '输入一个词或一句话')
            conn.execute(update(questions).where(questions.c.id == q['id']).values(status='open'))


def inspect_database(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('数据库完整性检查失败')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('数据库外键检查失败')
        version = conn.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        counts = {name: conn.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ('questions', 'answers', 'rule_sets', 'answer_terms')}
        return {'migration': version, 'counts': counts}


def backup(target):
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: never silently replace a prior backup.
    with target.open('xb'):
        pass
    try:
        with closing(sqlite3.connect(DB_PATH.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
            destination.execute('PRAGMA journal_mode=DELETE')
        summary = inspect_database(target)
        summary.update(created_at=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        target.with_suffix(target.suffix + '.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False))
        return summary
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def restore(source):
    source = Path(source).resolve()
    if source == DB_PATH:
        raise ValueError('源备份不能是当前数据库')
    summary = inspect_database(source)
    if summary['migration'] != '0001':
        raise ValueError('备份数据库版本与此程序不匹配')
    sidecar = source.with_suffix(source.suffix + '.json')
    if sidecar.exists():
        manifest = json.loads(sidecar.read_text(encoding='utf-8'))
        if manifest.get('sha256') != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError('备份文件校验和不一致')
    engine.dispose()
    if DB_PATH.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        saved = DB_PATH.parent / ('before-restore-' + stamp + '.db')
        backup(saved)
        print('已保存恢复前快照：' + str(saved))
    temporary = DB_PATH.with_name('restore-' + os.urandom(8).hex() + '.db')
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
            dst.execute('DELETE FROM admin_sessions')
            dst.execute("UPDATE rule_sets SET status='failed' WHERE status='building'")
            dst.commit()
        inspect_database(temporary)
        # Caller must have stopped app; no replacement of live DB/WAL is permitted.
        for suffix in ('-wal', '-shm'):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
        os.replace(temporary, DB_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    print('恢复完成；管理会话已失效。')
    return inspect_database(DB_PATH)


def export_config(target):
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        items = []
        for row in conn.execute(select(questions).order_by(questions.c.id)).mappings():
            config = conn.execute(select(rules.c.config).where(rules.c.id == row['rule_id'])).scalar_one()
            items.append({'text': row['text'], 'hint': row['hint'], 'rules': json.loads(config)})
    target = Path(target)
    with target.open('x', encoding='utf-8') as handle:
        json.dump({'format': 'wenjuan-cloud-config-v1', 'questions': items}, handle, ensure_ascii=False, indent=2)
    print(f'已导出 {len(items)} 轮题目及规则；不含回答、密码或会话。')


def import_config(source):
    from .main import QuestionInput, RuleInput, create_question
    source = Path(source)
    if source.stat().st_size > 5_000_000:
        raise ValueError('配置文件过大')
    data = json.loads(source.read_text(encoding='utf-8'))
    if data.get('format') != 'wenjuan-cloud-config-v1' or not isinstance(data.get('questions'), list) or len(data['questions']) > 1000:
        raise ValueError('不支持的配置格式')
    validated = []
    for item in data['questions']:
        q = QuestionInput(text=item['text'], hint=item['hint'])
        r = RuleInput(version=1, **item['rules'])
        validated.append((q, r.model_dump(exclude={'version'})))
    with transaction() as conn:
        if conn.execute(select(questions.c.id).limit(1)).first():
            raise ValueError('导入要求题目库为空，不能合并或覆盖现有轮次')
        for q, r in validated:
            create_question(conn, q.text, q.hint, r)
    print(f'已导入 {len(validated)} 轮；均处于暂停状态。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('migrate')
    for name in ('init', 'admin'):
        p = sub.add_parser(name)
        p.add_argument('--username', default='admin')
        p.add_argument('--reset', action='store_true')
        p.add_argument('--no-sample', action='store_true')
    for name in ('backup', 'restore', 'verify', 'export-config', 'import-config'):
        p = sub.add_parser(name); p.add_argument('path', type=Path)
        if name == 'restore':
            p.add_argument('--app-stopped', action='store_true', help='Required acknowledgement that the web service is stopped')
    args = parser.parse_args()
    offline = args.command in ('migrate', 'init', 'admin', 'restore', 'import-config')
    with service_lock() if offline else nullcontext():
        if args.command == 'migrate': migrate()
        elif args.command in ('init', 'admin'):
            migrate(); init_admin(args.username, args.reset)
            if args.command == 'init' and not args.no_sample: seed()
        elif args.command == 'backup': backup(args.path)
        elif args.command == 'verify': print(json.dumps(inspect_database(args.path), ensure_ascii=False))
        elif args.command == 'restore':
            if not args.app_stopped: raise ValueError('请先停止 Web 服务，再添加 --app-stopped 执行恢复')
            restore(args.path)
        elif args.command == 'export-config': export_config(args.path)
        elif args.command == 'import-config': migrate(); import_config(args.path)
if __name__ == '__main__':
    try: main()
    except (ValueError, FileExistsError, RuntimeError) as error:
        print('操作未完成：' + str(error), file=sys.stderr); sys.exit(1)
