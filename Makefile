.PHONY: help install test lint format test-frontend-unit test-frontend-e2e

help:
	@echo "install             - uv sync (server + dev deps)"
	@echo "test                - backend tests (tests/)"
	@echo "lint / format       - ruff check / ruff check --fix"
	@echo "test-frontend-unit  - frontend unit tests (Vitest)"
	@echo "test-frontend-e2e   - frontend E2E tests (Playwright)"

install:
	@uv sync

test:
	@uv run pytest -v --cov=vtt_server --cov-report=term-missing

lint:
	@uv run ruff check .

format:
	@uv run ruff check --fix .

test-frontend-unit:
	@cd frontend && { [ -d node_modules ] || npm install; } && npm run test

test-frontend-e2e:
	@cd frontend && { [ -d node_modules ] || npm install; } && npx playwright install chromium && npm run test:e2e -- --project=chromium
