import os
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from dotenv import load_dotenv
from sqlalchemy import (Column, ForeignKey, Index, Integer, MetaData, String,
                        Table, Text, UniqueConstraint, create_engine, event)

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
DATA = Path(os.getenv('APP_DATA_DIR', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'app.db'
engine = create_engine('sqlite:///' + DB_PATH.as_posix(), connect_args={'timeout': 10}, hide_parameters=True, pool_size=5, max_overflow=0)
metadata = MetaData()
write_lock = RLock()

questions = Table('questions', metadata,
    Column('id', Integer, primary_key=True), Column('text', Text, nullable=False),
    Column('hint', Text, nullable=False), Column('status', String, nullable=False),
    Column('created_at', String, nullable=False), Column('version', Integer, nullable=False, default=1),
    Column('rule_id', Integer, nullable=True))
Index('one_open_question', questions.c.status, unique=True, sqlite_where=questions.c.status == 'open')
rules = Table('rule_sets', metadata,
    Column('id', Integer, primary_key=True), Column('question_id', ForeignKey('questions.id'), nullable=False),
    Column('version', Integer, nullable=False), Column('config', Text, nullable=False),
    Column('status', String, nullable=False), Column('created_at', String, nullable=False),
    UniqueConstraint('question_id', 'version'))
answers = Table('answers', metadata,
    Column('id', Integer, primary_key=True), Column('question_id', ForeignKey('questions.id'), nullable=False),
    Column('raw_text', Text, nullable=False), Column('participant_hash', String, nullable=False),
    Column('request_id', String, nullable=False), Column('status', String, nullable=False),
    Column('created_at', String, nullable=False),
    UniqueConstraint('question_id', 'request_id'), UniqueConstraint('question_id', 'participant_hash'))
Index('answer_round_status', answers.c.question_id, answers.c.status, answers.c.id)
terms = Table('answer_terms', metadata,
    Column('answer_id', ForeignKey('answers.id'), primary_key=True),
    Column('rule_set_id', ForeignKey('rule_sets.id'), primary_key=True),
    Column('term', String, primary_key=True))
Index('term_lookup', terms.c.rule_set_id, terms.c.term, terms.c.answer_id)
admins = Table('admin_users', metadata,
    Column('id', Integer, primary_key=True), Column('username', String, nullable=False, unique=True),
    Column('password_hash', String, nullable=False))
sessions = Table('admin_sessions', metadata,
    Column('id_hash', String, primary_key=True), Column('admin_id', ForeignKey('admin_users.id'), nullable=False),
    Column('expires_at', Integer, nullable=False))

@event.listens_for(engine, 'connect')
def sqlite_settings(conn, _):
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')

@contextmanager
def transaction():
    with write_lock, engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
