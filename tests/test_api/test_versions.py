"""Every version is kept: the original text, each translation, each diarization (see vtt_server/versions.py)."""

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from vtt_transcribe import speakers

from vtt_server import jobqueue, versions
from vtt_server.database import Base
from vtt_server.jobstore import store
from vtt_server.models import User
from vtt_server.routes.transcription import jobs

TRANSCRIPT = (
    "[00:00:00 - 00:00:05] SPEAKER_00: Hello there\n"
    "[00:00:05 - 00:00:12] SPEAKER_01: Hi, how are you\n"
    "[00:00:12 - 00:00:14] SPEAKER_00: Fine\n"
    "[00:00:14 - 00:00:20] SPEAKER_02: Me too"
)
SPANISH = "[00:00:00 - 00:00:05] Hola\n[00:00:05 - 00:00:12] Hola, como estas\n[00:00:12 - 00:00:14] Bien\n[00:00:14 - 00:00:20] Yo tambien"
FRENCH = "[00:00:00 - 00:00:05] Salut\n[00:00:05 - 00:00:12] Salut, ca va\n[00:00:12 - 00:00:14] Bien\n[00:00:14 - 00:00:20] Moi aussi"
ORIGINAL = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in speakers.parse_transcript(TRANSCRIPT)]

pytestmark = pytest.mark.usefixtures("fake_auth")


@pytest.fixture
def db() -> Iterator[Any]:
    """An in-memory database behind the job store (as in test_queue)."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            session.add(User(username="alice", email="alice@example.com", hashed_password="x"))
            await session.commit()

    asyncio.run(setup())
    store.enabled = True
    store.session_factory = maker
    yield maker
    store.enabled = False
    store.session_factory = None


@pytest.fixture
def client() -> TestClient:
    from vtt_server.app import app

    return TestClient(app)


@pytest.fixture
def job() -> Iterator[str]:
    job_id = "ver-job"
    segs = speakers.parse_transcript(TRANSCRIPT)
    jobs[job_id] = {"job_id": job_id, "status": "completed", "filename": "a.mp3", "diarize": True, "engine": "openai"}
    versions.set_original(jobs[job_id], segs)
    versions.add_diarization(jobs[job_id], [], None, labels=[s["speaker"] for s in segs])
    yield job_id
    jobs.pop(job_id, None)


def translate(client: TestClient, job: str, language: str, text: str, **extra: Any) -> Any:
    with patch("vtt_server.routes.transcription.AudioTranslator") as translator:
        translator.return_value.translate_transcript.return_value = text
        return client.post(f"/api/jobs/{job}/translate", json={"target_language": language, "api_key": "k", **extra})


# ---- translations: added, never replacing the original ----


def test_original_is_unchanged_after_three_languages(client: TestClient, job: str) -> None:
    for language, text in (("Spanish", SPANISH), ("French", FRENCH), ("German", SPANISH)):
        assert translate(client, job, language, text).status_code == 200
    assert jobs[job]["segments"] == ORIGINAL
    assert set(jobs[job]["translations"]) == {"Spanish", "French", "German"}
    assert "Hello there" in jobs[job]["result"]  # the result stays in the spoken language
    listed = client.get(f"/api/jobs/{job}/versions").json()
    assert [t["language"] for t in listed["translations"]] == ["Spanish", "French", "German"]
    assert listed["original"]["segments"] == 4


def test_translations_are_selected_by_language(client: TestClient, job: str) -> None:
    translate(client, job, "Spanish", SPANISH)
    translate(client, job, "French", FRENCH)
    seg = client.get(f"/api/jobs/{job}/segments", params={"language": "Spanish"}).json()
    assert seg["language"] == "Spanish"
    assert seg["segments"][0]["translation"] == "Hola"
    assert seg["segments"][0]["text"] == "Hello there"
    french = client.get(f"/api/jobs/{job}/segments", params={"language": "french"}).json()  # case-insensitive
    assert french["segments"][0]["translation"] == "Salut"
    original = client.get(f"/api/jobs/{job}/segments", params={"language": "original"}).json()
    assert original["segments"][0]["translation"] is None
    assert client.get(f"/api/jobs/{job}/segments", params={"language": "Klingon"}).status_code == 404


def test_translating_an_existing_language_costs_nothing_unless_asked(client: TestClient, job: str) -> None:
    translate(client, job, "Spanish", SPANISH)
    again = translate(client, job, "spanish", "[00:00:00 - 00:00:05] changed\n" * 4)
    assert again.json()["existing"] is True
    assert jobs[job]["translations"]["Spanish"]["texts"][0] == "Hola"
    forced = translate(client, job, "Spanish", FRENCH, replace=True)
    assert forced.json()["existing"] is False
    assert jobs[job]["translations"]["Spanish"]["texts"][0] == "Salut"
    assert len(jobs[job]["translations"]) == 1


def test_deleting_one_language_keeps_the_others_and_the_original(client: TestClient, job: str) -> None:
    translate(client, job, "Spanish", SPANISH)
    translate(client, job, "French", FRENCH)
    assert client.delete(f"/api/jobs/{job}/translations/Spanish").status_code == 200
    assert list(jobs[job]["translations"]) == ["French"]
    assert jobs[job]["segments"] == ORIGINAL
    assert client.delete(f"/api/jobs/{job}/translations/Spanish").status_code == 404
    assert client.delete(f"/api/jobs/{job}/translations/original").status_code == 400  # the original cannot be deleted


# ---- diarizations: each run is a version ----


def rediarize(client: TestClient, job: str, turns: list[tuple[float, float, str]], n: int | None, tmp_path: Any) -> Any:
    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"x")
    jobs[job]["audio_path"] = str(audio)
    with patch("vtt_server.routes.transcription.run_diarizer", return_value=turns) as run:
        r = client.post(f"/api/jobs/{job}/rediarize", json={"num_speakers": n})
    assert run.call_args.args[-1] == n
    return r


TWO = [(0, 5, "SPEAKER_00"), (5, 12, "SPEAKER_01"), (12, 14, "SPEAKER_00"), (14, 20, "SPEAKER_01")]
ONE = [(0, 20, "SPEAKER_00")]


def test_rerunning_adds_versions_and_keeps_the_old_ones(client: TestClient, job: str, tmp_path: Any) -> None:
    first = rediarize(client, job, TWO, 2, tmp_path)
    second = rediarize(client, job, ONE, 1, tmp_path)
    assert first.json()["new_diarization"] == "d2"
    assert second.json()["new_diarization"] == "d3"
    listed = client.get(f"/api/jobs/{job}/versions").json()
    assert [d["id"] for d in listed["diarizations"]] == ["d1", "d2", "d3"]  # three versions after two re-runs
    assert [d["speakers"] for d in listed["diarizations"]] == [3, 2, 1]
    assert listed["active_diarization"] == "d3"
    assert jobs[job]["diarizations"][1]["turns"] == [list(t) for t in TWO]  # raw turns kept
    old = client.get(f"/api/jobs/{job}/segments", params={"diarization": "d1"}).json()
    assert [s["id"] for s in old["speakers"]] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]


def test_edits_touch_only_the_active_version(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, TWO, 2, tmp_path)  # d2 is active
    client.put(f"/api/jobs/{job}/speakers/SPEAKER_00", json={"name": "Alice"})
    r = client.post(f"/api/jobs/{job}/speakers/merge", json={"source_ids": ["SPEAKER_01"], "target_id": "SPEAKER_00"})
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["speakers"]] == ["SPEAKER_00"]
    d1 = jobs[job]["diarizations"][0]
    assert d1["names"] == {}  # v1 is intact: no names, three speakers
    assert len({lab for lab in d1["labels"] if lab}) == 3
    old = client.get(f"/api/jobs/{job}/segments", params={"diarization": "d1"}).json()
    assert old["segments"][0]["speaker"] == "Speaker 1"


def test_split_and_rename_follow_the_active_version(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, ONE, 1, tmp_path)
    split = client.post(f"/api/jobs/{job}/speakers/SPEAKER_00/split", json={"segment_indices": [2, 3]})
    assert split.status_code == 200
    assert split.json()["new_speaker_id"] == "SPEAKER_01"
    assert len(jobs[job]["diarizations"][0]["labels"]) == 4
    assert jobs[job]["diarizations"][0]["labels"] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00", "SPEAKER_02"]  # d1 untouched
    assert jobs[job]["diarizations"][1]["labels"][2] == "SPEAKER_01"


def test_switching_the_active_version_changes_default_exports(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, ONE, 1, tmp_path)
    one_speaker = client.get(f"/api/jobs/{job}/download", params={"format": "txt"}).text
    assert one_speaker.splitlines()[1].startswith("Speaker 1: Hi")
    assert client.put(f"/api/jobs/{job}/diarizations/active", json={"id": "d1"}).status_code == 200
    three = client.get(f"/api/jobs/{job}/download", params={"format": "txt"}).text
    assert three.splitlines()[1].startswith("Speaker 2: Hi")
    assert "Speaker 2: Hi" in jobs[job]["result"]
    assert client.put(f"/api/jobs/{job}/diarizations/active", json={"id": "d9"}).status_code == 404


def test_delete_and_rename_a_diarization_version(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, TWO, 2, tmp_path)
    renamed = client.put(f"/api/jobs/{job}/diarizations/d2", json={"label": "Interview, two voices"})
    assert renamed.json()["diarizations"][1]["label"] == "Interview, two voices"
    assert client.delete(f"/api/jobs/{job}/diarizations/d2").status_code == 200  # active one: falls back to d1
    assert client.get(f"/api/jobs/{job}/versions").json()["active_diarization"] == "d1"
    assert client.delete(f"/api/jobs/{job}/diarizations/d1").status_code == 409  # never the last
    assert client.delete(f"/api/jobs/{job}/diarizations/d7").status_code == 404
    again = rediarize(client, job, ONE, 1, tmp_path)
    assert again.json()["new_diarization"] == "d3"  # ids are never reused


def test_a_job_without_diarization_can_get_one_later(client: TestClient, tmp_path: Any) -> None:
    jobs["plain"] = {"job_id": "plain", "status": "completed", "filename": "a.mp3", "engine": "openai"}
    versions.set_original(jobs["plain"], speakers.parse_transcript("[00:00:00 - 00:00:05] one\n[00:00:05 - 00:00:10] two"))
    try:
        assert client.get("/api/jobs/plain/versions").json()["diarizations"] == []
        assert client.get("/api/jobs/plain/speakers").json()["speakers"] == []
        r = rediarize(client, "plain", [(0, 5, "SPEAKER_00"), (5, 10, "SPEAKER_01")], None, tmp_path)
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert client.get("/api/jobs/plain/versions").json()["diarizations"][0]["label"] == "Auto (2 found)"
    finally:
        jobs.pop("plain", None)


# ---- exports pick (language, diarization) explicitly ----


def test_exports_pick_language_and_diarization(client: TestClient, job: str, tmp_path: Any) -> None:
    translate(client, job, "Spanish", SPANISH)
    translate(client, job, "French", FRENCH)
    rediarize(client, job, ONE, 1, tmp_path)
    get = lambda **p: client.get(f"/api/jobs/{job}/download", params={"format": "txt", **p}).text
    assert get(language="Spanish", diarization="d1").splitlines()[:2] == ["Speaker 1: Hola", "Speaker 2: Hola, como estas"]
    assert get(language="French", diarization="d1").splitlines()[0] == "Speaker 1: Salut"
    assert get(language="original", diarization="d1").splitlines()[0] == "Speaker 1: Hello there"
    assert get(language="Spanish").splitlines()[1] == "Speaker 1: Hola, como estas"  # the active diarization has one speaker
    assert get(translated="true").splitlines()[0].endswith("Salut")  # legacy flag: the latest translation
    assert client.get(f"/api/jobs/{job}/download", params={"language": "Klingon"}).status_code == 404
    assert client.get(f"/api/jobs/{job}/download", params={"diarization": "d9"}).status_code == 404


def test_bilingual_export(client: TestClient, job: str) -> None:
    translate(client, job, "Spanish", SPANISH)
    txt = client.get(f"/api/jobs/{job}/download", params={"format": "txt", "language": "Spanish", "bilingual": "true"}).text
    assert txt.splitlines()[:3] == ["Speaker 1: Hello there", "Hola", ""]
    srt = client.get(f"/api/jobs/{job}/download", params={"format": "srt", "language": "Spanish", "bilingual": "true"}).text
    assert "00:00:00,000 --> 00:00:05,000\nSpeaker 1: Hello there\nHola" in srt
    vtt = client.get(f"/api/jobs/{job}/download", params={"format": "vtt", "language": "Spanish", "bilingual": "true"}).text
    assert vtt.startswith("WEBVTT") and "Hello there\nHola" in vtt
    js = client.get(f"/api/jobs/{job}/download", params={"format": "json", "language": "Spanish", "bilingual": "true"}).json()
    assert js["segments"][1]["text"] == "Hi, how are you"
    assert js["segments"][1]["translation"] == "Hola, como estas"
    assert client.get(f"/api/jobs/{job}/download", params={"bilingual": "true"}).status_code == 404  # needs a language


# ---- persistence, migration, retention ----


def test_all_versions_survive_a_restart(client: TestClient, job: str, tmp_path: Any, db: Any) -> None:
    translate(client, job, "Spanish", SPANISH)
    rediarize(client, job, TWO, 2, tmp_path)
    client.put(f"/api/jobs/{job}/speakers/SPEAKER_00", json={"name": "Alice"})
    jobs[job]["user_id"] = 1

    async def scenario() -> dict[str, Any]:
        await store.save(jobs[job])
        saved = dict(jobs[job])
        jobs.pop(job)
        with patch.object(jobqueue.queue, "ensure_running"):
            await jobqueue.recover(jobs)
        return saved

    saved = asyncio.run(scenario())
    back = jobs[job]
    assert back["segments"] == ORIGINAL
    assert back["translations"] == saved["translations"]
    assert back["diarizations"] == saved["diarizations"]
    assert back["active_diarization"] == "d2"
    assert back["diarizations"][1]["names"] == {"SPEAKER_00": "Alice"}
    assert back["result"] == saved["result"]


def legacy_job(**extra: Any) -> dict[str, Any]:
    """A job as the previous release stored it: speaker, translation and names inside each segment."""
    segments = [
        {"start": 0, "end": 5, "text": "Hello there", "speaker": "SPEAKER_00", "translation": "Hola"},
        {"start": 5, "end": 12, "text": "Hi", "speaker": "SPEAKER_01", "translation": "Hola"},
    ]
    return {
        "job_id": "old", "status": "completed", "segments": segments, "speaker_names": {"SPEAKER_00": "Alice"},
        "translated_to": "Spanish", "engine": "openai", "detected_language": "en", "num_speakers": 2,
        "result": "[00:00:00 - 00:00:05] Alice: Hola", "completed_at": "2026-01-01T00:00:00+00:00", **extra,
    }  # fmt: skip


def test_migration_keeps_the_translation_and_the_speaker_state() -> None:
    job = legacy_job()
    assert versions.migrate(job) is True
    assert job["segments"] == [{"start": 0, "end": 5, "text": "Hello there"}, {"start": 5, "end": 12, "text": "Hi"}]
    assert job["translations"]["Spanish"]["texts"] == ["Hola", "Hola"]
    assert job["translated_to"] == "Spanish"
    (dia,) = job["diarizations"]
    assert (dia["id"], dia["label"], dia["num_speakers"]) == ("d1", "2 speakers", 2)
    assert dia["labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert dia["names"] == {"SPEAKER_00": "Alice"}
    assert job["active_diarization"] == "d1"
    assert job["result"] == "[00:00:00 - 00:00:05] Alice: Hello there\n[00:00:05 - 00:00:12] Speaker 2: Hi"  # original again
    assert "speaker_names" not in job
    assert versions.migrate(job) is False  # idempotent


def test_migration_of_a_job_without_speakers_or_translation() -> None:
    job = {"job_id": "o", "status": "completed", "segments": [{"start": 0, "end": 1, "text": "hi", "speaker": None}]}
    versions.migrate(job)
    assert job["diarizations"] == [] and job["translations"] == {}
    assert job["active_diarization"] is None


def test_legacy_rows_are_migrated_when_loaded_and_saved_back(db: Any) -> None:
    import json

    from vtt_server.models import TranscriptionJob

    legacy = legacy_job(user_id=1, filename="a.mp3")

    async def scenario() -> tuple[dict[str, Any], str]:
        async with db() as session:
            session.add(
                TranscriptionJob(
                    job_id="old", user_id=1, filename="a.mp3", status="completed",
                    state=json.dumps({k: legacy[k] for k in ("result", "segments", "speaker_names", "translated_to", "detected_language")}),
                    options=json.dumps({"num_speakers": 2, "engine": "openai"}),
                )
            )  # fmt: skip
            await session.commit()
        jobs.pop("old", None)
        with patch.object(jobqueue.queue, "ensure_running"):
            await jobqueue.recover(jobs)
        async with db() as session:
            row = (await session.execute(__import__("sqlalchemy").select(TranscriptionJob))).scalars().first()
            return jobs["old"], row.state

    job, state = asyncio.run(scenario())
    jobs.pop("old", None)
    assert job["schema"] == 2
    assert json.loads(state)["schema"] == 2  # the upgrade was written back, not just done in memory
    assert json.loads(state)["translations"]["Spanish"]["texts"] == ["Hola", "Hola"]


def test_a_legacy_job_in_memory_is_upgraded_on_first_use(client: TestClient) -> None:
    jobs["old"] = legacy_job()
    try:
        data = client.get("/api/jobs/old/segments").json()
        assert data["translated_to"] == "Spanish"
        assert data["segments"][0]["translation"] == "Hola"
        assert data["segments"][0]["speaker"] == "Alice"
    finally:
        jobs.pop("old", None)


def test_deleting_a_job_deletes_every_version(client: TestClient, job: str) -> None:
    translate(client, job, "Spanish", SPANISH)
    assert client.delete(f"/api/jobs/{job}").status_code == 204
    assert job not in jobs  # versions live inside the job, so retention and deletion cover them together


# ---- the label says what was asked for and what was found ----


def labels(client: TestClient, job: str) -> list[str]:
    return [d["label"] for d in client.get(f"/api/jobs/{job}/versions").json()["diarizations"]]


def test_label_says_requested_and_found_when_they_differ(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, TWO, 3, tmp_path)  # three asked for, two found
    rediarize(client, job, TWO, 2, tmp_path)  # two asked for, two found
    rediarize(client, job, TWO, None, tmp_path)  # automatic
    rediarize(client, job, ONE, 1, tmp_path)
    assert labels(client, job) == ["Auto (3 found)", "3 requested, 2 found", "2 speakers", "Auto (2 found)", "1 speaker"]
    shown = client.get(f"/api/jobs/{job}/versions").json()["diarizations"]
    assert [(d["num_speakers"], d["found"]) for d in shown] == [(None, 3), (3, 2), (2, 2), (None, 2), (1, 1)]


def test_found_counts_the_diarizers_speakers_not_later_merges(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, TWO, 3, tmp_path)
    client.post(f"/api/jobs/{job}/speakers/merge", json={"source_ids": ["SPEAKER_01"], "target_id": "SPEAKER_00"})
    last = client.get(f"/api/jobs/{job}/versions").json()["diarizations"][-1]
    assert last["label"] == "3 requested, 2 found"  # what the run found, however it was edited since
    assert last["speakers"] == 1


def test_labels_stored_by_earlier_releases_are_shown_correctly() -> None:
    old = {"num_speakers": 3, "label": "3 speakers", "turns": [[0, 5, "SPEAKER_00"], [5, 9, "SPEAKER_01"]], "labels": []}
    assert versions.display_label(old) == "3 requested, 2 found"
    assert versions.display_label({**old, "label": "Auto (3 found)", "num_speakers": None}) == "Auto (2 found)"
    no_turns = {"num_speakers": 2, "label": "2 speakers", "turns": [], "labels": ["SPEAKER_00", None, "SPEAKER_01"]}
    assert versions.display_label(no_turns) == "2 speakers"  # migrated job: the labels are the record
    assert versions.display_label({**no_turns, "num_speakers": 3}) == "3 requested, 2 found"


def test_a_name_the_user_chose_is_kept(client: TestClient, job: str, tmp_path: Any) -> None:
    rediarize(client, job, TWO, 3, tmp_path)
    client.put(f"/api/jobs/{job}/diarizations/d2", json={"label": "Interview, two voices"})
    assert labels(client, job)[1] == "Interview, two voices"


def test_migrated_jobs_get_the_new_wording() -> None:
    job = legacy_job()  # asked for 2 speakers, two labelled
    versions.migrate(job)
    assert versions.summary(job)["diarizations"][0]["label"] == "2 speakers"
    job = legacy_job(num_speakers=3)
    versions.migrate(job)
    assert versions.summary(job)["diarizations"][0]["label"] == "3 requested, 2 found"
