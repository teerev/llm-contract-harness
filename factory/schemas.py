"""Pydantic models and load/save helpers."""

from __future__ import annotations

import json
import pathlib
import posixpath
import re
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _validate_relative_path(p: str) -> str:
    """Validate that *p* is a safe, relative path and return its normalized form."""
    if not p:
        raise ValueError("path must not be empty")
    # Reject absolute paths (POSIX or Windows style)
    if p.startswith("/") or pathlib.PurePosixPath(p).is_absolute():
        raise ValueError(f"path must be relative: {p}")
    # Reject Windows drive letters
    if re.match(r"^[A-Za-z]:", p):
        raise ValueError(f"path must not contain drive letters: {p}")
    normalized = posixpath.normpath(p)
    if normalized.startswith(".."):
        raise ValueError(f"normalized path must not start with '..': {p}")
    return normalized


# ---------------------------------------------------------------------------
# WorkOrder
# ---------------------------------------------------------------------------

class WorkOrder(BaseModel):
    id: str
    title: str
    intent: str
    allowed_files: list[str]
    forbidden: list[str]
    acceptance_commands: list[str]
    context_files: list[str]
    notes: Optional[str] = None

    @field_validator("allowed_files", "context_files", mode="before")
    @classmethod
    def _validate_paths(cls, v: list[str]) -> list[str]:
        return [_validate_relative_path(p) for p in v]

    @field_validator("acceptance_commands", mode="before")
    @classmethod
    def _acceptance_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("acceptance_commands must be non-empty")
        return v

    @model_validator(mode="after")
    def _check_context_constraints(self) -> "WorkOrder":
        if len(self.context_files) > 10:
            raise ValueError("context_files must have at most 10 entries")
        allowed_set = set(self.allowed_files)
        for cf in self.context_files:
            if cf not in allowed_set:
                raise ValueError(
                    f"context_files must be a subset of allowed_files: "
                    f"{cf!r} not in allowed_files"
                )
        return self


# ---------------------------------------------------------------------------
# FileWrite / WriteProposal
# ---------------------------------------------------------------------------

MAX_FILE_WRITE_BYTES = 200 * 1024   # 200 KB per file
MAX_TOTAL_WRITE_BYTES = 500 * 1024  # 500 KB total


class FileWrite(BaseModel):
    path: str
    base_sha256: str
    content: str

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_path(v)


class WriteProposal(BaseModel):
    summary: str
    writes: list[FileWrite]

    @field_validator("writes", mode="before")
    @classmethod
    def _writes_non_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("writes must be non-empty")
        return v

    @model_validator(mode="after")
    def _check_size_limits(self) -> "WriteProposal":
        total = 0
        for w in self.writes:
            size = len(w.content.encode("utf-8"))
            if size > MAX_FILE_WRITE_BYTES:
                raise ValueError(
                    f"file {w.path} content exceeds {MAX_FILE_WRITE_BYTES} bytes: {size}"
                )
            total += size
        if total > MAX_TOTAL_WRITE_BYTES:
            raise ValueError(
                f"total write content exceeds {MAX_TOTAL_WRITE_BYTES} bytes: {total}"
            )
        return self


# ---------------------------------------------------------------------------
# FailureBrief
# ---------------------------------------------------------------------------

ALLOWED_STAGES = frozenset({
    "preflight",
    "llm_output_invalid",
    "write_scope_violation",
    "stale_context",
    "write_failed",
    "verify_failed",
    "acceptance_failed",
    "exception",
})


class FailureBrief(BaseModel):
    stage: str
    command: Optional[str] = None
    exit_code: Optional[int] = None
    primary_error_excerpt: str
    constraints_reminder: str

    @field_validator("stage", mode="before")
    @classmethod
    def _validate_stage(cls, v: str) -> str:
        if v not in ALLOWED_STAGES:
            raise ValueError(f"stage must be one of {sorted(ALLOWED_STAGES)}: {v}")
        return v


# ---------------------------------------------------------------------------
# CmdResult / AttemptRecord / RunSummary
# ---------------------------------------------------------------------------

class CmdResult(BaseModel):
    command: list[str]
    exit_code: int
    stdout_trunc: str
    stderr_trunc: str
    stdout_path: str
    stderr_path: str
    duration_seconds: float


class AttemptRecord(BaseModel):
    attempt_index: int
    baseline_commit: str
    proposal_path: str
    touched_files: list[str]
    write_ok: bool
    verify: list[CmdResult]
    acceptance: list[CmdResult]
    failure_brief: Optional[FailureBrief] = None


class RunSummary(BaseModel):
    run_id: str
    work_order_id: str
    verdict: str
    total_attempts: int
    baseline_commit: str
    repo_tree_hash_after: Optional[str] = None
    attempts: list[AttemptRecord]


# ---------------------------------------------------------------------------
# Load helper
# ---------------------------------------------------------------------------

def load_work_order(path: str) -> WorkOrder:
    """Load a WorkOrder from a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return WorkOrder(**data)
