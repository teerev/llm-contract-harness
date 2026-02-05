from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Any, Literal

try:
    # Pydantic v2
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

    _PYDANTIC_V2 = True
except Exception:  # pragma: no cover
    # Pydantic v1 fallback
    from pydantic import BaseModel, Field, root_validator, validator  # type: ignore

    ConfigDict = None  # type: ignore
    _PYDANTIC_V2 = False


_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


def _normalize_relpath(p: str) -> str:
    """
    Normalize a relative path using POSIX semantics.

    Returns a normalized path string with forward slashes.
    """
    if "\x00" in p:
        raise ValueError("path contains NUL byte")
    if _DRIVE_LETTER_RE.match(p):
        raise ValueError("path must not contain a drive letter")
    # Treat backslashes as separators to avoid Windows-style traversal tricks.
    p2 = p.replace("\\", "/")
    if p2.startswith("/"):
        raise ValueError("path must be relative (must not be absolute)")
    if p2.strip() == "":
        raise ValueError("path must be non-empty")
    norm = posixpath.normpath(p2)
    if norm == ".":
        raise ValueError("path must point to a file, not '.'")
    if norm == ".." or norm.startswith("../"):
        raise ValueError("path must not start with '..'")
    if norm.startswith("/"):
        raise ValueError("path must be relative (must not be absolute)")
    return norm


class WorkOrder(BaseModel):
    id: str
    title: str
    intent: str
    allowed_files: list[str]
    forbidden: list[str]
    acceptance_commands: list[str]
    context_files: list[str]
    notes: str | None = None

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")  # type: ignore[misc]

        @field_validator("allowed_files", "context_files")
        @classmethod
        def _validate_paths_v2(cls, v: list[str], info: Any) -> list[str]:
            field_name = getattr(info, "field_name", "paths")
            normed: list[str] = []
            for p in v:
                try:
                    normed.append(_normalize_relpath(p))
                except Exception as e:
                    raise ValueError(f"invalid {field_name} entry {p!r}: {e}") from e
            return normed

        @field_validator("acceptance_commands")
        @classmethod
        def _validate_acceptance_commands_v2(cls, v: list[str]) -> list[str]:
            if not v:
                raise ValueError("acceptance_commands must be non-empty")
            return v

        @field_validator("context_files")
        @classmethod
        def _validate_context_bounds_v2(cls, v: list[str]) -> list[str]:
            if len(v) > 10:
                raise ValueError("context_files must have at most 10 entries")
            return v

        @model_validator(mode="after")
        def _validate_context_subset_v2(self) -> "WorkOrder":
            allowed = set(self.allowed_files)
            ctx = set(self.context_files)
            if not ctx.issubset(allowed):
                missing = sorted(ctx - allowed)
                raise ValueError(
                    "context_files must be a subset of allowed_files; "
                    f"not allowed: {missing}"
                )
            return self

    else:  # pragma: no cover
        class Config:
            extra = "forbid"

        @validator("allowed_files", "context_files", pre=True, each_item=False)
        def _validate_paths_v1(cls, v: Any, field: Any) -> list[str]:
            if v is None:
                return []
            if not isinstance(v, list):
                raise ValueError("must be a list of strings")
            field_name = getattr(field, "name", "paths")
            normed: list[str] = []
            for p in v:
                try:
                    normed.append(_normalize_relpath(str(p)))
                except Exception as e:
                    raise ValueError(f"invalid {field_name} entry {p!r}: {e}") from e
            return normed

        @validator("acceptance_commands")
        def _validate_acceptance_commands_v1(cls, v: list[str]) -> list[str]:
            if not v:
                raise ValueError("acceptance_commands must be non-empty")
            return v

        @validator("context_files")
        def _validate_context_bounds_v1(cls, v: list[str]) -> list[str]:
            if len(v) > 10:
                raise ValueError("context_files must have at most 10 entries")
            return v

        @root_validator
        def _validate_context_subset_v1(cls, values: dict[str, Any]) -> dict[str, Any]:
            allowed = set(values.get("allowed_files") or [])
            ctx = set(values.get("context_files") or [])
            if not ctx.issubset(allowed):
                missing = sorted(ctx - allowed)
                raise ValueError(
                    "context_files must be a subset of allowed_files; "
                    f"not allowed: {missing}"
                )
            return values


class PatchProposal(BaseModel):
    unified_diff: str
    summary: str

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")  # type: ignore[misc]
    else:  # pragma: no cover
        class Config:
            extra = "forbid"


FailureStage = Literal[
    "preflight",
    "llm_output_invalid",
    "patch_scope_violation",
    "patch_apply_failed",
    "verify_failed",
    "acceptance_failed",
    "exception",
]


class FailureBrief(BaseModel):
    stage: FailureStage
    command: str | None = None
    exit_code: int | None = None
    primary_error_excerpt: str
    constraints_reminder: str

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")  # type: ignore[misc]
    else:  # pragma: no cover
        class Config:
            extra = "forbid"


class CmdResult(BaseModel):
    command: list[str]
    exit_code: int
    stdout_trunc: str
    stderr_trunc: str
    stdout_path: str
    stderr_path: str
    duration_seconds: float

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")  # type: ignore[misc]
    else:  # pragma: no cover
        class Config:
            extra = "forbid"


class AttemptRecord(BaseModel):
    attempt_index: int
    baseline_commit: str
    patch_path: str
    touched_files: list[str]
    apply_ok: bool
    verify: list[CmdResult]
    acceptance: list[CmdResult]
    failure_brief: FailureBrief | None

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")  # type: ignore[misc]
    else:  # pragma: no cover
        class Config:
            extra = "forbid"


Verdict = Literal["PASS", "FAIL"]


class RunSummary(BaseModel):
    run_id: str
    repo_path: str
    work_order_path: str
    work_order_hash: str
    repo_baseline_commit: str
    repo_tree_hash_before: str
    repo_tree_hash_after: str
    max_attempts: int
    attempts: list[AttemptRecord]
    verdict: Verdict
    ended_stage: str
    started_utc: str
    ended_utc: str

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid")  # type: ignore[misc]
    else:  # pragma: no cover
        class Config:
            extra = "forbid"


def model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return getattr(model, "model_dump")()
    return model.dict()  # type: ignore[no-any-return]


def model_validate(model_cls: type[BaseModel], obj: Any) -> BaseModel:
    if hasattr(model_cls, "model_validate"):
        return getattr(model_cls, "model_validate")(obj)
    return model_cls.parse_obj(obj)  # type: ignore[no-any-return]


def load_work_order(path: Path) -> WorkOrder:
    data = json.loads(path.read_text(encoding="utf-8"))
    return model_validate(WorkOrder, data)  # type: ignore[return-value]


def write_pretty_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, BaseModel):
        payload = model_dump(obj)
    else:
        payload = obj
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

