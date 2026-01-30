"""handles work order loading and workspace setup."""

import shutil
from pathlib import Path
from typing import Tuple
import yaml
from schemas import WorkOrder


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
