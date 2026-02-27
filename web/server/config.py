"""Server configuration — resolved from environment variables with defaults."""

from __future__ import annotations

import os


def _env(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


HOST: str = _env("LLMCH_HOST", "127.0.0.1")
PORT: int = int(_env("LLMCH_PORT", "8000"))

ARTIFACTS_DIR: str = os.path.realpath(
    _env("LLMCH_ARTIFACTS_DIR", os.environ.get("ARTIFACTS_DIR", "").strip() or "./artifacts")
)
DEMO_REMOTE_URL: str = os.environ.get("LLMCH_DEMO_REMOTE_URL", "").strip()

STATIC_DIR: str | None = None
_candidate = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "dist")
if os.path.isdir(_candidate):
    STATIC_DIR = _candidate
