import os
import sys
import tempfile
from pathlib import Path

# Tests always use an isolated synthetic database, never the developer's .env data.
TEST_DATA = tempfile.TemporaryDirectory(prefix='wenjuan-tests-')
os.environ.update(APP_DATA_DIR=TEST_DATA.name, APP_SECRET='test-only-secret-not-for-production-1234567890', PUBLIC_ORIGIN='http://localhost:8000', COOKIE_SECURE='false', APP_ENV='test')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.db import engine, metadata, transaction
from app.main import app
from app.manage import migrate, init_admin
from app.security import buckets

migrate()

@pytest.fixture(autouse=True)
def fresh_database():
    with transaction() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(delete(table))
    buckets.clear()
    init_admin(password='synthetic-test-password-123')
    yield

@pytest.fixture
def client():
    with TestClient(app, base_url='http://localhost:8000') as value:
        yield value

@pytest.fixture
def admin(client):
    response = client.post('/api/admin/login', json={'username':'admin','password':'synthetic-test-password-123'})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    return client
