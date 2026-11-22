"""The job queue: one worker, strictly first-in-first-out, never two jobs at once.

Every submission becomes a ``pending`` job (waiting its turn, even when the server is idle). A single asyncio task
(:data:`queue`) picks the oldest pending job, runs it to the end, then the next. Jobs and their uploaded audio are
persisted (see :mod:`vtt_server.jobstore`), so after a restart :func:`recover` re-queues whatever was waiting or running.

Progress: ``job["step"]`` (queued, detecting, transcribing, diarizing, translating, done) and ``job["progress"]`` (0..1,
weighted by the steps this job needs). Engines that report fractions (core's ``progress_callback(fraction, message)``)
move the bar inside a step; others advance it step by step.
"""

import asyncio
import inspect
import itertools
import logging
from collections.abc import Callable
from typing import Any

from vtt_server import retention
from vtt_server.jobstore import store

logger = logging.getLogger(__name__)

ACTIVE = {"pending", "processing"}
_seq = itertools.count(1)

# Relative cost of each step, used to turn "step + fraction" into one overall percentage.
STEP_WEIGHTS = {"detecting": 0.05, "transcribing": 0.6, "diarizing": 0.2, "translating": 0.15}


class JobCancelled(Exception):  # noqa: N818 - control flow, not an error
    """Raised inside a running job when its owner cancelled it."""


def next_seq() -> int:
    return next(_seq)


def plan_for(job: dict[str, Any]) -> list[str]:
    """The pipeline steps this job will go through."""
    if job.get("diarize_only"):
        return ["diarizing"]
    steps = ["detecting", "transcribing"]
    if job.get("diarize"):
        steps.append("diarizing")
    if job.get("translate_to"):
        steps.append("translating")
    return steps


def overall(job: dict[str, Any], step: str, fraction: float | None) -> float:
    """Overall 0..1 progress when ``step`` is ``fraction`` done."""
    plan = plan_for(job)
    if step not in plan:
        return job.get("progress") or 0.0
    total = sum(STEP_WEIGHTS[s] for s in plan)
    before = sum(STEP_WEIGHTS[s] for s in plan[: plan.index(step)])
    frac = min(max(fraction or 0.0, 0.0), 1.0)
    return round((before + STEP_WEIGHTS[step] * frac) / total, 4)


def set_step(job: dict[str, Any], step: str, message: str | None = None, fraction: float | None = None) -> None:
    """Record the pipeline step (and optional position within it). Raises :class:`JobCancelled` if cancelled."""
    checkpoint(job)
    job["step"] = step
    job["progress"] = overall(job, step, fraction)
    if message:
        job["message"] = message


def checkpoint(job: dict[str, Any]) -> None:
    if job.get("cancel_requested"):
        raise JobCancelled


def progress_callback(job: dict[str, Any], step: str) -> Callable[[float, str | None], None]:
    """A ``progress_callback(fraction, message)`` for core calls (runs in the worker thread)."""

    def callback(fraction: float, message: str | None = None) -> None:
        checkpoint(job)  # lets a cancel abort the engine at its next report
        job["progress"] = overall(job, step, fraction)
        if message:
            job["message"] = message

    return callback


def accepts_progress(fn: Callable[..., Any]) -> bool:
    """True when ``fn`` explicitly declares a ``progress_callback`` parameter (older core releases do not)."""
    try:
        return "progress_callback" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def attach_progress(obj: Any, job: dict[str, Any], step: str) -> Callable[[float, str | None], None]:
    """Give a core object (transcriber, diarizer, translator) the job's ``progress_callback`` (core >= 0.6.2 reads
    ``.progress_callback``; older releases just ignore the attribute). Returns the callback."""
    callback = progress_callback(job, step)
    try:
        obj.progress_callback = callback
    except AttributeError:  # an object that refuses attributes simply reports no fine-grained progress
        logger.debug("%r does not accept progress_callback", obj)
    return callback


def with_progress(fn: Callable[..., Any], job: dict[str, Any], step: str) -> Callable[..., Any]:
    """Wrap ``fn`` so it receives ``progress_callback=`` when the installed core supports it."""
    if not accepts_progress(fn):
        return fn
    callback = progress_callback(job, step)
    return lambda *args, **kwargs: fn(*args, progress_callback=callback, **kwargs)


def waiting(jobs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Pending jobs, next-to-run first."""
    return sorted((j for j in jobs.values() if j.get("status") == "pending"), key=lambda j: j.get("queue_seq", 0))


def queue_position(jobs: dict[str, dict[str, Any]], job: dict[str, Any]) -> int | None:
    """1 for the job that runs next; ``None`` unless the job is waiting."""
    if job.get("status") != "pending":
        return None
    for i, candidate in enumerate(waiting(jobs), 1):
        if candidate is job:
            return i
    return None


class JobQueue:
    """Owns the single worker task."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self.current: str | None = None

    def _jobs(self) -> dict[str, dict[str, Any]]:
        from vtt_server.routes.transcription import jobs

        return jobs

    def ensure_running(self) -> None:
        """Start the worker on the running loop if needed, and wake it."""
        loop = asyncio.get_running_loop()
        if self._task is None or self._task.done() or self._loop is not loop:
            self._loop = loop
            self._wake = asyncio.Event()
            self._task = loop.create_task(self._run())
        if self._wake is not None:
            self._wake.set()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        wake = self._wake
        assert wake is not None
        while True:
            queued = waiting(self._jobs())
            if not queued:
                wake.clear()
                await wake.wait()
                continue
            await self.process(queued[0])

    async def process(self, job: dict[str, Any]) -> None:
        from vtt_server.routes import transcription as tx

        job_id = job["job_id"]
        self.current = job_id
        try:
            await tx.run_job(job)
        except asyncio.CancelledError:
            raise
        except Exception:  # run_job reports its own failures; this is the last line of defence
            logger.exception("Job crashed", extra={"job_id": job_id})
            job.update(status="failed", error="Internal error while processing the job")
            await store.save(job)
        finally:
            self.current = None

    def cancel(self, job: dict[str, Any]) -> str:
        """Cancel a waiting job at once, or flag a running one (it stops at its next checkpoint). Returns the new status."""
        if job.get("status") == "pending":
            job.update(status="cancelled", step="cancelled", message="Cancelled", completed_at=retention.utcnow().isoformat())
            job.pop("secret", None)
            retention.apply_expiry(job)
            return "cancelled"
        if job.get("status") == "processing":
            job["cancel_requested"] = True
            job["message"] = "Cancelling…"
            return "processing"
        return str(job.get("status"))


queue = JobQueue()


async def recover(jobs: dict[str, dict[str, Any]]) -> int:
    """Load persisted jobs into ``jobs``; re-queue anything that was waiting or running. Returns how many were re-queued."""
    requeued = 0
    for job in await store.load_all():
        if job["job_id"] in jobs:
            continue
        if job.get("status") in ACTIVE:
            job.update(status="pending", step="queued", progress=0.0, message="Re-queued after restart")
            job.pop("cancel_requested", None)
            requeued += 1
        job["queue_seq"] = next_seq()  # rows arrive oldest first, so FIFO order is preserved
        jobs[job["job_id"]] = job
        if job["status"] == "pending":
            await store.save(job)
    if requeued:
        queue.ensure_running()
    return requeued
