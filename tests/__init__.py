"""Tests for video-to-text."""

# 82 bytes: HS512 wants a key of at least 64 bytes (RFC 7518 section 3.2), or PyJWT warns
TEST_SECRET_KEY = "test-secret-key-for-jwt-signing-only-padded-to-at-least-sixty-four-bytes-for-hs512"
