"""Retention policy: how long a finished job (and its audio and results) is kept.

Per job (and as each user's default) a policy is one of

``keep``    kept until the user deletes it;
``delete``  removed shortly after the first download (``VTT_DELETE_GRACE_MINUTES``, default 15), or, if never downloaded,
            ``VTT_DELETE_UNDOWNLOADED_HOURS`` (default 24) after the job finished;
``hold``    kept for ``days`` days after the job finished (platform default: ``VTT_DEFAULT_RETENTION_DAYS``, 7).

Everything here is pure (the clock is passed in) so it is easy to unit-test; :func:`sweep` is the one function that deletes.
"""

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

MODES = ("keep", "delete", "hold")
MAX_HOLD_DAYS = 3650
FINISHED = {"completed", "failed", "cancelled"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def default_retention_days() -> int:
    """Platform default hold, in days (``VTT_DEFAULT_RETENTION_DAYS``, default 7)."""
    days = int(_float_env("VTT_DEFAULT_RETENTION_DAYS", 7))
    return days if 1 <= days <= MAX_HOLD_DAYS else 7


def platform_policy() -> dict[str, Any]:
    return {"mode": "hold", "days": default_retention_days()}


class RetentionError(ValueError):
    """An invalid retention policy."""


def normalize(mode: str | None, days: int | None) -> dict[str, Any]:
    """Validate a policy. ``hold`` needs ``days`` (1..3650); other modes carry no days."""
    if mode not in MODES:
        msg = f"retention mode must be one of: {', '.join(MODES)}"
        raise RetentionError(msg)
    if mode != "hold":
        return {"mode": mode, "days": None}
    if days is None:
        days = default_retention_days()
    if not 1 <= days <= MAX_HOLD_DAYS:
        msg = f"retention days must be between 1 and {MAX_HOLD_DAYS}"
        raise RetentionError(msg)
    return {"mode": "hold", "days": days}


def resolve(
    requested_mode: str | None, requested_days: int | None, user_mode: str | None, user_days: int | None
) -> dict[str, Any]:
    """Item choice, else the user's default, else the platform default."""
    if requested_mode:
        return normalize(requested_mode, requested_days)
    if user_mode:
        try:
            return normalize(user_mode, user_days)
        except RetentionError:
            pass
    return platform_policy()


def parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def compute_expiry(job: dict[str, Any]) -> datetime | None:
    """When this job should be deleted, or ``None`` (not finished yet, or ``keep``)."""
    finished = parse_ts(job.get("completed_at"))
    if job.get("status") not in FINISHED or finished is None:
        return None
    mode = job.get("retention_mode") or "hold"
    if mode == "keep":
        return None
    if mode == "delete":
        downloaded = parse_ts(job.get("downloaded_at"))
        if downloaded is not None:
            return downloaded + timedelta(minutes=_float_env("VTT_DELETE_GRACE_MINUTES", 15))
        return finished + timedelta(hours=_float_env("VTT_DELETE_UNDOWNLOADED_HOURS", 24))
    days = job.get("retention_days") or default_retention_days()
    return finished + timedelta(days=days)


def apply_expiry(job: dict[str, Any]) -> None:
    """Refresh ``job["expires_at"]`` (ISO string or ``None``) from its policy and timestamps."""
    expiry = compute_expiry(job)
    job["expires_at"] = expiry.isoformat() if expiry else None


def is_expired(job: dict[str, Any], now: datetime) -> bool:
    expiry = parse_ts(job.get("expires_at")) or compute_expiry(job)
    return expiry is not None and expiry <= now


async def sweep(
    jobs: dict[str, dict[str, Any]],
    delete: Callable[[str], Awaitable[None]],
    now: datetime | None = None,
) -> list[str]:
    """Delete every expired, finished job via ``delete(job_id)``; returns the removed ids."""
    now = now or utcnow()
    removed = []
    for job_id, job in list(jobs.items()):
        if job.get("status") in FINISHED and is_expired(job, now):
            await delete(job_id)
            removed.append(job_id)
    return removed
