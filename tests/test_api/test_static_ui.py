"""Optional single-container mode: VTT_STATIC_DIR serves the built frontend, API routes still win."""

import importlib
import sys

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("fake_auth")


def test_static_dir_serves_ui_and_keeps_api(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>ui</html>")
    monkeypatch.setenv("VTT_STATIC_DIR", str(tmp_path))
    import vtt_server.app  # noqa: F401  (ensure loaded)

    app_module = importlib.reload(sys.modules["vtt_server.app"])
    try:
        client = TestClient(app_module.app)
        assert "ui" in client.get("/").text
        assert client.get("/api/engines").status_code == 401  # the API is still served, and needs a login
        assert client.get("/health").status_code == 200
    finally:
        monkeypatch.setenv("VTT_STATIC_DIR", "")
        importlib.reload(sys.modules["vtt_server.app"])


def test_without_static_dir_root_is_api_info(monkeypatch):
    monkeypatch.setenv("VTT_STATIC_DIR", "")  # empty switches the UI off, even when a packaged copy exists
    app_module = importlib.reload(sys.modules["vtt_server.app"])
    try:
        assert "Welcome" in TestClient(app_module.app).get("/").json()["message"]
    finally:
        monkeypatch.setenv("VTT_STATIC_DIR", "")
        importlib.reload(sys.modules["vtt_server.app"])


def test_packaged_ui_is_the_default_when_no_directory_is_configured(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>packaged ui</html>")
    import vtt_server.app as app_module

    monkeypatch.delenv("VTT_STATIC_DIR", raising=False)
    monkeypatch.setattr(app_module, "_PACKAGED_UI", tmp_path)
    assert app_module._ui_dir() == tmp_path
    monkeypatch.setenv("VTT_STATIC_DIR", "")
    assert app_module._ui_dir() is None
    monkeypatch.setenv("VTT_STATIC_DIR", str(tmp_path / "missing"))
    assert app_module._ui_dir() is None
