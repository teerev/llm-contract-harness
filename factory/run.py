import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from .graph import build_graph
from .llm import get_model
from .workspace import apply_changes_back, load_work_order, prepare_workspace


def _resolve_repo(repo_arg: str | None, wo_repo: str | None, wo_path: Path) -> Path:
    if repo_arg:
        return Path(repo_arg).expanduser().resolve()
    if wo_repo:
        p = Path(wo_repo).expanduser()
        return p.resolve() if p.is_absolute() else (wo_path.parent / p).resolve()
    raise ValueError("No target repo provided. Use --repo or set repo: in work order front-matter.")


def main(argv: Sequence[str] | None = None) -> None:
    """
    entry point for `python -m factory`.
    """
    parser = argparse.ArgumentParser(prog="python -m factory")
    parser.add_argument("--work-order", required=True, help="Path to work order markdown.")
    parser.add_argument("--repo", default=None, help="Path to product repo (overrides work order repo).")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument(
        "--workspace-root",
        default=str(Path.home() / ".langgraph-prototype" / "workspaces"),
        help="Where to create per-run workspaces.",
    )
    parser.add_argument(
        "--apply-back",
        dest="apply_back",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Copy changes back on PASS. Use --no-apply-back to disable.",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    wo, body = load_work_order(args.work_order)
    wo_path = Path(args.work_order).resolve()
    product_repo = _resolve_repo(args.repo, wo.repo, wo_path)

    if not product_repo.is_dir():
        raise ValueError(f"Product repo is not a directory: {product_repo}")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    workspace = prepare_workspace(product_repo, Path(args.workspace_root).expanduser().resolve(), run_id)

    result = build_graph(get_model()).invoke(
        {
            "repo_path": str(workspace),
            "work_order": wo.model_dump(),
            "work_order_body": body,
            "iteration": 0,
            "max_iterations": args.max_iterations,
        }
    )

    po = result.get("po_report") or {}
    decision = po.get("decision")

    if decision == "PASS" and args.apply_back:
        applied = (result.get("tool_report") or {}).get("applied", [])
        apply_changes_back(product_repo, workspace, applied)

    print(f"\nFINAL: {decision}")
    print(f"Product repo: {product_repo}")
    print(f"Workspace:    {workspace}")
