"""The signed-in user's own settings: retention default, notification opt-out, default options, OpenAI key, account deletion.

The OpenAI key is stored encrypted (the ``api_keys`` table) and is never returned: only whether one is set and its last four
characters. Changing the password is ``POST /auth/change-password``.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from vtt_server import mailer, retention
from vtt_server.auth import get_current_active_user, verify_password
from vtt_server.database import get_db
from vtt_server.engines import ENGINES
from vtt_server.models import APIKey, User
from vtt_server.routes.api_keys import decrypt_key, encrypt_key
from vtt_server.routes.transcription import purge_user_jobs

router = APIRouter(prefix="/api/profile", tags=["profile"])

MIN_KEY_LENGTH = 8
MAX_SPEAKERS = 8


class ProfileUpdate(BaseModel):
    """Fields left out are unchanged. ``retention_mode: null``-equivalent "default" restores the platform default."""

    retention_mode: str | None = Field(None, description="keep | delete | hold | default")
    retention_days: int | None = None
    notify_job_ready: bool | None = None
    default_options: dict[str, Any] | None = None


class KeyBody(BaseModel):
    """An OpenAI API key to store (replaces any existing one)."""

    api_key: str = Field(min_length=1, max_length=500)


class DeleteAccountBody(BaseModel):
    """Account deletion needs the password again (not for accounts that sign in through OAuth)."""

    password: str | None = None


def _default_options(user: User) -> dict[str, Any]:
    try:
        value = json.loads(user.default_options) if user.default_options else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def _openai_key(db: AsyncSession, user: User) -> APIKey | None:
    result = await db.execute(select(APIKey).where(APIKey.user_id == user.id, APIKey.service == "openai"))
    return result.scalars().first()


def _hint(key: APIKey | None) -> str | None:
    """``****abcd``: enough to recognise a key, never enough to use it."""
    if key is None:
        return None
    try:
        return "****" + decrypt_key(key.encrypted_key)[-4:]
    except Exception:
        return "****"


async def _view(db: AsyncSession, user: User) -> dict[str, Any]:
    key = await _openai_key(db, user)
    effective = retention.resolve(None, None, user.retention_mode, user.retention_days)
    return {
        "username": user.username,
        "email": user.email,
        "oauth_provider": user.oauth_provider,
        "retention_mode": user.retention_mode or "default",
        "retention_days": user.retention_days,
        "effective_retention": effective,
        "platform_retention": retention.platform_policy(),
        "notify_job_ready": bool(user.notify_job_ready),
        "email_enabled": mailer.email_configured(),
        "default_options": _default_options(user),
        "has_openai_key": key is not None,
        "openai_key_hint": _hint(key),
    }


def _clean_options(options: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if options.get("engine") is not None:
        if options["engine"] not in ENGINES:
            raise HTTPException(status_code=422, detail=f"engine must be one of: {', '.join(ENGINES)}")
        clean["engine"] = options["engine"]
    if options.get("diarize") is not None:
        clean["diarize"] = bool(options["diarize"])
    if options.get("translate_to"):
        clean["translate_to"] = str(options["translate_to"])[:64]
    if options.get("num_speakers") is not None:
        try:
            speakers = int(options["num_speakers"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="num_speakers must be a number between 1 and 8") from None
        if not 1 <= speakers <= MAX_SPEAKERS:
            raise HTTPException(status_code=422, detail="num_speakers must be a number between 1 and 8")
        clean["num_speakers"] = speakers
    return clean


@router.get("")
async def get_profile(user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The user's settings (never the API key itself)."""
    return await _view(db, user)


@router.patch("")
async def update_profile(
    body: ProfileUpdate, user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Set the default retention policy, the job-ready email opt-out and default options."""
    if body.retention_mode is not None:
        if body.retention_mode == "default":
            user.retention_mode, user.retention_days = None, None
        else:
            try:
                policy = retention.normalize(body.retention_mode, body.retention_days)
            except retention.RetentionError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            user.retention_mode, user.retention_days = policy["mode"], policy["days"]
    if body.notify_job_ready is not None:
        user.notify_job_ready = body.notify_job_ready
    if body.default_options is not None:
        user.default_options = json.dumps(_clean_options(body.default_options))
    db.add(user)
    await db.commit()
    return await _view(db, user)


@router.put("/openai-key")
async def set_openai_key(
    body: KeyBody, user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Store (or replace) the user's OpenAI key; used when a job or translation request carries none."""
    api_key = body.api_key.strip()
    if len(api_key) < MIN_KEY_LENGTH:
        raise HTTPException(status_code=422, detail="That does not look like an API key")
    await db.execute(delete(APIKey).where(APIKey.user_id == user.id, APIKey.service == "openai"))
    db.add(APIKey(user_id=user.id, service="openai", encrypted_key=encrypt_key(api_key), key_name="Profile key"))
    await db.commit()
    return await _view(db, user)


@router.delete("/openai-key")
async def remove_openai_key(
    user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Remove the stored OpenAI key."""
    await db.execute(delete(APIKey).where(APIKey.user_id == user.id, APIKey.service == "openai"))
    await db.commit()
    return await _view(db, user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountBody, user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)
) -> None:
    """Delete your own account with all of its jobs, results, stored audio and API keys. This cannot be undone."""
    if user.oauth_provider is None and not (body.password and verify_password(body.password, user.hashed_password)):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    if user.is_superuser:
        query = select(User.id).where(User.is_superuser.is_(True), User.is_active.is_(True), User.id != user.id)
        others = await db.execute(query)
        if others.first() is None:
            raise HTTPException(status_code=400, detail="You are the last administrator: promote someone else first")
    user_id = user.id
    await db.execute(delete(APIKey).where(APIKey.user_id == user_id))
    await db.delete(user)
    await db.commit()
    purge_user_jobs(user_id)
