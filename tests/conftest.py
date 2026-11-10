"""Pytest configuration for loading environment variables."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Configure pytest-asyncio
pytest_plugins = ("pytest_asyncio",)

# Set required environment variables for API tests BEFORE any imports
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-signing-only")
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
