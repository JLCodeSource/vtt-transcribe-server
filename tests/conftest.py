"""Pytest configuration for loading environment variables."""

import asyncio
import os
import warnings
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests import TEST_SECRET_KEY

# Configure pytest-asyncio
pytest_plugins = ("pytest_asyncio",)

# Set required environment variables for API tests BEFORE any imports
os.environ.setdefault("SECRET_KEY", TEST_SECRET_KEY)
# Rate limiting is covered by its own tests; keep it out of the way of tests that sign in repeatedly
os.environ.setdefault("VTT_LOGIN_RATE_LIMIT", "0")
os.environ.setdefault("VTT_REGISTER_RATE_LIMIT", "0")
os.environ.setdefault("VTT_MAX_ACTIVE_JOBS_PER_USER", "0")  # job capacity has its own tests
os.environ.setdefault("VTT_MAX_ACTIVE_JOBS", "0")
os.environ.setdefault("VTT_STATIC_DIR", "")  # a locally built UI in vtt_server/static must not change the API tests
os.environ.setdefault("ENCRYPTION_KEY", "eZG7WcaEfouAvUvzsF8dpS1Arw-lfhjCs5LU4gzuXVE=")

# passlib (unmaintained) imports the stdlib 'crypt' module, deprecated in Python 3.12 and not ours to change.
# Import it once here under a narrow filter so the suite can run with -W error; this is the only warning filtered.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="'crypt' is deprecated", category=DeprecationWarning)
    import passlib.context  # noqa: F401

# Load .env file at module import time (before pytest collection)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=False)  # Don't override test values


@pytest.fixture(autouse=True)
def _no_job_persistence():
    """Job persistence and the queue worker are covered by their own tests; keep other tests off the real database."""
    from vtt_server.jobstore import store
    from vtt_server.routes.transcription import jobs

    for job_id in [k for k, j in jobs.items() if isinstance(j, dict) and j.get("status") in {"pending", "processing"}]:
        del jobs[job_id]  # a waiting job left behind by another test would run first in the FIFO queue
    store.enabled = False
    store.session_factory = None
    yield
    store.enabled = True
    store.session_factory = None


@pytest.fixture
def inline_queue(monkeypatch):
    """Run queued jobs to completion, in order, as soon as they are submitted (deterministic, no background task).

    A plain ``TestClient(app)`` opens a fresh event loop for every request and closes it afterwards, so the real worker
    task, which lives on the loop of the request that started it, is cancelled whenever it has not finished by then.
    Tests that submit a job and then read its result use this instead of polling for a task that may have been dropped.
    """
    import asyncio
    import threading

    from vtt_server import jobqueue

    def drain(self):  # noqa: ARG001
        def work():
            async def run_all():
                while queued := jobqueue.waiting(jobqueue.queue._jobs()):  # noqa: SLF001
                    await jobqueue.queue.process(queued[0])

            asyncio.run(run_all())

        thread = threading.Thread(target=work)
        thread.start()
        thread.join()

    monkeypatch.setattr(jobqueue.JobQueue, "ensure_running", drain)


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


OpenDb = Callable[[Callable[[AsyncSession], Awaitable[None]]], async_sessionmaker[AsyncSession]]


@pytest.fixture
def memory_db() -> Iterator[OpenDb]:
    """Opens an in-memory database with the schema created and ``seed(session)`` run; every engine is disposed afterwards."""
    from vtt_server.models import User  # imported late: the environment above must be set first

    engines = []

    def open_db(seed: Callable[[AsyncSession], Awaitable[None]]) -> async_sessionmaker[AsyncSession]:
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        engines.append(engine)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def create_and_seed() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(User.metadata.create_all)  # the models' own Base: other tests reload vtt_server.database
            async with maker() as session:
                await seed(session)

        asyncio.run(create_and_seed())
        return maker

    yield open_db
    for engine in engines:
        asyncio.run(engine.dispose())
