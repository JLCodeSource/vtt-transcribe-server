"""The suite runs with ``-W error``: our own code and tests must not emit warnings, and only named third-party ones are filtered."""

import os
import tomllib
import warnings
from pathlib import Path

from vtt_server.auth import ALGORITHM, create_access_token

HS512_MIN_KEY_BYTES = 64  # RFC 7518 section 3.2, enforced by PyJWT's InsecureKeyLengthWarning
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_suite_secret_key_is_long_enough_for_hs512() -> None:
    assert ALGORITHM == "HS512"
    assert len(os.environ["SECRET_KEY"].encode()) >= HS512_MIN_KEY_BYTES


def test_signing_a_token_emits_no_warnings() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        create_access_token({"sub": "someone"})


def test_only_the_named_third_party_warning_is_filtered() -> None:
    config = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]
    filters = config["filterwarnings"]
    assert filters == ["error", "ignore:'crypt' is deprecated:DeprecationWarning:passlib.*"]
