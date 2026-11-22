"""Failure messages shown to users never leak paths, commands or library internals."""

import pytest

from vtt_server.errors import GENERIC, friendly_error

LEAKY = "ffmpeg failed to decode /app/jobs/abc/input.mp3: [in#0 @ 0x5af16b401e00] Failed to find two consecutive MPEG frames."


@pytest.mark.parametrize(
    ("raw", "expected_fragment"),
    [
        (LEAKY, "could not be read"),
        ("Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-abc'}}", "rejected the API key"),
        ("Error code: 429 - rate limit reached", "limiting requests"),
        ("HTTPSConnectionPool(host='api.openai.com'): Read timed out.", "did not respond"),
        ("MemoryError", "ran out of memory"),
        ("model download failed: https://github.com/k2-fsa/x.onnx", "could not be downloaded"),
    ],
)
def test_known_failures_get_a_clear_sentence(raw: str, expected_fragment: str) -> None:
    message = friendly_error(RuntimeError(raw))
    assert expected_fragment in message
    assert "/app" not in message
    assert "sk-" not in message
    assert "0x" not in message


def test_unknown_failures_use_the_generic_message() -> None:
    assert friendly_error(KeyError("/srv/secret/path")) == GENERIC
    assert "/srv" not in GENERIC


def test_accepts_plain_strings() -> None:
    assert friendly_error("something odd") == GENERIC
