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

The repo is public, so GitHub Actions on standard runners are free. `.github/workflows/frontend-ci.yml` is path-filtered and
cancels superseded runs. Run the checks above locally first; CI is a safety net, not the first line of testing.

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
| `VTT_MAX_ACTIVE_JOBS_PER_USER` | `2` | running jobs per user before `429` (`0` disables) |
| `VTT_MAX_ACTIVE_JOBS` | `4` | running jobs on the whole server before `503` (`0` disables) |
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
