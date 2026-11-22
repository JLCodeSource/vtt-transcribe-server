"""Tests for speaker review endpoints and the diarize/translate/engine pipeline (pure speaker logic: core)."""

import asyncio
import io
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from vtt_transcribe import speakers

from vtt_server.routes.transcription import _process_transcription, jobs

TRANSCRIPT = (
    "[00:00:00 - 00:00:05] SPEAKER_00: Hello there\n"
    "[00:00:05 - 00:00:12] SPEAKER_01: Hi, how are you\n"
    "[00:00:12 - 00:00:14] SPEAKER_00: Fine\n"
    "[00:00:14 - 00:00:20] SPEAKER_02: Me too"
)


pytestmark = pytest.mark.usefixtures("fake_auth")


@pytest.fixture
def client():
    from vtt_server.app import app

    return TestClient(app)


@pytest.fixture
def job():
    """A completed, diarized job seeded in the in-memory store."""
    job_id = "spk-job"
    segs = speakers.parse_transcript(TRANSCRIPT)
    jobs[job_id] = {"job_id": job_id, "status": "completed", "filename": "a.mp3", "diarize": True}
    jobs[job_id]["speaker_names"] = {}
    from vtt_server.routes.transcription import set_segments

    set_segments(jobs[job_id], segs)
    yield job_id
    jobs.pop(job_id, None)


class TestSpeakerEndpoints:
    def test_list_speakers(self, client, job):
        data = client.get(f"/api/jobs/{job}/speakers").json()
        assert [s["display_name"] for s in data["speakers"]] == ["Speaker 1", "Speaker 2", "Speaker 3"]

    def test_unknown_and_incomplete_jobs(self, client):
        assert client.get("/api/jobs/nope/speakers").status_code == 404
        jobs["pending-job"] = {"job_id": "pending-job", "status": "processing"}
        assert client.get("/api/jobs/pending-job/speakers").status_code == 409
        jobs.pop("pending-job")

    def test_rename_applies_to_result_and_downloads(self, client, job):
        r = client.put(f"/api/jobs/{job}/speakers/SPEAKER_00", json={"name": "Alice"})
        assert r.status_code == 200
        assert r.json()["speakers"][0]["name"] == "Alice"
        assert "Alice: Hello there" in jobs[job]["result"]
        txt = client.get(f"/api/jobs/{job}/download", params={"format": "txt"}).text
        assert txt.splitlines()[0] == "Alice: Hello there"
        assert txt.splitlines()[1] == "Speaker 2: Hi, how are you"
        js = client.get(f"/api/jobs/{job}/download", params={"format": "json"}).json()
        assert js["segments"][0]["speaker"] == "Alice"
        assert "<v Alice>" in client.get(f"/api/jobs/{job}/download", params={"format": "vtt"}).text

    def test_rename_many_and_unknown(self, client, job):
        ok = client.put(f"/api/jobs/{job}/speakers/names", json={"names": {"SPEAKER_00": "A", "SPEAKER_01": "B"}})
        assert ok.status_code == 200
        assert client.put(f"/api/jobs/{job}/speakers/names", json={"names": {"NOPE": "x"}}).status_code == 400

    def test_merge(self, client, job):
        client.put(f"/api/jobs/{job}/speakers/SPEAKER_00", json={"name": "Alice"})
        r = client.post(f"/api/jobs/{job}/speakers/merge", json={"source_ids": ["SPEAKER_02"], "target_id": "SPEAKER_00"})
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert "Alice: Me too" in jobs[job]["result"]
        bad = client.post(f"/api/jobs/{job}/speakers/merge", json={"source_ids": ["SPEAKER_00"], "target_id": "SPEAKER_00"})
        assert bad.status_code == 400

    def test_split(self, client, job):
        client.post(f"/api/jobs/{job}/speakers/merge", json={"source_ids": ["SPEAKER_02"], "target_id": "SPEAKER_00"})
        r = client.post(f"/api/jobs/{job}/speakers/SPEAKER_00/split", json={"segment_indices": [3]})
        assert r.status_code == 200
        assert r.json()["new_speaker_id"] == "SPEAKER_02"
        assert r.json()["segments"][3]["speaker"] == "Speaker 3"

    def test_clip_uses_ffmpeg_and_stored_audio(self, client, job, tmp_path):
        audio = tmp_path / "in.mp3"
        audio.write_bytes(b"x")
        jobs[job]["audio_path"] = str(audio)
        proc = MagicMock(returncode=0, stdout=b"RIFFwav")
        with patch("vtt_server.routes.speakers.subprocess.run", return_value=proc) as run:
            r = client.get(f"/api/jobs/{job}/speakers/SPEAKER_01/clip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/wav"
        assert r.content == b"RIFFwav"
        cmd = run.call_args.args[0]
        assert cmd[cmd.index("-ss") + 1] == "5.000"
        assert client.get(f"/api/jobs/{job}/speakers/NOPE/clip").status_code == 404

    def test_clip_without_stored_audio(self, client, job):
        assert client.get(f"/api/jobs/{job}/speakers/SPEAKER_00/clip").status_code == 404

    def test_translate_job_keeps_speakers_and_exports_translation(self, client, job):
        client.put(f"/api/jobs/{job}/speakers/SPEAKER_00", json={"name": "Alice"})
        fake = "\n".join(
            [
                "[00:00:00 - 00:00:05] Hola",
                "[00:00:05 - 00:00:12] Hola, como estas",
                "[00:00:12 - 00:00:14] Bien",
                "[00:00:14 - 00:00:20] Yo tambien",
            ]
        )
        with patch("vtt_server.routes.transcription.AudioTranslator") as translator:
            translator.return_value.translate_transcript.return_value = fake
            r = client.post(f"/api/jobs/{job}/translate", json={"target_language": "Spanish", "api_key": "k"})
            sent = translator.return_value.translate_transcript.call_args.args[0]
        assert r.status_code == 200
        assert r.json()["translated_to"] == "Spanish"
        assert r.json()["engine"] == "openai"
        assert "Alice" not in sent
        assert "SPEAKER" not in sent
        assert client.get(f"/api/jobs/{job}/download", params={"format": "txt", "translated": "true"}).text.startswith(
            "Alice: Hola"
        )
        assert client.get(f"/api/jobs/{job}/download", params={"format": "txt"}).text.startswith("Alice: Hello there")

    def test_translate_line_count_mismatch_is_a_502(self, client, job):
        with patch("vtt_server.routes.transcription.AudioTranslator") as translator:
            translator.return_value.translate_transcript.return_value = "[00:00:00 - 00:00:05] only one"
            r = client.post(f"/api/jobs/{job}/translate", json={"target_language": "Spanish", "api_key": "k"})
        assert r.status_code == 502

    def test_rediarize_with_num_speakers(self, client, job, tmp_path):
        audio = tmp_path / "in.mp3"
        audio.write_bytes(b"x")
        jobs[job].update(audio_path=str(audio), raw_transcript="[00:00:00 - 00:00:05] a\n[00:00:05 - 00:00:10] b")
        with patch("vtt_server.routes.transcription.run_diarizer", return_value=[(0, 10, "SPEAKER_00")]) as run:
            r = client.post(f"/api/jobs/{job}/rediarize", json={"num_speakers": 1})
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["speakers"]] == ["SPEAKER_00"]
        assert run.call_args.args[-1] == 1

    def test_delete_job_removes_audio(self, client, job, tmp_path):
        audio = tmp_path / "in.mp3"
        audio.write_bytes(b"x")
        jobs[job]["audio_path"] = str(audio)
        assert client.delete(f"/api/jobs/{job}").status_code == 204
        assert client.get(f"/api/jobs/{job}").status_code == 404


class TestPipeline:
    def test_transcribe_with_sherpa_diarization_needs_no_token(self, client):
        with (
            patch("vtt_server.routes.transcription.VideoTranscriber") as vt,
            patch(
                "vtt_server.routes.transcription.run_diarizer", return_value=[(0, 4, "SPEAKER_00"), (5, 10, "SPEAKER_01")]
            ) as dia,
        ):
            vt.return_value.detect_language.return_value = "en"
            vt.return_value.transcribe.return_value = "[00:00:00 - 00:00:04] one\n[00:00:05 - 00:00:10] two"
            r = client.post(
                "/api/transcribe",
                files={"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")},
                data={"api_key": "k", "diarize": "true", "num_speakers": "2"},
            )
            assert r.status_code == 200
            job_id = r.json()["job_id"]
            for _ in range(100):  # the job runs as a background task; wait for it instead of racing it
                seg = client.get(f"/api/jobs/{job_id}/speakers")
                if seg.status_code != 409:
                    break
                time.sleep(0.05)
        assert seg.status_code == 200
        assert [s["id"] for s in seg.json()["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert dia.call_args.args[1] == 2
        assert client.delete(f"/api/jobs/{job_id}").status_code == 204

    def test_pipeline_labels_and_stores_segments(self):
        jobs["p1"] = {"job_id": "p1", "status": "pending"}
        with (
            patch("vtt_server.routes.transcription.VideoTranscriber") as vt,
            patch("vtt_server.routes.transcription.run_diarizer", return_value=[(0, 10, "SPEAKER_00")]),
        ):
            vt.return_value.detect_language.return_value = "en"
            vt.return_value.transcribe.return_value = "[00:00:00 - 00:00:10] hello"
            asyncio.run(_process_transcription("p1", b"x", "a.mp3", "k", True))
        assert jobs["p1"]["status"] == "completed"
        assert jobs["p1"]["segments"][0]["speaker"] == "SPEAKER_00"
        assert jobs["p1"]["result"] == "[00:00:00 - 00:00:10] Speaker 1: hello"
        from vtt_server.routes.transcription import purge_job

        purge_job("p1")

    def test_diarization_failure_marks_job_failed(self):
        jobs["p2"] = {"job_id": "p2", "status": "pending"}
        with (
            patch("vtt_server.routes.transcription.VideoTranscriber") as vt,
            patch("vtt_server.routes.transcription.run_diarizer", side_effect=RuntimeError("model download failed")),
        ):
            vt.return_value.transcribe.return_value = "[00:00:00 - 00:00:10] hello"
            asyncio.run(_process_transcription("p2", b"x", "a.mp3", "k", True))
        assert jobs["p2"]["status"] == "failed"
        assert "could not be downloaded" in jobs["p2"]["error"]  # friendly wording; the raw text stays in the log
        jobs.pop("p2")


class TestEngineSeam:
    def test_engines_listing(self, client):
        data = client.get("/api/engines").json()
        ids = {e["id"]: e for e in data["engines"]}
        assert ids["openai"]["available"] is True
        assert ids["local"]["available"] is False  # the test env does not install the 'local' extra
        assert "diarizers" not in data

    def test_engines_listing_reports_local_when_installed(self, client):
        with patch("vtt_server.engines.local_engine_available", return_value=True):
            ids = {e["id"]: e for e in client.get("/api/engines").json()["engines"]}
        assert ids["local"]["available"] is True
        assert ids["local"]["requires_api_key"] is False

    def test_local_engine_is_a_501_when_extra_not_installed(self, client):
        with patch("vtt_server.engines.local_engine_available", return_value=False):
            r = client.post(
                "/api/transcribe", files={"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")}, data={"engine": "local"}
            )
        assert r.status_code == 501
        assert "local" in r.json()["detail"]

    def test_local_engine_runs_without_a_key(self, client):
        with (
            patch("vtt_server.engines.local_engine_available", return_value=True),
            patch("vtt_server.routes.transcription.make_local_transcriber") as lt,
        ):
            lt.return_value.detect_language.return_value = "en"
            lt.return_value.transcribe.return_value = "[00:00:00 - 00:00:04] hello"
            r = client.post(
                "/api/transcribe", files={"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")}, data={"engine": "local"}
            )
            assert r.status_code == 200
            job_id = r.json()["job_id"]
            for _ in range(100):
                got = client.get(f"/api/jobs/{job_id}/segments")
                if got.status_code != 409:
                    break
                time.sleep(0.05)
        assert got.status_code == 200
        assert got.json()["segments"][0]["text"] == "hello"
        client.delete(f"/api/jobs/{job_id}")

    def test_local_translation_needs_no_key(self, client, job):
        jobs[job]["engine"] = "local"
        fake = "\n".join(f"[00:00:{a:02d} - 00:00:{b:02d}] t{a}" for a, b in [(0, 5), (5, 12), (12, 14), (14, 20)])
        with (
            patch("vtt_server.engines.local_engine_available", return_value=True),
            patch("vtt_transcribe.local_translate.LocalTextTranslator") as lt,
        ):
            lt.return_value.translate_text.return_value = fake
            r = client.post(f"/api/jobs/{job}/translate", json={"target_language": "French"})
        assert r.status_code == 200
        assert r.json()["translated_to"] == "French"
        assert r.json()["engine"] == "local"  # lets the UI skip the OpenAI key prompt

    def test_openai_translation_without_key_is_422(self, client, job):
        assert client.post(f"/api/jobs/{job}/translate", json={"target_language": "French"}).status_code == 422

    def test_unknown_engine_and_speaker_count(self, client):
        f = {"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")}
        assert client.post("/api/transcribe", files=f, data={"engine": "nope", "api_key": "k"}).status_code == 400
        f = {"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")}
        assert client.post("/api/diarize", files=f, data={"num_speakers": "99"}).status_code == 422

    def test_missing_key_is_422_for_openai(self, client):
        r = client.post("/api/transcribe", files={"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")})
        assert r.status_code == 422

    def test_diarize_only_stores_turns_as_speakers(self, client):
        with patch("vtt_server.routes.transcription.run_diarizer", return_value=[(0, 3, "SPEAKER_00"), (3, 9, "SPEAKER_01")]):
            r = client.post("/api/diarize", files={"file": ("t.mp3", io.BytesIO(b"x"), "audio/mpeg")})
            job_id = r.json()["job_id"]
            data = client.get(f"/api/jobs/{job_id}/speakers").json()
        assert [s["id"] for s in data["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
        client.delete(f"/api/jobs/{job_id}")
