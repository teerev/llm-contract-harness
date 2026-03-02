"""Server configuration — resolved from environment variables with defaults."""

from __future__ import annotations

import os


def _env(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


HOST: str = _env("LLMCH_HOST", "127.0.0.1")
PORT: int = int(os.environ.get("PORT", "").strip() or _env("LLMCH_PORT", "8000"))

ARTIFACTS_DIR: str = os.path.realpath(
    _env("LLMCH_ARTIFACTS_DIR", os.environ.get("ARTIFACTS_DIR", "").strip() or "./artifacts")
)
_demo_raw: str = os.environ.get("LLMCH_DEMO_REMOTE_URL", "").strip()
_demo_token: str = os.environ.get("LLMCH_DEMO_REMOTE_TOKEN", "").strip()

def _build_demo_url(raw: str, token: str) -> tuple[str, str]:
    """Return (push_url, safe_url) — safe_url has credentials scrubbed for logging."""
    if not raw:
        return "", ""
    if token and "github.com" in raw:
        # Convert SSH or plain HTTPS to token-authenticated HTTPS
        # git@github.com:org/repo.git → https://x-access-token:TOKEN@github.com/org/repo.git
        # https://github.com/org/repo.git → https://x-access-token:TOKEN@github.com/org/repo.git
        path = raw
        for prefix in ("git@github.com:", "https://github.com/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        if not path.endswith(".git"):
            path += ".git"
        push = f"https://x-access-token:{token}@github.com/{path}"
        safe = f"https://github.com/{path}"
        return push, safe
    return raw, raw

DEMO_REMOTE_URL, DEMO_REMOTE_URL_SAFE = _build_demo_url(_demo_raw, _demo_token)

RATE_LIMIT_PER_IP: int = int(_env("LLMCH_RATE_LIMIT_PER_IP", "100"))
RATE_LIMIT_GLOBAL: int = int(_env("LLMCH_RATE_LIMIT_GLOBAL", "100"))

STATIC_DIR: str | None = None
_candidate = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "dist")
if os.path.isdir(_candidate):
    STATIC_DIR = _candidate
