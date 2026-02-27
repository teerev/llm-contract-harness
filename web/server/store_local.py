"""Local-filesystem implementations of RunStore and FileStore."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from web.server import config
from web.server.interfaces import (
    VALID_ROOTS,
    RunMeta,
    RunOptions,
    TreeEntry,
)

from shared.run_context import generate_ulid

MAX_FILE_READ_BYTES = 1_048_576  # 1 MB

SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".mypy_cache", ".tox", ".venv", "venv", ".eggs", ".llmch_venv",
})


# ---------------------------------------------------------------------------
# LocalRunStore
# ---------------------------------------------------------------------------

class LocalRunStore:
    """Persists RunMeta as ``artifacts/pipeline/{id}/meta.json``."""

    def __init__(self, artifacts_dir: str | None = None) -> None:
        self._artifacts_dir = artifacts_dir or config.ARTIFACTS_DIR

    def _run_dir(self, run_id: str) -> str:
        return os.path.join(self._artifacts_dir, "pipeline", run_id)

    def _meta_path(self, run_id: str) -> str:
        return os.path.join(self._run_dir(run_id), "meta.json")

    def create(self, prompt: str, opts: RunOptions) -> str:
        run_id = generate_ulid()
        run_dir = self._run_dir(run_id)
        os.makedirs(run_dir, exist_ok=False)

        meta = RunMeta(
            pipeline_run_id=run_id,
            status="queued",
            prompt=prompt,
            started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            opts=opts,
        )
        self._write_meta(run_id, meta)
        return run_id

    def get(self, run_id: str) -> RunMeta:
        path = self._meta_path(run_id)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Run not found: {run_id}")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        opts_raw = data.pop("opts", {})
        opts = RunOptions(**opts_raw) if isinstance(opts_raw, dict) else RunOptions()
        return RunMeta(**data, opts=opts)

    def update(self, run_id: str, **fields) -> None:
        meta = self.get(run_id)
        for k, v in fields.items():
            if hasattr(meta, k):
                setattr(meta, k, v)
        self._write_meta(run_id, meta)

    def events_path(self, run_id: str) -> str:
        return os.path.join(self._run_dir(run_id), "events.jsonl")

    def _write_meta(self, run_id: str, meta: RunMeta) -> None:
        path = self._meta_path(run_id)
        data = meta.to_dict()
        data["opts"] = {"push_to_demo": meta.opts.push_to_demo, "branch_name": meta.opts.branch_name}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)


# ---------------------------------------------------------------------------
# LocalFileStore
# ---------------------------------------------------------------------------

class LocalFileStore:
    """Maps (run_id, root, path) to local filesystem paths."""

    def __init__(
        self,
        artifacts_dir: str | None = None,
        run_store: LocalRunStore | None = None,
    ) -> None:
        self._artifacts_dir = artifacts_dir or config.ARTIFACTS_DIR
        self._run_store = run_store

    def _resolve_base(self, run_id: str, root: str) -> str:
        if root not in VALID_ROOTS:
            raise ValueError(f"Invalid root: {root!r}. Must be one of {VALID_ROOTS}")

        if root == "repo":
            return os.path.join(self._artifacts_dir, "pipeline", run_id, "repo")

        if root == "artifacts":
            return self._artifacts_dir

        # root == "work_orders" -> canonical planner output
        if self._run_store:
            try:
                meta = self._run_store.get(run_id)
                if meta.planner_run_id:
                    return os.path.join(
                        self._artifacts_dir, "planner", meta.planner_run_id, "output"
                    )
            except FileNotFoundError:
                pass
        return os.path.join(self._artifacts_dir, "pipeline", run_id, "work_orders")

    def tree(self, run_id: str, root: str) -> list[TreeEntry]:
        base = self._resolve_base(run_id, root)
        if not os.path.isdir(base):
            return []

        entries: list[TreeEntry] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel_dir = os.path.relpath(dirpath, base)

            if rel_dir != ".":
                entries.append(TreeEntry(path=rel_dir + "/", type="dir"))

            for fname in sorted(filenames):
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, base)
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    size = 0
                lc = self._line_count(abs_path, size)
                entries.append(TreeEntry(path=rel_path, type="file", size=size, line_count=lc))

        entries.sort(key=lambda e: (e.type != "dir", e.path.lower()))
        return entries

    def read(self, run_id: str, root: str, path: str) -> bytes:
        base = self._resolve_base(run_id, root)
        full = os.path.realpath(os.path.join(base, path))
        if not full.startswith(os.path.realpath(base)):
            raise PermissionError("Path escapes root")
        if not os.path.isfile(full):
            raise FileNotFoundError(f"File not found: {root}/{path}")
        with open(full, "rb") as fh:
            return fh.read(MAX_FILE_READ_BYTES)

    def exists(self, run_id: str, root: str, path: str) -> bool:
        try:
            base = self._resolve_base(run_id, root)
        except (ValueError, FileNotFoundError):
            return False
        full = os.path.realpath(os.path.join(base, path))
        return full.startswith(os.path.realpath(base)) and os.path.exists(full)

    @staticmethod
    def _line_count(path: str, size: int) -> int | None:
        if size == 0:
            return 0
        if size > MAX_FILE_READ_BYTES:
            return None
        try:
            with open(path, "rb") as fh:
                return sum(1 for _ in fh)
        except (OSError, UnicodeDecodeError):
            return None
