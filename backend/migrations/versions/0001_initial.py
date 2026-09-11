"""Initial schema, frozen for v0.1.0."""
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Frozen DDL: do not import mutable application metadata into a migration.
    statements = [
        "CREATE TABLE questions (id INTEGER PRIMARY KEY, text TEXT NOT NULL, hint TEXT NOT NULL, status VARCHAR NOT NULL, created_at VARCHAR NOT NULL, version INTEGER NOT NULL DEFAULT 1, rule_id INTEGER)",
        "CREATE UNIQUE INDEX one_open_question ON questions(status) WHERE status = 'open'",
        "CREATE TABLE rule_sets (id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES questions(id), version INTEGER NOT NULL, config TEXT NOT NULL, status VARCHAR NOT NULL, created_at VARCHAR NOT NULL, UNIQUE(question_id,version))",
        "CREATE TABLE answers (id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES questions(id), raw_text TEXT NOT NULL, participant_hash VARCHAR NOT NULL, request_id VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at VARCHAR NOT NULL, UNIQUE(question_id,request_id), UNIQUE(question_id,participant_hash))",
        "CREATE INDEX answer_round_status ON answers(question_id,status,id)",
        "CREATE TABLE answer_terms (answer_id INTEGER NOT NULL REFERENCES answers(id), rule_set_id INTEGER NOT NULL REFERENCES rule_sets(id), term VARCHAR NOT NULL, PRIMARY KEY(answer_id,rule_set_id,term))",
        "CREATE INDEX term_lookup ON answer_terms(rule_set_id,term,answer_id)",
        "CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, password_hash VARCHAR NOT NULL)",
        "CREATE TABLE admin_sessions (id_hash VARCHAR PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admin_users(id), expires_at INTEGER NOT NULL)",
    ]
    for statement in statements:
        op.execute(statement)

def downgrade():
    for name in ['admin_sessions','admin_users','answer_terms','answers','rule_sets','questions']:
        op.drop_table(name)
