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
        # Handle legacy push_result field from older meta.json files
        push_result = data.pop("push_result", None)
        if push_result and isinstance(push_result, dict):
            data.setdefault("push_remote", push_result.get("remote"))
            data.setdefault("push_branch", push_result.get("branch"))
            data.setdefault("push_commit_sha", push_result.get("commit_sha"))
            data.setdefault("push_url", push_result.get("url"))
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

    def _get_artifacts_mapping(self, run_id: str) -> dict[str, str]:
        """Return virtual-path-prefix -> physical-dir mapping for artifacts root.
        
        Creates a virtual tree with:
          planner/{planner_run_id}/  -> artifacts/planner/{planner_run_id}/
          factory/{factory_run_id}/  -> artifacts/factory/{factory_run_id}/  (for each)
          pipeline/{pipeline_run_id}/ -> artifacts/pipeline/{pipeline_run_id}/
        """
        mapping: dict[str, str] = {}
        
        # Always include pipeline artifacts
        pipeline_dir = os.path.join(self._artifacts_dir, "pipeline", run_id)
        if os.path.isdir(pipeline_dir):
            mapping[f"pipeline/{run_id}"] = pipeline_dir
        
        if not self._run_store:
            return mapping
            
        try:
            meta = self._run_store.get(run_id)
        except FileNotFoundError:
            return mapping
        
        # Planner artifacts
        if meta.planner_run_id:
            planner_dir = os.path.join(self._artifacts_dir, "planner", meta.planner_run_id)
            if os.path.isdir(planner_dir):
                mapping[f"planner/{meta.planner_run_id}"] = planner_dir
        
        # Factory artifacts (one per WO execution)
        for fid in meta.factory_run_ids:
            factory_dir = os.path.join(self._artifacts_dir, "factory", fid)
            if os.path.isdir(factory_dir):
                mapping[f"factory/{fid}"] = factory_dir
        
        return mapping

    def _resolve_base(self, run_id: str, root: str) -> str:
        if root not in VALID_ROOTS:
            raise ValueError(f"Invalid root: {root!r}. Must be one of {VALID_ROOTS}")

        if root == "repo":
            return os.path.join(self._artifacts_dir, "pipeline", run_id, "repo")

        if root == "artifacts":
            # Special: handled by _get_artifacts_mapping for tree/read
            # Return artifacts_dir as a fallback (not used directly)
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

    def _resolve_artifacts_path(self, run_id: str, path: str) -> str | None:
        """Resolve a virtual artifacts path to a physical path."""
        mapping = self._get_artifacts_mapping(run_id)
        for prefix, phys_dir in mapping.items():
            if path == prefix or path == prefix + "/":
                return phys_dir
            if path.startswith(prefix + "/"):
                rel = path[len(prefix) + 1:]
                return os.path.join(phys_dir, rel)
        return None

    def tree(self, run_id: str, root: str) -> list[TreeEntry]:
        if root == "artifacts":
            return self._tree_artifacts(run_id)
        
        base = self._resolve_base(run_id, root)
        if not os.path.isdir(base):
            return []
        return self._walk_dir(base)

    def _tree_artifacts(self, run_id: str) -> list[TreeEntry]:
        """Build a virtual tree for artifacts root.

        Emits intermediate parent dirs (``planner/``, ``factory/``,
        ``pipeline/``) so the frontend tree renderer can discover them as
        top-level entries.
        """
        mapping = self._get_artifacts_mapping(run_id)
        if not mapping:
            return []

        emitted_parents: set[str] = set()
        entries: list[TreeEntry] = []

        for prefix, phys_dir in sorted(mapping.items()):
            # Emit the category parent dir (e.g. "planner/") once
            parent = prefix.split("/", 1)[0]
            if parent not in emitted_parents:
                emitted_parents.add(parent)
                entries.append(TreeEntry(path=parent + "/", type="dir"))

            # Emit the run-specific dir (e.g. "planner/01KJG.../")
            entries.append(TreeEntry(path=prefix + "/", type="dir"))

            # Walk the physical directory and prefix paths
            # Exclude "output" from planner dirs (redundant with work_orders root)
            # Exclude "repo" from pipeline dirs (redundant with repo root)
            extra_skip: set[str] = set()
            if parent == "planner":
                extra_skip.add("output")
            elif parent == "pipeline":
                extra_skip.add("repo")
            for entry in self._walk_dir(phys_dir, extra_skip=extra_skip):
                entries.append(TreeEntry(
                    path=f"{prefix}/{entry.path}",
                    type=entry.type,
                    size=entry.size,
                    line_count=entry.line_count,
                ))

        entries.sort(key=lambda e: (e.type != "dir", e.path.lower()))
        return entries

    def _walk_dir(self, base: str, extra_skip: set[str] | None = None) -> list[TreeEntry]:
        """Walk a physical directory and return entries with relative paths."""
        skip = SKIP_DIRS | (extra_skip or set())
        entries: list[TreeEntry] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in skip]
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
        return entries

    def read(self, run_id: str, root: str, path: str) -> bytes:
        if root == "artifacts":
            full = self._resolve_artifacts_path(run_id, path)
            if not full:
                raise FileNotFoundError(f"File not found: artifacts/{path}")
            full = os.path.realpath(full)
            base_real = os.path.realpath(self._artifacts_dir)
            if not (full == base_real or full.startswith(base_real + os.sep)):
                raise PermissionError("Path escapes root")
        else:
            base = self._resolve_base(run_id, root)
            full = os.path.realpath(os.path.join(base, path))
            base_real = os.path.realpath(base)
            if not (full == base_real or full.startswith(base_real + os.sep)):
                raise PermissionError("Path escapes root")
        
        if not os.path.isfile(full):
            raise FileNotFoundError(f"File not found: {root}/{path}")
        with open(full, "rb") as fh:
            return fh.read(MAX_FILE_READ_BYTES)

    def exists(self, run_id: str, root: str, path: str) -> bool:
        try:
            if root == "artifacts":
                full = self._resolve_artifacts_path(run_id, path)
                if not full:
                    return False
                full = os.path.realpath(full)
                base_real = os.path.realpath(self._artifacts_dir)
                return (full == base_real or full.startswith(base_real + os.sep)) and os.path.exists(full)
            else:
                base = self._resolve_base(run_id, root)
                full = os.path.realpath(os.path.join(base, path))
                base_real = os.path.realpath(base)
                return (full == base_real or full.startswith(base_real + os.sep)) and os.path.exists(full)
        except (ValueError, FileNotFoundError):
            return False

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
