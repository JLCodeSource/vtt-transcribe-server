"""Transcription API endpoints."""

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from vtt_transcribe import speakers
from vtt_transcribe.logging_config import get_logger
from vtt_transcribe.transcriber import VideoTranscriber
from vtt_transcribe.translator import AudioTranslator

from vtt_server import mailer, retention
from vtt_server.access import check_capacity, owned_job, public_view
from vtt_server.auth import get_current_active_user
from vtt_server.engines import (
    EngineUnavailableError,
    build_diarizer,
    engine_info,
    make_local_transcriber,
    make_local_translate_fn,
    translation_languages,
    validate_engine,
)
from vtt_server.errors import friendly_error
from vtt_server.jobqueue import (
    JobCancelled,
    attach_progress,
    checkpoint,
    next_seq,
    progress_callback,
    queue,
    set_step,
    with_progress,
)
from vtt_server.jobqueue import queue as job_queue  # noqa: F401
from vtt_server.jobstore import store
from vtt_server.models import User

# Every /api route needs a signed-in, active user; job routes additionally require owning the job (see access.owned_job).
router = APIRouter(prefix="/api", tags=["transcription"], dependencies=[Depends(get_current_active_user)])
logger = get_logger(__name__)

# Jobs dictionary now includes progress_updates queue for each job
jobs: dict[str, dict[str, Any]] = {}


def _emit_progress(job_id: str, message: str, progress_type: str = "info") -> None:
    """Emit a progress update for a job.

    Args:
        job_id: Job identifier
        message: Progress message
        progress_type: Type of progress update (info, chunk, diarization, language, translation)
    """
    if job_id in jobs and "progress_updates" in jobs[job_id]:
        update = {
            "type": progress_type,
            "message": message,
            "timestamp": time.time(),
        }
        try:
            jobs[job_id]["progress_updates"].put_nowait(update)
        except asyncio.QueueFull:
            # Log but don't fail if queue is full - oldest events will be consumed first
            logger.warning(
                "Progress queue full for job - dropping event",
                extra={"job_id": job_id, "progress_message": message, "progress_type": progress_type},
            )
        except Exception as exc:
            # Log but don't fail if progress update cannot be enqueued for other reasons
            logger.warning(
                "Failed to enqueue progress update for job",
                extra={
                    "job_id": job_id,
                    "progress_message": message,
                    "error": repr(exc),
                },
            )


# Largest accepted upload (MAX_UPLOAD_SIZE, bytes). Default 4 GiB: video files are big. Uploads are streamed to disk in
# chunks, never held in memory, so the limit is about disk space and what you are willing to host.
DEFAULT_MAX_UPLOAD = 4 * 1024**3


def _configured_max_upload() -> int:
    try:
        value = int(os.environ.get("MAX_UPLOAD_SIZE") or DEFAULT_MAX_UPLOAD)
    except ValueError:
        return DEFAULT_MAX_UPLOAD
    return value if value > 0 else DEFAULT_MAX_UPLOAD


MAX_FILE_SIZE = _configured_max_upload()
UPLOAD_CHUNK = 1024 * 1024


def _too_large(size_so_far: int) -> HTTPException:
    limit = MAX_FILE_SIZE
    shown = f"{limit / 1024**3:.1f} GB" if limit >= 1024**3 else f"{limit // (1024 * 1024)} MB"
    return HTTPException(
        status_code=413,
        detail=f"File too large (over {size_so_far} bytes). Maximum size: {limit} bytes ({shown})",
    )

# Maximum progress queue size (to prevent unbounded memory growth)
# A typical transcription job emits ~10-20 progress events, so 100 is generous
MAX_PROGRESS_QUEUE_SIZE = 100

# Upper bound for an explicit speaker count
MAX_SPEAKERS = 12

# Supported file extensions
SUPPORTED_EXTENSIONS = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".mpeg", ".mpga", ".webm"}


def _job_dir(job_id: str) -> Path:
    """Directory holding a job's uploaded audio (kept so speaker sample clips and re-diarization work)."""
    return Path(os.environ.get("VTT_DATA_DIR") or Path(tempfile.gettempdir()) / "vtt-jobs") / job_id


def _save_audio(job_id: str, content: bytes, filename: str) -> Path:
    directory = _job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"input{Path(filename).suffix.lower()}"
    path.write_bytes(content)
    jobs[job_id]["audio_path"] = str(path)
    return path


def _audio_for(job: dict[str, Any], content: bytes | None, filename: str) -> Path:
    """The job's audio: save the upload when given (direct calls), else use what the queue stored at submission."""
    return _save_audio(job["job_id"], content, filename) if content is not None else Path(job["audio_path"])


def purge_job(job_id: str) -> None:
    """Forget a job in memory and delete its stored audio and files."""
    jobs.pop(job_id, None)
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)


def purge_user_jobs(user_id: int) -> None:
    """Forget all of a user's jobs in memory and on disk (account deletion). Running jobs are told to stop."""
    for job_id, job in list(jobs.items()):
        if job.get("user_id") == user_id:
            job["cancel_requested"] = True
            purge_job(job_id)


async def remove_job(job_id: str) -> None:
    """Delete a job everywhere: memory, audio on disk, and its database row."""
    await asyncio.to_thread(purge_job, job_id)
    await store.remove(job_id)


async def persist(job_id: str) -> None:
    """Write a job's current state through to the database (best effort)."""
    job = jobs.get(job_id)
    if job is not None:  # a job deleted meanwhile must not be written back
        await store.save(job)


def _now_iso() -> str:
    return retention.utcnow().isoformat()


async def resolve_openai_key(user_id: int | None, explicit: str | None) -> str | None:
    """The request's key if given, else the user's stored OpenAI key (profile), else ``None``."""
    if explicit or user_id is None:
        return explicit
    from vtt_server.routes.api_keys import lookup_user_key

    return await lookup_user_key(user_id, "openai")


async def sweep_expired(now: Any = None) -> list[str]:
    """Delete every job whose retention has run out (called by the background sweeper)."""
    return await retention.sweep(jobs, remove_job, now)


def _check_engine(engine: str, api_key: str | None) -> None:
    try:
        validate_engine(engine, api_key)
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422 if "api_key" in str(exc) else 400, detail=str(exc)) from exc


def validate_num_speakers(num_speakers: int | None) -> None:
    if num_speakers is not None and not 1 <= num_speakers <= MAX_SPEAKERS:
        raise HTTPException(status_code=422, detail=f"num_speakers must be between 1 and {MAX_SPEAKERS}")


@router.get("/engines")
async def list_engines() -> dict[str, Any]:
    """Selectable transcription engines (drives the frontend's engine picker)."""
    return {
        "engines": engine_info(),
        "max_speakers": MAX_SPEAKERS,
        "languages": translation_languages(),
        "max_upload_bytes": MAX_FILE_SIZE,
    }


def _retention_for(user: User, mode: str | None, days: int | None) -> dict[str, Any]:
    try:
        return retention.resolve(mode, days, user.retention_mode, user.retention_days)
    except retention.RetentionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _copy_upload(file: UploadFile, path: Path) -> int:
    """Stream an upload to ``path`` in chunks (never all in memory), refusing it as soon as it passes the size limit."""
    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(UPLOAD_CHUNK):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                logger.warning("Upload refused: file too large", extra={"file_name": file.filename, "size": size})
                raise _too_large(size)
            await asyncio.to_thread(out.write, chunk)
    return size


async def _store_upload(file: UploadFile, job: dict[str, Any]) -> int:
    """Validate the type, then stream the upload to the job's folder in chunks, enforcing the size limit as it goes.

    Sets ``job["audio_path"]`` and returns the size in bytes. A refused or failed upload leaves nothing behind.
    """
    file_ext = Path(file.filename or "").suffix.lower()
    if file_ext not in SUPPORTED_EXTENSIONS:
        logger.warning("Job creation failed: unsupported file type", extra={"file_name": file.filename, "extension": file_ext})
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    directory = _job_dir(job["job_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"input{file_ext}"
    try:
        size = await _copy_upload(file, path)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    job["audio_path"] = str(path)
    return size


async def _enqueue(job: dict[str, Any], size: int) -> dict[str, str]:
    """Record the (already stored) upload as a pending job and wake the worker. Nothing runs here: jobs run one by one."""
    job_id = job["job_id"]
    job.update(
        status="pending",
        step="queued",
        progress=0.0,
        message="Waiting in the queue",
        created_at=_now_iso(),
        queue_seq=next_seq(),
        file_size=size,
        progress_updates=asyncio.Queue(maxsize=MAX_PROGRESS_QUEUE_SIZE),  # live events for the job's websocket
    )
    jobs[job_id] = job
    await store.save(job)
    queue.ensure_running()
    return {"job_id": job_id, "status": "pending", "message": "Job queued"}


@router.post("/transcribe")
async def create_transcription_job(
    file: UploadFile = File(...),
    api_key: str | None = Form(None),
    diarize: bool = Form(False),  # noqa: FBT001, FBT003
    translate_to: str | None = Form(None),
    engine: str = Form("openai"),
    num_speakers: int | None = Form(None),
    retention_mode: str | None = Form(None, description="keep | delete | hold (default: your profile, then the platform)"),
    retention_days: int | None = Form(None, description="days to hold when retention_mode=hold"),
    user: User = Depends(get_current_active_user),
) -> dict[str, str]:
    """Queue a transcription job (it runs after the jobs ahead of it; see ``GET /api/jobs``).

    Args:
        file: Audio or video file
        api_key: OpenAI API key for ``engine=openai``; when omitted the key saved in the user's profile is used
        diarize: Enable speaker diarization
        translate_to: Optional target language for translation (e.g., "Spanish", "French")
        engine: ``openai`` or ``local``
        num_speakers: Known speaker count; detected automatically when omitted
        retention_mode / retention_days: how long to keep the result (see :mod:`vtt_server.retention`)

    Returns:
        Job ID and status
    """
    logger.info("Creating transcription job", extra={"file_name": file.filename, "diarize": diarize, "engine": engine})

    if not file.filename:
        logger.warning("Job creation failed: missing filename")
        raise HTTPException(status_code=422, detail="File must have a filename")

    check_capacity(user)
    stored_key = await resolve_openai_key(user.id, api_key) if engine == "openai" else api_key
    _check_engine(engine, stored_key)
    validate_num_speakers(num_speakers)
    policy = _retention_for(user, retention_mode, retention_days)

    job: dict[str, Any] = {
        "job_id": str(uuid.uuid4()),
        "user_id": user.id,
        "filename": file.filename,
        "engine": engine,
        "diarize": diarize,
        "num_speakers": num_speakers if diarize else None,
        "translate_to": translate_to,
        "retention_mode": policy["mode"],
        "retention_days": policy["days"],
    }
    if api_key:  # a key typed into the request is kept (encrypted) only until this job ends
        from vtt_server.routes.api_keys import encrypt_key

        job["secret"] = encrypt_key(api_key)
    size = await _store_upload(file, job)
    logger.info("Transcription job queued", extra={"job_id": job["job_id"], "file_name": file.filename})
    return await _enqueue(job, size)


@router.get("/jobs")
async def list_jobs(
    user: User = Depends(get_current_active_user),
    all_users: bool = Query(False, alias="all", description="Administrators: include every user's jobs"),  # noqa: FBT001, FBT003
) -> dict[str, Any]:
    """The caller's jobs, newest first, with queue position, progress and expiry. Used by the Jobs page."""
    mine = [j for j in jobs.values() if (user.is_superuser and all_users) or j.get("user_id") == user.id]
    mine.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return {
        "jobs": [public_view(j) for j in mine],
        "queue_length": sum(1 for j in jobs.values() if j.get("status") == "pending"),
        "running": queue.current,
    }


class RetentionBody(BaseModel):
    """Change how long a job is kept."""

    mode: str
    days: int | None = None


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job: dict[str, Any] = Depends(owned_job)) -> dict[str, Any]:
    """Cancel a waiting job immediately, or ask a running one to stop at its next checkpoint."""
    if job.get("status") not in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail=f"Job already {job.get('status')}")
    queue.cancel(job)
    await store.save(job)
    return public_view(job)


@router.put("/jobs/{job_id}/retention")
async def set_job_retention(body: RetentionBody, job: dict[str, Any] = Depends(owned_job)) -> dict[str, Any]:
    """Change a job's retention (keep / delete / hold N days); ``expires_at`` is recalculated."""
    try:
        policy = retention.normalize(body.mode, body.days)
    except retention.RetentionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job.update(retention_mode=policy["mode"], retention_days=policy["days"])
    retention.apply_expiry(job)
    await store.save(job)
    return public_view(job)


@router.get("/jobs/{job_id}")
async def get_job_status(job: dict[str, Any] = Depends(owned_job)) -> dict[str, Any]:
    """Get status of a transcription job."""
    return public_view(job)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job: dict[str, Any] = Depends(owned_job)) -> None:
    """Delete a job, its results and its stored audio (a running job is cancelled first)."""
    if job.get("status") == "processing":
        raise HTTPException(status_code=409, detail="The job is running: cancel it first, then delete it")
    await remove_job(job["job_id"])


@router.post("/detect-language")
async def detect_language(
    file: UploadFile = File(...),
    api_key: str = Form(...),
) -> dict[str, str]:
    """Detect language of audio file.

    Args:
        file: Audio or video file
        api_key: OpenAI API key

    Returns:
        Detected language code and original filename
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="File must have a filename")

    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    handle, name = tempfile.mkstemp(suffix=file_ext)
    os.close(handle)
    tmp_path = Path(name)
    try:
        await _copy_upload(file, tmp_path)  # a too-large file is refused here, with its own 413
        try:
            transcriber = VideoTranscriber(api_key)
            language_code = await asyncio.to_thread(transcriber.detect_language, tmp_path)
            return {
                "language_code": language_code,
                "filename": file.filename,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Language detection failed: {e!s}") from e
    finally:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)


@router.post("/translate")
async def translate_transcript(
    transcript: str = Form(...),
    target_language: str = Form(...),
    api_key: str = Form(...),
    preserve_timestamps: bool = Form(True),  # noqa: FBT001, FBT003
) -> dict[str, str]:
    """Translate a transcript to target language.

    Args:
        transcript: Original transcript text
        target_language: Target language name (e.g., "Spanish", "French")
        api_key: OpenAI API key
        preserve_timestamps: If True, preserve timestamp format in output

    Returns:
        Translated transcript
    """
    try:
        translator = AudioTranslator(api_key)
        translated = await asyncio.to_thread(
            translator.translate_transcript, transcript, target_language, preserve_timestamps=preserve_timestamps
        )

        return {
            "original": transcript,
            "translated": translated,
            "target_language": target_language,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {e!s}") from e


@router.post("/diarize")
async def create_diarization_job(
    file: UploadFile = File(...),
    num_speakers: int | None = Form(None),
    retention_mode: str | None = Form(None),
    retention_days: int | None = Form(None),
    user: User = Depends(get_current_active_user),
) -> dict[str, str]:
    """Queue a diarization-only job (who speaks when; no transcript, no API key).

    Args:
        file: Audio or video file
        num_speakers: Known speaker count; detected automatically when omitted
        retention_mode / retention_days: how long to keep the result

    Returns:
        Job ID and status
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="File must have a filename")

    check_capacity(user)
    validate_num_speakers(num_speakers)
    policy = _retention_for(user, retention_mode, retention_days)
    job: dict[str, Any] = {
        "job_id": str(uuid.uuid4()),
        "user_id": user.id,
        "filename": file.filename,
        "diarize_only": True,
        "num_speakers": num_speakers,
        "retention_mode": policy["mode"],
        "retention_days": policy["days"],
    }
    size = await _store_upload(file, job)
    result = await _enqueue(job, size)
    result["message"] = "Diarization job queued"
    return result


_step_cb = progress_callback


def run_diarizer(
    audio_path: Path, num_speakers: int | None, progress: Callable[[float, str | None], None] | None = None
) -> list[tuple[float, float, str]]:
    """Blocking: run sherpa diarization over a file (call via ``asyncio.to_thread``)."""
    backend = build_diarizer(num_speakers=num_speakers)
    if progress is not None:
        backend.progress_callback = progress  # core >= 0.6.2: 0.0 at start, 1.0 at finish
    return backend.diarize_audio(audio_path)


def set_segments(job: dict[str, Any], segments: list[dict[str, Any]]) -> None:
    """Store structured segments and refresh the human-readable ``result`` (names applied)."""
    job["segments"] = segments
    job.setdefault("speaker_names", {})
    translated = bool(job.get("translated_to")) and any(s.get("translation") for s in segments)
    job["result"] = speakers.to_result(speakers.labelled(segments, job["speaker_names"], translated=translated))


async def run_job(job: dict[str, Any]) -> None:
    """Run one queued job to the end (called by the worker, one job at a time)."""
    job_id = job["job_id"]
    job.setdefault("progress_updates", asyncio.Queue(maxsize=MAX_PROGRESS_QUEUE_SIZE))
    job.update(status="processing", step="starting", message="Starting", started_at=_now_iso())
    await persist(job_id)
    if job.get("diarize_only"):
        await _process_diarization(job_id, None, job["filename"], job.get("num_speakers"))
        return
    api_key = None
    if job.get("secret"):
        from vtt_server.routes.api_keys import decrypt_key

        try:
            api_key = decrypt_key(job["secret"])
        except Exception:  # key rotated or corrupt: fall back to the profile key
            logger.warning("Stored job key unreadable", extra={"job_id": job_id})
    await _process_transcription(
        job_id,
        None,
        job["filename"],
        api_key,
        bool(job.get("diarize")),
        job.get("translate_to"),
        engine=job.get("engine") or "openai",
        num_speakers=job.get("num_speakers"),
    )


async def _finish(job: dict[str, Any], status: str, error: str | None = None) -> None:
    """Common end of a job: final status, timestamps, expiry, secrets dropped, persisted, owner notified."""
    job["status"] = status
    job["step"] = {"completed": "done", "failed": "failed", "cancelled": "cancelled"}[status]
    if status == "completed":
        job["progress"] = 1.0
        job["message"] = "Done"
    if error is not None:
        job["error"] = error
        job["message"] = error
    job["completed_at"] = _now_iso()
    job.pop("secret", None)
    job.pop("cancel_requested", None)
    retention.apply_expiry(job)
    await persist(job["job_id"])
    if status != "cancelled":
        mailer.notify_job_finished(job)


async def _process_diarization(
    job_id: str,
    content: bytes | None,
    filename: str,
    num_speakers: int | None = None,
) -> None:
    """Process a diarization-only job: who speaks when."""
    job = jobs[job_id]
    try:
        job["status"] = "processing"
        set_step(job, "diarizing", "Identifying speakers")
        _emit_progress(job_id, "Starting diarization", "diarization")
        audio_path = _audio_for(job, content, filename)

        _emit_progress(job_id, "Processing audio for speaker segments", "diarization")
        turns = await asyncio.to_thread(run_diarizer, audio_path, num_speakers, _step_cb(job, "diarizing"))
        checkpoint(job)

        set_segments(job, speakers.turns_to_segments(turns))
        _emit_progress(job_id, "Diarization complete", "diarization")
        await _finish(job, "completed")
    except JobCancelled:
        _emit_progress(job_id, "Cancelled", "error")
        await _finish(job, "cancelled")
    except Exception as e:
        logger.exception("Diarization job failed", extra={"job_id": job_id})
        reason = friendly_error(e)
        _emit_progress(job_id, f"Diarization failed: {reason}", "error")
        await _finish(job, "failed", reason)


async def _process_transcription(
    job_id: str,
    content: bytes | None,
    filename: str,
    api_key: str | None,
    diarize: bool = False,  # noqa: FBT001, FBT002
    translate_to: str | None = None,
    *,
    engine: str = "openai",
    num_speakers: int | None = None,
) -> None:
    """Process one transcription job: transcribe -> (diarize) -> (translate).

    ``content`` is the upload when called directly; the queue passes ``None`` because the audio is already stored.
    """
    job = jobs[job_id]
    start_time = time.time()
    logger.info(
        "Starting transcription job processing",
        extra={"job_id": job_id, "file_name": filename, "translate_to": translate_to, "engine": engine},
    )

    try:
        job["status"] = "processing"
        set_step(job, "detecting", "Starting transcription")
        _emit_progress(job_id, "Starting transcription", "info")
        audio_path = _audio_for(job, content, filename)

        if engine == "openai" and not api_key:
            api_key = await resolve_openai_key(job.get("user_id"), None)  # profile key when the request carried none
        transcriber = make_transcriber(engine, api_key)  # openai or local (core's LocalTranscriber)

        # Detect language before transcription
        set_step(job, "detecting", "Detecting language")
        _emit_progress(job_id, "Detecting language", "language")
        detected_language = await asyncio.to_thread(transcriber.detect_language, audio_path)
        job["detected_language"] = detected_language
        _emit_progress(job_id, f"Detected language: {detected_language}", "language")
        await persist(job_id)

        # Transcribe audio (fine-grained progress when the core reports it, step-level otherwise)
        set_step(job, "transcribing", "Transcribing audio")
        _emit_progress(job_id, "Transcribing audio", "info")
        attach_progress(transcriber, job, "transcribing")  # core >= 0.6.2: per-segment fractions
        result = await asyncio.to_thread(with_progress(transcriber.transcribe, job, "transcribing"), audio_path)
        checkpoint(job)
        _emit_progress(job_id, "Transcription complete", "info")
        job["raw_transcript"] = result

        if diarize:
            set_step(job, "diarizing", "Identifying speakers")
            _emit_progress(job_id, "Identifying speakers", "diarization")
            turns = await asyncio.to_thread(run_diarizer, audio_path, num_speakers, _step_cb(job, "diarizing"))
            checkpoint(job)
            result = label_transcript(result, turns)
            _emit_progress(job_id, "Speaker identification complete", "diarization")

        segments = speakers.parse_transcript(result)
        if segments:
            set_segments(job, segments)
        else:  # engine returned something unstructured; keep it verbatim
            job["result"] = result

        # If translation requested, translate the transcript
        if translate_to:
            set_step(job, "translating", f"Translating to {translate_to}")
            _emit_progress(job_id, f"Translating to {translate_to}", "translation")
            await translate_job_segments(job, api_key, translate_to, engine=engine)
            checkpoint(job)
            _emit_progress(job_id, f"Translation to {translate_to} complete", "translation")

        _emit_progress(job_id, "Job completed successfully", "info")
        await _finish(job, "completed")

        logger.info(
            "Transcription job completed successfully",
            extra={
                "job_id": job_id,
                "duration_seconds": round(time.time() - start_time, 2),
                "result_length": len(job.get("result", "")),
            },
        )

    except JobCancelled:
        logger.info("Transcription job cancelled", extra={"job_id": job_id})
        _emit_progress(job_id, "Cancelled", "error")
        await _finish(job, "cancelled")
    except Exception as e:
        logger.exception(
            "Transcription job failed",
            extra={"job_id": job_id, "duration_seconds": round(time.time() - start_time, 2), "error": str(e)},
        )
        reason = friendly_error(e)
        _emit_progress(job_id, f"Transcription failed: {reason}", "error")
        await _finish(job, "failed", reason)


def make_transcriber(engine: str, api_key: str | None) -> Any:
    """The engine object (``detect_language`` / ``transcribe``): OpenAI or core's local faster-whisper."""
    validate_engine(engine, api_key)
    return make_local_transcriber() if engine == "local" else VideoTranscriber(api_key)


def make_translate_fn(
    engine: str,
    api_key: str | None,
    target: str,
    source: str | None,
    progress: Callable[[float, str | None], None] | None = None,
) -> Callable[[str], str]:
    """``transcript -> translated transcript`` (keeps ``[timestamp]`` lines) for the job's engine."""
    validate_engine(engine, api_key)
    if engine == "local":
        return make_local_translate_fn(target, source, progress)
    translator = AudioTranslator(api_key)
    return lambda text: translator.translate_transcript(text, target, preserve_timestamps=True)


def label_transcript(transcript: str, turns: list[tuple[float, float, str]]) -> str:
    """Attach ``SPEAKER_NN:`` labels to a timestamped transcript (core's midpoint matching)."""
    from vtt_transcribe.diarization_common import SpeakerLabeler

    return SpeakerLabeler().apply_speakers_to_transcript(transcript, turns)


async def translate_job_segments(
    job: dict[str, Any], api_key: str | None, target_language: str, *, engine: str | None = None
) -> None:
    """Translate a job's segments in place (``translation`` per segment) with the job's engine.

    Speakers are not sent to the model (core's ``translate_segments``), so names/ids can be renamed or merged
    before *or* after translating without redoing it. ``engine=local`` translates on-box and needs no key.
    """
    engine = engine or job.get("engine") or "openai"
    translate = make_translate_fn(
        engine, api_key, target_language, job.get("detected_language"), progress_callback(job, "translating")
    )
    segments = job.get("segments")
    if not segments:  # unstructured result: translate it verbatim
        job["result"] = await asyncio.to_thread(translate, job["result"])
        job["translated_to"] = target_language
        return
    translated = await asyncio.to_thread(speakers.translate_segments, segments, translate)
    job["translated_to"] = target_language
    set_segments(job, translated)  # result now reads in the target language


def _parse_transcript_segments(transcript: str) -> list[dict[str, Any]]:
    """Parse transcript text into segments with timestamps.

    Args:
        transcript: Formatted transcript text with timestamps

    Returns:
        List of segment dictionaries with start, end, text, and optional speaker
    """
    segments = []
    lines = transcript.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try to match format with speaker: [Speaker] [HH:MM:SS - HH:MM:SS] text
        speaker_match = re.match(r"^\[([^\]]+)\]\s*\[(\d{2}):(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2}):(\d{2})\]\s*(.+)$", line)
        if speaker_match:
            speaker, start_h, start_m, start_s, end_h, end_m, end_s, text = speaker_match.groups()
            segments.append(
                {
                    "speaker": speaker,
                    "start": int(start_h) * 3600 + int(start_m) * 60 + int(start_s),
                    "end": int(end_h) * 3600 + int(end_m) * 60 + int(end_s),
                    "text": text.strip(),
                }
            )
            continue

        # Try to match format without speaker: [HH:MM:SS - HH:MM:SS] text
        no_speaker_match = re.match(r"^\[(\d{2}):(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2}):(\d{2})\]\s*(.+)$", line)
        if no_speaker_match:
            start_h, start_m, start_s, end_h, end_m, end_s, text = no_speaker_match.groups()
            segments.append(
                {
                    "start": int(start_h) * 3600 + int(start_m) * 60 + int(start_s),
                    "end": int(end_h) * 3600 + int(end_m) * 60 + int(end_s),
                    "text": text.strip(),
                }
            )

    return segments


def _format_as_txt(segments: list[dict[str, Any]]) -> str:
    """Format segments as plain text."""
    lines = []
    for seg in segments:
        speaker_prefix = f"[{seg['speaker']}] " if seg.get("speaker") else ""
        lines.append(f"{speaker_prefix}{seg['text']}")
    return "\n".join(lines)


def _format_as_vtt(segments: list[dict[str, Any]]) -> str:
    """Format segments as WebVTT."""
    lines = ["WEBVTT", ""]

    for i, seg in enumerate(segments, 1):
        start_time = _format_vtt_time(seg["start"])
        end_time = _format_vtt_time(seg["end"])
        lines.append(str(i))
        lines.append(f"{start_time} --> {end_time}")
        if seg.get("speaker"):
            lines.append(f"<v {seg['speaker']}>{seg['text']}")
        else:
            lines.append(seg["text"])
        lines.append("")

    return "\n".join(lines)


def _format_as_srt(segments: list[dict[str, Any]]) -> str:
    """Format segments as SRT."""
    lines = []

    for i, seg in enumerate(segments, 1):
        start_time = _format_srt_time(seg["start"])
        end_time = _format_srt_time(seg["end"])
        lines.append(str(i))
        lines.append(f"{start_time} --> {end_time}")
        speaker_prefix = f"[{seg['speaker']}] " if seg.get("speaker") else ""
        lines.append(f"{speaker_prefix}{seg['text']}")
        lines.append("")

    return "\n".join(lines)


def _format_vtt_time(seconds: int) -> str:
    """Format seconds as VTT timestamp (HH:MM:SS.mmm)."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.000"


def _format_srt_time(seconds: int) -> str:
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},000"


_MEDIA_TYPES = {
    "txt": "text/plain",
    "vtt": "text/vtt",
    "srt": "application/x-subrip",
    "json": "application/json",
}


@router.get("/jobs/{job_id}/download")
async def download_transcript(
    job: dict[str, Any] = Depends(owned_job),
    format: str = Query("txt", pattern="^(txt|vtt|srt|json)$"),  # noqa: A002
    translated: bool = Query(False, description="Export the translation instead of the original text"),  # noqa: FBT001, FBT003
) -> PlainTextResponse:
    """Download transcript in specified format, with speaker names applied as ``Name: text``.

    Args:
        job_id: Job ID
        format: Output format (txt, vtt, srt or json)
        translated: Use the translated text when the job has a translation

    Returns:
        Formatted transcript file
    """
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Job not completed. Status: {job['status']}")

    if not job.get("downloaded_at"):  # starts the grace period of retention=delete
        job["downloaded_at"] = _now_iso()
        retention.apply_expiry(job)
        await store.save(job)

    result = job.get("result", "")
    if not result:
        raise HTTPException(status_code=404, detail="No transcript available")

    if job.get("segments"):
        segments = speakers.labelled(job["segments"], job.get("speaker_names", {}), translated=translated)
    else:
        # Legacy / unstructured result: parse it
        segments = _parse_transcript_segments(result)

    if not segments:
        raise HTTPException(status_code=404, detail="No segments found in transcript")

    if format == "json":
        content = json.dumps({"segments": segments}, ensure_ascii=False, indent=2)
    elif job.get("segments"):
        content = {"txt": speakers.to_txt, "vtt": speakers.to_vtt, "srt": speakers.to_srt}[format](segments)
    elif format == "txt":
        content = _format_as_txt(segments)
    elif format == "vtt":
        content = _format_as_vtt(segments)
    else:  # srt
        content = _format_as_srt(segments)

    return PlainTextResponse(
        content=content,
        media_type=_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="transcript.{format}"'},
    )
