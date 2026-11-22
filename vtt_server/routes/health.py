"""Health check endpoints."""

from typing import Any

from fastapi import APIRouter
from vtt_transcribe import __version__ as core_version
from vtt_transcribe.logging_config import get_logger

from vtt_server._version import __version__

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    logger.debug("Health check request received")
    return {"status": "healthy", "version": __version__, "core_version": core_version}
