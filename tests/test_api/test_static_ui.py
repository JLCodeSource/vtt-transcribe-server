"""Optional single-container mode: VTT_STATIC_DIR serves the built frontend, API routes still win."""

import importlib
import sys

from fastapi.testclient import TestClient


def test_static_dir_serves_ui_and_keeps_api(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>ui</html>")
    monkeypatch.setenv("VTT_STATIC_DIR", str(tmp_path))
    import vtt_server.app  # noqa: F401  (ensure loaded)

    app_module = importlib.reload(sys.modules["vtt_server.app"])
    try:
        client = TestClient(app_module.app)
        assert "ui" in client.get("/").text
        assert client.get("/api/engines").status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        monkeypatch.delenv("VTT_STATIC_DIR")
        importlib.reload(sys.modules["vtt_server.app"])


def test_without_static_dir_root_is_api_info():
    from vtt_server.app import app

    assert "Welcome" in TestClient(app).get("/").json()["message"]
