"""entry point for running the se -> tr -> po loop."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from graph import build_graph
from llm import get_model
from workspace import load_work_order, prepare_workspace


def _resolve_repo(repo_arg: str | None, wo_repo: str | None, wo_path: Path) -> Path:
    if repo_arg:
        return Path(repo_arg).expanduser().resolve()
    if wo_repo:
        p = Path(wo_repo).expanduser()
        return p.resolve() if p.is_absolute() else (wo_path.parent / p).resolve()
    raise ValueError("No target repo provided. Use --repo or set repo: in work order front-matter.")


def main():
    parser = argparse.ArgumentParser(prog="prototype")
    parser.add_argument("--work-order", required=True, help="Path to work order markdown.")
    parser.add_argument("--repo", default=None, help="Path to product repo (overrides work order repo).")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument(
        "--workspace-root",
        default=str(Path.home() / ".langgraph-prototype" / "workspaces"),
        help="Where to create per-run workspaces.",
    )

    args = parser.parse_args()

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
    print(f"\nFINAL: {po.get('decision')}")
    print(f"Product repo: {product_repo}")
    print(f"Workspace:    {workspace}")


if __name__ == "__main__": 
    main()
