"""User-facing wording for job failures.

Raw exceptions can contain server paths, command lines and library internals. Those stay in the server log (the job
runner logs the full traceback); the job, the WebSocket and the notification email get a short, safe sentence.
"""

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"ffmpeg|invalid data found|could not (decode|read)|moov atom|no audio|two consecutive", re.I),
        "The file could not be read as audio or video. Check it is not corrupted, or convert it to MP3 or WAV and try again.",
    ),
    (
        re.compile(r"model (download|files?)|could not download|failed to download|huggingface", re.I),
        "A model this job needs could not be downloaded. Try again in a few minutes.",
    ),
    (
        re.compile(r"incorrect api key|invalid api key|authentication|\b401\b|invalid_api_key", re.I),
        "OpenAI rejected the API key. Check the key in your profile and try again.",
    ),
    (
        re.compile(r"rate limit|quota|\b429\b|insufficient_quota", re.I),
        "OpenAI is limiting requests or the account has no quota left. Try again later or check your OpenAI plan.",
    ),
    (
        re.compile(r"timed? ?out|connection (error|reset|refused)|temporarily unavailable|\b5\d\d\b", re.I),
        "A service the job depends on did not respond. Try again in a few minutes.",
    ),
    (
        re.compile(r"out of memory|cannot allocate memory|\bkilled\b|memoryerror", re.I),
        "The server ran out of memory for this file. Try a shorter file or a smaller model.",
    ),
]

GENERIC = "The job failed on the server. Try again; if it keeps failing, contact the administrator."


def friendly_error(exc: BaseException | str) -> str:
    """A safe, human-readable reason for a failed job (never includes paths or command output)."""
    text = str(exc)
    for pattern, message in _PATTERNS:
        if pattern.search(text):
            return message
    return GENERIC
