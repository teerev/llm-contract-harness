"""Protocol definitions for storage and execution backends.

Local implementations live in store_local.py / runner_fake.py / runner_local.py.
AWS implementations can be swapped in without changing API routes or the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RunOptions:
    push_to_demo: bool = False
    branch_name: str | None = None


@dataclass
class RunMeta:
    pipeline_run_id: str
    status: str = "queued"  # queued|planning|building|pushing|complete|failed
    prompt: str = ""
    planner_run_id: str | None = None
    factory_run_ids: list[str] = field(default_factory=list)
    work_order_count: int = 0
    work_order_verdicts: dict[str, str] = field(default_factory=dict)
    push_result: dict[str, Any] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    opts: RunOptions = field(default_factory=RunOptions)

    def to_dict(self) -> dict:
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "status": self.status,
            "prompt": self.prompt,
            "planner_run_id": self.planner_run_id,
            "factory_run_ids": self.factory_run_ids,
            "work_order_count": self.work_order_count,
            "work_order_verdicts": self.work_order_verdicts,
            "push_result": self.push_result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass
class TreeEntry:
    path: str
    type: str  # "file" | "dir"
    size: int = 0
    line_count: int | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"path": self.path, "type": self.type, "size": self.size}
        if self.line_count is not None:
            d["line_count"] = self.line_count
        return d


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

VALID_ROOTS = {"work_orders", "artifacts", "repo"}


@runtime_checkable
class FileStore(Protocol):
    def tree(self, run_id: str, root: str) -> list[TreeEntry]: ...
    def read(self, run_id: str, root: str, path: str) -> bytes: ...
    def exists(self, run_id: str, root: str, path: str) -> bool: ...


@runtime_checkable
class RunStore(Protocol):
    def create(self, prompt: str, opts: RunOptions) -> str: ...
    def get(self, run_id: str) -> RunMeta: ...
    def update(self, run_id: str, **fields: Any) -> None: ...
    def events_path(self, run_id: str) -> str: ...


@runtime_checkable
class Runner(Protocol):
    def start(self, run_id: str, prompt: str, opts: RunOptions) -> None: ...
