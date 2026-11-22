"""Admin API: list, deactivate, force reset, delete, and the guards around them (in-memory SQLite)."""

import asyncio
from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from vtt_server.app import app
from vtt_server.auth import get_password_hash
from vtt_server.database import Base, get_db
from vtt_server.models import TranscriptionJob, User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            hashed = get_password_hash(PASSWORD)
            db.add(User(username="admin", email="admin@example.com", hashed_password=hashed, is_superuser=True))
            db.add(User(username="alice", email="alice@example.com", hashed_password=hashed))
            db.add(User(username="bob", email="bob@example.com", hashed_password=hashed))
            await db.commit()
            db.add(TranscriptionJob(job_id="job-1", user_id=2, filename="a.wav", status="completed"))
            await db.commit()

    asyncio.run(setup())

    async def override() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/auth/token", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_requires_admin(client: TestClient) -> None:
    assert client.get("/admin/users").status_code == 401
    assert client.get("/admin/users", headers=login(client, "alice")).status_code == 403


def test_list_search_and_counts(client: TestClient) -> None:
    h = login(client, "admin")
    body = client.get("/admin/users", headers=h).json()
    assert body["total"] == 3
    assert {u["username"]: u["job_count"] for u in body["users"]} == {"admin": 0, "alice": 1, "bob": 0}
    assert "hashed_password" not in client.get("/admin/users", headers=h).text
    found = client.get("/admin/users", params={"q": "ali"}, headers=h).json()
    assert [u["username"] for u in found["users"]] == ["alice"]


def test_deactivate_blocks_login_and_existing_token(client: TestClient) -> None:
    alice_h = login(client, "alice")
    h = login(client, "admin")
    assert client.post("/admin/users/2/deactivate", headers=h).json()["is_active"] is False
    assert client.get("/auth/me", headers=alice_h).status_code == 400  # existing token stops working
    r = client.post("/auth/token", data={"username": "alice", "password": PASSWORD})
    assert r.status_code == 403
    assert client.post("/admin/users/2/activate", headers=h).json()["is_active"] is True
    login(client, "alice")


def test_force_reset_requires_change_before_use(client: TestClient) -> None:
    h = login(client, "admin")
    r = client.post("/admin/users/2/reset-password", headers=h)
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert len(temp) >= 8

    assert client.post("/auth/token", data={"username": "alice", "password": PASSWORD}).status_code == 401  # old one dead
    token = client.post("/auth/token", data={"username": "alice", "password": temp}).json()
    assert token["must_change_password"] is True
    ah = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.get("/auth/me", headers=ah).json()["must_change_password"] is True
    assert client.get("/user/jobs/", headers=ah).status_code == 403  # blocked until changed

    too_short = {"current_password": temp, "new_password": "short"}
    assert client.post("/auth/change-password", json=too_short, headers=ah).status_code == 400
    good = {"current_password": temp, "new_password": "a-brand-new-pass"}
    ok = client.post("/auth/change-password", json=good, headers=ah)
    assert ok.status_code == 204
    assert client.get("/user/jobs/", headers=ah).status_code == 200
    relogin = client.post("/auth/token", data={"username": "alice", "password": "a-brand-new-pass"})
    assert relogin.json()["must_change_password"] is False


def test_reset_with_chosen_password(client: TestClient) -> None:
    h = login(client, "admin")
    chosen = {"temporary_password": "temp-pass-123"}
    r = client.post("/admin/users/3/reset-password", json=chosen, headers=h)
    assert r.json()["temporary_password"] == "temp-pass-123"
    weak = {"temporary_password": "x"}
    assert client.post("/admin/users/3/reset-password", json=weak, headers=h).status_code == 400


def test_delete_user_removes_jobs(client: TestClient) -> None:
    h = login(client, "admin")
    assert client.delete("/admin/users/2", headers=h).status_code == 204
    assert client.get("/admin/users/2", headers=h).status_code == 404
    assert client.get("/admin/users", headers=h).json()["total"] == 2
    assert client.post("/auth/token", data={"username": "alice", "password": PASSWORD}).status_code == 401


def test_guards_on_self_and_last_admin(client: TestClient) -> None:
    h = login(client, "admin")
    assert client.delete("/admin/users/1", headers=h).status_code == 400
    assert client.post("/admin/users/1/deactivate", headers=h).status_code == 400
    assert client.post("/admin/users/1/remove-admin", headers=h).status_code == 400
    # a second admin can demote the first, but only while another active admin remains
    assert client.post("/admin/users/2/make-admin", headers=h).json()["is_superuser"] is True
    h2 = login(client, "alice")
    assert client.post("/admin/users/1/remove-admin", headers=h2).status_code == 200
    assert client.post("/admin/users/3/deactivate", headers=h).status_code == 403  # the demoted admin lost access


def test_unknown_user_404(client: TestClient) -> None:
    h = login(client, "admin")
    assert client.get("/admin/users/999", headers=h).status_code == 404
    assert client.delete("/admin/users/999", headers=h).status_code == 404
