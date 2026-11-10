# vtt-transcribe-server

Private server side of **vtt-transcribe**: FastAPI API (`vtt_server/`), users / OAuth / DB, the Svelte 5 web
app (`frontend/`), and Docker / docker-compose deployment.

This repo was extracted (with full git history) from
[JLCodeSource/vtt-transcribe](https://github.com/JLCodeSource/vtt-transcribe).

## Three-repo layout

| Repo | Visibility | Contents |
|------|-----------|----------|
| `vtt-transcribe` | public | Python CLI/library: Whisper transcription, chunking, diarization, translator. Published to PyPI + Docker Hub. |
| `vtt-transcribe-server` (this repo) | private | FastAPI API, users/OAuth/DB, Svelte web app, Docker/compose. **Depends on** `vtt-transcribe`. |
| `vtt-transcribe-web` | — | Separate static (BYOK/WASM) web app. |

The dependency is one-way: the server imports `VideoTranscriber`, `AudioTranslator`, `get_logger` and
`__version__` from `vtt_transcribe`; the core never imports the server.

The server package is named **`vtt_server`** (it used to be `vtt_transcribe.api`). A regular package named
`vtt_transcribe` cannot share its namespace with the installed core, hence the rename.

## How the core is consumed

PyPI `vtt-transcribe` 0.3.1 (Feb 2026) is too old: it lacks `vtt_transcribe.logging_config` and
`vtt_transcribe.translator`, and predates the torch 2.15 / Python >=3.11 move. So `pyproject.toml` pins the public
GitHub repo at a commit:

```
vtt-transcribe @ git+https://github.com/JLCodeSource/vtt-transcribe@1cadf06c8529e63177a5f0a230772db91916aa1b
```

Once a core release containing these modules is on PyPI, replace it with `vtt-transcribe>=X.Y` (and the same for the
`diarization` extra) and re-run `uv lock`. Bump the pin deliberately when you need newer core behaviour.

## Run locally

Requirements: Python >=3.11, [uv](https://docs.astral.sh/uv/), git, ffmpeg (for real transcription).

```bash
uv sync                         # server + dev deps (core installed from the pinned git commit)
uv sync --extra diarization     # optional: pyannote/torch via vtt-transcribe[diarization] (large)
cp .env.example .env
uv run python scripts/generate_secrets.py   # fill SECRET_KEY / ENCRYPTION_KEY / SESSION_SECRET in .env
uv run vtt-server               # dev server on :8000 (docs at /docs)
```

Full stack (Postgres + API + worker, `--profile frontend` adds the web app): see
[docs/DOCKER_COMPOSE.md](docs/DOCKER_COMPOSE.md).

```bash
docker compose up -d --build
docker compose --profile frontend up -d --build
```

The `Dockerfile` has `api` and `worker` targets; it `uv sync`s the lockfile (git is needed in the builder to fetch the
core). Build with diarization: `--build-arg SYNC_ARGS="--extra diarization"`.

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
  'apt-get update -qq && apt-get install -y -qq git && pip install -q uv && uv sync --frozen && /opt/venv/bin/pytest -q -p no:cacheprovider && /opt/venv/bin/ruff check .'
docker run --rm -v "$PWD/frontend":/w -w /w node:22-slim bash -c 'npm ci && npm run test'
```

## CI

GitHub Actions are **disabled** on this private repo to keep cost at zero. `.github/workflows/frontend-ci.yml`
(path-filtered) is kept for when Actions are enabled; it does nothing until then.

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
