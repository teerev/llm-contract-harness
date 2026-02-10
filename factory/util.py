"""Hashing, truncation, JSON IO, canonical JSON bytes, command runner, path helpers."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import posixpath
import shlex
import subprocess
import tempfile
import time
from typing import Any

from factory.defaults import (  # noqa: F401 — re-exported for backward compat
    ARTIFACT_ACCEPTANCE_RESULT,
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_PROPOSED_WRITES,
    ARTIFACT_RAW_LLM_RESPONSE,
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_SE_PROMPT,
    ARTIFACT_VERIFY_RESULT,
    ARTIFACT_WORK_ORDER,
    ARTIFACT_WRITE_RESULT,
    MAX_EXCERPT_CHARS,
    RUN_ID_HEX_LENGTH,
)
from factory.schemas import CmdResult

# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    """Return hex SHA-256 of a file's content (empty-bytes hash if missing)."""
    try:
        with open(path, "rb") as fh:
            return sha256_bytes(fh.read())
    except FileNotFoundError:
        return sha256_bytes(b"")


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, minimal separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_run_id(work_order_dict: dict, baseline_commit: str) -> str:
    """Deterministic run_id = sha256(canonical_work_order + '\\n' + baseline_commit)[:16]."""
    h = hashlib.sha256()
    h.update(canonical_json_bytes(work_order_dict))
    h.update(b"\n")
    h.update(baseline_commit.encode("utf-8"))
    return h.hexdigest()[:RUN_ID_HEX_LENGTH]


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def truncate(text: str, max_chars: int = MAX_EXCERPT_CHARS) -> str:
    """Truncate *text*, appending a marker when shortened."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


# ---------------------------------------------------------------------------
# JSON IO
# ---------------------------------------------------------------------------


def save_json(data: Any, path: str) -> None:
    """Write *data* as pretty-printed, sorted-key JSON, atomically.

    Uses tempfile + fsync + os.replace so that a crash mid-write never
    leaves a truncated file at *path*.  (M-05: matches the atomic pattern
    already used by ``planner/io.py::_atomic_write`` and
    ``factory/nodes_tr.py::_atomic_write``.)
    """
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    parent = pathlib.Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: str) -> Any:
    """Read JSON from *path*."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------------


def run_command(
    cmd: list[str],
    cwd: str,
    timeout: int,
    stdout_path: str,
    stderr_path: str,
) -> CmdResult:
    """Run *cmd* with no shell, capture output to files, enforce *timeout*."""
    pathlib.Path(stdout_path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(stderr_path).parent.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        duration = time.monotonic() - start

        with open(stdout_path, "wb") as fh:
            fh.write(proc.stdout)
        with open(stderr_path, "wb") as fh:
            fh.write(proc.stderr)

        return CmdResult(
            command=cmd,
            exit_code=proc.returncode,
            stdout_trunc=truncate(proc.stdout.decode("utf-8", errors="replace")),
            stderr_trunc=truncate(proc.stderr.decode("utf-8", errors="replace")),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_seconds=round(duration, 3),
        )

    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        out = exc.stdout or b""
        err = exc.stderr or b""
        with open(stdout_path, "wb") as fh:
            fh.write(out)
        with open(stderr_path, "wb") as fh:
            fh.write(err)
        return CmdResult(
            command=cmd,
            exit_code=-1,
            stdout_trunc=truncate(out.decode("utf-8", errors="replace")),
            stderr_trunc=truncate(
                err.decode("utf-8", errors="replace") + "\n[TIMEOUT]"
            ),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_seconds=round(duration, 3),
        )

    except OSError as exc:
        # Covers PermissionError (no +x bit), FileNotFoundError (missing
        # interpreter), and other OS-level launch failures.  Return a
        # failed CmdResult instead of letting the exception crash the run.
        duration = time.monotonic() - start
        err_msg = f"[OSError] {exc}\n"
        with open(stdout_path, "wb") as fh:
            fh.write(b"")
        with open(stderr_path, "wb") as fh:
            fh.write(err_msg.encode("utf-8", errors="replace"))
        return CmdResult(
            command=cmd,
            exit_code=-1,
            stdout_trunc="",
            stderr_trunc=truncate(err_msg),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_seconds=round(duration, 3),
        )


# ---------------------------------------------------------------------------
# Shell-command splitting
# ---------------------------------------------------------------------------


def split_command(cmd_str: str) -> list[str]:
    """Split a shell command string into a list via ``shlex``."""
    return shlex.split(cmd_str)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def normalize_path(p: str) -> str:
    """Normalize a relative path using POSIX rules."""
    return posixpath.normpath(p)


def is_path_inside_repo(rel_path: str, repo_root: str) -> bool:
    """Return True if *rel_path* resolves to within *repo_root*."""
    abs_path = os.path.realpath(os.path.join(repo_root, rel_path))
    abs_root = os.path.realpath(repo_root)
    return abs_path == abs_root or abs_path.startswith(abs_root + os.sep)


# ---------------------------------------------------------------------------
# Artifact path helpers
# ---------------------------------------------------------------------------


def make_attempt_dir(out_dir: str, run_id: str, attempt_index: int) -> str:
    """Return the artifact directory path for a specific attempt."""
    return os.path.join(out_dir, run_id, f"attempt_{attempt_index}")
