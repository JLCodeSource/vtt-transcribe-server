# Multi-stage Dockerfile for vtt-transcribe-server (API + worker images)
# The core transcription package (vtt-transcribe) is installed from PyPI as a dependency,
# resolved in uv.lock. The CLI image lives in the public core repo.
#
# Build args:
#   SYNC_ARGS  extra flags for `uv sync`. Default "--extra sherpa" = key-free CPU diarization
#              (sherpa-onnx, ~46 MB models, no torch). Use "--extra diarization" to add pyannote/torch (large).

# Build stage: resolve and install dependencies into /opt/venv
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

ARG SYNC_ARGS="--extra sherpa"

# Dependency layer (cached until pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project ${SYNC_ARGS}

# Project source
COPY vtt_server ./vtt_server
RUN uv sync --frozen --no-dev --no-editable ${SYNC_ARGS}

# Runtime stage: minimal image with only runtime dependencies
FROM python:3.12-slim AS base

# Configurable UID for non-root user (default 1000 for typical Linux desktops)
ARG USER_UID=1000

# Runtime system dependencies (ffmpeg for moviepy/Whisper audio handling, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u "$USER_UID" vttuser

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# ============================================================================
# API Target - for FastAPI server
# ============================================================================
FROM base AS api

COPY --from=builder /opt/venv /opt/venv

RUN mkdir -p /app/uploads /app/logs && \
    chown -R vttuser:vttuser /app

WORKDIR /app
USER vttuser

EXPOSE 8000

CMD ["uvicorn", "vtt_server.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================================
# Worker Target - for background transcription jobs
# ============================================================================
FROM base AS worker

COPY --from=builder /opt/venv /opt/venv

RUN mkdir -p /app/uploads /app/logs && \
    chown -R vttuser:vttuser /app

WORKDIR /app
USER vttuser

# TODO: Uncomment when worker module is implemented
# CMD ["python", "-m", "vtt_server.worker"]
# Placeholder: long-running no-op to prevent restart loops
CMD ["sleep", "infinity"]
