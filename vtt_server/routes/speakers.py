"""Speaker review endpoints: list speakers (with sample clips), rename, merge, split, re-diarize, translate.

All of these operate on an existing completed job's structured ``segments`` / ``speaker_names``
(``vtt_transcribe.speakers``). Renames and merges are applied to the job ``result`` and every export
(``GET /api/jobs/{id}/download``) as ``Name: text``.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from vtt_transcribe import speakers

from vtt_server.engines import EngineUnavailableError
from vtt_server.routes import transcription as tx

router = APIRouter(prefix="/api/jobs/{job_id}", tags=["speakers"])

MAX_CLIP_SECONDS = 15.0


class RenameBody(BaseModel):
    """New display name; blank clears it (back to ``Speaker N``)."""

    name: str = Field(max_length=100)


class NamesBody(BaseModel):
    """Rename several speakers at once: ``{"SPEAKER_00": "Alice", ...}``."""

    names: dict[str, str]


class MergeBody(BaseModel):
    """Fold ``source_ids`` into ``target_id``."""

    source_ids: list[str] = Field(min_length=1)
    target_id: str


class SplitBody(BaseModel):
    """Move these segment indices (of the speaker in the URL) to a new speaker."""

    segment_indices: list[int] = Field(min_length=1)


class RediarizeBody(BaseModel):
    """Re-run diarization on the stored audio with a different speaker count (resets renames/merges)."""

    num_speakers: int | None = Field(None, ge=1)


class TranslateBody(BaseModel):
    """Translate the job's segments to ``target_language`` (OpenAI key needed unless the job's engine is local)."""

    target_language: str = Field(min_length=1, max_length=64)
    api_key: str | None = None
    engine: str | None = Field(None, description="Defaults to the job's own engine (openai for diarize-only jobs)")


def _job(job_id: str) -> dict[str, Any]:
    job = tx.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job not completed. Status: {job['status']}")
    if not job.get("segments"):
        raise HTTPException(status_code=404, detail="Job has no structured segments")
    return job


def _stored_audio(job: dict[str, Any]) -> str:
    audio = job.get("audio_path")
    if not audio or not Path(audio).exists():
        raise HTTPException(status_code=404, detail="Stored audio is no longer available")
    return str(audio)


def _view(job: dict[str, Any]) -> dict[str, Any]:
    """Speakers + segments as the UI needs them."""
    names = job.get("speaker_names", {})
    segments = [
        {
            "index": i,
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
            "translation": s.get("translation"),
            "speaker_id": s.get("speaker"),
            "speaker": speakers.display_name(s.get("speaker"), names),
        }
        for i, s in enumerate(job["segments"])
    ]
    return {
        "job_id": job["job_id"],
        "translated_to": job.get("translated_to"),
        "engine": job.get("engine") or "openai",
        "speakers": speakers.summarize(job["segments"], names),
        "segments": segments,
    }


def _apply(job: dict[str, Any], segments: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    job["speaker_names"] = names
    tx.set_segments(job, segments)
    return _view(job)


def _guard(fn):
    try:
        return fn()
    except speakers.SpeakerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/segments")
async def get_segments(job_id: str) -> dict[str, Any]:
    """Structured transcript: segments (original + translation, speaker id and display name) and speakers."""
    return _view(_job(job_id))


@router.get("/speakers")
async def list_speakers(job_id: str) -> dict[str, Any]:
    """Speakers with talk time, a quote, and sample ranges playable via ``.../speakers/{id}/clip``."""
    view = _view(_job(job_id))
    return {"job_id": job_id, "speakers": view["speakers"]}


@router.get("/speakers/{speaker_id}/clip")
async def speaker_clip(
    job_id: str,
    speaker_id: str,
    start: float | None = Query(None, ge=0),
    end: float | None = Query(None, ge=0),
) -> Response:
    """A short WAV excerpt of the stored audio. Defaults to the speaker's first sample range."""
    job = _job(job_id)
    summary = next(
        (s for s in speakers.summarize(job["segments"], job.get("speaker_names", {})) if s["id"] == speaker_id), None
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Speaker not found")
    if start is None or end is None:
        sample = summary["samples"][0]
        start, end = sample["start"], sample["end"]
    duration = min(max(end - start, 0.5), MAX_CLIP_SECONDS)
    audio = await asyncio.to_thread(_stored_audio, job)
    cmd = ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", audio,
           "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"]  # fmt: skip
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=501, detail="ffmpeg is not installed on the server") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise HTTPException(status_code=500, detail="Could not extract audio clip")
    return Response(content=proc.stdout, media_type="audio/wav", headers={"Cache-Control": "private, max-age=300"})


@router.put("/speakers/names")
async def rename_many(job_id: str, body: NamesBody) -> dict[str, Any]:
    """Set several display names at once (the review screen's "Looks good")."""
    job = _job(job_id)

    def run() -> dict[str, str]:
        names = job.get("speaker_names", {})
        for sid, name in body.names.items():
            names = speakers.rename(job["segments"], names, sid, name)
        return names

    return _apply(job, job["segments"], _guard(run))


@router.put("/speakers/{speaker_id}")
async def rename_speaker(job_id: str, speaker_id: str, body: RenameBody) -> dict[str, Any]:
    """Rename one speaker; the name appears as ``Name: text`` in the result and all exports."""
    job = _job(job_id)
    names = _guard(lambda: speakers.rename(job["segments"], job.get("speaker_names", {}), speaker_id, body.name))
    return _apply(job, job["segments"], names)


@router.post("/speakers/merge")
async def merge_speakers(job_id: str, body: MergeBody) -> dict[str, Any]:
    """Merge speakers that are really the same person into ``target_id`` (target's name is kept)."""
    job = _job(job_id)
    segments, names = _guard(
        lambda: speakers.merge(job["segments"], job.get("speaker_names", {}), body.source_ids, body.target_id)
    )
    return _apply(job, segments, names)


@router.post("/speakers/{speaker_id}/split")
async def split_speaker(job_id: str, speaker_id: str, body: SplitBody) -> dict[str, Any]:
    """Split the chosen segments of a speaker off into a new speaker (returned as ``new_speaker_id``)."""
    job = _job(job_id)
    segments, new_id = _guard(lambda: speakers.split(job["segments"], speaker_id, body.segment_indices))
    return {**_apply(job, segments, job.get("speaker_names", {})), "new_speaker_id": new_id}


@router.post("/rediarize")
async def rediarize(job_id: str, body: RediarizeBody) -> dict[str, Any]:
    """Re-run diarization on the stored audio, e.g. with the right ``num_speakers``. Resets renames/merges."""
    job = _job(job_id)
    raw = job.get("raw_transcript")
    if raw is None:
        raise HTTPException(status_code=409, detail="Only transcription jobs can be re-diarized")
    tx.validate_num_speakers(body.num_speakers)
    audio = await asyncio.to_thread(_stored_audio, job)
    turns = await asyncio.to_thread(tx.run_diarizer, Path(audio), body.num_speakers)
    segments = speakers.parse_transcript(tx.label_transcript(raw, turns))
    job.update(num_speakers=body.num_speakers, translated_to=None)
    return _apply(job, segments, {})


@router.post("/translate")
async def translate_job(job_id: str, body: TranslateBody) -> dict[str, Any]:
    """Translate the job into ``target_language``; speaker names are untouched and exports gain ``?translated=true``."""
    job = _job(job_id)
    try:
        await tx.translate_job_segments(job, body.api_key, body.target_language, engine=body.engine)
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        if isinstance(exc, speakers.SpeakerError):  # translation changed the line count
            raise HTTPException(status_code=502, detail=f"Translation failed: {exc!s}") from exc
        raise HTTPException(status_code=422 if "api_key" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Translation failed: {exc!s}") from exc
    return _view(job)
