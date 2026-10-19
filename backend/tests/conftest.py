import os
import tempfile

# Configure before the app is imported.
_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long!!"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import bus  # noqa: E402
from app.db import Base, SessionLocal, engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_state():
    init_db()
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    bus.set_clients(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    yield sync
    bus.set_clients(None, None)
    Base.metadata.drop_all(engine)


@pytest.fixture
def r(fresh_state):
    return fresh_state


@pytest.fixture
def session_factory():
    return SessionLocal


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def register(client, email="ada@example.com", password="correct-horse"):
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return register(client)
