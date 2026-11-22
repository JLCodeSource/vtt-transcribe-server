# Changelog

All notable changes to vtt-transcribe-server. Versions follow [Semantic Versioning](https://semver.org/).

## 0.2.0

Every version of a job's output is kept.

- The original-language text is stored once, never changes, and is always exportable.
- Translating adds a language version; choosing another language no longer replaces the current output. Each language can
  be selected, exported (including a bilingual file with original and translation together) and deleted on its own.
- Each speaker-identification run is its own version: re-running with a different number of speakers adds one; merge, split
  and rename edit only the active version; switch the active version, rename it, or delete it (never the last).
- API: `GET /api/jobs/{id}/versions`; `language=` and `diarization=` on `segments`, `speakers`, speaker clips and `download`;
  `bilingual=true`; `PUT /diarizations/active`, `PUT|DELETE /diarizations/{id}`, `DELETE /translations/{language}`;
  `translate` and `rediarize` add versions. Existing fields (`translated=true`, `translated_to`) still work.
- Web UI: language switcher, speaker-version selector with re-run, side-by-side view, export of the chosen combination.
- Migration: jobs saved by 0.1.0 are converted at startup and written back (their translation becomes a language entry, their
  speaker state diarization `d1`; the result reads in the original language again). Retention and deletion cover all versions.
- Uploads: streamed to disk, `MAX_UPLOAD_SIZE` honoured (default 4 GiB); translation languages come from the server.

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
