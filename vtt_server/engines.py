"""Transcription / translation engine selection and diarizer construction.

Engines
-------
``openai``  Whisper + chat translation through the OpenAI API (core's ``VideoTranscriber`` / ``AudioTranslator``);
            needs the caller's API key.
``local``   Key-free on-box faster-whisper + M2M100 translation (core's ``LocalTranscriber`` /
            ``LocalTextTranslator``). Needs the ``local`` extra (``uv sync --extra local``, or build the image with
            ``--build-arg SYNC_ARGS="--extra local"``); without it ``engine=local`` is HTTP 501 and
            ``GET /api/engines`` reports it unavailable. Model: ``VTT_LOCAL_MODEL`` (default ``small``).

Diarization
-----------
sherpa-onnx via core's ``SherpaDiarizer``: CPU only, no token, no torch (~46 MB models fetched on first use).
"""

import importlib.util
import os
from collections.abc import Callable
from typing import Any

ENGINES = ("openai", "local")

_LOCAL_MSG = "The local (key-free) engine is not installed in this deployment; use engine=openai or install the 'local' extra."


class EngineUnavailableError(RuntimeError):
    """The requested engine cannot run in this deployment."""


def local_engine_available() -> bool:
    """True when the ``local`` extra (faster-whisper, sentencepiece) is installed."""
    return all(importlib.util.find_spec(m) is not None for m in ("faster_whisper", "sentencepiece"))


def engine_info() -> list[dict[str, Any]]:
    """Describe the selectable engines (served by ``GET /api/engines`` for the UI's engine picker)."""
    return [
        {"id": "openai", "label": "OpenAI Whisper (API)", "requires_api_key": True, "available": True},
        {
            "id": "local",
            "label": "Local Whisper (no key)",
            "requires_api_key": False,
            "available": local_engine_available(),
        },
    ]


def validate_engine(name: str, api_key: str | None) -> None:
    """Raise ``ValueError`` for unknown engines / missing key, ``EngineUnavailableError`` if not installed."""
    if name not in ENGINES:
        msg = f"Unknown engine: {name}. Choose one of: {', '.join(ENGINES)}"
        raise ValueError(msg)
    if name == "local" and not local_engine_available():
        raise EngineUnavailableError(_LOCAL_MSG)
    if name == "openai" and not api_key:
        msg = "api_key is required for the openai engine"
        raise ValueError(msg)


def make_local_transcriber() -> Any:
    """Core's ``LocalTranscriber`` (faster-whisper); same interface as ``VideoTranscriber``, needs no key."""
    from vtt_transcribe.local_whisper import LocalTranscriber

    return LocalTranscriber(model=os.environ.get("VTT_LOCAL_MODEL") or None)


def make_local_translate_fn(
    target_language: str, source_language: str | None, progress: Callable[[float, str | None], None] | None = None
) -> Callable[[str], str]:
    """``transcript -> translated transcript`` on-box (core's ``LocalTextTranslator``, M2M100)."""
    from vtt_transcribe.local_translate import LocalTextTranslator

    translator = LocalTextTranslator(source_language)
    if progress is not None:
        translator.progress_callback = progress  # core >= 0.6.2
    return lambda text: translator.translate_text(text, target_language)


def build_diarizer(*, num_speakers: int | None) -> Any:
    """Instantiate the sherpa diarizer (imported lazily so tests can run without the models)."""
    from vtt_transcribe.sherpa_diarization import SherpaDiarizer

    return SherpaDiarizer(num_speakers=num_speakers)
