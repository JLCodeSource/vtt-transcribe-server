# Changelog

All notable changes to vtt-transcribe-server. Versions follow [Semantic Versioning](https://semver.org/).

## 0.1.0

First public release.

- FastAPI server with accounts (username/password and OAuth), per-user encrypted OpenAI keys, administration page.
- Persistent first-in-first-out job queue (one worker): queue position, per-step progress, cancel, survives restarts;
  SQLite or PostgreSQL.
- Results kept until a per-item, per-user or platform retention policy deletes them (`VTT_DEFAULT_RETENTION_DAYS`).
- Speaker diarization (sherpa-onnx, CPU only), speaker review (rename, merge, split, listen to samples), translation.
- Optional key-free local engine (`pip install "vtt-transcribe-server[local]"`): faster-whisper and local translation.
- Svelte web UI served by the same process (bundled in the wheel and in the Docker image): upload several files,
  Jobs page, Profile page with defaults, retention and key management; light and dark themes.
- Email notifications over SMTP (welcome message, job finished or failed); docker compose runs a local Postfix relay.
- `vtt-server` command (`vtt-server init` writes a `.env` with fresh secrets), PyPI package and GHCR Docker images
  (lean and `local` variants).
