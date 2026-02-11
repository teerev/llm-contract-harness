"""Shared infrastructure for canonical artifact storage.

Provides:
- ULID generation (sortable, collision-resistant run IDs)
- SHA-256 hashing utilities
- Artifact root resolution
- run.json management (write-early / update-on-completion)
- Tool version detection
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# ULID generation (no external dependency)
# ---------------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    """Encode an integer as Crockford's Base32 with fixed width."""
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def generate_ulid() -> str:
    """Generate a ULID: 26-char, lexicographically sortable, collision-resistant.

    Layout: 10-char timestamp (ms since epoch) + 16-char random.
    """
    ts_ms = int(time.time() * 1000)
    rand_int = int.from_bytes(os.urandom(10), "big")
    return _encode_crockford(ts_ms, 10) + _encode_crockford(rand_int, 16)


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string with microseconds."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    """Return hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    """Return hex SHA-256 of a file's content."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    """Return hex SHA-256 of canonical JSON (sorted keys, minimal separators)."""
    data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Artifact root resolution
# ---------------------------------------------------------------------------

def resolve_artifacts_root(cli_arg: str | None = None) -> str:
    """Resolve canonical artifacts root: CLI > env ARTIFACTS_DIR > ./artifacts."""
    if cli_arg:
        root = os.path.realpath(cli_arg)
    else:
        env = os.environ.get("ARTIFACTS_DIR", "").strip()
        root = os.path.realpath(env) if env else os.path.realpath("./artifacts")
    os.makedirs(root, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Run directory management
# ---------------------------------------------------------------------------

def init_run_dir(artifacts_root: str, tool: str, run_id: str) -> str:
    """Create an immutable run directory. Raises FileExistsError on collision.

    Returns the absolute path to the created directory.
    """
    run_dir = os.path.join(artifacts_root, tool, run_id)
    os.makedirs(run_dir, exist_ok=False)
    return run_dir


# ---------------------------------------------------------------------------
# run.json management
# ---------------------------------------------------------------------------

def write_run_json(run_dir: str, data: dict) -> str:
    """Atomically write (or update) run.json in the run directory.

    Returns the path to run.json.
    """
    path = os.path.join(run_dir, "run.json")
    _atomic_write_json(path, data)
    return path


def read_run_json(run_dir: str) -> dict:
    """Read run.json from a run directory."""
    path = os.path.join(run_dir, "run.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write_json(path: str, data: dict) -> None:
    """Atomic JSON write: temp → fsync → replace."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Tool version detection
# ---------------------------------------------------------------------------

def get_tool_version() -> dict:
    """Return git commit hash (short) and dirty flag for this tool's repo.

    Best-effort: returns nulls on any failure.
    """
    try:
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, text=True, timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()[:12]
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_dir, text=True, timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        return {"git_commit": commit, "git_dirty": bool(porcelain)}
    except Exception:
        return {"git_commit": None, "git_dirty": None}
