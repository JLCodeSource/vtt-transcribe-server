# vtt-transcribe-server

Server side of **vtt-transcribe**: FastAPI API (`vtt_server/`), users / OAuth / DB, the Svelte 5 web
app (`frontend/`), and Docker / docker-compose deployment.

This repo was extracted (with full git history) from
[JLCodeSource/vtt-transcribe](https://github.com/JLCodeSource/vtt-transcribe).

## Three-repo layout

| Repo | Visibility | Contents |
|------|-----------|----------|
| `vtt-transcribe` | public | Python CLI/library: Whisper transcription, chunking, diarization, translator. Published to PyPI + Docker Hub. |
| `vtt-transcribe-server` (this repo) | public | FastAPI API, users/OAuth/DB, Svelte web app, Docker/compose. **Depends on** `vtt-transcribe`. |
| `vtt-transcribe-web` | public | Separate static (BYOK/WASM) web app. |

The dependency is one-way: the server imports `VideoTranscriber`, `AudioTranslator`, `get_logger` and
`__version__` from `vtt_transcribe`; the core never imports the server.

The server package is named **`vtt_server`** (it used to be `vtt_transcribe.api`). A regular package named
`vtt_transcribe` cannot share its namespace with the installed core, hence the rename.

## How the core is consumed

`vtt-transcribe[sherpa]>=0.6.0` from PyPI (transcription, translation, shared speaker logic, and the key-free sherpa-onnx
diarizer: CPU, no torch, no token). Optional extra `local` adds key-free on-box Whisper and translation
(`uv sync --extra local`). pyannote/torch was dropped to keep the install and image small.
See [docs/DESIGN.md](docs/DESIGN.md) for the target API, job model and deployment plan.

## Run locally

Requirements: Python >=3.11, [uv](https://docs.astral.sh/uv/), ffmpeg (for real transcription).

```bash
uv sync                         # server + dev deps + key-free speaker diarization
cp .env.example .env
uv run python scripts/generate_secrets.py   # fill SECRET_KEY / ENCRYPTION_KEY / SESSION_SECRET in .env
uv run vtt-server               # dev server on :8000 (docs at /docs)
```

Full stack (Postgres + API, `--profile frontend` adds the web app): see
[docs/DOCKER_COMPOSE.md](docs/DOCKER_COMPOSE.md).

```bash
docker compose up -d --build
docker compose --profile frontend up -d --build
```

The `Dockerfile` has two targets (~530 MB each, or ~930 MB with `--build-arg SYNC_ARGS="--extra local"`; ffmpeg is the static binary from the venv, not the apt package):

```bash
docker build --target api    -t vtt-server .      # API only; the frontend ships as its own nginx image (frontend/Dockerfile)
docker build --target api-ui -t vtt-server-ui .   # API + built frontend served at / (single container)
```

The frontend always calls relative `/api/...`, so it works unchanged behind nginx, behind the API, or via the Vite dev proxy.

## Tests and lint

```bash
uv run pytest                   # backend (tests/test_api, docker-compose, generate_secrets)
uv run ruff check .
cd frontend && npm ci && npm run test        # frontend unit tests (Vitest)
cd frontend && npm run test:e2e              # Playwright E2E (needs the backend running)
```

No make/uv/node on the host? Use containers:

```bash
docker run --rm -v "$PWD":/w -w /w -e UV_PROJECT_ENVIRONMENT=/opt/venv python:3.12-slim bash -c \
  'pip install -q uv && uv sync --frozen && /opt/venv/bin/pytest -q -p no:cacheprovider && /opt/venv/bin/ruff check .'
docker run --rm -v "$PWD/frontend":/w -w /w node:22-slim bash -c 'npm ci && npm run test'
```

## CI

Three workflows, all path-filtered, with superseded runs cancelled and a timeout on every job:

| Workflow | Runs when | Does |
|---|---|---|
| `ci.yml` | backend files change | `ruff check` and `pytest` (one job) |
| `docker-build-test.yml` | anything in the image changes | builds the lean image, checks it runs as non-root, starts and answers `/health`, serves the UI, protects `/api` |
| `frontend-ci.yml` | frontend or backend files change | Vitest unit tests; the browser end-to-end job is manual (`workflow_dispatch`) |

The repo is public, so standard-runner minutes are free, but run the checks above locally first: CI is a safety net, not the
first line of testing. Nothing is pushed to a registry by CI.

## Job queue

Every upload joins a queue; one worker runs jobs strictly one at a time, in order. After submitting, the UI opens the **Jobs**
page, which shows each job's queue position and, while it runs, its step (detecting language, transcribing, identifying
speakers, translating) and a progress percentage. Waiting jobs can be cancelled; a running job stops at its next checkpoint.
Jobs, results and uploaded audio are stored (database + `VTT_DATA_DIR`), so the queue and finished transcripts survive a
restart; anything that was waiting or running is re-queued on startup. Run **one** API process: the queue lives inside it.

Results stay downloadable and reviewable until their retention runs out (see the table below). Each user sets a default
under **Profile**, can override it per file and later per job; the Jobs page shows the expiry date. The Profile page also holds
your OpenAI key (stored encrypted, shown only as its last four characters, used when a request has none), the email opt-out,
password change and account deletion. Email is optional (compose runs a local Postfix relay for it): with `SMTP_HOST` set, new users get a welcome email and job owners
get a "ready / failed" email linking to the job unless they opt out. There is no email verification step.

## Security

Every `/api` endpoint and the job WebSocket require a signed-in, active user, and each job belongs to the user who
created it (others get `404`; administrators can see all). A user whose password an administrator has reset must change it
before the API will serve them. Administrators manage users on the **Admin** page or under `/admin`
([docs/USER_MANAGEMENT.md](docs/USER_MANAGEMENT.md)).

Uploads are limited to 100 MB. Environment settings (all optional):

| Variable | Default | Effect |
|---|---|---|
| `VTT_ALLOW_REGISTRATION` | `true` | `false` closes self-service sign-up; create users another way and promote with `python -m vtt_server.manage` |
| `VTT_LOGIN_RATE_LIMIT` / `VTT_REGISTER_RATE_LIMIT` | `10` / `5` | attempts per client address per 5 minutes before `429` (`0` disables) |
| `VTT_MAX_ACTIVE_JOBS_PER_USER` | `10` | queued + running jobs per user before `429` (`0` disables) |
| `VTT_MAX_ACTIVE_JOBS` | `50` | queued + running jobs on the whole server before `503` (`0` disables) |
| `VTT_DATA_DIR` | system temp | where uploaded audio is stored; **use a persistent volume** so queued jobs survive a restart |
| `VTT_DEFAULT_RETENTION_DAYS` | `7` | platform default: finished jobs are kept this many days (users can choose keep / delete / hold N days in their profile, per upload, or later on the job) |
| `VTT_DELETE_GRACE_MINUTES` / `VTT_DELETE_UNDOWNLOADED_HOURS` | `15` / `24` | policy "delete": removed 15 minutes after the first download, or 24 h after finishing if never downloaded |
| `VTT_SWEEP_INTERVAL_SECONDS` | `300` | how often expired jobs and their files are deleted |
| `VTT_PUBLIC_URL` | _(empty)_ | base URL used for links in emails (`<url>/?job=<id>`) |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `SMTP_FROM` `SMTP_STARTTLS` | `mail` / `25` / … / `noreply@$VTT_MAIL_DOMAIN` / `false` (compose); empty outside compose | outgoing email (welcome message, "your job is ready"). Compose points these at the bundled `mail` relay; set `SMTP_HOST` to your own server instead, or empty to switch email off. Without `SMTP_HOST` nothing is sent and nothing fails. Port 465 uses implicit TLS |
| `VTT_MAIL_DOMAIN` `VTT_MAIL_HOSTNAME` `RELAYHOST` `RELAYHOST_USERNAME` `RELAYHOST_PASSWORD` | `localhost` / `mail.$VTT_MAIL_DOMAIN` / _(empty)_ | the bundled Postfix `mail` service (compose only, not published on the host): sender domain, its HELO name, and an optional smarthost to send through. With no `RELAYHOST` it delivers directly; without SPF/DKIM/reverse DNS for your domain such mail is often filtered as spam |
| `ENVIRONMENT` | `development` | set `production` only when served over HTTPS (it makes session cookies HTTPS-only) |

Before exposing an instance to the internet:

- **Serve it over HTTPS** (for example Caddy in front). Passwords and tokens are otherwise sent in clear text.
- Audio-clip and WebSocket URLs carry the token as `?access_token=` because browsers cannot set headers there; keep HTTPS on
  and avoid logging query strings in your proxy.
- Generate real `SECRET_KEY`, `ENCRYPTION_KEY` and `SESSION_SECRET` values (`scripts/generate_secrets.py`) and never reuse the examples.
- Jobs and uploads are held on the server's disk and in memory; there is no automatic retention yet, so delete jobs you no longer
  need and size the host for the local engines (`engine=local` wants 2 GB or more).
- Rate limits are per process and in memory, so they reset on restart and are not shared between replicas.

Report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) rather than a public issue.

## Judgment calls in the extraction

- Dropped core-only: CLI/transcriber/chunker/diarization/handlers/formatter/translator modules, CLI tests and smoke
  tests, sample media, `Dockerfile.diarization*` and the `cli` Docker target, PyPI/Docker-Hub/release/nox workflows,
  release/PyPI/changelog/registry docs, `noxfile.py`, devcontainer, beads tracker, Copilot/AGENTS instructions,
  pre-commit hooks, dependabot config.
- `docs/USER_SETUP_GUIDE.md` (despite the name, it is a PyPI-publishing guide) was **not** kept: it belongs to the core.
- Kept `docs/DOCKER_SETUP.md` (compose-based) alongside `docs/DOCKER_COMPOSE.md`.
- Server Python base image moved from 3.13 to 3.12-slim; `.python-version` is 3.12.
- `api` extra became normal dependencies; `dev`/`build` extras folded into the `dev` dependency group (no build/publish
  tooling: the package is not published).
- `vtt_server/routes/health.py` and `app.py` still report the **core** `__version__`.
- The Postgres DB/user names (`vtt_transcribe`, `vtt_user`) and the `vtt-transcribe-*` container names were left
  unchanged to avoid breaking existing deployments/volumes.
- E2E (Playwright) tests were not run locally (need browsers); backend and frontend unit tests were.
