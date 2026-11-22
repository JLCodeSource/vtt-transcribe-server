"""Administrator endpoints: review, deactivate, force a password reset for, and delete users."""

import secrets
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from vtt_server.auth import get_current_admin, get_password_hash
from vtt_server.database import get_db
from vtt_server.models import TranscriptionJob, User
from vtt_server.routes.transcription import _job_dir, purge_user_jobs

router = APIRouter(prefix="/admin", tags=["admin"])

MIN_PASSWORD_LENGTH = 8


class AdminUser(BaseModel):
    """A user as seen by an administrator (never includes the password hash)."""

    id: int
    username: str
    email: str
    is_active: bool
    is_superuser: bool
    must_change_password: bool
    oauth_provider: str | None
    created_at: str
    job_count: int


class UserList(BaseModel):
    """A page of users."""

    total: int
    users: list[AdminUser]


class PasswordResetRequest(BaseModel):
    """Optional temporary password; one is generated when omitted."""

    temporary_password: str | None = None


class PasswordResetResponse(BaseModel):
    """The temporary password, shown once."""

    user_id: int
    temporary_password: str
    must_change_password: bool = True


def _view(user: User, job_count: int) -> AdminUser:
    return AdminUser(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        must_change_password=user.must_change_password,
        oauth_provider=user.oauth_provider,
        created_at=user.created_at.isoformat() if user.created_at else "",
        job_count=job_count,
    )


async def _job_count(db: AsyncSession, user_id: int) -> int:
    query = select(func.count()).select_from(TranscriptionJob).where(TranscriptionJob.user_id == user_id)
    return int((await db.execute(query)).scalar_one())


async def _get_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _other_active_admins(db: AsyncSession, excluding_id: int) -> int:
    query = (
        select(func.count())
        .select_from(User)
        .where(User.is_superuser.is_(True), User.is_active.is_(True), User.id != excluding_id)
    )
    return int((await db.execute(query)).scalar_one())


@router.get("/users", response_model=UserList)
async def list_users(
    _admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(None, description="Substring of username or email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> UserList:
    """List users, newest first, optionally filtered by username or email."""
    where = []
    if q:
        like = f"%{q}%"
        where.append(or_(User.username.ilike(like), User.email.ilike(like)))
    total = int((await db.execute(select(func.count()).select_from(User).where(*where))).scalar_one())
    page = select(User).where(*where).order_by(User.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(page)).scalars().all()
    count_query = (
        select(TranscriptionJob.user_id, func.count())
        .where(TranscriptionJob.user_id.in_([u.id for u in rows]))
        .group_by(TranscriptionJob.user_id)
    )
    counts = dict((await db.execute(count_query)).all())
    return UserList(total=total, users=[_view(u, counts.get(u.id, 0)) for u in rows])


@router.get("/users/{user_id}", response_model=AdminUser)
async def get_user(
    user_id: int,
    _admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    """One user's details."""
    user = await _get_user(db, user_id)
    return _view(user, await _job_count(db, user.id))


@router.post("/users/{user_id}/deactivate", response_model=AdminUser)
async def deactivate_user(
    user_id: int,
    admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    """Block a user from signing in or using the API. Their data is kept."""
    user = await _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if user.is_superuser and await _other_active_admins(db, user.id) == 0:
        raise HTTPException(status_code=400, detail="Cannot deactivate the last active administrator")
    user.is_active = False
    await db.commit()
    return _view(user, await _job_count(db, user.id))


@router.post("/users/{user_id}/activate", response_model=AdminUser)
async def activate_user(
    user_id: int,
    _admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    """Re-enable a deactivated user."""
    user = await _get_user(db, user_id)
    user.is_active = True
    await db.commit()
    return _view(user, await _job_count(db, user.id))


@router.post("/users/{user_id}/reset-password", response_model=PasswordResetResponse)
async def force_password_reset(
    user_id: int,
    _admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: PasswordResetRequest | None = None,
) -> PasswordResetResponse:
    """Set a temporary password and require a change at next sign-in. The temporary password is returned once."""
    user = await _get_user(db, user_id)
    if user.oauth_provider is not None:
        raise HTTPException(status_code=400, detail="This account signs in with an external provider and has no password")
    temporary = (body.temporary_password if body else None) or secrets.token_urlsafe(12)
    if len(temporary) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Temporary password must be at least {MIN_PASSWORD_LENGTH} characters")
    user.hashed_password = get_password_hash(temporary)
    user.must_change_password = True
    await db.commit()
    return PasswordResetResponse(user_id=user.id, temporary_password=temporary)


@router.post("/users/{user_id}/make-admin", response_model=AdminUser)
async def make_admin(
    user_id: int,
    _admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    """Grant administrator rights."""
    user = await _get_user(db, user_id)
    user.is_superuser = True
    await db.commit()
    return _view(user, await _job_count(db, user.id))


@router.post("/users/{user_id}/remove-admin", response_model=AdminUser)
async def remove_admin(
    user_id: int,
    admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    """Revoke administrator rights (not your own, and never the last administrator)."""
    user = await _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own administrator rights")
    if user.is_superuser and await _other_active_admins(db, user.id) == 0:
        raise HTTPException(status_code=400, detail="Cannot remove the last active administrator")
    user.is_superuser = False
    await db.commit()
    return _view(user, await _job_count(db, user.id))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    admin: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Permanently delete a user, their API keys, job history and stored job files."""
    user = await _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if user.is_superuser and await _other_active_admins(db, user.id) == 0:
        raise HTTPException(status_code=400, detail="Cannot delete the last active administrator")
    job_ids = (await db.execute(select(TranscriptionJob.job_id).where(TranscriptionJob.user_id == user.id))).scalars().all()
    await db.delete(user)
    await db.commit()
    for job_id in job_ids:
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    purge_user_jobs(user.id)
