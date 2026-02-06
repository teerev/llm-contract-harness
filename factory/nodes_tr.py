"""TR node — scope checks, base-hash checks, atomic file writes, write_result."""

from __future__ import annotations

import os
import tempfile

from factory.schemas import FailureBrief, WorkOrder, WriteProposal
from factory.util import (
    ARTIFACT_WRITE_RESULT,
    is_path_inside_repo,
    make_attempt_dir,
    normalize_path,
    save_json,
    sha256_file,
    truncate,
)


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write(target_path: str, content: str) -> None:
    """Write *content* atomically: temp file → fsync → rename."""
    parent = os.path.dirname(target_path)
    os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# TR failure helper
# ---------------------------------------------------------------------------


def _tr_fail(
    stage: str,
    excerpt: str,
    reminder: str,
    touched_files: list[str],
    attempt_dir: str,
) -> dict:
    """Build a TR failure: persist write_result.json and return failure state.

    Centralises the five identical failure paths in ``tr_node``.  The
    ``write_result.json`` artifact and the returned state dict are
    byte-identical to the previously-inlined versions (``save_json`` uses
    ``sort_keys=True``, so Python dict insertion order is irrelevant).
    """
    fb = FailureBrief(
        stage=stage,
        primary_error_excerpt=excerpt,
        constraints_reminder=reminder,
    )
    save_json(
        {"write_ok": False, "touched_files": touched_files,
         "errors": [fb.primary_error_excerpt]},
        os.path.join(attempt_dir, ARTIFACT_WRITE_RESULT),
    )
    return {
        "write_ok": False,
        "touched_files": touched_files,
        "failure_brief": fb.model_dump(),
    }


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def tr_node(state: dict) -> dict:
    """TR node — validate scope & hashes, apply writes, emit write_result."""
    work_order = WorkOrder(**state["work_order"])
    proposal = WriteProposal(**state["proposal"])
    repo_root: str = state["repo_root"]
    attempt_index: int = state["attempt_index"]
    run_id: str = state["run_id"]
    out_dir: str = state["out_dir"]

    attempt_dir = make_attempt_dir(out_dir, run_id, attempt_index)
    os.makedirs(attempt_dir, exist_ok=True)

    # Normalize paths
    touched_files = sorted({normalize_path(w.path) for w in proposal.writes})
    allowed_set = set(normalize_path(p) for p in work_order.allowed_files)

    # ------------------------------------------------------------------
    # 0. Duplicate-path check — reject proposals that write the same file twice
    # ------------------------------------------------------------------
    if len(touched_files) < len(proposal.writes):
        from collections import Counter

        counts = Counter(normalize_path(w.path) for w in proposal.writes)
        dupes = sorted(p for p, n in counts.items() if n > 1)
        return _tr_fail(
            stage="write_scope_violation",
            excerpt=f"Duplicate write paths in proposal: {dupes}",
            reminder="Each file may only appear once in the writes array.",
            touched_files=touched_files,
            attempt_dir=attempt_dir,
        )

    # ------------------------------------------------------------------
    # 1. Scope check — all proposed files must be in allowed_files
    # ------------------------------------------------------------------
    out_of_scope = [f for f in touched_files if f not in allowed_set]
    if out_of_scope:
        return _tr_fail(
            stage="write_scope_violation",
            excerpt=f"Files outside allowed scope: {out_of_scope}",
            reminder="All proposed file paths must be in the work order's allowed_files list.",
            touched_files=touched_files,
            attempt_dir=attempt_dir,
        )

    # ------------------------------------------------------------------
    # 2. Path-safety check — paths must resolve inside repo
    # ------------------------------------------------------------------
    for f in touched_files:
        if not is_path_inside_repo(f, repo_root):
            return _tr_fail(
                stage="write_scope_violation",
                excerpt=f"Path escapes repo root: {f}",
                reminder="All file paths must resolve to inside the product repo.",
                touched_files=touched_files,
                attempt_dir=attempt_dir,
            )

    # ------------------------------------------------------------------
    # 3. Base-hash check — ALL files checked BEFORE any writes
    # ------------------------------------------------------------------
    for w in proposal.writes:
        norm = normalize_path(w.path)
        abs_path = os.path.join(repo_root, norm)
        actual_hash = sha256_file(abs_path)
        if actual_hash != w.base_sha256:
            return _tr_fail(
                stage="stale_context",
                excerpt=(
                    f"Hash mismatch for {norm}: "
                    f"expected {w.base_sha256}, actual {actual_hash}"
                ),
                reminder=(
                    "base_sha256 must match the current file content. "
                    "Re-read context files and use their current sha256."
                ),
                touched_files=touched_files,
                attempt_dir=attempt_dir,
            )

    # ------------------------------------------------------------------
    # 4. Apply writes
    # ------------------------------------------------------------------
    for w in proposal.writes:
        norm = normalize_path(w.path)
        abs_path = os.path.join(repo_root, norm)
        try:
            _atomic_write(abs_path, w.content)
        except Exception as exc:
            return _tr_fail(
                stage="write_failed",
                excerpt=truncate(f"Failed to write {norm}: {exc}"),
                reminder="Atomic file write failed.",
                touched_files=touched_files,
                attempt_dir=attempt_dir,
            )

    # All writes succeeded
    save_json(
        {"write_ok": True, "touched_files": touched_files, "errors": []},
        os.path.join(attempt_dir, ARTIFACT_WRITE_RESULT),
    )
    return {
        "write_ok": True,
        "touched_files": touched_files,
        "failure_brief": None,
    }
