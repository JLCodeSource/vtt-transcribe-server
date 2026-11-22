"""Access control for the job endpoints: ownership, per-user capacity and rate limiting."""

import os
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from vtt_server.auth import get_current_active_user_header_or_query
from vtt_server.models import User


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _jobs() -> dict[str, dict[str, Any]]:
    from vtt_server.routes.transcription import jobs

    return jobs


def may_access(job: dict[str, Any], user: User) -> bool:
    """Owners and administrators may see a job. Jobs with no recorded owner are administrator-only."""
    return bool(user.is_superuser or (job.get("user_id") is not None and job.get("user_id") == user.id))


async def owned_job(
    job_id: str,
    user: User = Depends(get_current_active_user_header_or_query),
) -> dict[str, Any]:
    """The job, if it exists and belongs to the caller. Others get 404 so job ids cannot be probed."""
    job = _jobs().get(job_id)
    if job is None or not may_access(job, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def check_capacity(user: User) -> None:
    """Refuse a new job when this user, or the whole server, already has too many running.

    Limits come from ``VTT_MAX_ACTIVE_JOBS_PER_USER`` (default 2) and ``VTT_MAX_ACTIVE_JOBS`` (default 4); 0 disables.
    """
    per_user = _int_env("VTT_MAX_ACTIVE_JOBS_PER_USER", 2)
    total = _int_env("VTT_MAX_ACTIVE_JOBS", 4)
    if not per_user and not total:
        return
    active = [j for j in _jobs().values() if isinstance(j, dict) and j.get("status") in {"pending", "processing"}]
    if per_user and sum(1 for j in active if j.get("user_id") == user.id) >= per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You already have jobs running. Wait for one to finish before starting another.",
        )
    if total and len(active) >= total:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The server is busy. Try again in a few minutes.",
            headers={"Retry-After": "60"},
        )


class RateLimiter:
    """In-memory sliding-window limiter keyed by client address (use as a route dependency).

    ``limit_env`` names an environment variable holding the allowed hits per window; ``0`` disables the limit.
    """

    def __init__(self, limit_env: str, default_limit: int, window_seconds: int) -> None:
        self.limit_env = limit_env
        self.default_limit = default_limit
        self.window = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def __call__(self, request: Request) -> None:
        limit = _int_env(self.limit_env, self.default_limit)
        if limit <= 0:
            return
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > self.window:
            window.popleft()
        if len(window) >= limit:
            retry = max(1, int(self.window - (now - window[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Try again later.",
                headers={"Retry-After": str(retry)},
            )
        window.append(now)
        if len(self.hits) > 10_000:  # bound memory under a spray of addresses
            for stale in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window]:
                del self.hits[stale]


def registration_open() -> bool:
    """New sign-ups are allowed unless ``VTT_ALLOW_REGISTRATION`` is 0/false/no/off."""
    return os.getenv("VTT_ALLOW_REGISTRATION", "true").strip().lower() not in {"0", "false", "no", "off"}
