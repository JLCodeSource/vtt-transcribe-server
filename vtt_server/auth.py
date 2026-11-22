"""Authentication and authorization utilities."""

import os
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vtt_server.database import get_db
from vtt_server.models import User

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    dev_mode = os.getenv("VTT_TRANSCRIBE_DEV_MODE", "").lower()
    if dev_mode in {"1", "true", "yes"}:
        SECRET_KEY = "development-secret-key-change-in-production"  # noqa: S105
    else:
        msg = (
            "SECRET_KEY environment variable must be set for JWT signing. "
            "To use the built-in development key, explicitly set VTT_TRANSCRIBE_DEV_MODE=1."
        )
        raise RuntimeError(msg)
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS512") or "HS512"
if ALGORITHM in {"ES256", "ES384", "ES512"}:
    msg = (
        "ECDSA JWT algorithms are not permitted due upstream python-ecdsa security risk. "
        "Use HS512 (default), HS256/HS384, or RSA algorithms."
    )
    raise RuntimeError(msg)
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)  # type: ignore[no-any-return]


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)  # type: ignore[no-any-return]


def create_access_token(data: dict[str, str | datetime], expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    # Convert datetime objects to ISO format strings for JSON serialization
    for key, value in to_encode.items():
        if isinstance(value, datetime):
            to_encode[key] = value.isoformat()
    expire = datetime.now(UTC) + expires_delta if expires_delta else datetime.now(UTC) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)  # type: ignore[arg-type]


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get user by email."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Get user by username."""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Authenticate a user by username and password."""
    user = await get_user_by_username(db, username)
    if not user:
        return None
    # Prevent password-based login for OAuth users
    if user.oauth_provider is not None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def user_from_token(token: str | None, db: AsyncSession) -> User:
    """Resolve a JWT to its user, or raise 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    if SECRET_KEY is None:
        raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError as e:
        raise credentials_exception from e

    user = await get_user_by_username(db, username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user from JWT token."""
    return await user_from_token(token, db)


async def get_current_user_allow_reset(current_user: User = Depends(get_current_user)) -> User:
    """Current active user, even if they still have to change their password (used by /auth/me and change-password)."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


async def get_current_active_user(current_user: User = Depends(get_current_user_allow_reset)) -> User:
    """Get current active user. Users who must change their password are blocked until they do."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    if current_user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return current_user


async def get_current_active_user_header_or_query(
    token: str | None = Depends(oauth2_scheme),
    access_token: str | None = Query(None, description="Bearer token for <audio src> and WebSocket URLs"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Like get_current_active_user, but the token may also arrive as ``?access_token=`` (media elements, WebSockets)."""
    user = await user_from_token(token or access_token, db)
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


async def get_current_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """Current active user, who must be an administrator."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return current_user


# Optional authentication - doesn't raise error if no token
async def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Get current user if authenticated, otherwise None."""
    if not token:
        return None
    if SECRET_KEY is None:
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            return None
    except JWTError:
        return None

    user = await get_user_by_username(db, username)
    return user if user and user.is_active else None
