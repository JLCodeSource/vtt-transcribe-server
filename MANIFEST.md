# MANIFEST: core cleanup after server extraction

Source: original repo `JLCodeSource/vtt-transcribe` at commit `1cadf06c8529e63177a5f0a230772db91916aa1b`
(`main` after PR #435). Everything below refers to paths in THAT repo. The server repo
(`vtt-transcribe-server`) pins the core at this exact commit, so core history must not be rewritten.

## (a) DELETE from core (whole path now lives in the server repo)

| Core path | Now in server repo |
|-----------|--------------------|
| `vtt_transcribe/api/` (whole dir, incl. `README.md`, `routes/`) | `vtt_server/` |
| `frontend/` (whole dir: Svelte app, `Dockerfile`, `nginx.conf`, `Makefile`, e2e) | `frontend/` |
| `tests/test_api/` (whole dir) | `tests/test_api/` |
| `tests/test_docker_compose.py` | `tests/test_docker_compose.py` |
| `tests/test_generate_secrets.py` | `tests/test_generate_secrets.py` |
| `scripts/generate_secrets.py` | same |
| `scripts/init_db.py` | same |
| `docker/` (`init-db.sql`; dir becomes empty) | `docker/` |
| `docker-compose.yml` | same |
| `.env.example` (DB/OAuth/API/compose template, server-only) | same |
| `.github/workflows/frontend-ci.yml` | same (not running; Actions disabled there) |
| `docs/DOCKER_COMPOSE.md` | same |
| `docs/DOCKER_SETUP.md` (compose guide) | same |
| `docs/USER_MANAGEMENT.md` | same |

After deleting `scripts/generate_secrets.py` and `scripts/init_db.py`, `scripts/` is empty: remove it too, and remove
the `"scripts/*.py" = ["INP001"]` ruff ignore (see b).

## (b) EDIT in core (do not delete the file)

- `pyproject.toml`
  - delete the whole `api = [...]` block under `[project.optional-dependencies]` (fastapi, uvicorn, python-multipart,
    sqlalchemy, asyncpg, aiosqlite, pyjwt, passlib, bcrypt, cryptography, email-validator, authlib, httpx, itsdangerous);
  - in the `dev` extra delete `"httpx>=0.27.0",  # For TestClient support` (only used by API tests);
  - in `[dependency-groups] dev` delete `fastapi`, `uvicorn[standard]`, `python-multipart`, `httpx`;
  - `pytest-asyncio` + `asyncio_mode`/`asyncio_default_fixture_loop_scope` in `[tool.pytest.ini_options]`: only API tests
    needed them. Remove only after confirming no remaining core test is `async`
    (`grep -rn "async def test\|pytest.mark.asyncio" tests`); otherwise keep;
  - keep: `diarization` extra, `build` extra, pillow `override-dependencies`, `[project.scripts] vtt`.
  - then `uv lock` (lock shrinks a lot).
- `Dockerfile`: delete stage `FROM builder AS builder-api` (+ its `RUN uv pip install ".[api]"`), the `api` target
  (`FROM base AS api` ... `CMD uvicorn vtt_transcribe.api.app:app`) and the `worker` target (`FROM base AS worker`);
  the `cli` target (`FROM base AS cli`, `ENTRYPOINT ["vtt"]`) stays and must remain the DEFAULT (last) target.
  Update the header comment. `curl` in the `base` stage was only for API healthchecks (optional to drop).
- `.github/workflows/`: `docker-build-test.yml`, `docker-build-test-diarization*.yml`, `docker-publish.yml`, `ci.yml`,
  `release.yml`, `nox-main.yml`: grep showed no `api`/`frontend`/`compose` references, so no edit expected; re-check that
  none passes `--target api|worker`, installs `.[api]`, or has `paths:` filters naming `frontend/**`, `docker-compose.yml`,
  `vtt_transcribe/api/**`.
- `noxfile.py`: lines ~25, 52, 70 install `".[dev,api]"` / `".[dev,api,diarization]"` -> drop `api` from the extras
  (nox sessions with API tests, if any, go away with `tests/test_api`).
- `.github/dependabot.yml`: delete the `npm` ecosystem entry for `directory: "/frontend"`.
- `Makefile`: delete targets `install-api`, `test-frontend-unit`, `test-frontend-e2e`, `test-frontend`; remove `--extra api`
  from `install-diarization-gpu`/`install-diarization-cpu` (line ~91) and `install-all` (line ~127); `test-all` should
  become just `test` (or be removed); update `.PHONY`, `help` echoes (the `install-api`, `For API server`, `test-frontend*`,
  `test-all` lines, and "base + api + ..." wording).
- `ruff.toml`: delete `"vtt_transcribe/api/**/*.py" = ["B008"]`; delete `"scripts/*.py" = ["INP001"]` if `scripts/` is removed.
- `.mypy.ini`: no API-specific entries in core (nothing to do); `Makefile` `mypy` target already targets `vtt_transcribe` only.
- `tests/conftest.py`: delete the two `os.environ.setdefault("SECRET_KEY"...)` / `("ENCRYPTION_KEY"...)` lines and the
  comment "Set required environment variables for API tests"; keep `pytest_plugins = ("pytest_asyncio",)` only if
  pytest-asyncio stays; the rest (`load_env`, `mock_stdin_tty`, `gpu_available`) stays.
- `tests/__init__.py`, `tests/utils/`: stay (core).
- `.dockerignore`: no required change (it already excludes `docs/`, `tests/`); optional: drop `docker-compose*.yml`.
- `.gitignore`: optional; leave.
- `README.md`: remove sections "### Using Docker Compose (Full Stack)" (~line 149) and "### Web Frontend (Development)"
  (~line 167) through the line before the next `##` heading ("Upgrading from 0.2.0", ~197); remove mentions of
  `pip install vtt-transcribe[api]`, `make install-api`, `make test-frontend*`; in "Makefile targets" (~349) drop
  api/frontend targets; in the Files/structure list drop `vtt_transcribe/api/*` references if any. Do not link the private
  repo's details; optionally one sentence "A hosted/server edition is developed separately".
- `.github/copilot-instructions.md`, `AGENTS.md`, `.devcontainer/*` (incl. `.devcontainer/docker-compose.yml`, which is the
  devcontainer's own compose file, NOT the root `docker-compose.yml`): grep for `api`, `frontend`, `uvicorn`, `npm`;
  edit any hits (copilot-instructions had none for API at last check apart from "API key" = OpenAI key, which stays).
- `docs/TESTING.md`, `docs/CONTRIBUTING.md`, `docs/INFRASTRUCTURE_CICD.md`, `docs/ROADMAP.md`, `docs/CHANGELOG.md`:
  remove API/frontend/compose testing/CI sections and project-tree entries (`api/`, `frontend/`) where present.
  CHANGELOG history stays; add an "Unreleased" note: server/web moved to a separate private repository.
- `TODO-DOCKER-E2E.md`: check; if it is about the compose stack/API e2e, delete (it was NOT copied to the server repo).

## (c) MUST STAY in core (do not delete)

- Package `vtt_transcribe/` except `api/`: `__init__.py`, `__main__.py`, `audio_chunker.py`, `audio_manager.py`, `cli.py`,
  `dependencies.py`, `diarization.py`, `handlers.py`, `logging_config.py`, `main.py`, `transcriber.py`,
  `transcript_formatter.py`, `translator.py`.
  **The server imports exactly: `vtt_transcribe.__version__`, `vtt_transcribe.logging_config.get_logger`,
  `vtt_transcribe.transcriber.VideoTranscriber`, `vtt_transcribe.translator.AudioTranslator`.** Keep these names
  and import paths stable (and `VideoTranscriber`/`AudioTranslator` constructor/method signatures used by
  `vtt_server/routes/transcription.py`).
- Core tests: everything in `tests/` except the three items in (a) (`test_audio_*`, `test_cli`, `test_dependencies`,
  `test_diarization`, `test_ffmpeg_check`, `test_handlers`, `test_language_detection`, `test_logging_config`, `test_main`,
  `test_package_structure`, `test_stdin_mode`, `test_transcriber`, `test_transcript_formatter`, `test_translator`,
  `test_version`), `tests/smoke/`, `tests/utils/`, `tests/hello_*.mp3|mp4`.
- Docker: `Dockerfile` (builder/base/cli), `Dockerfile.diarization`, `Dockerfile.diarization-gpu`, workflows
  `ci.yml`, `docker-build-test*.yml`, `docker-publish.yml`, `publish.yml`, `publish-testpypi.yml`, `release.yml`,
  `nox-main.yml`, `close-superseded-prs.yml`.
- Docs: `docs/USER_SETUP_GUIDE.md` (it is a PyPI publishing guide, so core, not server), `DOCKER_HUB.md`,
  `DOCKER_REGISTRY.md`, `RELEASE_CHECKLIST.md`, `LOGGING.md`, `PYANNOTE_MODEL_ACCEPTANCE.md`, `CHANGELOG.md`,
  `LOGGING_IMPLEMENTATION.md`, `noxfile.py`, `.beads/`, `.devcontainer/`, `.pre-commit-config.yaml`, `AGENTS.md`.
- `pyproject.toml` keeps name `vtt-transcribe`, hatch `packages = ["vtt_transcribe"]`, `diarization` extra with pinned
  torch 2.15 / torchaudio / torchcodec / pyannote.
- DO NOT change published artifacts: PyPI releases and Docker Hub images of the core are unaffected by this cleanup.

## Verify after cleanup

`grep -rn "vtt_transcribe.api\|fastapi\|frontend\|docker-compose\|\.\[api\]\|extra api" . --exclude-dir=.git --exclude=uv.lock`
should only hit CHANGELOG/history text; then `uv lock && uv sync && uv run pytest && uv run ruff check .`.
