# Multi-stage Dockerfile for vtt-transcribe-server (API + worker images)
# The core transcription package (vtt-transcribe) is installed from PyPI as a dependency,
# resolved in uv.lock. The CLI image lives in the public core repo.
#
# Build args:
# Targets:
#   api     FastAPI server only (the frontend ships separately as its own nginx image, see frontend/Dockerfile)
#   api-ui  same server with the built frontend bundled in and served at / (single container)
# Build arg SYNC_ARGS adds extras to `uv sync`; the default (empty) is the lean OpenAI-only image. For the key-free local
# engine (faster-whisper + local translation; bigger image):  --build-arg SYNC_ARGS="--extra local"
# Diarization is sherpa-onnx (CPU, ~46 MB models fetched on first use, no torch, no token).

# Build stage: resolve and install dependencies into /opt/venv
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

# Dependency layer (cached until pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock README.md ./
ARG SYNC_ARGS=""

RUN uv sync --frozen --no-dev --no-install-project ${SYNC_ARGS}

# Project source
COPY vtt_server ./vtt_server
RUN uv sync --frozen --no-dev --no-editable ${SYNC_ARGS}

# Runtime stage: minimal image with only runtime dependencies
FROM python:3.12-slim AS base

# Configurable UID for non-root user (default 1000 for typical Linux desktops)
ARG USER_UID=1000

# Runtime system dependencies: curl for healthchecks only. ffmpeg is NOT installed from apt (Debian's pulls in
# ~450 MB of codec/GPU libraries); the static build bundled with imageio-ffmpeg (a moviepy dependency) is
# linked onto PATH in the api stage instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u "$USER_UID" vttuser

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
# sherpa diarization models (~46 MB) are downloaded once on first use; mount a volume here to keep them
ENV VTT_MODEL_DIR=/app/models
# uploaded audio kept per job for speaker sample clips (deleted with the job)
ENV VTT_DATA_DIR=/app/jobs

# ============================================================================
# API Target - for FastAPI server
# ============================================================================
FROM base AS api

COPY --from=builder /opt/venv /opt/venv

# core and the speaker-clip endpoint call a plain `ffmpeg`; reuse the static binary already in the venv
RUN ln -s /opt/venv/lib/python3.*/site-packages/imageio_ffmpeg/binaries/ffmpeg-* /usr/local/bin/ffmpeg && ffmpeg -version | head -1

RUN mkdir -p /app/uploads /app/logs /app/models /app/jobs && \
    chown -R vttuser:vttuser /app

WORKDIR /app
USER vttuser

EXPOSE 8000

CMD ["uvicorn", "vtt_server.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================================
# Frontend build (only used by the api-ui target)
# ============================================================================
FROM node:24-alpine AS web-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ============================================================================
# API + bundled UI - single container
# ============================================================================
FROM api AS api-ui
COPY --from=web-builder /web/dist /app/static
ENV VTT_STATIC_DIR=/app/static
