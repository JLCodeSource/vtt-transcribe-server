# Target design: vtt-transcribe-server

One implementation (the `vtt-transcribe` core, from PyPI) behind one HTTP API and one frontend, deployed as a single
Docker image. The browser-only app [vtt-transcribe-web](https://github.com/JLCodeSource/vtt-transcribe-web) is the
functional reference for the UI; the server exists for what a browser cannot do well (long files, server-side models,
accounts, history).

Status legend: **[done]** in this repo now, **[todo]** not built.

## 1. Core dependency

`vtt-transcribe[sherpa]>=0.6.0` from PyPI (sherpa-onnx, CPU, ~46 MB models, no token, no torch). One optional extra,
`local` (`vtt-transcribe[local]`: faster-whisper + sentencepiece, still no torch). pyannote/torch (core's `diarization`
extra) was dropped deliberately to keep one diarizer, no HF token and a small image. The server never re-implements
transcription, translation, diarization or speaker logic; it orchestrates core classes (`VideoTranscriber`,
`LocalTranscriber`, `AudioTranslator`, `LocalTextTranslator`, `SherpaDiarizer`, and `vtt_transcribe.speakers` for
rename/merge/split/export/translate_segments) and owns only jobs, storage, the HTTP API and the sample-clip endpoint.

## 2. Job model

A job is one uploaded file moving `pending (queued) -> processing -> completed | failed | cancelled`.
`routes/transcription.py: jobs` is the in-memory working set; every change is written through to the `transcription_jobs`
table (`vtt_server/jobstore.py`) and reloaded at startup (`jobqueue.recover`), so queued work and finished results survive a
restart **[done]**. Per job:

| Field | Meaning |
|-------|---------|
| `engine`, `diarize`, `diarizer`, `num_speakers`, `translate_to` | options chosen at creation |
| `raw_transcript` | engine output before speaker labels (lets us re-diarize without re-transcribing) |
| `segments` | `[{start, end, text, speaker: "SPEAKER_00"\|null, translation?}]`, the source of truth |
| `speaker_names` | `{SPEAKER_00: "Alice"}`; blank/absent = default `Speaker 1` |
| `result` | derived: `[HH:MM:SS - HH:MM:SS] Name: text` (translated text once translated); used by the websocket |
| `audio_path` | stored upload under `VTT_DATA_DIR/<job_id>/`, kept for sample clips + re-diarization, removed by `DELETE` or retention |
| `status`, `step`, `progress`, `message`, `queue_position` | queue state: `step` is queued/detecting/transcribing/diarizing/translating/done; `progress` is 0..1 overall |
| `retention_mode`, `retention_days`, `expires_at`, `downloaded_at` | retention policy and the computed deletion time |
| `secret` | request-supplied API key, encrypted with `ENCRYPTION_KEY`, kept only until the job ends (never returned) |

Speaker ids are stable (`SPEAKER_NN` from the diarizer). Names are a presentation layer: merge/split edit `segments`;
rename edits `speaker_names`. Every export renders through `speakers.display_name`, so a rename shows up everywhere as
`Name: text` without re-running anything. Translation never sends speaker names to the model (only `[ts] text` lines, zipped
back by index), so rename/merge before or after translating cost nothing.

### Queue

`vtt_server/jobqueue.py`: one asyncio task (started on first submit and at startup) takes the pending job with the lowest
`queue_seq` and runs it to the end before looking at the next one, so jobs never overlap and order is strictly FIFO, even
when the server is idle. Every submission is persisted first (row + audio on disk). On startup `recover` loads all rows;
`pending`/`processing` ones become `pending` again, in creation order. Cancel: a waiting job is cancelled at once; a running
job gets `cancel_requested` and stops at the next checkpoint (step boundary, or the next `progress_callback` report, which
raises `JobCancelled`). The in-flight engine call itself is not killed. Because the queue is in-process, run one API process.

Progress: `plan_for(job)` lists the steps a job needs and weights them (detecting 5, transcribing 60, diarizing 20, translating
15) into one overall fraction. Engines that report fractions move the bar inside a step: `with_progress` passes
`progress_callback(fraction, message)` to core's `transcribe` when its signature declares that parameter (core >= the release
that adds it); older cores just advance step by step. Live events still flow over `WS /ws/jobs/{id}`; the Jobs page polls
`GET /api/jobs` (2 s while anything is active).

### Retention

`vtt_server/retention.py`. A policy is `keep` (until the user deletes), `delete`, or `hold N days`.
`hold`: expires N days after the job finished. `delete`: expires `VTT_DELETE_GRACE_MINUTES` (15) after the first download
(`GET .../download` stamps `downloaded_at`); if never downloaded, `VTT_DELETE_UNDOWNLOADED_HOURS` (24) after finishing.
Failed and cancelled jobs follow the same policy from their end time. Resolution order: the item's choice, the user's profile
default, the platform default `hold VTT_DEFAULT_RETENTION_DAYS` (7). `expires_at` is recomputed whenever the policy or
`downloaded_at` changes. The sweeper (`app._sweep_loop`, every `VTT_SWEEP_INTERVAL_SECONDS`) calls
`retention.sweep(jobs, delete, now)`, which removes expired finished jobs (memory, audio directory, DB row); `now` is a
parameter, so tests inject the clock. Running or queued jobs are never swept.

### Email and profile

`vtt_server/mailer.py` (SMTP via env, plain text + HTML, sent from a worker thread, failures logged) sends the welcome mail on
registration and a ready/failed mail when a job ends (not on cancel), to owners who have not opted out
(`users.notify_job_ready`). Without `SMTP_HOST` it logs "skipped"; docker compose ships a Postfix `mail` service and points `SMTP_HOST` at it. Tests swap `mailer.transport_factory` for a fake. Email
verification is deliberately not implemented. `routes/profile.py` (`/api/profile`): retention default, opt-out, default options,
OpenAI key (the existing encrypted `api_keys` table; only a `****abcd` hint is returned), and self-service account deletion
(password required; removes jobs, audio and keys). When an openai job or translation has no key it falls back to the stored one.

## 3. API

All under `/api`, JSON unless noted, signed-in users only; jobs are scoped to their owner (administrators see all).

| Endpoint | Purpose |
|----------|---------|
| `GET /engines` | engines (`openai`, `local`) with `available`, `max_speakers`; drives the UI picker **[done]** |
| `POST /transcribe` (multipart) | `file`, `engine`=openai\|local, `api_key` (openai; optional if a key is saved in the profile), `diarize`, `num_speakers`, `translate_to`, `retention_mode`, `retention_days` **[done]**; the job is queued (`status: pending`) |
| `POST /diarize` (multipart) | diarization only: `file`, `num_speakers`; result is text-less segments **[done]** |
| `GET /jobs[?all=true]` | the caller's jobs newest first with `step`, `progress`, `queue_position`, `expires_at` (`all` = administrators) **[done]** |
| `POST /jobs/{id}/cancel` / `PUT /jobs/{id}/retention` `{mode, days}` | cancel a queued/running job; change keep/delete/hold **[done]** |
| `GET /jobs/{id}` | status + options (no bulky fields) **[done]**; `WS /ws/jobs/{id}` pushes progress/result **[done]** |
| `GET /jobs/{id}/segments` | structured transcript: segments (`text`, `translation`, `speaker_id`, display `speaker`) + speaker summaries **[done]** |
| `POST /jobs/{id}/translate` `{target_language, api_key}` | adds `translation` per segment via core's `AudioTranslator`; 502 if the model returns a different line count **[done]** |
| `GET /jobs/{id}/speakers` | per speaker: `id`, `name`, `display_name`, `seconds`, `segment_count`, `sample_text`, up to 3 `samples` (longest turns, capped 8 s) **[done]** |
| `GET /jobs/{id}/speakers/{sid}/clip[?start&end]` | WAV excerpt cut from the stored audio with ffmpeg (<= 15 s); plain `<audio src>` target **[done]** |
| `PUT /jobs/{id}/speakers/{sid}` `{name}` / `PUT .../speakers/names` `{names}` | rename one / many; blank clears **[done]** |
| `POST /jobs/{id}/speakers/merge` `{source_ids, target_id}` | fold voices that are one person; target keeps its name **[done]** |
| `POST /jobs/{id}/speakers/{sid}/split` `{segment_indices}` | move the picked lines to a new speaker (`new_speaker_id`) **[done]** |
| `POST /jobs/{id}/rediarize` `{num_speakers, diarizer}` | re-run diarization on stored audio (the automatic "wrong count" fix; resets names/merges) **[done]** |
| `GET /jobs/{id}/download?format=txt\|vtt\|srt\|json&translated=` | exports with `Name: text` (VTT uses `<v Name>`) **[done]** |
| `DELETE /jobs/{id}` | forget job + delete audio **[done]** |

Existing `/detect-language` and `/translate` (raw text) stay as they were. `user/jobs/*` history routes are unchanged.

Decisions worth knowing:

- **Split is line-based, not embedding-based.** The web app re-clusters voice embeddings in the browser; core does not
  expose embeddings, so the server lets the reviewer pick the lines that belong to someone else, and offers `rediarize`
  with a corrected `num_speakers` for the automatic route. If core later exposes embeddings (or a `split` helper) the
  endpoint can grow an `auto: true` mode without changing its shape.
- **Defaults follow the web app**: unnamed speakers are `Speaker N`; clips are the 3 longest turns capped at 8 s.
- **No API key or token for diarization**: sherpa needs nothing, so the old "HF token required" rule is gone (legacy
  `hf_token`/`device`/`diarizer` form fields are ignored). `api_key` is required only for `engine=openai`.
- **Models** download on first use into `VTT_MODEL_DIR` (`/app/models`; mount a volume or bake them into the image
  at deploy time).

## 4. Engines

The pipeline asks `vtt_server/engines.py` for the engine; everything else is engine-agnostic.

- `openai`: core `VideoTranscriber` with the caller's key **[done]**.
- `local` (key-free): core's `LocalTranscriber` (faster-whisper, model `VTT_LOCAL_MODEL`, default `small`, cached under
  `VTT_MODEL_DIR/whisper`) and `LocalTextTranslator` (M2M100 418M) **[done]**. It needs the `local` extra
  (`uv sync --extra local`, or `docker build --build-arg SYNC_ARGS="--extra local"`, ~930 MB vs ~530 MB lean). Availability is
  detected at runtime (`engines.local_engine_available()`): in the lean image `POST /transcribe` answers 501 and
  `GET /engines` reports `available: false`, so the UI disables the option (" — not installed"). A job remembers its engine;
  `POST /jobs/{id}/translate` uses it (local = no key) unless the body overrides `engine`. Translation with `engine=openai`
  still needs the caller's key.

## 5. Frontend alignment with vtt-transcribe-web

Same flow and vocabulary, different backend: engine picker -> options (diarize, number of speakers, translate) ->
progress -> transcript -> **speaker review** -> exports. Components (`frontend/src`):

| Web app | Server frontend | Status |
|---------|-----------------|--------|
| `EnginePicker` (engine, diarize, speaker count) | `FileUpload` options, engines from `GET /engines` | **[done]** |
| `SpeakerReview` (name, play samples, merge, split) | `SpeakerReview.svelte` (same affordances; samples are server clips; split = pick lines) | **[done]** |
| `TranscriptViewer` (translated toggle, `Name: text` exports) | `TranscriptViewer.svelte` + Translate box, JSON export | **[done]** |
| `lib/speakers.ts` logic in-browser | core `vtt_transcribe.speakers` (same operations, same defaults), used by the server | **[done]** |
| Review step before translation | translation runs after (or at) upload; review can happen before or after because names are not sent to the model | differs deliberately |

Parity is kept by (a) mirroring the operation set (name/merge/split/rediarize-by-count, `Name: text`, `Speaker N`),
(b) a short checklist in PRs touching either UI, and (c) the shared vocabulary above. Extracting a shared Svelte package
is **[todo]**, only worth it once a third surface exists.

## 6. Deployment

One Dockerfile, two interchangeable shapes **[done]**: `api` (API only; the frontend is a separate nginx image, `compose --profile frontend`)
and `api-ui` (same API with the built frontend bundled and served at `/` via `VTT_STATIC_DIR`; single container, recommended for
prod). The frontend only uses relative `/api` and `/ws` URLs, so both work unchanged. Both images are ~530 MB (was ~970 MB: ffmpeg
now comes from the static binary already shipped by `imageio-ffmpeg` instead of the ~450 MB apt package). There is no `worker`
image: it was a placeholder; add one back when background jobs move out of process. Defaults: SQLite for a single small host,
Postgres via compose when needed; `SECRET_KEY`, `ENCRYPTION_KEY`, `SESSION_SECRET` from the host's secret store.

- **Host**: Fly.io (one `shared-cpu` machine, volume mounted at `/app/models` and `/app/jobs`) or a small VPS running
  `docker compose` behind Caddy/Traefik for TLS. Sherpa is CPU-only and small, so no GPU is needed; the `local` Whisper engine will set the
  real memory floor, so size after core ships it.
- **Registry**: `ghcr.io/jlcodesource/vtt-transcribe-server`, tags `sha-<commit>` and `latest` (from `main`).
- **CI/CD (designed, not enabled)**: on push to `main`: run the container test commands from the README (backend + frontend),
  `docker buildx build --push` to GHCR, then deploy (`flyctl deploy --image ...@sha` or `ssh host 'docker compose pull && up -d'`).
  The workflows in `.github/workflows` need adapting (`docker-build-test.yml`, `ci.yml`) before Actions are switched on. The repo
  is public, so standard-runner minutes are free; keep runs path-filtered and cancel superseded ones.
- **Health/rollback**: `/health` for the platform check; rollback = redeploy the previous `sha-` tag. Persistent state lives only in the DB and
  the two volumes, so images are disposable.
- **Config/secrets still to wire** **[todo]**: Fly app config or a prod compose file, TLS, a first-boot model prefetch
  (`python -c "from vtt_transcribe.sherpa_diarization import ensure_models; ensure_models()"`), a retention job.

## 7. What remains

1. Job persistence + per-user auth on the job routes; TTL cleanup of stored audio.
2. Pick the local Whisper model per request (today one `VTT_LOCAL_MODEL` per deployment); size the host for it.
3. Prod deploy config + enabling CI/CD (section 6) once the full solution is ready.
4. Optional: embedding-based auto-split if core exposes embeddings.
5. Frontend e2e (Playwright) coverage for the review flow against a live backend.
6. If pyannote quality is ever needed again: re-add it as a separate image variant (core's `diarization` extra), not a default.
