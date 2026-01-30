"""handles loading work orders and preparing workspaces."""

import shutil
from pathlib import Path
from typing import Tuple
import yaml
from .schemas import WorkOrder
from .util import normalize_rel_path, safe_join


def load_work_order(path: str) -> Tuple[WorkOrder, str]:
    """loads a work order from markdown with yaml front-matter."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")

    meta = {}
    body = text

    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            raw_meta = parts[0].removeprefix("---\n")
            meta = yaml.safe_load(raw_meta) or {}
            body = parts[1]

    wo = WorkOrder.model_validate(meta)
    return wo, body.strip() + "\n"



def prepare_workspace(product_repo: Path, workspace_root: Path, run_id: str) -> Path:
    """creates a workspace by copying the product repo."""
    workspace = (workspace_root / run_id).resolve()
    if workspace.exists():
        raise RuntimeError(f"Workspace already exists: {workspace}")

    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(product_repo, workspace, dirs_exist_ok=False)
    return workspace


def apply_changes_back(product_repo: Path, workspace: Path, applied_changes: list[dict]) -> None:
    """
    Apply TR-reported changed paths back from workspace to product repo.

    Each item: {"path": "<relative>", "action": "create|replace|delete"}.
    """
    product_repo = product_repo.resolve()
    workspace = workspace.resolve()

    for ch in applied_changes:
        rel = normalize_rel_path(str(ch.get("path", "")))
        action = str(ch.get("action", ""))

        src = safe_join(workspace, rel)
        dst = safe_join(product_repo, rel)

        if action == "delete":
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            continue

        # create / replace
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
