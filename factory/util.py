"""Hashing, truncation, JSON IO, canonical JSON bytes, command runner, path helpers."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import posixpath
import shlex
import subprocess
import time
from typing import Any

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
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

MAX_EXCERPT_CHARS = 2000


def truncate(text: str, max_chars: int = MAX_EXCERPT_CHARS) -> str:
    """Truncate *text*, appending a marker when shortened."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


# ---------------------------------------------------------------------------
# JSON IO
# ---------------------------------------------------------------------------


def save_json(data: Any, path: str) -> None:
    """Write *data* as pretty-printed, sorted-key JSON."""
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


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
# Artifact path helpers  (Phase 2 — single source of truth for artifact names)
# ---------------------------------------------------------------------------

# Per-attempt artifact filenames
ARTIFACT_SE_PROMPT = "se_prompt.txt"
ARTIFACT_PROPOSED_WRITES = "proposed_writes.json"
ARTIFACT_RAW_LLM_RESPONSE = "raw_llm_response.json"
ARTIFACT_WRITE_RESULT = "write_result.json"
ARTIFACT_VERIFY_RESULT = "verify_result.json"
ARTIFACT_ACCEPTANCE_RESULT = "acceptance_result.json"
ARTIFACT_FAILURE_BRIEF = "failure_brief.json"

# Per-run artifact filenames
ARTIFACT_WORK_ORDER = "work_order.json"
ARTIFACT_RUN_SUMMARY = "run_summary.json"


def make_attempt_dir(out_dir: str, run_id: str, attempt_index: int) -> str:
    """Return the artifact directory path for a specific attempt."""
    return os.path.join(out_dir, run_id, f"attempt_{attempt_index}")
