"""Database persistence for jobs (``TranscriptionJob`` rows) behind the in-memory ``jobs`` working set.

The routes and the worker operate on plain dicts in ``vtt_server.routes.transcription.jobs``; every state change is
written through here, so a restart loses nothing: finished results stay viewable and unfinished jobs are re-queued.
Persistence is best-effort (failures are logged, never raised into a request), and can be switched off (``enabled``).
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select

from vtt_server import retention
from vtt_server.models import TranscriptionJob

logger = logging.getLogger(__name__)

# Options and results live in JSON columns; everything else has its own column.
OPTION_KEYS = ("engine", "diarize", "num_speakers", "translate_to", "diarize_only", "file_size")
STATE_KEYS = (
    "result", "raw_transcript", "segments", "speaker_names", "audio_path", "detected_language", "translated_to", "message",
)  # fmt: skip
TIME_KEYS = ("created_at", "started_at", "completed_at", "expires_at", "downloaded_at")


class JobStore:
    """Reads/writes job rows. ``session_factory`` defaults to the app's database; tests may point it elsewhere."""

    def __init__(self) -> None:
        self.enabled = True
        self.session_factory: Callable[[], Any] | None = None

    def factory(self) -> Callable[[], Any] | None:
        if not self.enabled:
            return None
        if self.session_factory is not None:
            return self.session_factory
        from vtt_server import database

        return database.AsyncSessionLocal

    async def save(self, job: dict[str, Any]) -> None:
        factory = self.factory()
        if factory is None or job.get("user_id") is None or not job.get("job_id"):
            return
        try:
            async with factory() as db:
                query = select(TranscriptionJob).where(TranscriptionJob.job_id == job["job_id"])
                row = (await db.execute(query)).scalar_one_or_none()
                if row is None:
                    row = TranscriptionJob(
                        job_id=job["job_id"], user_id=job["user_id"], filename=job.get("filename") or "audio"
                    )
                    db.add(row)
                _to_row(job, row)
                await db.commit()
        except Exception:
            logger.exception("Could not persist job", extra={"job_id": job.get("job_id")})

    async def remove(self, job_id: str) -> None:
        factory = self.factory()
        if factory is None:
            return
        try:
            async with factory() as db:
                await db.execute(delete(TranscriptionJob).where(TranscriptionJob.job_id == job_id))
                await db.commit()
        except Exception:
            logger.exception("Could not delete job row", extra={"job_id": job_id})

    async def load_all(self) -> list[dict[str, Any]]:
        factory = self.factory()
        if factory is None:
            return []
        try:
            async with factory() as db:
                query = select(TranscriptionJob).order_by(TranscriptionJob.created_at, TranscriptionJob.id)
                rows = (await db.execute(query)).scalars().all()
                return [_from_row(r) for r in rows]
        except Exception:
            logger.exception("Could not load persisted jobs")
            return []


store = JobStore()


def _iso(value: Any) -> str | None:
    parsed = retention.parse_ts(value)
    return parsed.isoformat() if parsed else None


def _to_row(job: dict[str, Any], row: TranscriptionJob) -> None:
    row.filename = job.get("filename") or row.filename
    row.status = job.get("status", "pending")
    row.step = job.get("step")
    row.progress = job.get("progress")
    row.error = job.get("error")
    row.with_diarization = bool(job.get("diarize") or job.get("diarize_only"))
    row.detected_language = job.get("detected_language")
    row.translated_to = job.get("translated_to")
    row.retention_mode = job.get("retention_mode")
    row.retention_days = job.get("retention_days")
    row.transcript = job.get("result") if isinstance(job.get("result"), str) else None
    row.secret = job.get("secret")
    for key in TIME_KEYS:
        parsed = retention.parse_ts(job.get(key))
        if parsed is not None or key != "created_at":
            setattr(row, key, parsed)
    row.options = json.dumps({k: job[k] for k in OPTION_KEYS if k in job})
    row.state = json.dumps({k: job[k] for k in STATE_KEYS if k in job}, ensure_ascii=False)


def _from_row(row: TranscriptionJob) -> dict[str, Any]:
    job: dict[str, Any] = {
        "job_id": row.job_id,
        "user_id": row.user_id,
        "filename": row.filename,
        "status": row.status,
        "step": row.step,
        "progress": row.progress,
        "error": row.error,
        "retention_mode": row.retention_mode,
        "retention_days": row.retention_days,
        "secret": row.secret,
    }
    for key in TIME_KEYS:
        job[key] = _iso(getattr(row, key))
    for blob in (row.options, row.state):
        try:
            job.update(json.loads(blob) if blob else {})
        except ValueError:
            logger.warning("Unreadable job state", extra={"job_id": row.job_id})
    return job
