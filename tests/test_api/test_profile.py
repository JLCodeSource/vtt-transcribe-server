"""Profile settings: retention default, notification opt-out, stored OpenAI key, password change, account deletion."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests import TEST_SECRET_KEY
from tests.conftest import OpenDb
from vtt_server import mailer
from vtt_server.app import app
from vtt_server.auth import get_password_hash
from vtt_server.database import get_db
from vtt_server.models import APIKey, User
from vtt_server.routes import transcription as tx

PASSWORD = "correct-horse-battery"
SECRET = "sk-test-0123456789abcdef"


@pytest.fixture
def db(memory_db: OpenDb) -> async_sessionmaker[AsyncSession]:
    async def seed(session: AsyncSession) -> None:
        hashed = get_password_hash(PASSWORD)
        session.add(User(username="admin", email="admin@example.com", hashed_password=hashed, is_superuser=True))
        session.add(User(username="alice", email="alice@example.com", hashed_password=hashed))
        await session.commit()

    return memory_db(seed)


@pytest.fixture
def client(db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[TestClient]:
    monkeypatch.setattr("vtt_server.auth.SECRET_KEY", TEST_SECRET_KEY)  # other tests reload auth
    monkeypatch.setenv("VTT_DATA_DIR", str(tmp_path))

    async def override() -> AsyncGenerator[AsyncSession, None]:
        async with db() as session:
            yield session

    app.dependency_overrides[get_db] = override
    saved = dict(tx.jobs)
    tx.jobs.clear()
    yield TestClient(app)
    tx.jobs.clear()
    tx.jobs.update(saved)
    app.dependency_overrides.clear()


def login(client: TestClient, username: str = "alice", password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/auth/token", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_profile_requires_login(client: TestClient) -> None:
    assert client.get("/api/profile").status_code == 401


def test_defaults(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VTT_DEFAULT_RETENTION_DAYS", raising=False)
    profile = client.get("/api/profile", headers=login(client)).json()
    assert profile["retention_mode"] == "default"
    assert profile["effective_retention"] == {"mode": "hold", "days": 7}
    assert profile["notify_job_ready"] is True
    assert profile["has_openai_key"] is False
    assert profile["openai_key_hint"] is None


def test_retention_default_and_email_opt_out(client: TestClient) -> None:
    h = login(client)
    r = client.patch(
        "/api/profile", headers=h, json={"retention_mode": "hold", "retention_days": 14, "notify_job_ready": False}
    )
    assert r.status_code == 200
    assert r.json()["effective_retention"] == {"mode": "hold", "days": 14}
    assert r.json()["notify_job_ready"] is False
    assert (
        client.patch("/api/profile", headers=h, json={"retention_mode": "keep"}).json()["effective_retention"]["mode"]
        == "keep"
    )
    assert client.patch("/api/profile", headers=h, json={"retention_mode": "hold", "retention_days": 0}).status_code == 422
    assert client.patch("/api/profile", headers=h, json={"retention_mode": "nope"}).status_code == 422
    back = client.patch("/api/profile", headers=h, json={"retention_mode": "default"}).json()
    assert back["retention_mode"] == "default"
    assert back["effective_retention"]["mode"] == "hold"


def test_default_options_are_validated(client: TestClient) -> None:
    h = login(client)
    ok = client.patch("/api/profile", headers=h, json={"default_options": {"engine": "local", "diarize": True, "junk": 1}})
    assert ok.json()["default_options"] == {"engine": "local", "diarize": True}
    assert client.patch("/api/profile", headers=h, json={"default_options": {"engine": "bogus"}}).status_code == 422


def test_default_speakers_and_translation_language(client: TestClient) -> None:
    h = login(client)
    ok = client.patch("/api/profile", headers=h, json={"default_options": {"num_speakers": "3", "translate_to": "Spanish"}})
    assert ok.json()["default_options"] == {"translate_to": "Spanish", "num_speakers": 3}
    for bad in (0, 9, "many"):
        assert client.patch("/api/profile", headers=h, json={"default_options": {"num_speakers": bad}}).status_code == 422


def test_openai_key_is_stored_encrypted_masked_and_replaceable(
    client: TestClient, db: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    h = login(client)
    with caplog.at_level(logging.DEBUG):
        r = client.put("/api/profile/openai-key", headers=h, json={"api_key": SECRET})
        profile = client.get("/api/profile", headers=h)
    assert r.status_code == 200
    assert r.json()["has_openai_key"] is True
    assert r.json()["openai_key_hint"] == "****cdef"
    assert SECRET not in r.text
    assert SECRET not in profile.text
    assert SECRET not in caplog.text

    async def stored() -> list[str]:
        async with db() as session:
            return [k.encrypted_key for k in (await session.execute(select(APIKey))).scalars()]

    rows = asyncio.run(stored())
    assert len(rows) == 1
    assert SECRET not in rows[0]
    replaced = client.put("/api/profile/openai-key", headers=h, json={"api_key": "sk-another-key-9999"})
    assert replaced.json()["openai_key_hint"] == "****9999"
    assert len(asyncio.run(stored())) == 1
    assert client.put("/api/profile/openai-key", headers=h, json={"api_key": "short"}).status_code == 422
    removed = client.delete("/api/profile/openai-key", headers=h)
    assert removed.json()["has_openai_key"] is False
    assert asyncio.run(stored()) == []


def test_the_stored_key_is_used_when_a_request_carries_none(client: TestClient, db: async_sessionmaker[AsyncSession]) -> None:
    from vtt_server.jobstore import store

    h = login(client)
    client.put("/api/profile/openai-key", headers=h, json={"api_key": SECRET})
    store.enabled, store.session_factory = True, db
    try:
        with patch("vtt_server.jobqueue.JobQueue.ensure_running"):
            r = client.post(
                "/api/transcribe", headers=h, files={"file": ("a.mp3", b"x", "audio/mpeg")}, data={"engine": "openai"}
            )
        assert r.status_code == 200  # no api_key in the request, yet accepted
        assert "secret" not in tx.jobs[r.json()["job_id"]]  # resolved at run time, nothing stored per job
        assert asyncio.run(tx.resolve_openai_key(2, None)) == SECRET
    finally:
        store.enabled, store.session_factory = False, None
    # without a stored key the request is still refused
    client.delete("/api/profile/openai-key", headers=h)
    r = client.post("/api/transcribe", headers=h, files={"file": ("a.mp3", b"x", "audio/mpeg")}, data={"engine": "openai"})
    assert r.status_code == 422


def test_change_password(client: TestClient) -> None:
    h = login(client)
    r = client.post(
        "/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": "a-brand-new-pass"}
    )
    assert r.status_code == 204
    assert client.post("/auth/token", data={"username": "alice", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/token", data={"username": "alice", "password": "a-brand-new-pass"}).status_code == 200


def test_delete_account_removes_user_jobs_and_audio(client: TestClient, db: async_sessionmaker[AsyncSession]) -> None:
    h = login(client)
    client.put("/api/profile/openai-key", headers=h, json={"api_key": SECRET})
    tx.jobs["j1"] = {"job_id": "j1", "user_id": 2, "status": "completed", "filename": "a.mp3"}
    tx.jobs["other"] = {"job_id": "other", "user_id": 1, "status": "completed", "filename": "b.mp3"}
    tx._job_dir("j1").mkdir(parents=True)
    (tx._job_dir("j1") / "input.mp3").write_bytes(b"x")

    assert client.request("DELETE", "/api/profile", headers=h, json={"password": "wrong"}).status_code == 400
    assert client.request("DELETE", "/api/profile", headers=h, json={"password": PASSWORD}).status_code == 204

    assert set(tx.jobs) == {"other"}
    assert not tx._job_dir("j1").exists()

    async def counts() -> tuple[int, int]:
        async with db() as session:
            users = len((await session.execute(select(User))).scalars().all())
            keys = len((await session.execute(select(APIKey))).scalars().all())
            return users, keys

    assert asyncio.run(counts()) == (1, 0)
    assert client.post("/auth/token", data={"username": "alice", "password": PASSWORD}).status_code == 401


def test_last_administrator_cannot_delete_themselves(client: TestClient) -> None:
    h = login(client, "admin")
    r = client.request("DELETE", "/api/profile", headers=h, json={"password": PASSWORD})
    assert r.status_code == 400


def test_registration_sends_a_welcome_email_when_smtp_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []

    class Fake:
        def __init__(self, _settings: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> "Fake":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def starttls(self, **_: Any) -> None:
            return None

        def login(self, *_: Any) -> None:
            return None

        def send_message(self, msg: Any) -> None:
            sent.append(msg)

    monkeypatch.setattr(mailer, "transport_factory", Fake)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("VTT_REGISTER_RATE_LIMIT", "0")
    r = client.post("/auth/register", json={"username": "carol", "email": "carol@example.com", "password": PASSWORD})
    assert r.status_code == 201
    # TestClient drops its loop after the request; run the pending welcome send the way the server would
    subject, text, body = mailer.welcome_message("carol")
    asyncio.run(mailer.send_email("carol@example.com", subject, text, body))
    assert sent[-1]["To"] == "carol@example.com"
    assert sent[-1]["Subject"] == "Welcome to VTT Transcribe"


def test_registration_works_without_smtp(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    r = client.post("/auth/register", json={"username": "dave", "email": "dave@example.com", "password": PASSWORD})
    assert r.status_code == 201
