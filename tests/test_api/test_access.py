"""Access control with real tokens: login required, job ownership, capacity, rate limits, registration switch."""

import asyncio
from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from vtt_server.app import app
from vtt_server.auth import get_password_hash
from vtt_server.database import Base, get_db
from vtt_server.models import User
from vtt_server.routes.transcription import jobs

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

    asyncio.run(setup())

    async def override() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = override
    saved = dict(jobs)
    jobs.clear()
    try:
        yield TestClient(app)
    finally:
        jobs.clear()
        jobs.update(saved)
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/auth/token", data={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def token_of(headers: dict[str, str]) -> str:
    return headers["Authorization"].split(" ", 1)[1]


def make_job(job_id: str, user_id: int | None, status: str = "completed") -> None:
    job = {
        "job_id": job_id,
        "status": status,
        "filename": "a.wav",
        "result": "[00:00:00 - 00:00:02] Hello",
        "segments": [{"start": 0, "end": 2, "text": "Hello", "speaker": "SPEAKER_00"}],
    }
    if user_id is not None:
        job["user_id"] = user_id
    jobs[job_id] = job


# ---- login required ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/engines"),
        ("post", "/api/transcribe"),
        ("post", "/api/diarize"),
        ("post", "/api/detect-language"),
        ("post", "/api/translate"),
        ("get", "/api/jobs/x"),
        ("delete", "/api/jobs/x"),
        ("get", "/api/jobs/x/download"),
        ("get", "/api/jobs/x/segments"),
        ("get", "/api/jobs/x/speakers"),
        ("post", "/api/jobs/x/translate"),
    ],
)
def test_api_requires_login(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


def test_websocket_requires_login(client: TestClient) -> None:
    make_job("j1", 2)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/jobs/j1"):
        pass


def test_user_who_must_change_password_is_blocked(client: TestClient) -> None:
    bob = login(client, "bob")  # a token issued before the reset
    assert client.get("/api/engines", headers=bob).status_code == 200
    client.post("/admin/users/3/reset-password", headers=login(client, "admin"))
    assert client.get("/api/engines", headers=bob).status_code == 403


# ---- ownership ---------------------------------------------------------------------------------------------------


def test_owner_sees_job_others_get_404(client: TestClient) -> None:
    make_job("j1", 2)  # alice
    alice, bob, admin = login(client, "alice"), login(client, "bob"), login(client, "admin")
    assert client.get("/api/jobs/j1", headers=alice).status_code == 200
    assert client.get("/api/jobs/j1", headers=admin).status_code == 200
    assert client.get("/api/jobs/j1", headers=bob).status_code == 404
    assert client.get("/api/jobs/nope", headers=bob).status_code == 404  # indistinguishable from "not yours"


def test_job_status_hides_owner_id(client: TestClient) -> None:
    make_job("j1", 2)
    body = client.get("/api/jobs/j1", headers=login(client, "alice")).json()
    assert "user_id" not in body


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/jobs/j1/download"),
        ("get", "/api/jobs/j1/segments"),
        ("get", "/api/jobs/j1/speakers"),
        ("put", "/api/jobs/j1/speakers/SPEAKER_00"),
        ("post", "/api/jobs/j1/rediarize"),
        ("post", "/api/jobs/j1/translate"),
        ("delete", "/api/jobs/j1"),
    ],
)
def test_other_users_cannot_touch_a_job(client: TestClient, method: str, path: str) -> None:
    make_job("j1", 2)
    r = getattr(client, method)(path, headers=login(client, "bob"))
    assert r.status_code == 404
    assert "j1" in jobs  # nothing was deleted


def test_owner_can_delete_own_job(client: TestClient) -> None:
    make_job("j1", 2)
    assert client.delete("/api/jobs/j1", headers=login(client, "alice")).status_code == 204
    assert "j1" not in jobs


def test_job_without_owner_is_admin_only(client: TestClient) -> None:
    make_job("legacy", None)
    assert client.get("/api/jobs/legacy", headers=login(client, "alice")).status_code == 404
    assert client.get("/api/jobs/legacy", headers=login(client, "admin")).status_code == 200


def test_token_in_query_works_for_media_urls_but_only_for_the_owner(client: TestClient) -> None:
    make_job("j1", 2)
    alice, bob = token_of(login(client, "alice")), token_of(login(client, "bob"))
    assert client.get("/api/jobs/j1/speakers", params={"access_token": alice}).status_code == 200
    assert client.get("/api/jobs/j1/speakers", params={"access_token": bob}).status_code == 404
    assert client.get("/api/jobs/j1/speakers", params={"access_token": "garbage"}).status_code == 401


def test_websocket_owner_only(client: TestClient) -> None:
    make_job("j1", 2)
    with client.websocket_connect(f"/ws/jobs/j1?access_token={token_of(login(client, 'alice'))}") as ws:
        assert ws.receive_json()["job_id"] == "j1"
    with client.websocket_connect(f"/ws/jobs/j1?access_token={token_of(login(client, 'bob'))}") as ws:
        assert ws.receive_json()["error"] == "Job not found"


# ---- capacity ----------------------------------------------------------------------------------------------------


def test_per_user_cap_and_job_owner(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VTT_MAX_ACTIVE_JOBS_PER_USER", "1")
    monkeypatch.setenv("VTT_MAX_ACTIVE_JOBS", "0")
    make_job("running", 2, status="processing")
    files = {"file": ("a.mp3", b"x", "audio/mpeg")}
    alice, bob = login(client, "alice"), login(client, "bob")
    blocked = client.post("/api/diarize", files=files, headers=alice)
    assert blocked.status_code == 429
    ok = client.post("/api/diarize", files={"file": ("a.mp3", b"x", "audio/mpeg")}, headers=bob)
    assert ok.status_code == 200, ok.text
    assert jobs[ok.json()["job_id"]]["user_id"] == 3  # bob


def test_server_wide_cap_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VTT_MAX_ACTIVE_JOBS_PER_USER", "0")
    monkeypatch.setenv("VTT_MAX_ACTIVE_JOBS", "1")
    make_job("running", 3, status="pending")
    r = client.post("/api/diarize", files={"file": ("a.mp3", b"x", "audio/mpeg")}, headers=login(client, "alice"))
    assert r.status_code == 503
    assert r.headers["retry-after"] == "60"


def test_diarize_validates_file_type_and_size(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    h = login(client, "alice")
    bad_type = client.post("/api/diarize", files={"file": ("a.exe", b"x", "application/octet-stream")}, headers=h)
    assert bad_type.status_code == 400
    monkeypatch.setattr("vtt_server.routes.transcription.MAX_FILE_SIZE", 5)
    too_big = client.post("/api/diarize", files={"file": ("a.mp3", b"123456", "audio/mpeg")}, headers=h)
    assert too_big.status_code == 413


# ---- rate limits and registration --------------------------------------------------------------------------------


def test_login_is_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from vtt_server.routes.auth import login_limiter

    login_limiter.hits.clear()
    monkeypatch.setenv("VTT_LOGIN_RATE_LIMIT", "3")
    codes = [client.post("/auth/token", data={"username": "alice", "password": "wrong"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert int(client.post("/auth/token", data={"username": "alice", "password": "wrong"}).headers["retry-after"]) > 0
    login_limiter.hits.clear()


def test_registration_can_be_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"username": "carol", "email": "carol@example.com", "password": PASSWORD}
    monkeypatch.setenv("VTT_ALLOW_REGISTRATION", "false")
    r = client.post("/auth/register", json=body)
    assert r.status_code == 403
    assert r.json()["detail"] == "Registration is closed"
    monkeypatch.setenv("VTT_ALLOW_REGISTRATION", "true")
    assert client.post("/auth/register", json=body).status_code == 201


def test_rate_limiter_window_expires() -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    from vtt_server.access import RateLimiter

    limiter = RateLimiter("VTT_TEST_LIMIT_UNSET", 2, 1)
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))
    limiter(request)  # type: ignore[arg-type]
    limiter(request)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        limiter(request)  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    limiter.hits["1.2.3.4"][0] -= 5  # age both hits past the window
    limiter.hits["1.2.3.4"][1] -= 5
    limiter(request)  # type: ignore[arg-type]
