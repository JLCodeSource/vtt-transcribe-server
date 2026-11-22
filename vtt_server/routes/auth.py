"""Authentication routes for user registration and login."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from vtt_server import mailer
from vtt_server.access import RateLimiter, registration_open
from vtt_server.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_access_token,
    get_current_user_allow_reset,
    get_password_hash,
    get_user_by_email,
    get_user_by_username,
    verify_password,
)
from vtt_server.database import get_db
from vtt_server.models import User

router = APIRouter(prefix="/auth", tags=["authentication"])

# Sign-in and sign-up are brute-force targets: limit attempts per client address
# (VTT_LOGIN_RATE_LIMIT / VTT_REGISTER_RATE_LIMIT per 5 minutes; 0 disables).
login_limiter = RateLimiter("VTT_LOGIN_RATE_LIMIT", 10, 300)
register_limiter = RateLimiter("VTT_REGISTER_RATE_LIMIT", 5, 300)


# Request/Response models
class UserCreate(BaseModel):
    """User registration request."""

    username: str
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User response (without password)."""

    model_config = {"from_attributes": True}

    id: int
    username: str
    email: str
    is_active: bool
    is_superuser: bool
    must_change_password: bool = False


class Token(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str
    must_change_password: bool = False


class PasswordChange(BaseModel):
    """Change-password request."""

    current_password: str
    new_password: str


class TokenData(BaseModel):
    """Token payload data."""

    username: str | None = None


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_limiter)],
)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    """Register a new user (unless an administrator has switched sign-ups off with VTT_ALLOW_REGISTRATION=false)."""
    if not registration_open():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is closed")
    # Check if username already exists
    existing_user = await get_user_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    # Check if email already exists
    existing_email = await get_user_by_email(db, user_data.email)
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password,
        is_active=True,
        is_superuser=False,
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    mailer.notify_welcome(new_user.email, new_user.username)  # background; skipped when SMTP is not configured

    return new_user


@router.post("/token", response_model=Token, dependencies=[Depends(login_limiter)])
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Login with username and password to get access token."""
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)

    return Token(access_token=access_token, token_type="bearer", must_change_password=bool(user.must_change_password))  # noqa: S106


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user_allow_reset)) -> User:
    """Get current user information (works while a password change is pending, so the UI can prompt for it)."""
    return current_user


MIN_PASSWORD_LENGTH = 8


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(get_current_user_allow_reset),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Change your own password. Clears a pending administrator-forced reset."""
    if current_user.oauth_provider is not None:
        raise HTTPException(status_code=400, detail="Password sign-in is not available for this account")
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(data.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"New password must be at least {MIN_PASSWORD_LENGTH} characters")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")
    current_user.hashed_password = get_password_hash(data.new_password)
    current_user.must_change_password = False
    db.add(current_user)
    await db.commit()
