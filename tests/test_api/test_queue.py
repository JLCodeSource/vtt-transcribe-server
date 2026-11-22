"""The job queue: FIFO single worker, persistence and recovery, progress, cancel, retention, email."""

import asyncio
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from vtt_server import jobqueue, mailer, retention
from vtt_server.app import app
from vtt_server.auth import get_password_hash
from vtt_server.database import Base, get_db
from vtt_server.jobstore import store
from vtt_server.models import User
from vtt_server.routes import transcription as tx

PASSWORD = "correct-horse-battery"


def make_job(job_id: str, user_id: int = 1, **extra: Any) -> dict[str, Any]:
    job = {"job_id": job_id, "user_id": user_id, "filename": f"{job_id}.mp3", "status": "pending", "step": "queued",
           "progress": 0.0, "queue_seq": jobqueue.next_seq(), "engine": "local", "created_at": datetime.now(UTC).isoformat(),
           "retention_mode": "hold", "retention_days": 7}  # fmt: skip
    job.update(extra)
    return job


@pytest.fixture
def clean_jobs() -> Iterator[dict[str, dict[str, Any]]]:
    saved = dict(tx.jobs)
    tx.jobs.clear()
    yield tx.jobs
    tx.jobs.clear()
    tx.jobs.update(saved)


@pytest.fixture
def db() -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            hashed = get_password_hash(PASSWORD)
            session.add(User(username="alice", email="alice@example.com", hashed_password=hashed))
            session.add(User(username="bob", email="bob@example.com", hashed_password=hashed))
            await session.commit()

    asyncio.run(setup())
    store.enabled = True
    store.session_factory = maker
    yield maker
    store.enabled = False
    asyncio.run(engine.dispose())


# ---- worker ----


def test_worker_runs_jobs_one_at_a_time_in_submission_order(clean_jobs: dict[str, dict[str, Any]]) -> None:
    events: list[str] = []

    async def fake_run(job: dict[str, Any]) -> None:
        events.append(f"start {job['job_id']}")
        job["status"] = "processing"
        await asyncio.sleep(0.02)
        job["status"] = "completed"
        events.append(f"end {job['job_id']}")

    async def scenario() -> None:
        for name in ("a", "b", "c"):
            clean_jobs[name] = make_job(name)
        with patch.object(tx, "run_job", fake_run):
            jobqueue.queue.ensure_running()
            for _ in range(200):
                if all(j["status"] == "completed" for j in clean_jobs.values()):
                    break
                await asyncio.sleep(0.01)
        await jobqueue.queue.stop()

    asyncio.run(scenario())
    assert events == ["start a", "end a", "start b", "end b", "start c", "end c"]


def test_queue_position_and_cancel_waiting_job(clean_jobs: dict[str, dict[str, Any]]) -> None:
    first, second, third = (make_job(n) for n in "abc")
    clean_jobs.update(a=first, b=second, c=third)
    first["status"] = "processing"
    assert jobqueue.queue_position(clean_jobs, first) is None
    assert jobqueue.queue_position(clean_jobs, second) == 1
    assert jobqueue.queue_position(clean_jobs, third) == 2
    assert jobqueue.queue.cancel(second) == "cancelled"
    assert second["completed_at"] and second["expires_at"]
    assert jobqueue.queue_position(clean_jobs, third) == 1
    assert jobqueue.queue.cancel(first) == "processing"  # running: flagged, stops at its next checkpoint
    assert first["cancel_requested"]


def test_overall_progress_is_weighted_by_the_steps_the_job_needs() -> None:
    plain = {"diarize": False}
    full = {"diarize": True, "translate_to": "French"}
    assert jobqueue.overall(plain, "detecting", 0) == 0.0
    assert jobqueue.overall(plain, "transcribing", 0.5) == pytest.approx((0.05 + 0.3) / 0.65, abs=1e-3)
    assert jobqueue.overall(full, "diarizing", 0) == pytest.approx(0.65, abs=1e-3)
    assert jobqueue.overall(full, "translating", 1) == 1.0
    assert jobqueue.plan_for({"diarize_only": True}) == ["diarizing"]


def test_progress_callback_moves_the_bar_inside_a_step(clean_jobs: dict[str, dict[str, Any]]) -> None:
    seen: list[float] = []

    class Transcriber:
        def detect_language(self, _path: Any) -> str:
            return "en"

        def transcribe(self, _path: Any, progress_callback: Any = None) -> str:
            progress_callback(0.5, "half way")
            seen.append(clean_jobs["p"]["progress"])
            return "[00:00 - 00:05] hello"

    job = make_job("p", audio_path="/nonexistent/in.mp3")
    clean_jobs["p"] = job
    with patch.object(tx, "make_transcriber", return_value=Transcriber()):
        asyncio.run(tx._process_transcription("p", None, "p.mp3", "k"))
    assert job["status"] == "completed"
    assert job["progress"] == 1.0
    assert 0 < seen[0] < 1


def test_cores_without_progress_callback_are_called_plainly(clean_jobs: dict[str, dict[str, Any]]) -> None:
    job = make_job("q")
    mock = MagicMock(return_value="x")
    assert not jobqueue.accepts_progress(mock)
    assert jobqueue.with_progress(mock, job, "transcribing") is mock


def test_running_job_stops_at_a_checkpoint_when_cancelled(clean_jobs: dict[str, dict[str, Any]]) -> None:
    class Transcriber:
        def detect_language(self, _path: Any) -> str:
            clean_jobs["c"]["cancel_requested"] = True  # the owner cancels while we work
            return "en"

        def transcribe(self, _path: Any) -> str:  # pragma: no cover - must not be reached
            raise AssertionError("cancelled job kept running")

    clean_jobs["c"] = make_job("c", audio_path="/nonexistent/in.mp3")
    with patch.object(tx, "make_transcriber", return_value=Transcriber()):
        asyncio.run(tx._process_transcription("c", None, "c.mp3", "k"))
    assert clean_jobs["c"]["status"] == "cancelled"
    assert not clean_jobs["c"].get("cancel_requested")


# ---- persistence and recovery ----


def test_jobs_survive_a_restart_and_unfinished_ones_are_requeued(
    clean_jobs: dict[str, dict[str, Any]], db: async_sessionmaker[AsyncSession]
) -> None:
    done = make_job(
        "done", status="completed", step="done", progress=1.0, result="Alice: hi", segments=[{"start": 0, "end": 1, "text": "hi"}],
        speaker_names={"S": "Alice"}, completed_at="2026-01-01T00:00:00+00:00",
    )  # fmt: skip
    running = make_job("running", status="processing", step="transcribing", progress=0.4, secret="enc")
    waiting = make_job("waiting")

    async def scenario() -> int:
        for job in (done, running, waiting):
            await store.save(job)
        clean_jobs.clear()  # "restart"
        with patch.object(jobqueue.queue, "ensure_running"):
            return await jobqueue.recover(clean_jobs)

    assert asyncio.run(scenario()) == 2
    assert clean_jobs["done"]["status"] == "completed"
    assert clean_jobs["done"]["segments"] == done["segments"]
    assert clean_jobs["done"]["speaker_names"] == {"S": "Alice"}
    assert clean_jobs["running"]["status"] == "pending"
    assert clean_jobs["running"]["secret"] == "enc"  # the encrypted per-job key is kept until it ends
    assert [j["job_id"] for j in jobqueue.waiting(clean_jobs)] == ["running", "waiting"]  # FIFO preserved


def test_removing_a_job_deletes_its_row(clean_jobs: dict[str, dict[str, Any]], db: async_sessionmaker[AsyncSession]) -> None:
    async def scenario() -> list[dict[str, Any]]:
        clean_jobs["x"] = make_job("x")
        await store.save(clean_jobs["x"])
        await tx.remove_job("x")
        return await store.load_all()

    assert asyncio.run(scenario()) == []
    assert "x" not in clean_jobs


# ---- retention ----

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def finished(**extra: Any) -> dict[str, Any]:
    return make_job("f", status="completed", completed_at=NOW.isoformat(), **extra)


def test_hold_expires_after_n_days(monkeypatch: pytest.MonkeyPatch) -> None:
    job = finished(retention_mode="hold", retention_days=3)
    assert retention.compute_expiry(job) == NOW + timedelta(days=3)
    assert not retention.is_expired({**job, "expires_at": None}, NOW + timedelta(days=2, hours=23))
    assert retention.is_expired({**job, "expires_at": None}, NOW + timedelta(days=3))


def test_keep_never_expires_and_unfinished_jobs_have_no_expiry() -> None:
    assert retention.compute_expiry(finished(retention_mode="keep")) is None
    assert retention.compute_expiry(make_job("p")) is None


def test_delete_policy_uses_grace_after_download_else_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VTT_DELETE_GRACE_MINUTES", raising=False)
    monkeypatch.delenv("VTT_DELETE_UNDOWNLOADED_HOURS", raising=False)
    job = finished(retention_mode="delete")
    assert retention.compute_expiry(job) == NOW + timedelta(hours=24)
    job["downloaded_at"] = (NOW + timedelta(hours=1)).isoformat()
    assert retention.compute_expiry(job) == NOW + timedelta(hours=1, minutes=15)


def test_defaults_and_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VTT_DEFAULT_RETENTION_DAYS", raising=False)
    assert retention.platform_policy() == {"mode": "hold", "days": 7}
    monkeypatch.setenv("VTT_DEFAULT_RETENTION_DAYS", "30")
    assert retention.resolve(None, None, None, None) == {"mode": "hold", "days": 30}
    assert retention.resolve(None, None, "keep", None) == {"mode": "keep", "days": None}  # the user's default
    assert retention.resolve("delete", None, "keep", None) == {"mode": "delete", "days": None}  # the item wins
    assert retention.resolve("hold", None, None, None)["days"] == 30
    with pytest.raises(retention.RetentionError):
        retention.normalize("forever", None)
    with pytest.raises(retention.RetentionError):
        retention.normalize("hold", 0)


def test_sweep_deletes_only_expired_finished_jobs_with_an_injected_clock(
    clean_jobs: dict[str, dict[str, Any]], tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VTT_DATA_DIR", str(tmp_path))
    for name, days in (("old", 1), ("fresh", 30)):
        job = finished(retention_mode="hold", retention_days=days)
        job["job_id"] = name
        retention.apply_expiry(job)
        clean_jobs[name] = job
        tx._job_dir(name).mkdir(parents=True)
        (tx._job_dir(name) / "input.mp3").write_bytes(b"x")
    clean_jobs["kept"] = {**finished(retention_mode="keep"), "job_id": "kept"}
    clean_jobs["busy"] = make_job("busy", status="processing")
    removed = asyncio.run(tx.sweep_expired(NOW + timedelta(days=2)))
    assert removed == ["old"]
    assert set(clean_jobs) == {"fresh", "kept", "busy"}
    assert not tx._job_dir("old").exists()
    assert tx._job_dir("fresh").exists()


# ---- API ----


@pytest.fixture
def client(db: async_sessionmaker[AsyncSession], clean_jobs: dict[str, dict[str, Any]], tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("VTT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("vtt_server.auth.SECRET_KEY", "test-secret-key-for-jwt-signing-only")  # other tests reload auth

    async def override() -> AsyncGenerator[AsyncSession, None]:
        async with db() as session:
            yield session

    app.dependency_overrides[get_db] = override
    with patch.object(jobqueue.JobQueue, "ensure_running"):
        yield TestClient(app)
    app.dependency_overrides.clear()


def login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/auth/token", data={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def submit(client: TestClient, headers: dict[str, str], **data: str) -> dict[str, Any]:
    r = client.post("/api/transcribe", headers=headers, files={"file": ("a.mp3", b"audio", "audio/mpeg")},
                    data={"engine": "local", **data})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_submissions_are_queued_persisted_and_listed_with_positions(client: TestClient, db: Any) -> None:
    alice = login(client, "alice")
    with patch("vtt_server.engines.local_engine_available", return_value=True):
        first = submit(client, alice)
        second = submit(client, alice, retention_mode="keep")
    assert first["status"] == "pending"
    listing = client.get("/api/jobs", headers=alice).json()
    assert [j["job_id"] for j in listing["jobs"]] == [second["job_id"], first["job_id"]]  # newest first
    by_id = {j["job_id"]: j for j in listing["jobs"]}
    assert by_id[first["job_id"]]["queue_position"] == 1
    assert by_id[second["job_id"]]["queue_position"] == 2
    assert by_id[first["job_id"]]["step"] == "queued"
    assert by_id[first["job_id"]]["retention_mode"] == "hold"  # platform default
    assert by_id[first["job_id"]]["retention_days"] == 7
    assert by_id[second["job_id"]]["retention_mode"] == "keep"
    assert "secret" not in by_id[first["job_id"]]
    assert tx._job_dir(first["job_id"]).joinpath("input.mp3").exists()  # audio is on disk
    rows = asyncio.run(store.load_all())
    assert {r["job_id"] for r in rows} == {first["job_id"], second["job_id"]}
    assert client.get("/api/jobs", headers=login(client, "bob")).json()["jobs"] == []  # other users see nothing


def test_cancel_and_retention_endpoints(client: TestClient) -> None:
    alice = login(client, "alice")
    with patch("vtt_server.engines.local_engine_available", return_value=True):
        job_id = submit(client, alice)["job_id"]
    changed = client.put(f"/api/jobs/{job_id}/retention", headers=alice, json={"mode": "hold", "days": 2})
    assert changed.status_code == 200 and changed.json()["retention_days"] == 2
    assert client.put(f"/api/jobs/{job_id}/retention", headers=alice, json={"mode": "bogus"}).status_code == 422
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=login(client, "bob")).status_code == 404
    cancelled = client.post(f"/api/jobs/{job_id}/cancel", headers=alice)
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["expires_at"]
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=alice).status_code == 409


def test_user_default_retention_applies_and_item_overrides(client: TestClient, db: Any) -> None:
    async def set_default() -> None:
        async with db() as session:
            alice = (await session.execute(__import__("sqlalchemy").select(User).where(User.username == "alice"))).scalar_one()
            alice.retention_mode = "delete"
            await session.commit()

    asyncio.run(set_default())
    alice = login(client, "alice")
    with patch("vtt_server.engines.local_engine_available", return_value=True):
        inherited = submit(client, alice)["job_id"]
        override = submit(client, alice, retention_mode="hold", retention_days="30")["job_id"]
        bad = client.post("/api/transcribe", headers=alice, files={"file": ("a.mp3", b"x", "audio/mpeg")},
                          data={"engine": "local", "retention_mode": "hold", "retention_days": "0"})  # fmt: skip
    assert tx.jobs[inherited]["retention_mode"] == "delete"
    assert (tx.jobs[override]["retention_mode"], tx.jobs[override]["retention_days"]) == ("hold", 30)
    assert bad.status_code == 422


def test_download_marks_the_job_downloaded_and_starts_the_delete_grace(client: TestClient) -> None:
    alice = login(client, "alice")
    with patch("vtt_server.engines.local_engine_available", return_value=True):
        job_id = submit(client, alice, retention_mode="delete")["job_id"]
    job = tx.jobs[job_id]
    segments = [{"start": 0, "end": 2, "text": "hello", "speaker": "SPEAKER_00"}]
    job.update(status="completed", completed_at=datetime.now(UTC).isoformat(), result="x")
    tx.set_segments(job, segments)
    retention.apply_expiry(job)
    before = job["expires_at"]
    assert client.get(f"/api/jobs/{job_id}/download", headers=alice).status_code == 200
    assert job["downloaded_at"]
    assert job["expires_at"] < before  # 15 minutes after download instead of 24 hours after finishing


def test_openai_key_falls_back_to_the_profile_key(clean_jobs: dict[str, dict[str, Any]], db: Any) -> None:
    from vtt_server.models import APIKey
    from vtt_server.routes.api_keys import encrypt_key

    async def scenario() -> tuple[str | None, str | None, str | None]:
        async with db() as session:
            session.add(APIKey(user_id=1, service="openai", encrypted_key=encrypt_key("sk-stored")))
            await session.commit()
        return (
            await tx.resolve_openai_key(1, None),
            await tx.resolve_openai_key(1, "sk-explicit"),
            await tx.resolve_openai_key(2, None),
        )

    assert asyncio.run(scenario()) == ("sk-stored", "sk-explicit", None)


# ---- email ----


class FakeSMTP:
    sent: list[Any] = []  # noqa: RUF012
    logins: list[tuple[str, str]] = []  # noqa: RUF012
    started_tls = False

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self, **_: Any) -> None:
        FakeSMTP.started_tls = True

    def login(self, user: str, password: str) -> None:
        FakeSMTP.logins.append((user, password))

    def send_message(self, msg: Any) -> None:
        FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.started_tls = [], [], False
    monkeypatch.setattr(mailer, "transport_factory", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SMTP_FROM", "VTT <vtt@example.com>")
    monkeypatch.setenv("VTT_PUBLIC_URL", "https://vtt.example.com/")
    return FakeSMTP


def test_email_is_skipped_without_smtp_and_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.email_configured() is False
    assert mailer.send_email_sync("a@example.com", "Hi", "text", "<p>html</p>") is False
    mailer.notify_welcome("a@example.com", "alice")  # no error, nothing sent
    mailer.notify_job_finished({"job_id": "j", "user_id": 1})


def test_email_is_sent_through_the_transport_with_text_and_html(smtp: type[FakeSMTP]) -> None:
    assert mailer.send_email_sync("alice@example.com", "Subject\r\nBcc: evil@example.com", "plain", "<b>rich</b>") is True
    msg = smtp.sent[0]
    assert msg["To"] == "alice@example.com"
    assert msg["From"] == "VTT <vtt@example.com>"
    assert "\n" not in msg["Subject"]  # no header injection
    assert msg.get_body(("plain",)).get_content().strip() == "plain"
    assert "<b>rich</b>" in msg.get_body(("html",)).get_content()
    assert smtp.started_tls
    assert smtp.logins == [("mailer", "pw")]


def test_send_failures_are_logged_not_raised(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_settings: dict[str, Any]) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(mailer, "transport_factory", boom)
    assert mailer.send_email_sync("a@example.com", "Hi", "t", "<p>t</p>") is False


def test_job_message_links_to_the_job_and_escapes_html(smtp: type[FakeSMTP]) -> None:
    job = {"job_id": "abc", "filename": "<script>.mp3", "status": "completed", "expires_at": "2026-06-08T12:00:00+00:00"}
    subject, text, body = mailer.job_message("al<ice>", job)
    assert "https://vtt.example.com/?job=abc" in text and "https://vtt.example.com/?job=abc" in body
    assert "<script>" not in body and "al<ice>" not in body
    assert "2026-06-08" in text
    failed = mailer.job_message("alice", {"job_id": "x", "filename": "f.mp3", "status": "failed", "error": "boom"})
    assert "failed" in failed[0].lower()
    assert "boom" in failed[1]


def test_finished_job_emails_the_owner_unless_opted_out(
    smtp: type[FakeSMTP], clean_jobs: dict[str, dict[str, Any]], db: Any
) -> None:
    async def scenario(*, opt_out: bool) -> None:
        if opt_out:
            async with db() as session:
                alice = await session.get(User, 1)
                alice.notify_job_ready = False
                await session.commit()
        job = make_job("n", user_id=1, status="processing")
        clean_jobs["n"] = job
        await tx._finish(job, "completed")
        await asyncio.gather(*list(mailer._tasks))

    asyncio.run(scenario(opt_out=False))
    assert len(smtp.sent) == 1
    assert smtp.sent[0]["To"] == "alice@example.com"
    smtp.sent.clear()
    asyncio.run(scenario(opt_out=True))
    assert smtp.sent == []


def test_attach_progress_sets_the_core_attribute_and_reports_within_the_step() -> None:
    job = make_job("a", diarize=True)
    obj = MagicMock()
    callback = jobqueue.attach_progress(obj, job, "diarizing")
    assert obj.progress_callback is callback
    callback(0.5, "Identifying speakers")
    assert job["progress"] == jobqueue.overall(job, "diarizing", 0.5)
    assert job["message"] == "Identifying speakers"
    job["cancel_requested"] = True
    with pytest.raises(jobqueue.JobCancelled):
        callback(0.6, "x")  # a cancel aborts the engine at its next report


def test_run_diarizer_hands_the_callback_to_the_backend() -> None:
    backend = MagicMock()
    backend.diarize_audio.return_value = []
    cb = MagicMock()
    with patch.object(tx, "build_diarizer", return_value=backend):
        tx.run_diarizer(MagicMock(), 2, cb)
    assert backend.progress_callback is cb
