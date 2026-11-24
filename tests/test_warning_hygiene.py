"""The suite runs with ``-W error``: our code and tests emit no warnings; only one named third-party one is filtered."""

import os
import tomllib
import warnings
from pathlib import Path

import pytest

from vtt_server.auth import ALGORITHM, create_access_token

HS512_MIN_KEY_BYTES = 64  # RFC 7518 section 3.2, enforced by PyJWT's InsecureKeyLengthWarning
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_suite_secret_key_is_long_enough_for_hs512() -> None:
    assert ALGORITHM == "HS512"
    assert len(os.environ["SECRET_KEY"].encode()) >= HS512_MIN_KEY_BYTES


def test_signing_a_token_emits_no_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vtt_server.auth.SECRET_KEY", os.environ["SECRET_KEY"])  # other tests reload auth with their own env
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        create_access_token({"sub": "someone"})


def test_only_the_named_third_party_warning_is_filtered() -> None:
    """A command-line -W error overrides an ini 'ignore', so the one filter lives in conftest and the ini stays strict."""
    config = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]
    assert config["filterwarnings"] == ["error"]
    conftest = (Path(__file__).parent / "conftest.py").read_text()
    assert conftest.count("simplefilter(") + conftest.count("filterwarnings(") == 1
    assert "'crypt' is deprecated" in conftest
