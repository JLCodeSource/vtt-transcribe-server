"""Versions and speaker review: the original, every translation, every diarization (see :mod:`vtt_server.versions`).

Reads take ``language`` (``original`` or a stored translation) and ``diarization`` (a version id; default the active
one). Rename / merge / split edit the ACTIVE diarization only; re-diarizing and translating ADD versions; any version
can be deleted except the original text (and the last diarization). Exports are in ``GET /api/jobs/{id}/download``.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from vtt_transcribe import speakers

from vtt_server import versions
from vtt_server.access import owned_job
from vtt_server.engines import EngineUnavailableError
from vtt_server.routes import transcription as tx

# Everything here operates on one job: the caller must own it (or be an administrator).
router = APIRouter(prefix="/api/jobs/{job_id}", tags=["speakers"], dependencies=[Depends(owned_job)])

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
    """Run diarization again on the stored audio (e.g. with the right speaker count); it is ADDED as a new version."""

    num_speakers: int | None = Field(None, ge=1)


class ActiveBody(BaseModel):
    """Which diarization version is active (the one edits, speaker clips and the default export follow)."""

    id: str


class LabelBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class TranslateBody(BaseModel):
    """Translate the job's segments to ``target_language`` (OpenAI key needed unless the job's engine is local)."""

    target_language: str = Field(min_length=1, max_length=64)
    api_key: str | None = None
    engine: str | None = Field(None, description="Defaults to the job's own engine (openai for diarize-only jobs)")
    replace: bool = Field(False, description="Translate again even if this language already exists (replaces that version)")  # noqa: FBT003


def _job(job_id: str) -> dict[str, Any]:
    job = tx.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job not completed. Status: {job['status']}")
    versions.migrate(job)  # a job still in the earlier layout is upgraded on first touch (normally done at startup)
    if not versions.has_versions(job):
        raise HTTPException(status_code=404, detail="Job has no structured segments")
    return job


def _stored_audio(job: dict[str, Any]) -> str:
    audio = job.get("audio_path")
    if not audio or not Path(audio).exists():
        raise HTTPException(status_code=404, detail="Stored audio is no longer available")
    return str(audio)


def _pick(job: dict[str, Any], language: str | None, diarization: str | None) -> tuple[str | None, str | None]:
    """Resolve the requested language (omitted: the latest translation, as before; ``original``: none) and diarization."""
    try:
        key = versions.find_language(job, language) if language else versions.latest_language(job)
        version = versions.get_diarization(job, diarization)
    except versions.VersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return key, version["id"] if version else None


def _view(job: dict[str, Any], language: str | None = None, diarization: str | None = None) -> dict[str, Any]:
    """Speakers + segments as the UI needs them, for one (language, diarization) pair."""
    key, dia_id = _pick(job, language, diarization)
    segs, names = versions.materialize(job, language=key, dia_id=dia_id)
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
        for i, s in enumerate(segs)
    ]
    return {
        "job_id": job["job_id"],
        "translated_to": key,
        "language": key or "original",
        "diarization": dia_id,
        "active_diarization": job.get("active_diarization"),
        "engine": job.get("engine") or "openai",
        "speakers": speakers.summarize(segs, names),
        "segments": segments,
    }


async def _edited(job: dict[str, Any], segments: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    """Write a speaker edit to the active diarization (only) and persist it."""
    try:
        versions.commit_edit(job, segments, names)
    except versions.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await tx.persist(job["job_id"])  # reviews survive a restart
    return _view(job)


def _guard(fn):
    try:
        return fn()
    except speakers.SpeakerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/versions")
async def get_versions(job_id: str) -> dict[str, Any]:
    """What exists for this job: the original, every translation, every diarization (and which one is active)."""
    return versions.summary(_job(job_id))


@router.get("/segments")
async def get_segments(
    job_id: str,
    language: str | None = Query(None, description="'original' or a stored translation (default: the latest translation)"),
    diarization: str | None = Query(None, description="Diarization version id (default: the active one)"),
) -> dict[str, Any]:
    """Structured transcript: segments (original + the chosen translation, speaker id and display name) and speakers."""
    return _view(_job(job_id), language, diarization)


@router.get("/speakers")
async def list_speakers(job_id: str, diarization: str | None = Query(None)) -> dict[str, Any]:
    """Speakers with talk time, a quote, and sample ranges playable via ``.../speakers/{id}/clip``."""
    view = _view(_job(job_id), "original", diarization)
    return {"job_id": job_id, "diarization": view["diarization"], "speakers": view["speakers"]}


@router.get("/speakers/{speaker_id}/clip")
async def speaker_clip(
    job_id: str,
    speaker_id: str,
    start: float | None = Query(None, ge=0),
    end: float | None = Query(None, ge=0),
    diarization: str | None = Query(None, description="Diarization version id (default: the active one)"),
) -> Response:
    """A short WAV excerpt of the stored audio. Defaults to the speaker's first sample range."""
    job = _job(job_id)
    try:
        segs, names = versions.materialize(job, dia_id=diarization)
    except versions.VersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    summary = next((s for s in speakers.summarize(segs, names) if s["id"] == speaker_id), None)
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
    """Set several display names at once on the active diarization (the review screen's "Looks good")."""
    job = _job(job_id)
    segs, names = versions.with_speakers(job)

    def run() -> dict[str, str]:
        updated = names
        for sid, name in body.names.items():
            updated = speakers.rename(segs, updated, sid, name)
        return updated

    return await _edited(job, segs, _guard(run))


@router.put("/speakers/{speaker_id}")
async def rename_speaker(job_id: str, speaker_id: str, body: RenameBody) -> dict[str, Any]:
    """Rename one speaker of the active diarization; the name appears as ``Name: text`` in every export of it."""
    job = _job(job_id)
    segs, names = versions.with_speakers(job)
    return await _edited(job, segs, _guard(lambda: speakers.rename(segs, names, speaker_id, body.name)))


@router.post("/speakers/merge")
async def merge_speakers(job_id: str, body: MergeBody) -> dict[str, Any]:
    """Merge speakers that are really the same person (active diarization only; the target's name is kept)."""
    job = _job(job_id)
    segs, names = versions.with_speakers(job)
    merged, new_names = _guard(lambda: speakers.merge(segs, names, body.source_ids, body.target_id))
    return await _edited(job, merged, new_names)


@router.post("/speakers/{speaker_id}/split")
async def split_speaker(job_id: str, speaker_id: str, body: SplitBody) -> dict[str, Any]:
    """Split chosen segments of a speaker off into a new speaker (active diarization only; ``new_speaker_id``)."""
    job = _job(job_id)
    segs, names = versions.with_speakers(job)
    split, new_id = _guard(lambda: speakers.split(segs, speaker_id, body.segment_indices))
    return {**await _edited(job, split, names), "new_speaker_id": new_id}


@router.post("/rediarize")
async def rediarize(job_id: str, body: RediarizeBody) -> dict[str, Any]:
    """Run diarization again (e.g. with the right ``num_speakers``): ADDS a version and makes it active. Others are kept."""
    job = _job(job_id)
    if job.get("diarize_only"):
        raise HTTPException(status_code=409, detail="Only transcription jobs can be re-diarized")
    tx.validate_num_speakers(body.num_speakers)
    audio = await asyncio.to_thread(_stored_audio, job)
    turns = await asyncio.to_thread(tx.run_diarizer, Path(audio), body.num_speakers)
    version = versions.add_diarization(job, turns, body.num_speakers)
    await tx.persist(job["job_id"])
    return {**_view(job), "new_diarization": version["id"]}


@router.put("/diarizations/active")
async def set_active_diarization(job_id: str, body: ActiveBody) -> dict[str, Any]:
    """Switch the active diarization version (edits, speaker clips and the default export follow it)."""
    job = _job(job_id)
    try:
        versions.set_active(job, body.id)
    except versions.VersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await tx.persist(job["job_id"])
    return _view(job)


@router.put("/diarizations/{dia_id}")
async def rename_diarization(job_id: str, dia_id: str, body: LabelBody) -> dict[str, Any]:
    """Give a diarization version a name of your own."""
    job = _job(job_id)
    try:
        versions.rename_diarization(job, dia_id, body.label)
    except versions.VersionError as exc:
        raise HTTPException(status_code=404 if "No diarization" in str(exc) else 422, detail=str(exc)) from exc
    await tx.persist(job["job_id"])
    return versions.summary(job)


@router.delete("/diarizations/{dia_id}")
async def delete_diarization(job_id: str, dia_id: str) -> dict[str, Any]:
    """Delete one diarization version (never the last one)."""
    job = _job(job_id)
    try:
        versions.get_diarization(job, dia_id)
    except versions.VersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if len(job["diarizations"]) <= 1:
        raise HTTPException(status_code=409, detail="This is the only diarization version; it cannot be deleted")
    versions.remove_diarization(job, dia_id)
    await tx.persist(job["job_id"])
    return versions.summary(job)


@router.delete("/translations/{language}")
async def delete_translation(job_id: str, language: str) -> dict[str, Any]:
    """Delete one translation (the original text and the other languages are kept)."""
    job = _job(job_id)
    try:
        versions.remove_translation(job, language)
    except versions.VersionError as exc:
        raise HTTPException(status_code=404 if "No translation" in str(exc) else 400, detail=str(exc)) from exc
    await tx.persist(job["job_id"])
    return versions.summary(job)


@router.post("/translate")
async def translate_job(job_id: str, body: TranslateBody) -> dict[str, Any]:
    """Translate into ``target_language``: ADDS a language version (the original and other languages are kept).

    A language that already exists is returned as it is (``existing: true``) unless ``replace`` is set.
    """
    job = _job(job_id)
    if not any(s["text"].strip() for s in job["segments"]):
        raise HTTPException(status_code=409, detail="This job has no text to translate")
    try:
        existing = versions.find_language(job, body.target_language)
    except versions.VersionError:
        existing = None
    if existing and not body.replace:
        return {**_view(job, existing), "existing": True}
    api_key = body.api_key
    if not api_key and (body.engine or job.get("engine") or "openai") == "openai":
        api_key = await tx.resolve_openai_key(job.get("user_id"), None)  # fall back to the key saved in the profile
    try:
        key = await tx.translate_job_segments(job, api_key, body.target_language, engine=body.engine)
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        if isinstance(exc, speakers.SpeakerError):  # translation changed the line count
            raise HTTPException(status_code=502, detail=f"Translation failed: {exc!s}") from exc
        raise HTTPException(status_code=422 if "api_key" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Translation failed: {exc!s}") from exc
    await tx.persist(job["job_id"])
    return {**_view(job, key), "existing": False}
