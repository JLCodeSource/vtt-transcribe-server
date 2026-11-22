"""The ``vtt-server`` command and the wheel: the package must install with its web UI and start without dev secrets."""

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from vtt_server import cli

ROOT = Path(__file__).resolve().parent.parent
SECRET_ENV = ("SECRET_KEY", "ENCRYPTION_KEY", "VTT_TRANSCRIBE_DEV_MODE", "DATABASE_URL", "VTT_DATA_DIR", "VTT_MODEL_DIR")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in (*SECRET_ENV, "API_HOST", "API_PORT", "VTT_SERVER_DATA_DIR", "VTT_STATIC_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_help_works_without_any_secrets():
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    result = subprocess.run(
        [sys.executable, "-m", "vtt_server.cli", "--help"], capture_output=True, text=True, env=env, check=False, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "vtt-server init" in result.stdout
    assert "--database-url" in result.stdout


def test_init_writes_fresh_secrets_readable_only_by_the_owner(clean_env, capsys):
    assert cli.main(["init"]) == 0
    env_file = clean_env / ".env"
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert len(values["SECRET_KEY"]) == 64
    assert len(values["SESSION_SECRET"]) == 64
    from cryptography.fernet import Fernet

    Fernet(values["ENCRYPTION_KEY"].encode())  # a valid Fernet key
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "vtt-server" in capsys.readouterr().out

    first = env_file.read_text()
    assert cli.main(["init"]) == 1  # never overwrites by accident
    assert env_file.read_text() == first
    assert cli.main(["init", "--force"]) == 0
    assert env_file.read_text() != first


@pytest.mark.usefixtures("clean_env")
def test_run_without_secrets_explains_how_to_create_them(capsys, monkeypatch):
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: called.append((a, k)))
    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "SECRET_KEY" in err
    assert "ENCRYPTION_KEY" in err
    assert "vtt-server init" in err
    assert not called


@pytest.mark.usefixtures("clean_env")
def test_dev_mode_still_needs_an_encryption_key(monkeypatch):
    monkeypatch.setenv("VTT_TRANSCRIBE_DEV_MODE", "1")
    assert cli.missing_secrets() == ["ENCRYPTION_KEY"]


def test_run_reads_env_file_and_applies_options(clean_env, monkeypatch):
    (clean_env / ".env").write_text('SECRET_KEY=abc\nENCRYPTION_KEY="def"\n# comment\nexport API_PORT=9123\n')
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
    data = clean_env / "data"
    assert cli.main(["--data-dir", str(data), "--host", "0.0.0.0"]) == 0  # noqa: S104
    (target,), kwargs = calls[0]
    assert target == "vtt_server.app:app"
    assert kwargs["host"] == "0.0.0.0"  # noqa: S104
    assert kwargs["port"] == 9123  # from the .env file
    assert os.environ["ENCRYPTION_KEY"] == "def"
    assert os.environ["DATABASE_URL"] == f"sqlite+aiosqlite:///{data.resolve() / 'vtt.db'}"
    assert os.environ["VTT_DATA_DIR"] == str(data.resolve() / "jobs")
    assert (data / "jobs").is_dir()
    for name in ("SECRET_KEY", "ENCRYPTION_KEY", "API_PORT", "DATABASE_URL", "VTT_DATA_DIR", "VTT_MODEL_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_environment_beats_the_env_file_and_database_option_beats_both(clean_env, monkeypatch):
    (clean_env / ".env").write_text("SECRET_KEY=from-file\nENCRYPTION_KEY=k\n")
    monkeypatch.setenv("SECRET_KEY", "from-env")
    monkeypatch.setattr("uvicorn.run", lambda *_args, **_kwargs: None)
    assert cli.main(["--data-dir", str(clean_env / "d"), "--database-url", "postgresql://u:p@h/db"]) == 0
    assert os.environ["SECRET_KEY"] == "from-env"
    assert os.environ["DATABASE_URL"] == "postgresql://u:p@h/db"
    for name in ("ENCRYPTION_KEY", "DATABASE_URL", "VTT_DATA_DIR", "VTT_MODEL_DIR"):
        monkeypatch.delenv(name, raising=False)


def _build_wheel(tmp_path: Path, *, with_ui: bool, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Build a wheel from a copy of the project (offline: hatchling comes from the test environment)."""
    src = tmp_path / "src"
    shutil.copytree(ROOT / "vtt_server", src / "vtt_server", ignore=shutil.ignore_patterns("__pycache__", "static"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "hatch_build.py"):
        shutil.copy(ROOT / name, src / name)
    if with_ui:
        (src / "vtt_server" / "static" / "assets").mkdir(parents=True)
        (src / "vtt_server" / "static" / "index.html").write_text("<html>ui</html>")
        (src / "vtt_server" / "static" / "assets" / "app.js").write_text("//")
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path / "dist")],
        cwd=src,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=300,
    )


def test_wheel_contains_the_ui_and_the_console_script(tmp_path):
    result = _build_wheel(tmp_path, with_ui=True)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next((tmp_path / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        assert "vtt_server/static/index.html" in names
        assert "vtt_server/static/assets/app.js" in names
        assert "vtt_server/cli.py" in names
        entry_points = next(zf.read(n).decode() for n in names if n.endswith("entry_points.txt"))
        metadata = next(zf.read(n).decode() for n in names if n.endswith("METADATA"))
    assert "vtt-server = vtt_server.cli:main" in entry_points
    assert "License" in metadata
    assert "Project-URL: Repository, https://github.com/JLCodeSource/vtt-transcribe-server" in metadata
    assert "Requires-Dist: vtt-transcribe[sherpa]" in metadata
    assert "Provides-Extra: local" in metadata


def test_building_without_the_ui_fails_with_instructions(tmp_path):
    result = _build_wheel(tmp_path, with_ui=False)
    assert result.returncode != 0
    assert "scripts/build_ui.sh" in result.stdout + result.stderr


def test_api_only_build_is_possible_on_purpose(tmp_path):
    result = _build_wheel(tmp_path, with_ui=False, env_extra={"VTT_ALLOW_NO_UI": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next((tmp_path / "dist").glob("*.whl"))) as zf:
        assert "vtt_server/static/index.html" not in zf.namelist()


def _pyproject_version() -> str:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_health_reports_the_server_version_and_the_core_version():
    from fastapi.testclient import TestClient

    from vtt_server._version import __version__
    from vtt_server.app import app

    data = TestClient(app).get("/health").json()
    assert data["version"] == __version__
    assert data["core_version"]
    assert __version__ == _pyproject_version()  # the packaged version is the one in pyproject.toml


def test_release_notes_come_from_the_changelog():
    spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    changelog = "# Changelog\n\n## 0.2.0\n\n- new\n\n## 0.1.0\n\nFirst.\n- a\n"
    assert module.section(changelog, "0.2.0") == "- new"
    assert module.section(changelog, "0.1.0") == "First.\n- a"
    assert module.section(changelog, "0.3.0") is None
    notes = module.section((ROOT / "docs" / "CHANGELOG.md").read_text(), _pyproject_version())
    assert notes  # the version being released has notes


def test_frontend_nginx_leaves_the_upload_limit_to_the_api():
    conf = (ROOT / "frontend" / "nginx.conf").read_text()
    assert "client_max_body_size 0;" in conf  # nginx's default of 1 MB rejects ordinary audio; the API enforces the real limit
