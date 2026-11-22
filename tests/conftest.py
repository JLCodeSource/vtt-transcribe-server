"""Pytest configuration for loading environment variables."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Configure pytest-asyncio
pytest_plugins = ("pytest_asyncio",)

# Set required environment variables for API tests BEFORE any imports
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-signing-only")
# Rate limiting is covered by its own tests; keep it out of the way of tests that sign in repeatedly
os.environ.setdefault("VTT_LOGIN_RATE_LIMIT", "0")
os.environ.setdefault("VTT_REGISTER_RATE_LIMIT", "0")
os.environ.setdefault("VTT_MAX_ACTIVE_JOBS_PER_USER", "0")  # job capacity has its own tests
os.environ.setdefault("VTT_MAX_ACTIVE_JOBS", "0")
os.environ.setdefault("ENCRYPTION_KEY", "eZG7WcaEfouAvUvzsF8dpS1Arw-lfhjCs5LU4gzuXVE=")

# Load .env file at module import time (before pytest collection)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=False)  # Don't override test values


@pytest.fixture(scope="session", autouse=True)
def load_env() -> None:
    """Ensure environment variables are loaded from .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)  # Don't override test values


def _auth_dependencies(_app) -> set:  # type: ignore[no-untyped-def]
    """The auth dependency callables the routers captured at import time (FastAPI keys overrides by function identity)."""
    from vtt_server import access
    from vtt_server.routes import transcription, websockets

    return {
        transcription.get_current_active_user,
        access.get_current_active_user_header_or_query,
        websockets.get_current_active_user_header_or_query,
    }


def _apps() -> list:  # type: ignore[type-arg]
    """Every FastAPI instance tests may be using (test_static_ui reloads vtt_server.app, which creates a new one)."""
    import sys

    from fastapi import FastAPI

    import vtt_server.app  # noqa: F401

    candidates = [getattr(sys.modules.get("vtt_server.app"), "app", None), getattr(sys.modules.get("vtt_server"), "app", None)]
    apps: list = []
    for candidate in candidates:
        if isinstance(candidate, FastAPI) and all(candidate is not a for a in apps):
            apps.append(candidate)
    return apps


@pytest.fixture
def fake_auth():
    """Bypass authentication for tests that exercise job behaviour rather than access control.

    The stand-in user is an administrator, so ownership checks pass for jobs created directly in ``jobs``.
    Access-control behaviour itself is covered in tests/test_api/test_access.py with real tokens.
    """
    from vtt_server.models import User

    user = User(id=1, username="tester", email="tester@example.com", hashed_password="x", is_active=True, is_superuser=True)
    user.must_change_password = False
    overridden = []
    for app in _apps():
        for call in _auth_dependencies(app):
            app.dependency_overrides[call] = lambda: user
            overridden.append((app, call))
    yield user
    for app, call in overridden:
        app.dependency_overrides.pop(call, None)
