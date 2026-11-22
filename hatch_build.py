"""Hatch build hook: a release build must ship the web UI.

The UI is built from ``frontend/`` into ``vtt_server/static`` (``scripts/build_ui.sh``) before ``uv build`` / ``hatch build``.
Wheels built from an sdist need nothing extra: the sdist carries the built copy. Editable installs (development) and
``VTT_ALLOW_NO_UI=1`` (the API-only Docker image) skip the check.
"""

from __future__ import annotations

import os
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class UIBundleHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        if version == "editable" or os.environ.get("VTT_ALLOW_NO_UI") == "1":
            return
        index = Path(self.root) / "vtt_server" / "static" / "index.html"
        if not index.is_file():
            msg = (
                "vtt_server/static/index.html is missing: build the web UI first with scripts/build_ui.sh "
                "(or set VTT_ALLOW_NO_UI=1 to build an API-only package)."
            )
            raise RuntimeError(msg)
