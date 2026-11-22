"""FastAPI application factory and configuration."""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from vtt_transcribe import __version__

from vtt_server import jobqueue
from vtt_server.database import init_db
from vtt_server.routes import admin, api_keys, auth, health, jobs, speakers, transcription, websockets

# OAuth is optional - only available if authlib/httpx are installed
try:
    from vtt_server.routes import oauth

    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False
    oauth = None  # type: ignore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore
    """Lifespan event handler for database initialization."""
    # Startup: Initialize database tables
    with contextlib.suppress(Exception):
        # Database initialization failed, continue without DB functionality
        await init_db()
        # Bring persisted jobs back: finished ones stay viewable, queued/running ones are re-queued
        await jobqueue.recover(transcription.jobs)
    sweeper = asyncio.create_task(_sweep_loop())
    yield
    # Shutdown: stop the sweeper and the queue worker (unfinished jobs are re-queued on the next start)
    sweeper.cancel()
    await jobqueue.queue.stop()


async def _sweep_loop() -> None:
    """Delete jobs whose retention has run out, every ``VTT_SWEEP_INTERVAL_SECONDS`` (default 300)."""
    try:
        interval = max(1.0, float(os.getenv("VTT_SWEEP_INTERVAL_SECONDS", "300")))
    except ValueError:
        interval = 300.0
    while True:
        try:
            removed = await transcription.sweep_expired()
            if removed:
                logger.info("Retention sweep removed %d job(s)", len(removed))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention sweep failed")
        await asyncio.sleep(interval)


app = FastAPI(
    title="vtt-transcribe API",
    version=__version__,
    description="REST API for transcribing audio and video files using OpenAI Whisper",
    lifespan=lifespan,
)

# Session middleware for OAuth state management (CSRF protection)
# Generate secure secret with: python -c "import secrets; print(secrets.token_hex(32))"
session_secret = os.getenv("SESSION_SECRET")
if not session_secret:
    session_secret = os.urandom(32).hex()
    logger.warning(
        "SESSION_SECRET not set - using random fallback. "
        "All sessions will be invalidated on restart. "
        "Set SESSION_SECRET environment variable in production."
    )

# Detect if we're in production (HTTPS should be enforced)
environment = os.getenv("ENVIRONMENT", "development").lower()
https_only = environment == "production"

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="vtt_session",
    max_age=3600,  # 1 hour
    same_site="lax",
    https_only=https_only,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public routes
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)

# OAuth routes (optional - only if dependencies installed)
if OAUTH_AVAILABLE and oauth is not None:
    app.include_router(oauth.router)
    logger.info("OAuth routes registered (authlib/httpx available)")
else:
    logger.warning("OAuth routes not available (authlib/httpx not installed)")

# API routes (authentication handled per-endpoint via dependencies)
app.include_router(api_keys.router)
app.include_router(jobs.router)
app.include_router(transcription.router)
app.include_router(speakers.router)
app.include_router(websockets.router)


# Optional single-container mode: serve the built frontend (target ``api-ui`` in the Dockerfile sets
# VTT_STATIC_DIR). Without it the API runs alone and the frontend ships as its own nginx image.
_static_dir = os.getenv("VTT_STATIC_DIR")
_serve_ui = bool(_static_dir) and Path(_static_dir).is_dir()


@app.get("/" if not _serve_ui else "/api-info")
async def root() -> dict[str, Any]:
    """Root endpoint with welcome message."""
    return {
        "message": "Welcome to vtt-transcribe API",
        "version": __version__,
        "docs": "/docs",
        "websocket": "/ws/jobs/{job_id}",
    }


if _serve_ui:
    from fastapi.staticfiles import StaticFiles

    # Registered last so every API / websocket route above takes precedence.
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="ui")
