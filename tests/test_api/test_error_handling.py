"""Tests for API error handling and validation."""

import io

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("fake_auth")


def test_file_size_limit_enforcement(monkeypatch) -> None:
    """Test that files exceeding size limits are rejected."""
    from vtt_server.app import app

    client = TestClient(app)

    monkeypatch.setattr("vtt_server.routes.transcription.MAX_FILE_SIZE", 1024)
    large_content = b"x" * (3 * 1024 * 1024)  # several streaming chunks over the limit
    files = {"file": ("large.mp3", io.BytesIO(large_content), "audio/mpeg")}
    data = {"api_key": "test_key"}

    response = client.post("/api/transcribe", files=files, data=data)

    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


def test_unsupported_file_type() -> None:
    """Test rejection of unsupported file types."""
    from vtt_server.app import app

    client = TestClient(app)

    fake_content = b"fake content"
    files = {"file": ("test.txt", io.BytesIO(fake_content), "text/plain")}
    data = {"api_key": "test_key"}

    response = client.post("/api/transcribe", files=files, data=data)

    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_missing_api_key() -> None:
    """Test that missing API key returns 422."""
    from vtt_server.app import app

    client = TestClient(app)

    fake_content = b"fake content"
    files = {"file": ("test.mp3", io.BytesIO(fake_content), "audio/mpeg")}

    response = client.post("/api/transcribe", files=files)

    assert response.status_code == 422
    assert "api_key" in response.json()["detail"]


def test_missing_file() -> None:
    """Test that missing file returns 422."""
    from vtt_server.app import app

    client = TestClient(app)

    data = {"api_key": "test_key"}
    response = client.post("/api/transcribe", data=data)

    assert response.status_code == 422


def test_invalid_job_id_format() -> None:
    """Test that invalid job ID format is handled."""
    from vtt_server.app import app

    client = TestClient(app)

    response = client.get("/api/jobs/invalid-not-a-uuid")

    # Should return 404 for non-existent job (UUID validation optional)
    assert response.status_code == 404


def test_empty_filename() -> None:
    """Test that files without filenames are rejected."""
    from vtt_server.app import app

    client = TestClient(app)

    fake_content = b"fake content"
    files = {"file": ("", io.BytesIO(fake_content), "audio/mpeg")}
    data = {"api_key": "test_key"}

    response = client.post("/api/transcribe", files=files, data=data)

    assert response.status_code == 422
    # FastAPI returns validation errors as a list
    detail = response.json()["detail"]
    assert isinstance(detail, (str, list))


def test_error_response_structure() -> None:
    """Test that error responses have consistent structure."""
    from vtt_server.app import app

    client = TestClient(app)

    response = client.get("/api/jobs/nonexistent-job-id")

    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert isinstance(data["detail"], str)
