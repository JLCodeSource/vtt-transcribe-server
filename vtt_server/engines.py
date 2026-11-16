"""Transcription-engine and diarizer selection.

Engines
-------
``openai``  Whisper via the OpenAI API (core's ``VideoTranscriber``); needs the caller's API key.
``local``   **SEAM, not implemented.** Key-free on-box Whisper, planned for core's ``--engine local``.
            Until core ships it, selecting it fails with :class:`EngineUnavailableError` (HTTP 501).
            When core releases it, replace the body of :func:`_local_engine` (and flip ``LOCAL_ENGINE_AVAILABLE``);
            nothing else in the server should need to change. See docs/DESIGN.md.

Diarization
-----------
sherpa-onnx via core's ``SherpaDiarizer``: CPU only, no token, no torch (~46 MB models fetched on first use).
It is the only backend; pyannote/torch was dropped to keep the image small.
"""

from pathlib import Path
from typing import Any, Protocol

ENGINES = ("openai", "local")

_LOCAL_MSG = "The local (key-free) Whisper engine is not available yet; use engine=openai."

#: Flip to True once the installed vtt-transcribe provides ``--engine local`` (see ``_local_engine``).
LOCAL_ENGINE_AVAILABLE = False


class EngineUnavailableError(RuntimeError):
    """The requested engine or diarizer cannot run in this deployment."""


class Engine(Protocol):
    """What the job pipeline needs from a transcription engine."""

    def detect_language(self, audio_path: Path) -> str:
        """Return the spoken language code."""
        ...

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> str:
        """Return a ``[HH:MM:SS - HH:MM:SS] text`` transcript."""
        ...


def engine_info() -> list[dict[str, Any]]:
    """Describe the selectable engines (served by ``GET /api/engines`` for the UI's engine picker)."""
    return [
        {"id": "openai", "label": "OpenAI Whisper (API)", "requires_api_key": True, "available": True},
        {
            "id": "local",
            "label": "Local Whisper (no key)",
            "requires_api_key": False,
            "available": LOCAL_ENGINE_AVAILABLE,
        },
    ]


def _local_engine() -> Engine:
    """SEAM: construct core's local Whisper engine once it ships (``vtt-transcribe --engine local``)."""
    raise EngineUnavailableError(_LOCAL_MSG)


def validate_engine(name: str, api_key: str | None) -> None:
    """Raise ``ValueError`` for unknown engines / missing key, ``EngineUnavailableError`` for unshipped ones."""
    if name not in ENGINES:
        msg = f"Unknown engine: {name}. Choose one of: {', '.join(ENGINES)}"
        raise ValueError(msg)
    if name == "local" and not LOCAL_ENGINE_AVAILABLE:
        raise EngineUnavailableError(_LOCAL_MSG)
    if name == "openai" and not api_key:
        msg = "api_key is required for the openai engine"
        raise ValueError(msg)


def build_diarizer(*, num_speakers: int | None) -> Any:
    """Instantiate the sherpa diarizer (imported lazily so tests can run without the models)."""
    from vtt_transcribe.sherpa_diarization import SherpaDiarizer

    return SherpaDiarizer(num_speakers=num_speakers)
