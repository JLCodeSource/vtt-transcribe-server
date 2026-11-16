"""Transcription API endpoints."""

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from vtt_transcribe.logging_config import get_logger
from vtt_transcribe.transcriber import VideoTranscriber
from vtt_transcribe.translator import AudioTranslator

from vtt_server import speakers
from vtt_server.engines import (
    EngineUnavailableError,
    build_diarizer,
    engine_info,
    validate_engine,
)

router = APIRouter(prefix="/api", tags=["transcription"])
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


# Maximum file size: 100MB
MAX_FILE_SIZE = 100 * 1024 * 1024

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


def purge_job(job_id: str) -> None:
    """Forget a job and delete its stored audio."""
    jobs.pop(job_id, None)
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)


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
    return {"engines": engine_info(), "max_speakers": MAX_SPEAKERS}


@router.post("/transcribe")
async def create_transcription_job(
    file: UploadFile = File(...),
    api_key: str | None = Form(None),
    diarize: bool = Form(False),  # noqa: FBT001, FBT003
    translate_to: str | None = Form(None),
    engine: str = Form("openai"),
    num_speakers: int | None = Form(None),
) -> dict[str, str]:
    """Create a new transcription job.

    Args:
        file: Audio or video file
        api_key: OpenAI API key (required for ``engine=openai``)
        diarize: Enable speaker diarization
        translate_to: Optional target language for translation (e.g., "Spanish", "French")
        engine: ``openai`` or ``local`` (local is a stub until core ships it; returns 501)
        num_speakers: Known speaker count; detected automatically when omitted

    Returns:
        Job ID and status
    """
    logger.info(
        "Creating transcription job",
        extra={"file_name": file.filename, "diarize": diarize, "engine": engine},
    )

    if not file.filename:
        logger.warning("Job creation failed: missing filename")
        raise HTTPException(status_code=422, detail="File must have a filename")

    _check_engine(engine, api_key)
    validate_num_speakers(num_speakers)

    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in SUPPORTED_EXTENSIONS:
        logger.warning(
            "Job creation failed: unsupported file type",
            extra={"file_name": file.filename, "extension": file_ext},
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # Read and validate file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        logger.warning(
            "Job creation failed: file too large",
            extra={"file_name": file.filename, "file_size_bytes": len(content), "max_size_bytes": MAX_FILE_SIZE},
        )
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(content)} bytes. Maximum size: {MAX_FILE_SIZE} bytes ({max_mb}MB)",
        )

    job_id = str(uuid.uuid4())

    logger.info(
        "Transcription job created successfully",
        extra={"job_id": job_id, "file_name": file.filename, "file_size_mb": round(len(content) / (1024 * 1024), 2)},
    )

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "filename": file.filename,
        "file_size": len(content),
        "engine": engine,
        "diarize": diarize,
        "num_speakers": num_speakers if diarize else None,
        "translate_to": translate_to,
        # Bounded queue for progress updates
        "progress_updates": asyncio.Queue(maxsize=MAX_PROGRESS_QUEUE_SIZE),
    }

    task = asyncio.create_task(
        _process_transcription(
            job_id,
            content,
            file.filename or "audio.mp3",
            api_key,
            diarize,
            translate_to,
            engine=engine,
            num_speakers=num_speakers,
        )
    )
    _ = task

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Transcription job created",
    }


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Get status of a transcription job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    # Exclude non-serialisable / bulky internals; speaker data has its own endpoints
    hidden = {"progress_updates", "segments", "speaker_names", "audio_path", "raw_transcript"}
    return {k: v for k, v in jobs[job_id].items() if k not in hidden}


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    """Delete a job and its stored audio."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    await asyncio.to_thread(purge_job, job_id)


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

    # Read and validate file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(content)} bytes. Maximum size: {MAX_FILE_SIZE} bytes ({max_mb}MB)",
        )

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            transcriber = VideoTranscriber(api_key)
            language_code = await asyncio.to_thread(transcriber.detect_language, tmp_path)

            return {
                "language_code": language_code,
                "filename": file.filename,
            }

        finally:
            await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Language detection failed: {e!s}") from e


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
) -> dict[str, str]:
    """Create a diarization-only job (who speaks when; no transcript, no API key).

    Args:
        file: Audio or video file
        num_speakers: Known speaker count; detected automatically when omitted

    Returns:
        Job ID and status
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="File must have a filename")

    validate_num_speakers(num_speakers)

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "filename": file.filename,
        "diarize_only": True,
        "num_speakers": num_speakers,
        # Bounded queue for progress updates
        "progress_updates": asyncio.Queue(maxsize=MAX_PROGRESS_QUEUE_SIZE),
    }

    content = await file.read()
    task = asyncio.create_task(_process_diarization(job_id, content, file.filename, num_speakers))
    _ = task

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Diarization job created",
    }


def run_diarizer(audio_path: Path, num_speakers: int | None) -> list[tuple[float, float, str]]:
    """Blocking: run sherpa diarization over a file (call via ``asyncio.to_thread``)."""
    backend = build_diarizer(num_speakers=num_speakers)
    return backend.diarize_audio(audio_path)


def set_segments(job: dict[str, Any], segments: list[dict[str, Any]]) -> None:
    """Store structured segments and refresh the human-readable ``result`` (names applied)."""
    job["segments"] = segments
    job.setdefault("speaker_names", {})
    translated = bool(job.get("translated_to")) and any(s.get("translation") for s in segments)
    job["result"] = speakers.to_result(speakers.labelled(segments, job["speaker_names"], translated=translated))


async def _process_diarization(
    job_id: str,
    content: bytes,
    filename: str,
    num_speakers: int | None = None,
) -> None:
    """Process diarization-only job asynchronously."""
    try:
        jobs[job_id]["status"] = "processing"
        _emit_progress(job_id, "Starting diarization", "diarization")
        audio_path = _save_audio(job_id, content, filename)  # small sync write, as before

        _emit_progress(job_id, "Processing audio for speaker segments", "diarization")
        turns = await asyncio.to_thread(run_diarizer, audio_path, num_speakers)

        set_segments(jobs[job_id], speakers.turns_to_segments(turns))
        jobs[job_id]["status"] = "completed"
        _emit_progress(job_id, "Diarization complete", "diarization")
    except Exception as e:
        logger.exception("Diarization job failed", extra={"job_id": job_id})
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _emit_progress(job_id, f"Diarization failed: {e}", "error")


async def _process_transcription(
    job_id: str,
    content: bytes,
    filename: str,
    api_key: str | None,
    diarize: bool = False,  # noqa: FBT001, FBT002
    translate_to: str | None = None,
    *,
    engine: str = "openai",
    num_speakers: int | None = None,
) -> None:
    """Process transcription job asynchronously: transcribe -> (diarize) -> (translate)."""
    start_time = time.time()
    logger.info(
        "Starting transcription job processing",
        extra={"job_id": job_id, "file_name": filename, "translate_to": translate_to, "engine": engine},
    )

    try:
        jobs[job_id]["status"] = "processing"
        _emit_progress(job_id, "Starting transcription", "info")
        audio_path = _save_audio(job_id, content, filename)  # small sync write, as before

        # ``engine=local`` is a seam (see vtt_server/engines.py); validate_engine already rejected it at the API
        # boundary, this keeps direct callers honest too.
        validate_engine(engine, api_key)
        transcriber = VideoTranscriber(api_key)

        # Detect language before transcription
        _emit_progress(job_id, "Detecting language", "language")
        detected_language = await asyncio.to_thread(transcriber.detect_language, audio_path)
        jobs[job_id]["detected_language"] = detected_language
        _emit_progress(job_id, f"Detected language: {detected_language}", "language")

        # Transcribe audio
        _emit_progress(job_id, "Transcribing audio", "info")
        result = await asyncio.to_thread(transcriber.transcribe, audio_path)
        _emit_progress(job_id, "Transcription complete", "info")
        jobs[job_id]["raw_transcript"] = result

        if diarize:
            _emit_progress(job_id, "Identifying speakers", "diarization")
            turns = await asyncio.to_thread(run_diarizer, audio_path, num_speakers)
            result = label_transcript(result, turns)
            _emit_progress(job_id, "Speaker identification complete", "diarization")

        segments = speakers.parse_transcript(result)
        if segments:
            set_segments(jobs[job_id], segments)
        else:  # engine returned something unstructured; keep it verbatim
            jobs[job_id]["result"] = result

        # If translation requested, translate the transcript
        if translate_to:
            _emit_progress(job_id, f"Translating to {translate_to}", "translation")
            await translate_job_segments(jobs[job_id], api_key, translate_to)
            _emit_progress(job_id, f"Translation to {translate_to} complete", "translation")

        jobs[job_id]["status"] = "completed"
        _emit_progress(job_id, "Job completed successfully", "info")

        logger.info(
            "Transcription job completed successfully",
            extra={
                "job_id": job_id,
                "duration_seconds": round(time.time() - start_time, 2),
                "result_length": len(jobs[job_id].get("result", "")),
            },
        )

    except Exception as e:
        logger.exception(
            "Transcription job failed",
            extra={"job_id": job_id, "duration_seconds": round(time.time() - start_time, 2), "error": str(e)},
        )
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        _emit_progress(job_id, f"Transcription failed: {e}", "error")


def label_transcript(transcript: str, turns: list[tuple[float, float, str]]) -> str:
    """Attach ``SPEAKER_NN:`` labels to a timestamped transcript (core's midpoint matching)."""
    from vtt_transcribe.diarization_common import SpeakerLabeler

    return SpeakerLabeler().apply_speakers_to_transcript(transcript, turns)


async def translate_job_segments(job: dict[str, Any], api_key: str | None, target_language: str) -> None:
    """Translate a job's segments in place (``translation`` per segment) via core's AudioTranslator.

    Speakers are not sent to the model: only ``[ts] text`` lines are translated and zipped back by index, so
    names/ids can be renamed or merged before *or* after translating without redoing it.
    """
    segments = job.get("segments")
    translator = AudioTranslator(api_key)
    if not segments:  # unstructured result: translate it verbatim
        job["result"] = await asyncio.to_thread(translator.translate_transcript, job["result"], target_language)
        job["translated_to"] = target_language
        return
    plain = [{**s, "speaker": None} for s in segments]
    translated = await asyncio.to_thread(
        translator.translate_transcript, speakers.to_result(plain), target_language, preserve_timestamps=True
    )
    parsed = speakers.parse_transcript(translated)
    if len(parsed) != len(segments):
        msg = f"Translation returned {len(parsed)} lines for {len(segments)} segments"
        raise RuntimeError(msg)
    for seg, tr in zip(segments, parsed, strict=True):
        seg["translation"] = tr["text"]
    job["translated_to"] = target_language
    set_segments(job, segments)  # result now reads in the target language


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
    job_id: str,
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
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Job not completed. Status: {job['status']}")

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
