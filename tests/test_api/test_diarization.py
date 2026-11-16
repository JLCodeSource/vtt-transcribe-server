"""Tests for diarization API endpoints."""

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    from vtt_server.app import app

    return TestClient(app)


class TestDiarizationEndpoint:
    """Tests for diarization-enabled transcription."""


    @patch("vtt_server.routes.transcription.VideoTranscriber")
    def test_transcribe_with_diarization_needs_no_token(self, mock_transcriber, client):
        """POST /transcribe with diarize=true needs no token."""
        mock_instance = mock_transcriber.return_value
        mock_instance.transcribe.return_value = "[00:00 - 00:05] Speaker 1: Test"

        response = client.post(
            "/api/transcribe",
            files={"file": ("test.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
            data={"api_key": "test-key", "diarize": "true"},
        )
        assert response.status_code in [200, 201, 202]
        assert "job_id" in response.json()




class TestDiarizeOnlyEndpoint:
    """Tests for diarization-only endpoint."""

    def test_diarize_endpoint_exists(self, client):
        """POST /diarize endpoint should exist."""
        response = client.post("/api/diarize")
        assert response.status_code in [400, 422]  # Bad request or validation error, but endpoint exists


    @patch("vtt_server.routes.transcription.VideoTranscriber")
    def test_diarize_needs_no_token(self, mock_transcriber, client):
        """POST /diarize needs no token."""
        mock_instance = mock_transcriber.return_value
        mock_instance.transcribe.return_value = "[00:00 - 00:05] Speaker 1: Test"

        response = client.post(
            "/api/diarize",
            files={"file": ("test.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        )
        assert response.status_code in [200, 201, 202]
        assert "job_id" in response.json()




class TestNoPyannote:
    """pyannote/HF-token options were dropped; stray legacy form fields are ignored, not errors."""

    def test_legacy_form_fields_are_ignored(self, client):
        response = client.post(
            "/api/diarize",
            files={"file": ("test.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
            data={"diarizer": "pyannote", "hf_token": "x", "device": "cpu"},
        )
        assert response.status_code == 200
